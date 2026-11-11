"""Optional shared, encrypted counters. SQL stays authoritative.

Workers share one data volume. A local generation fence invalidates cache keys
at every committed relevant write, even while Redis is unavailable or a CLI
writer has no Redis connection. Shared writer locks also fence concurrent commits;
cache readers only probe an exclusive lock, never hold it while querying SQL.
"""
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from app.data_crypto import StorageError
from app.process_locks import sync_file_lock, try_file_lock

logger=logging.getLogger('drg.redis')
CACHE_SECONDS=5


class RedisCache:
    def __init__(self,backend,url=None):
        self.backend=backend
        self.url=os.getenv('REDIS_URL','') if url is None else url
        if self.url:
            try:
                parsed=urlsplit(self.url)
                if (parsed.scheme not in ('redis','rediss') or not parsed.hostname
                        or parsed.port is not None and not 0<parsed.port<65536
                        or parsed.path and not re.fullmatch(r'/[0-9]{1,4}',parsed.path)
                        or parsed.fragment or parsed.query):
                    raise ValueError()
            except ValueError:
                raise StorageError('REDIS_URL must be redis:// or rediss:// with an optional database number; URL options are unsupported.') from None
        self.path=Path(backend.root)/'.redis-cache-generation'
        self.client=None
        self._mutex=threading.Lock()
        self._retry_at=0
        self._fence_ok=True
        self._counts={'hits':0,'misses':0,'errors':0}

    @property
    def prefix(self):
        cipher=self.backend.cipher
        key_id=hashlib.sha256(cipher.key).hexdigest() if cipher.enabled else 'plain'
        identity=f'{self.backend.schema}:{self.backend.storage_id}:{key_id}'
        return 'drg-site:cache:v1:'+hashlib.sha256(identity.encode()).hexdigest()[:40]

    def start(self):
        """Create the bounded connection pool only after Gunicorn fork."""
        if not self.url or self.client is not None:return
        from redis import Redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        self.client=Redis.from_url(self.url,protocol=2,max_connections=8,
            socket_connect_timeout=.15,socket_timeout=.15,
            retry=Retry(NoBackoff(),0),health_check_interval=30,
            decode_responses=False,client_name='drg-space-cache')
        try:
            with sync_file_lock(self.backend.root,'redis-cache.lock',shared=True):
                self._rotate()
                self._fence_ok=True
        except OSError:
            self._break_fence()
        self._call(lambda client:client.ping())

    def close(self):
        client,self.client=self.client,None
        if client is not None:
            try:
                client.close()
                client.connection_pool.disconnect()
            except OSError:pass

    def stats(self):
        with self._mutex:
            return {**self._counts,'configured':bool(self.url),
                    'ready':self.client is not None and self._fence_ok and time.monotonic()>=self._retry_at}

    def _call(self,action):
        if self.client is None:return None
        with self._mutex:
            if time.monotonic()<self._retry_at:return None
        from redis.exceptions import RedisError
        try:return action(self.client)
        except (RedisError,OSError,ValueError):
            # Do not print errors, URLs, credentials, key names, or data.
            with self._mutex:
                report=time.monotonic()>=self._retry_at
                self._retry_at=time.monotonic()+10
                self._counts['errors']+=1
            if report:logger.warning('Optional Redis cache unavailable; using SQL until retry.')
            return None

    def _rotate(self):
        temporary=self.path.with_name(self.path.name+'.'+secrets.token_hex(12)+'.tmp')
        try:
            descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(descriptor,'wb') as output:output.write(secrets.token_hex(24).encode())
            os.replace(temporary,self.path)
        finally:temporary.unlink(missing_ok=True)

    def _break_fence(self):
        self._fence_ok=False
        # Other workers must also bypass old keys if the generation could not
        # be advanced. Never let an optional cache prevent the SQL commit.
        try:self.path.unlink(missing_ok=True)
        except OSError:pass

    @contextmanager
    def write_barrier(self):
        # An unconfigured CLI still invalidates a previously enabled cache.
        if not self.url and not self.path.exists():
            yield
            return
        lease=None
        try:
            from app.process_locks import FileLease,open_lock
            import fcntl
            lease=FileLease(open_lock(self.backend.root,'redis-cache.lock'))
            fcntl.flock(lease.fd,fcntl.LOCK_SH)
            self._rotate()
        except OSError:
            self._break_fence()
            if lease:lease.close();lease=None
        try:yield
        finally:
            if lease:
                try:self._rotate()
                except OSError:self._break_fence()
                finally:lease.close()

    def _generation(self):
        if not self._fence_ok:return None
        try:
            lease=try_file_lock(self.backend.root,'redis-cache.lock')
            if lease is None:return None
            with lease:
                descriptor=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(descriptor,'rb') as source:value=source.read(49)
                return value.decode() if re.fullmatch(b'[a-f0-9]{48}',value) else None
        except OSError:return None

    def lookup(self,kind,uid,stamp,*,scope=''):
        """Return an optional fill ticket and (count, semantic expiry) hit."""
        with self._mutex:
            if self.client is None or time.monotonic()<self._retry_at:return None,None
        generation=self._generation()
        if generation is None:return None,None
        suffix=hashlib.sha256((kind+':'+uid+':'+scope).encode()).hexdigest()
        key=self.prefix+':'+generation+':'+suffix
        ticket=(generation,key)
        body=self._call(lambda client:client.get(key))
        if body is not None and generation==self._generation():
            try:
                value=json.loads(self.backend.cipher.open(body,'redis-cache:'+key))
                count,deadline,until=value['count'],value['deadline'],value['until']
                if (type(count) is int and 0<=count<=2**53-1 and type(deadline) is int
                        and type(until) is int and stamp<until<=stamp+CACHE_SECONDS and deadline>=until):
                    with self._mutex:self._counts['hits']+=1
                    return ticket,(count,deadline)
            except (StorageError,ValueError,TypeError,KeyError):pass
        with self._mutex:self._counts['misses']+=1
        return ticket,None

    def put(self,ticket,count,deadline,stamp):
        if ticket is None:return
        generation,key=ticket
        if generation!=self._generation():return
        until=min(stamp+CACHE_SECONDS,deadline)
        if until<=stamp:return
        body=self.backend.cipher.seal(json.dumps({'count':count,'deadline':deadline,'until':until},separators=(',',':')).encode(),'redis-cache:'+key)
        self._call(lambda client:client.set(key,body,ex=until-stamp))
