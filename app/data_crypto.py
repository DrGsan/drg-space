"""Authenticated storage encryption. App keys never enter archives or PostgreSQL queries."""
import base64
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, AESSIV
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


class StorageError(RuntimeError):
    """Safe to report without leaking keys, connection strings or user content."""


def enabled_setting(value):
    if value.lower() in ('true', '1', 'yes'): return True
    if value.lower() in ('false', '0', 'no'): return False
    raise StorageError('DATA_ENCRYPTION_ENABLED must be true or false.')


class DataCipher:
    MAGIC = b'DRG-DATA-01\x00'
    TEXT = 'drg:encrypted:v1:'

    def __init__(self, key=None, enabled=True):
        self.enabled = enabled
        if enabled:
            if not isinstance(key, bytes) or len(key) != 32:
                raise StorageError('Storage encryption requires a 32-byte key.')
            self.key = key
            self.sqlite_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                                   info=b'drg/storage/sqlcipher/v1').derive(key)
            self.aead = AESGCM(key)
            self.siv = AESSIV(HKDF(algorithm=hashes.SHA256(), length=64, salt=None,
                                  info=b'drg/storage/lookup/v1').derive(key))

    @classmethod
    def from_env(cls):
        enabled = enabled_setting(os.getenv('DATA_ENCRYPTION_ENABLED', 'true'))
        if not enabled: return cls(enabled=False)
        value = os.getenv('DATA_ENCRYPTION_KEY', '')
        try:
            key = base64.b64decode(value.encode('ascii'), altchars=b'-_', validate=True)
        except (ValueError, UnicodeError):
            raise StorageError('DATA_ENCRYPTION_KEY must be a base64-encoded 32-byte key; use scripts/init-env.py.') from None
        if len(key) != 32:
            raise StorageError('DATA_ENCRYPTION_KEY must be a base64-encoded 32-byte key; use scripts/init-env.py.')
        return cls(key)

    @classmethod
    def ephemeral(cls): return cls(secrets.token_bytes(32))

    def seal(self, body, context):
        if not self.enabled: return body
        nonce = secrets.token_bytes(12)
        return self.MAGIC + nonce + self.aead.encrypt(nonce, body, self.MAGIC + context.encode())

    def open(self, body, context):
        if not self.enabled: return body
        if not body.startswith(self.MAGIC) or len(body) < len(self.MAGIC) + 28:
            raise StorageError('Encrypted storage contains unencrypted or invalid data; automatic conversion is disabled.')
        offset = len(self.MAGIC)
        try: return self.aead.decrypt(body[offset:offset+12], body[offset+12:], self.MAGIC + context.encode())
        except InvalidTag:
            raise StorageError('Storage authentication failed: wrong key, changed context or damaged data.') from None

    def encode(self, value, context, deterministic=False):
        if value is None or not self.enabled: return value
        if isinstance(value, bytes): return self.seal(value, context)
        if not isinstance(value, str): raise StorageError('Unsupported encrypted column value.')
        if deterministic:
            body = self.siv.encrypt(value.encode(), [context.encode()])
            return self.TEXT + 's:' + base64.urlsafe_b64encode(body).decode()
        return self.TEXT + 'g:' + base64.urlsafe_b64encode(self.seal(value.encode(), context)).decode()

    def decode(self, value, context, deterministic=False):
        if value is None or not self.enabled: return value
        if isinstance(value, memoryview): value = bytes(value)
        if isinstance(value, bytes): return self.open(value, context)
        prefix = self.TEXT + ('s:' if deterministic else 'g:')
        if not isinstance(value, str) or not value.startswith(prefix):
            raise StorageError('Encrypted column contains invalid data; plaintext fallback is disabled.')
        try:
            body = base64.b64decode(value[len(prefix):].encode(), altchars=b'-_', validate=True)
            if deterministic: return self.siv.decrypt(body, [context.encode()]).decode()
            return self.open(body, context).decode()
        except (InvalidTag, ValueError, UnicodeError):
            raise StorageError('Encrypted column authentication failed.') from None


class MediaStore:
    def __init__(self, root, cipher, objects=None):
        self.root = Path(root) / 'media'
        if self.root.is_symlink():raise StorageError('Media directory must not be a symbolic link.')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cipher = cipher
        self.objects = objects
        if objects is None and os.getenv('FILE_STORAGE', 'local') == 's3':
            from app.file_store import FileStore
            self.objects = FileStore(root, cipher)

    def key(self, asset_id):
        from app.file_store import object_id
        return '/'.join(filter(None, (self.objects.prefix, self.objects.storage_id, 'media', object_id(asset_id))))

    def local_ids(self):
        import re
        return {p.name for p in self.root.iterdir() if p.is_file() and not p.is_symlink() and re.fullmatch(r'[a-f0-9]{32}', p.name)}

    def ids(self):
        found = self.local_ids()
        if self.objects:
            prefix = self.key('0'*32)[:-32]
            try:
                import re
                for page in self.objects.client.get_paginator('list_objects_v2').paginate(Bucket=self.objects.bucket, Prefix=prefix):
                    for item in page.get('Contents', []):
                        name = item['Key'][len(prefix):]
                        if re.fullmatch(r'[a-f0-9]{32}', name):found.add(name)
            except Exception:raise StorageError('Could not list private media storage.') from None
        return found

    def read(self, asset_id):
        from app.file_store import object_id
        object_id(asset_id)
        path = self.root / asset_id
        # Legacy volume assets remain readable until verified transfer completes.
        if path.is_symlink():raise StorageError('Media file is missing or invalid.')
        if path.is_file():return self.cipher.open(path.read_bytes(), 'media:' + asset_id)
        if not self.objects:raise StorageError('Media file is missing or invalid.')
        return self.read_remote(asset_id)

    def exists(self, asset_id):
        from app.file_store import object_id,local_exists,remote_exists
        object_id(asset_id)
        if local_exists(self.root/asset_id):return True
        return remote_exists(self.objects.client,self.objects.bucket,self.key(asset_id)) if self.objects else False

    def read_remote(self, asset_id):
        try:
            stream = self.objects.client.get_object(Bucket=self.objects.bucket, Key=self.key(asset_id))['Body']
            try:body = stream.read(50*1024**2+1024)
            finally:stream.close()
        except Exception:raise StorageError('Could not open private media object.') from None
        return self.cipher.open(body, 'media:' + asset_id)

    def write(self, asset_id, body):
        from app.file_store import object_id
        object_id(asset_id)
        if self.objects:
            try:self.objects.client.put_object(Bucket=self.objects.bucket, Key=self.key(asset_id), Body=self.cipher.seal(body, 'media:' + asset_id), ContentType='application/octet-stream', IfNoneMatch='*')
            except Exception:raise StorageError('Could not create private media object.') from None
            return
        path = self.root / asset_id
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as file:
                file.write(self.cipher.seal(body, 'media:' + asset_id))
                file.flush(); os.fsync(file.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def delete(self, asset_id):
        from app.file_store import object_id
        object_id(asset_id)
        if self.objects:
            try:self.objects.client.delete_object(Bucket=self.objects.bucket, Key=self.key(asset_id))
            except Exception:raise StorageError('Could not remove private media object.') from None
        (self.root / asset_id).unlink(missing_ok=True)

    def transfer_local(self):
        """Exclusive storage gate required; remove a local copy only after readback."""
        if not self.objects:return
        remote = self.ids()-self.local_ids()
        # Include remote keys also present locally to make retries idempotent.
        prefix = self.key('0'*32)[:-32]
        for page in self.objects.client.get_paginator('list_objects_v2').paginate(Bucket=self.objects.bucket, Prefix=prefix):
            remote.update(item['Key'][len(prefix):] for item in page.get('Contents', []))
        for identity in self.local_ids():
            body = self.read(identity)
            if identity not in remote:self.write(identity, body)
            if self.read_remote(identity) != body:raise StorageError('Media transfer verification failed. Local copy preserved.')
            (self.root / identity).unlink()


def media_store(db, root):
    backend = getattr(db, '__self__', None)
    return MediaStore(root, getattr(backend, 'cipher', DataCipher(enabled=False)))
