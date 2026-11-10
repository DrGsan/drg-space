"""Kernel-owned coordination for workers sharing one local data directory.

Lock files are stable inodes: never unlink or replace them while the application
is running. Every holder opens its own descriptor, including within one process.
"""
import asyncio
import fcntl
import os
import re
import shutil
import stat
import tempfile
import threading
import weakref
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path


POLL_SECONDS = 0.01


def open_lock(root, name):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', name):
        raise ValueError('Invalid process lock name.')
    directory = Path(root) / '.drg-process-locks'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise OSError('Process lock directory must not be a symbolic link.')
    return _open_file(directory / name)


def _open_file(path):
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError('Process lock must be a regular file.')
    return descriptor


def try_lock(descriptor, shared=False):
    try:
        fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


class FileLease:
    """A synchronous, idempotent release usable after the async loop stops."""
    def __init__(self, descriptor):
        self.fd = descriptor
        self._mutex = threading.Lock()

    def close(self):
        with self._mutex:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def try_file_lock(root, name, shared=False):
    lease = FileLease(open_lock(root, name))
    try:
        if try_lock(lease.fd, shared):
            return lease
    except BaseException:
        lease.close()
        raise
    lease.close()
    return None


@asynccontextmanager
async def async_file_lock(root, name, shared=False):
    lease = FileLease(open_lock(root, name))
    try:
        while not try_lock(lease.fd, shared):
            await asyncio.sleep(POLL_SECONDS)
        yield lease
    finally:
        lease.close()


@contextmanager
def sync_file_lock(root, name, shared=False):
    """Short constructor critical sections, before an async runtime exists."""
    with FileLease(open_lock(root, name)) as lease:
        fcntl.flock(lease.fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield lease


@contextmanager
def _temp_registry(root):
    # Synchronous callers hold this only while installing or claiming a lease.
    with sync_file_lock(root, 'temporary-directories.lock'):
        yield


def _remove_owned(directory, lease):
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        pass
    finally:
        lease.close()


class LeasedTemporaryDirectory:
    """TemporaryDirectory API with a lifetime lease for pack and download."""
    def __init__(self, root, prefix):
        if not prefix or '/' in prefix or '\\' in prefix:
            raise ValueError('Invalid temporary directory prefix.')
        root = Path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _temp_registry(root):
            self.name = tempfile.mkdtemp(prefix=prefix, dir=root)
            lease = None
            try:
                lease = FileLease(_open_file(Path(self.name) / '.owner-lock'))
                if not try_lock(lease.fd):
                    raise RuntimeError('New temporary directory is already leased.')
            except BaseException:
                shutil.rmtree(self.name)
                if lease:
                    lease.close()
                raise
        self._cleanup_mutex = threading.Lock()
        self._finalizer = weakref.finalize(self, _remove_owned, self.name, lease)

    def cleanup(self):
        with self._cleanup_mutex:
            self._finalizer()

    def __enter__(self):
        return self.name

    def __exit__(self, *_exc):
        self.cleanup()


def leased_temp(root, prefix):
    return LeasedTemporaryDirectory(root, prefix)


def cleanup_temps(root, prefix):
    """Remove orphan stages, skipping even a live pack/download outside gate."""
    if not prefix or '/' in prefix or '\\' in prefix:
        raise ValueError('Invalid temporary directory prefix.')
    root = Path(root)
    for directory in list(root.glob(prefix + '*')):
        lease = None
        with _temp_registry(root):
            if directory.is_symlink() or not directory.is_dir():
                continue
            try:
                lease = FileLease(_open_file(directory / '.owner-lock'))
            except FileNotFoundError:
                continue
            try:
                if not try_lock(lease.fd):
                    lease.close()
                    continue
            except BaseException:
                lease.close()
                raise
        # Keep the orphan's lease until rmtree finishes, but release the short
        # registry lock so a large abandoned archive cannot block new exports.
        _remove_owned(directory, lease)
