"""Disposable, encrypted and bounded local derivatives of immutable file objects."""
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import stat
import time

from app.data_crypto import StorageError
from app.file_store import object_id
from app.process_locks import sync_file_lock, try_file_lock

logger = logging.getLogger(__name__)
FORMAT = hashlib.sha256(b'webp:256:quality80:exif-transpose:v1').hexdigest()[:16]
ENTRY = re.compile(r'([a-f0-9]{32})-[a-f0-9]{16}')
MAGIC = b'DRG-THUMB-01\0'


class ThumbnailCache:
    """Shared by processes on one data volume; never a source of authorization."""
    def __init__(self, store, *, maximum=256*1024**2, items=4096, item_limit=256*1024,
                 ttl=7*86400, clock=time.time):
        if not store.thumbnail_derivatives:
            raise StorageError('Ordinary thumbnails are unavailable for this storage namespace.')
        self.base, self.cipher, self.storage_id = Path(store.base), store.cipher, store.storage_id
        self.root = self.base / 'file-thumbnails'
        self.maximum, self.items, self.item_limit, self.ttl = maximum, items, item_limit, ttl
        self.clock = clock

    def lock(self, identity):
        identity = object_id(identity)
        # Fixed inodes: lock files must not grow with the number of source files.
        return sync_file_lock(self.base, f'file-thumbnail-{int(identity[:2],16)%64:02x}.lock')

    def _directory(self, create=False):
        if self.root.is_symlink():
            raise StorageError('Thumbnail cache must not be a symbolic link.')
        if create:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return self.root.is_dir()

    def path(self, identity):
        return self.root / (object_id(identity) + '-' + FORMAT)

    def context(self, name):
        return 'file-thumbnail:v1:' + self.storage_id + ':' + name

    @staticmethod
    def _read(path, maximum):
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise StorageError('Invalid thumbnail cache entry.')
            return stream.read(maximum + 1), info

    def read(self, identity):
        path = self.path(identity)
        try:
            body, info = self._read(path, self.item_limit + 128)
            if self.clock() - info.st_mtime > self.ttl:
                path.unlink(missing_ok=True)
                return None
            packed = self.cipher.open(body, self.context(path.name))
            prefix = len(MAGIC) + 32
            decoded = packed[prefix:]
            if (not packed.startswith(MAGIC) or len(decoded) > self.item_limit
                    or packed[len(MAGIC):prefix] != hashlib.sha256(decoded).digest()):
                raise StorageError('Invalid thumbnail cache size.')
            if self.clock() - info.st_mtime > 60:
                os.utime(path, (self.clock(), self.clock()), follow_symlinks=False)
            return decoded
        except FileNotFoundError:
            return None
        except StorageError:
            path.unlink(missing_ok=True)
            return None

    @contextmanager
    def _build_slot(self):
        while True:
            for index in range(2):
                lease = try_file_lock(self.base, f'file-thumbnail-build-{index}.lock')
                if lease is not None:
                    with lease:
                        yield
                    return
            time.sleep(.01)

    def get(self, identity, build):
        """Only callers that already checked access may enter; recheck before reply."""
        self._directory(create=True)
        with self.lock(identity):
            try:
                cached = self.read(identity)
            except OSError:
                cached = None
            if cached is not None:
                return cached
            with self._build_slot():
                body = build()
            if len(body) <= self.item_limit:
                try:
                    self._put(identity, body)
                except OSError:
                    # A disposable cache may be full/read-only; the authorized
                    # original still supplies the response. No plaintext is written.
                    logger.warning('Could not persist a disposable file thumbnail.')
            return body

    def _atomic(self, path, body):
        temporary = self.root / ('.tmp-' + secrets.token_hex(16))
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _index(self):
        try:
            raw, _ = self._read(self.root/'index', 4096)
            value = json.loads(self.cipher.open(raw, self.context('index')))
            if (value['version'] == 1 and type(value['bytes']) is int and value['bytes'] >= 0
                    and type(value['items']) is int and value['items'] >= 0
                    and isinstance(value['checked'], (int,float)) and 0 <= value['checked'] <= self.clock()):
                return value
        except (OSError, StorageError, ValueError, KeyError, TypeError):
            pass
        return None

    def _save_index(self, value):
        self._atomic(self.root/'index', self.cipher.seal(json.dumps(value).encode(), self.context('index')))

    def _sweep(self, needed=0, referenced=None):
        entries = []
        for path in self.root.iterdir():
            match = ENTRY.fullmatch(path.name)
            if not match:
                if re.fullmatch(r'\.tmp-[a-f0-9]{32}', path.name):
                    path.unlink(missing_ok=True)  # All publishers hold the budget lock.
                continue
            try:
                info = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_size > self.item_limit+128
                    or self.clock()-info.st_mtime > self.ttl
                    or referenced is not None and match[1] not in referenced):
                path.unlink(missing_ok=True)
            else:
                entries.append((info.st_mtime, path, info.st_size))
        size = sum(entry[2] for entry in entries)
        entries.sort(key=lambda entry:(entry[0],entry[1].name))
        while entries and (size+needed > self.maximum or len(entries)+bool(needed) > self.items):
            _, path, length = entries.pop(0)
            path.unlink(missing_ok=True)
            size -= length
        return {'version':1,'bytes':size,'items':len(entries),'checked':self.clock()}

    def _put(self, identity, body):
        path = self.path(identity)
        encoded = self.cipher.seal(MAGIC+hashlib.sha256(body).digest()+body, self.context(path.name))
        if len(encoded) > self.maximum or self.items < 1:
            return
        with sync_file_lock(self.base, 'file-thumbnail-budget.lock'):
            value = self._index()
            if (value is None or self.clock()-value['checked'] >= 3600
                    or value['bytes']+len(encoded) > self.maximum or value['items']+1 > self.items):
                value = self._sweep(len(encoded))
            # Reserve BEFORE publication. A crash can overcount (safe), never
            # publish an unaccounted entry. The next sweep repairs the index.
            value['bytes'] += len(encoded)
            value['items'] += 1
            self._save_index(value)
            self._atomic(path, encoded)

    def discard_locked(self, identity):
        """Called with the source lock, including before physical source deletion."""
        identity = object_id(identity)
        if self._directory():
            for path in self.root.glob(identity+'-*'):
                if ENTRY.fullmatch(path.name):
                    path.unlink(missing_ok=True)

    def prune(self, referenced=None):
        if not self._directory():
            return
        with sync_file_lock(self.base, 'file-thumbnail-budget.lock'):
            value = self._index()
            if referenced is not None or value is None or self.clock()-value['checked'] >= 3600:
                self._save_index(self._sweep(referenced=referenced))
