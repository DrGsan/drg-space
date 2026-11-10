"""Coordinate consistent snapshots across workers sharing a local data root."""
from app.i18n import message as localized_message
import asyncio
import contextvars
import fcntl
import threading
from contextlib import asynccontextmanager, contextmanager
from functools import partial
from app.http import HTTPError
from app.process_locks import FileLease, open_lock, try_lock, POLL_SECONDS


async def blocking(function, *args, **kwargs):
    """Do not release storage leases while a cancelled disk thread still runs."""
    context = contextvars.copy_context()
    # An executor Future is not an asyncio Task: runtime shutdown must cancel
    # the waiting handler, not independently cancel a to_thread child Task.
    worker = asyncio.get_running_loop().run_in_executor(None, partial(context.run, function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        worker.result()
        raise


class GateLease:
    def __init__(self, gate, file=None):
        self.gate, self.file = gate, file
        self._closed = False
        self._mutex = threading.Lock()

    def close(self):
        """Response.close may run in a Flask thread after the async loop stops."""
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            try:
                if self.file:
                    self.file.close()
            finally:
                with self.gate._local_mutex:
                    self.gate.active -= 1


class StorageGate:
    def __init__(self, root=None):
        self.root = root
        self.active = 0  # Local diagnostic count; kernel locks track all workers.
        self._local_pending = False
        self._local_mutex = threading.Lock()

    @property
    def exclusive_pending(self):
        with self._local_mutex:
            if self._local_pending:
                return True
        if self.root is None:
            return False
        with FileLease(open_lock(self.root, 'storage-entry.lock')) as entry:
            return not try_lock(entry.fd, shared=True)

    def acquire_shared(self):
        file = None
        if self.root is None:
            with self._local_mutex:
                if self._local_pending:
                    raise HTTPError(503, localized_message('operationWithDataIsInProgressRetryLater'))
                self.active += 1
        else:
            with FileLease(open_lock(self.root, 'storage-entry.lock')) as entry:
                if not try_lock(entry.fd, shared=True):
                    raise HTTPError(503, localized_message('operationWithDataIsInProgressRetryLater'))
                file = FileLease(open_lock(self.root, 'storage-data.lock'))
                try:
                    if not try_lock(file.fd, shared=True):
                        raise HTTPError(503, localized_message('operationWithDataIsInProgressRetryLater'))
                except BaseException:
                    file.close()
                    raise
                with self._local_mutex:
                    self.active += 1
        return GateLease(self, file)

    @asynccontextmanager
    async def shared(self):
        lease = self.acquire_shared()
        try:
            yield
        finally:
            lease.close()

    @asynccontextmanager
    async def shared_wait(self):
        """Keep established sockets alive while another worker owns storage."""
        while True:
            try:
                lease=self.acquire_shared()
                break
            except HTTPError as error:
                if error.status_code!=503:raise
                await asyncio.sleep(POLL_SECONDS)
        try:
            yield
        finally:
            lease.close()

    def _begin_exclusive(self):
        if self.root is None:
            with self._local_mutex:
                if self._local_pending:
                    raise HTTPError(423, localized_message('operationWithDataIsAlreadyInProgressRetry'))
                self._local_pending = True
            return None, None
        entry = FileLease(open_lock(self.root, 'storage-entry.lock'))
        try:
            if not try_lock(entry.fd):
                raise HTTPError(423, localized_message('operationWithDataIsAlreadyInProgressRetry'))
            data = FileLease(open_lock(self.root, 'storage-data.lock'))
        except BaseException:
            entry.close()
            raise
        with self._local_mutex:
            self._local_pending = True
        return entry, data

    def _end_exclusive(self, entry, data):
        if data:
            data.close()
        if entry:
            entry.close()
        with self._local_mutex:
            self._local_pending = False

    @asynccontextmanager
    async def exclusive(self):
        entry, data = self._begin_exclusive()
        try:
            if data:
                while not try_lock(data.fd):
                    await asyncio.sleep(POLL_SECONDS)
            else:
                while True:
                    with self._local_mutex:
                        if self.active == 0:
                            break
                    await asyncio.sleep(POLL_SECONDS)
            yield
        finally:
            self._end_exclusive(entry, data)

    @contextmanager
    def exclusive_sync(self):
        """Initialization before the async runtime starts uses the same gate."""
        if self.root is None:
            raise RuntimeError('Synchronous initialization requires a data root.')
        # Startup workers wait their turn instead of treating another worker's
        # initialization as a conflicting user-requested restore.
        with FileLease(open_lock(self.root, 'storage-entry.lock')) as entry:
            fcntl.flock(entry.fd, fcntl.LOCK_EX)
            with FileLease(open_lock(self.root, 'storage-data.lock')) as data:
                fcntl.flock(data.fd, fcntl.LOCK_EX)
                yield

    @asynccontextmanager
    async def try_exclusive(self):
        """Background work defers immediately instead of draining readers.

        A failed probe releases admission before yielding to the caller. User
        restore/export operations continue to use the waiting exclusive gate.
        """
        try:entry,data=self._begin_exclusive()
        except HTTPError as error:
            if error.status_code!=423:raise
            yield False
            return
        owned=True
        try:
            if data:available=try_lock(data.fd)
            else:
                with self._local_mutex:available=self.active==0
            if not available:
                self._end_exclusive(entry,data);owned=False
            yield available
        finally:
            if owned:self._end_exclusive(entry,data)


class StorageMiddleware:
    def __init__(self, gate):
        self.gate = gate

    async def handle(self, request, call_next, runtime):
        # Upload/restore handlers acquire exclusive access after authentication.
        controls = {'/api/account/deletion','/api/admin/backups/export', '/api/admin/backups/restore', '/api/admin/users/delete',
                    '/api/account/data/export', '/api/account/data/preview', '/api/account/data/import',
                    '/api/account/notion/preview', '/api/account/notion/import',
                    '/api/account/outline/preview', '/api/account/outline/import'}
        if request.url.path == '/api/health' or request.url.path in controls:
            return await call_next(request)
        try:
            lease = self.gate.acquire_shared()
        except HTTPError as error:
            from app.storage_unavailable import unavailable
            return unavailable(request,error)
        try:
            response = await call_next(request)
            if response.is_streamed:
                # Kernel lease survives the coroutine and does not depend on
                # runtime.run() remaining available during shutdown.
                response.call_on_close(lease.close)
            else:
                lease.close()
            return response
        except BaseException:
            lease.close()
            raise
