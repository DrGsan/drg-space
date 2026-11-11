"""Invalidations and socket leases for workers sharing one local data directory.

PostgreSQL carries only a wake-up signal. Documents/messages remain canonical in
the database, and a periodic reconciliation also recovers missed notifications.
Socket liveness is a kernel-held file lock: no PID reuse or guessed heartbeat
expiry can evict a healthy peer. Private cursor metadata is encrypted at rest.
"""
import asyncio
import contextvars
import fcntl
import hashlib
import json
import logging
import os
import secrets
from contextlib import contextmanager, suppress
from functools import partial
from pathlib import Path

from app.data_crypto import StorageError

logger = logging.getLogger(__name__)


def channel_for(schema, storage_id):
    return 'drg_' + hashlib.sha256((schema + ':' + storage_id).encode()).hexdigest()[:48]


def notify_transaction(connection):
    """Called before commit; PostgreSQL publishes only a committed invalidation."""
    backend = connection.backend
    if backend.kind == 'postgres' and getattr(backend, 'storage_id', None):
        connection.raw.execute('SELECT pg_notify(%s,%s)',
                               (channel_for(backend.schema, backend.storage_id), 'changed'))


class PeerLimit(Exception):
    pass


class PeerCollision(Exception):
    pass


class PeerSuperseded(Exception):
    pass


class PeerLease:
    def __init__(self, coordinator, token, fd):
        self.coordinator, self.token, self.fd = coordinator, token, fd

    async def update(self, metadata):
        await self.coordinator._operation(self.coordinator._update, self.token, metadata)

    async def is_current(self):
        return await self.coordinator._operation(self.coordinator._current, self.token)

    async def close(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            await self.coordinator._operation(self.coordinator._remove, self.token)
        finally:
            os.close(fd)
            self.coordinator._leases.discard(self)


class ProcessCoordinator:
    def __init__(self, database, root, cipher, gate):
        self.database, self.cipher, self.gate = database, cipher, gate
        storage_id = getattr(database, 'storage_id', None)
        if not storage_id:
            storage_id = json.loads((Path(root) / '.storage-format.json').read_text())['storage_id']
        self.channel = channel_for(database.schema, storage_id)
        parent = Path(root) / '.process-state'
        if parent.is_symlink():
            raise StorageError('Worker coordination directory must not be a symbolic link.')
        parent.mkdir(mode=0o700, exist_ok=True)
        self.root = parent / self.channel
        if self.root.is_symlink():
            raise StorageError('Worker coordination directory must not be a symbolic link.')
        self.root.mkdir(mode=0o700, exist_ok=True)
        self._callbacks, self._leases, self._tasks = set(), set(), []
        self._change_callbacks = {}
        self.listening = False
        self._changed = asyncio.Event()
        self._subscription_ready = asyncio.Event()

    def subscribe(self, callback):
        self._callbacks.add(callback)
        self._changed.set()

    def unsubscribe(self, callback):
        self._callbacks.discard(callback)

    def subscribe_changes(self, callback, *, fallback=1):
        """Durable-data consumers: signals, reconnect, and a bounded recovery poll."""
        self._change_callbacks[callback]=(fallback,0)
        self._changed.set()

    def unsubscribe_changes(self, callback):
        self._change_callbacks.pop(callback,None)

    @property
    def recovery_interval(self):
        return 30 if self.listening else 1

    async def start(self):
        if self._tasks:
            return
        self._subscription_ready.clear()
        self._tasks.append(asyncio.create_task(self._reconcile(), name='worker-reconciliation'))
        if self.database.kind == 'postgres':
            self._tasks.append(asyncio.create_task(self._listen(), name='postgres-invalidations'))

    async def stop(self):
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for lease in tuple(self._leases):
            await lease.close()

    async def _listen(self):
        import psycopg
        delay=1
        while True:
            try:
                # One dedicated connection, opened after fork. Never query it
                # while the notification generator owns its connection lock.
                async with await psycopg.AsyncConnection.connect(
                        self.database.url, autocommit=True, connect_timeout=10,
                        application_name='drg-space-invalidations-' + self.channel[-24:]) as connection:
                    await connection.execute('LISTEN ' + self.channel)
                    delay=1
                    self.listening=True
                    self._subscription_ready.set()
                    self._changed.set()  # Subscribe first, then resync the gap.
                    async for _notification in connection.notifies():
                        self._changed.set()
            except (psycopg.Error, OSError):
                self.listening=False
                logger.warning('Worker invalidation connection unavailable; retrying.')
                self._subscription_ready.set()  # Fall back to reconciliation.
                self._changed.set()
                await asyncio.sleep(delay)
                delay=min(30,delay*2)
            finally:
                self.listening=False

    async def _reconcile(self):
        if self.database.kind == 'postgres':
            await self._subscription_ready.wait()
        while True:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._changed.wait(), 1)
            changed=self._changed.is_set()
            self._changed.clear()
            callbacks=tuple(self._callbacks)
            stamp=asyncio.get_running_loop().time()
            for callback,(fallback,deadline) in tuple(self._change_callbacks.items()):
                if changed or stamp>=deadline:
                    callbacks+=(callback,)
                    self._change_callbacks[callback]=(fallback,stamp+(30 if self.listening else fallback))
            for callback in callbacks:
                try:
                    await callback()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Retry on the next tick without leaking documents or keys
                    # through exception text from a failed storage operation.
                    logger.warning('Worker reconciliation failed; will retry.')

    @contextmanager
    def _guard(self):
        fd = os.open(self.root / '.registry.lock',
                     os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    async def _operation(self, function, *args):
        # A cancelled socket still must finish registration/cleanup, otherwise
        # an acquired lifetime lock could be orphaned inside its live worker.
        context=contextvars.copy_context()
        # Runtime shutdown cancels all Tasks. An executor Future remains owned
        # by this handler, so its running thread cannot lose an acquired lease.
        worker=asyncio.get_running_loop().run_in_executor(None,partial(context.run,function,*args))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:await asyncio.shield(worker)
                except asyncio.CancelledError:continue
            result=worker.result()
            if isinstance(result, PeerLease):
                await result.close()
            raise

    def _path(self, token):
        return self.root / (token + '.state')

    def _write(self, token, record):
        temporary = self.root / (token + '.' + secrets.token_hex(8) + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(self.cipher.seal(json.dumps(record, ensure_ascii=False).encode(),
                                              'worker-peer:' + self.channel + ':' + token))
                stream.flush()
            os.replace(temporary, self._path(token))
        finally:
            temporary.unlink(missing_ok=True)

    def _records(self):
        """Called under the registry guard; collect dead workers and leases."""
        records = []
        for path in self.root.glob('*.lease'):
            token = path.stem
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    state = self._path(token)
                    if not state.exists():  # Superseded but still open TCP socket.
                        continue
                    if state.is_symlink():
                        raise StorageError('Invalid worker peer metadata.')
                    record = json.loads(self.cipher.open(state.read_bytes(),
                                        'worker-peer:' + self.channel + ':' + token))
                    record['token'] = token
                    record['room'] = tuple(record['room'])
                    records.append(record)
                else:
                    self._path(token).unlink(missing_ok=True)
                    path.unlink(missing_ok=True)
            finally:
                os.close(fd)
        return records

    def _register(self, scope, uid, room, cid, metadata):
        with self._guard():
            records = self._records()
            replaced = [r for r in records if r['scope'] == scope and r['room'] == tuple(room) and r['cid'] == cid]
            if any(r['uid'] != uid for r in replaced):
                raise PeerCollision()
            if sum(r['scope'] == scope and r['uid'] == uid for r in records if r not in replaced) >= 12:
                raise PeerLimit()
            token = secrets.token_hex(24)
            path = self.root / (token + '.lease')
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._write(token, {'scope': scope, 'uid': uid, 'room': list(room),
                                    'cid': cid, 'metadata': metadata or {}})
                for old in replaced:
                    self._path(old['token']).unlink(missing_ok=True)
            except BaseException:
                os.close(fd)
                path.unlink(missing_ok=True)
                self._path(token).unlink(missing_ok=True)
                raise
            lease = PeerLease(self, token, fd)
            self._leases.add(lease)
            return lease

    async def register_peer(self, scope, uid, room, cid, metadata=None):
        lease = await self._operation(self._register, scope, uid, room, cid, metadata)
        self._changed.set()
        return lease

    def _update(self, token, metadata):
        with self._guard():
            record = next((r for r in self._records() if r['token'] == token), None)
            if record is None:
                raise PeerSuperseded()
            record['metadata'] = metadata
            record.pop('token')
            self._write(token, record)

    def _current(self, token):
        with self._guard():
            return any(r['token'] == token for r in self._records())

    def _remove(self, token):
        with self._guard():
            self._path(token).unlink(missing_ok=True)
            (self.root / (token + '.lease')).unlink(missing_ok=True)

    def _peers(self, scope, room):
        with self._guard():
            return [r for r in self._records() if r['scope'] == scope and (room is None or r['room'] == tuple(room))]

    async def peers(self, scope, room=None):
        return await self._operation(self._peers, scope, room)

    def peers_sync(self, scope, room=None):
        """Inspect live leases from a synchronous HTTP/database operation."""
        return self._peers(scope, room)
