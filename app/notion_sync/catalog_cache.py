"""Disposable, encrypted directory snapshots shared by local app workers."""
from contextlib import suppress
import hmac
import json
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile
import time

from app.data_crypto import DataCipher, StorageError
from app.notion_sync.client import NotionError
from app.process_locks import try_file_lock


TTL = 300
REFRESH_INTERVAL = 30
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024


class CatalogCache:
    def __init__(self, data_dir, secret, *, clock=time.time):
        self.root = Path(data_dir) / '.drg-notion-catalog'
        self.data_dir = Path(data_dir)
        self.clock = clock
        self.cipher = DataCipher(hmac.digest(secret.encode(), b'drg/notion/catalog/v1', 'sha256'))

    def _lock(self, key):
        # Stable stripes keep the number of kernel lock files independent of
        # the number of accounts and credential rotations.
        return try_file_lock(self.data_dir, f'notion-catalog-{key[:2]}.lock')

    def _read(self, path, scope, refresh):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SNAPSHOT_BYTES:
                    return None
                body = source.read(info.st_size + 1)
            if len(body) != info.st_size:
                return None
            grant, created, result = json.loads(self.cipher.open(body, path.stem))
            lifetime = REFRESH_INTERVAL if refresh else TTL
            if (grant == scope.key and type(created) in (int, float) and 0 <= self.clock() - created < lifetime
                    and isinstance(result, dict) and isinstance(result.get('items'), list)):
                return result
        except (OSError, StorageError, ValueError, TypeError):
            return None

    def _write(self, path, scope, result):
        body = self.cipher.seal(json.dumps([scope.key, self.clock(), result], ensure_ascii=False).encode(), path.stem)
        if len(body) > MAX_SNAPSHOT_BYTES:
            return
        temporary = None
        try:
            with NamedTemporaryFile(dir=self.root, prefix=path.stem + '.', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                output.write(body)
            os.replace(temporary, path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def get(self, key, scope, refresh, read_scope, build):
        try:
            self.root.mkdir(mode=0o700, exist_ok=True)
            if self.root.is_symlink():
                return build()
        except OSError:
            return build()

        path = self.root / (key + '.snapshot')
        deadline = time.monotonic() + 35
        while True:
            stored = self._read(path, scope, refresh)
            if stored is not None:
                return scope, stored
            try:
                lease = self._lock(key)
            except OSError:
                return build()
            if lease is not None:
                with lease:
                    scope = read_scope()
                    stored = self._read(path, scope, refresh)
                    if stored is not None:
                        return scope, stored
                    scope, result = build()
                    with suppress(OSError):
                        self._write(path, scope, result)
                    return scope, result
            if time.monotonic() >= deadline:
                raise NotionError('notionRetryLater', 503, 5)
            time.sleep(.05)

    def prune(self):
        if self.root.is_symlink() or not self.root.is_dir():
            return
        cutoff = self.clock() - TTL
        for path in self.root.iterdir():
            key, _, suffix = path.name.partition('.')
            if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
                continue
            if suffix != 'snapshot' and not suffix.endswith('.tmp'):
                continue
            with suppress(OSError):
                if path.lstat().st_mtime > cutoff:
                    continue
                lease = self._lock(key)
                if lease is not None:
                    with lease:
                        if path.lstat().st_mtime <= cutoff:
                            path.unlink()
