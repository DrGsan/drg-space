"""Real shared Redis counters, isolated keys, SQL fallback and write races."""
from contextlib import contextmanager
import json
import os
import secrets
import socket
import time

import pytest
import redis

from app.database import Database
from app.data_crypto import DataCipher,StorageError
from app.notifications import unread_count
from app.redis_cache import RedisCache
from tests.test_idle_database_work import Counted


@pytest.fixture(params=['sqlite','postgres'])
def cached(request,tmp_path):
    redis_url=os.getenv('DRG_TEST_REDIS_URL')
    if not redis_url:pytest.skip('Set DRG_TEST_REDIS_URL for isolated keys on a real Redis.')
    engine=request.param;url=os.getenv('DRG_TEST_DATABASE_URL','') if engine=='postgres' else ''
    if engine=='postgres' and not url:pytest.skip('Requires disposable PostgreSQL schema access.')
    schema='drg_redis_test_'+secrets.token_hex(8)
    backend=Database(tmp_path,url=url,schema=schema,cipher=DataCipher.ephemeral(),redis_url=redis_url)
    backend.initialize();backend.start_pool();backend.redis_cache.start()
    assert backend.redis_cache.stats()['ready']
    workers=[backend]
    def clone(*,enabled=True):
        worker=Database(tmp_path,url=url,schema=schema,cipher=backend.cipher,redis_url=redis_url if enabled else '')
        worker.initialize();worker.start_pool()
        if enabled:worker.redis_cache.start()
        workers.append(worker)
        return worker
    try:yield backend,clone
    finally:
        # Only these generated storage-ID/key namespaces are eligible for deletion.
        prefix=backend.redis_cache.prefix+':'
        client=redis.Redis.from_url(redis_url,protocol=2,socket_timeout=1)
        keys=list(client.scan_iter(match=prefix+'*',count=100))
        if keys:client.delete(*keys)
        client.close()
        for worker in workers:worker.redis_cache.close();worker.close_pool()
        if engine=='postgres':
            import psycopg
            from psycopg import sql
            with psycopg.connect(url,autocommit=True) as c:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def seed(backend):
    uid='a'*32;stamp=int(time.time())
    settings={'modules':{'chat':True,'family_photos':True}}
    with backend.connection() as c:
        c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',
                  (uid,'private-login','test','owner',json.dumps(settings),stamp))
        for i in range(100):
            c.execute('INSERT INTO notifications(user_id,kind,payload,created) VALUES(?,?,?,?)',
                      (uid,'family_message',json.dumps({'family_group_id':'-1001','space_id':'telegram:-1001','message_id':i+1,'preview':'Private message'}),stamp))
    return uid,stamp


def count(backend,uid,stamp):
    statements=[]
    with backend.connection() as raw:value=unread_count(Counted(raw,statements),uid,stamp)
    return value,statements


def test_shared_encrypted_cache_reuses_another_workers_count_and_invalidates_on_commit(cached):
    backend,clone=cached;other=clone();uid,stamp=seed(backend)
    value,queries=count(backend,uid,stamp);assert value==100 and len(queries)==3
    value,queries=count(other,uid,stamp);assert value==100 and len(queries)==2
    assert other.redis_cache.stats()['hits']==1
    client=backend.redis_cache.client
    keys=list(client.scan_iter(match=backend.redis_cache.prefix+':*',count=100))
    assert len(keys)==1 and uid not in keys[0].decode()
    assert client.get(keys[0]).startswith(DataCipher.MAGIC)
    assert client.ttl(keys[0]) in range(1,6)
    with backend.connection() as c:c.execute('UPDATE notifications SET read=1 WHERE user_id=?',(uid,))
    value,queries=count(other,uid,stamp);assert value==0 and len(queries)==3


def test_unconfigured_cli_invalidates_existing_cache_and_failed_transactions_do_not(cached):
    backend,clone=cached;cli=clone(enabled=False);uid,stamp=seed(backend)
    assert count(backend,uid,stamp)[0]==100
    with pytest.raises(RuntimeError):
        with cli.connection() as c:
            c.execute('UPDATE notifications SET read=1 WHERE user_id=?',(uid,))
            # A transaction must see its own writes, never an old cached result.
            assert unread_count(c,uid,stamp)==0
            raise RuntimeError('rollback')
    value,queries=count(backend,uid,stamp);assert value==100 and len(queries)==2
    with cli.connection() as c:c.execute('UPDATE notifications SET read=1 WHERE user_id=?',(uid,))
    assert count(backend,uid,stamp)[0]==0


def test_read_during_commit_bypasses_cache_and_late_fill_cannot_republish_old_snapshot(cached):
    backend,clone=cached;other=clone();cache=backend.redis_cache;stamp=int(time.time())
    ticket,hit=cache.lookup('test-counter','private-user',stamp)
    assert ticket and hit is None
    with other.redis_cache.write_barrier():
        assert cache.lookup('test-counter','private-user',stamp)==(None,None)
    cache.put(ticket,999,stamp+30,stamp)
    assert not cache.client.exists(ticket[1])
    fresh,_=cache.lookup('test-counter','private-user',stamp)
    cache.put(fresh,1,stamp+1,stamp)
    assert cache.lookup('test-counter','private-user',stamp)[1]==(1,stamp+1)
    assert cache.lookup('test-counter','private-user',stamp+1)[1] is None


def test_corrupt_cache_and_connection_failure_fall_back_to_sql_without_logging_data(cached,monkeypatch,caplog):
    backend,_=cached;uid,stamp=seed(backend)
    assert count(backend,uid,stamp)[0]==100
    client=backend.redis_cache.client
    keys=list(client.scan_iter(match=backend.redis_cache.prefix+':*',count=100))
    client.set(keys[0],b'wrong-key-or-corrupt',ex=5)
    assert count(backend,uid,stamp)[0]==100
    def failed(*args,**kwargs):raise redis.ConnectionError('redis://private-user:password@secret-host')
    with monkeypatch.context() as patch:
        patch.setattr(client,'get',failed)
        value,queries=count(backend,uid,stamp)
        assert value==100 and len(queries)==3
        assert count(backend,uid,stamp)[0]==100
        assert backend.redis_cache.stats()['errors']==1
    assert 'password' not in caplog.text and 'secret-host' not in caplog.text


def test_disabled_cache_opens_no_connections_and_unreachable_service_does_not_block_sql(tmp_path,monkeypatch):
    backend=Database(tmp_path,url='',cipher=DataCipher.ephemeral(),redis_url='');backend.initialize()
    with monkeypatch.context() as patch:
        patch.setattr(redis.Redis,'from_url',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('disabled Redis was contacted')))
        backend.redis_cache.start();uid,stamp=seed(backend)
        assert count(backend,uid,stamp)[0]==100
    # A bound socket accepts no connections: only this test's unused port fails.
    with socket.socket() as unavailable:
        unavailable.bind(('127.0.0.1',0));port=unavailable.getsockname()[1]
        cache=RedisCache(backend,f'redis://127.0.0.1:{port}/0')
        monkeypatch.setattr(backend,'redis_cache',cache)
        start=time.monotonic()
        try:
            cache.start()
            assert count(backend,uid,stamp)[0]==100
            assert count(backend,uid,stamp)[0]==100
            assert time.monotonic()-start<1
            assert cache.stats()['errors']==1
        finally:cache.close()


@pytest.mark.parametrize('url',['http://host','redis://host?socket_timeout=60','redis://host:0','redis://host/not-a-db','redis://host?password=private'])
def test_bad_configuration_has_a_sanitized_error(tmp_path,url):
    with pytest.raises(StorageError,match='REDIS_URL') as error:
        Database(tmp_path,url='',cipher=DataCipher.ephemeral(),redis_url=url)
    assert url not in str(error.value)


def test_full_restore_discards_warmed_counts_without_changing_archive_format(cached):
    from tests.test_storage_encryption import backup_service
    backend,_=cached;uid,stamp=seed(backend)
    service=backup_service(backend);service.now=lambda:stamp
    with service.temp() as stage:
        manifest=service.snapshot(stage)
        assert count(backend,uid,stamp)[0]==100
        with backend.connection() as c:c.execute('UPDATE notifications SET read=1 WHERE user_id=?',(uid,))
        assert count(backend,uid,stamp)[0]==0
        service.restore(stage,manifest)
        value,queries=count(backend,uid,stamp)
        assert value==100 and len(queries)==3
    assert not list(backend.root.glob('.drg-backup-work-*'))


def test_failed_generation_rotation_disables_cache_without_rolling_back_sql(cached,monkeypatch):
    backend,clone=cached;other=clone();uid,stamp=seed(backend)
    assert count(backend,uid,stamp)[0]==100
    def cannot_rotate():raise OSError('simulated local metadata failure')
    monkeypatch.setattr(backend.redis_cache,'_rotate',cannot_rotate)
    with backend.connection() as c:c.execute('UPDATE notifications SET read=1 WHERE user_id=?',(uid,))
    value,queries=count(other,uid,stamp)
    assert value==0 and len(queries)==3


def test_redis_reconnects_after_circuit_cooldown(cached,monkeypatch):
    backend,_=cached;uid,stamp=seed(backend);cache=backend.redis_cache
    assert count(backend,uid,stamp)[0]==100
    def failed(*args):raise redis.ConnectionError('test connection lost')
    with monkeypatch.context() as patch:
        patch.setattr(cache.client,'get',failed)
        assert count(backend,uid,stamp)[0]==100
    assert not cache.stats()['ready']
    cache._retry_at=0  # Advance the circuit deadline without sleeping ten seconds.
    cache.client.connection_pool.disconnect()
    value,queries=count(backend,uid,stamp)
    assert value==100 and len(queries)==2 and cache.stats()['hits']==1


def test_silent_redis_socket_times_out_once_and_sql_remains_available(tmp_path):
    import threading
    release=threading.Event()
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen(1)
        def silent():
            connection,_=listener.accept()
            with connection:release.wait(2)
        thread=threading.Thread(target=silent,daemon=True);thread.start()
        backend=Database(tmp_path,url='',cipher=DataCipher.ephemeral(),
                         redis_url=f'redis://127.0.0.1:{listener.getsockname()[1]}/0')
        backend.initialize();uid,stamp=seed(backend)
        try:
            start=time.monotonic();backend.redis_cache.start()
            assert count(backend,uid,stamp)[0]==100
            assert count(backend,uid,stamp)[0]==100
            assert time.monotonic()-start<1
            assert backend.redis_cache.stats()['errors']==1
        finally:
            backend.redis_cache.close();release.set();thread.join(2)


def test_cached_notification_count_respects_calendar_history_boundary(cached):
    from app.chat_storage import cutoff
    backend,_=cached;uid,stamp=seed(backend)
    with backend.connection() as c:
        c.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',('room','family','Room',uid,1))
        c.execute('INSERT INTO space_members VALUES(?,?,?)',('room',uid,1))
        mid=c.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',('room',uid,'old-boundary-message','Body',cutoff(stamp+1))).lastrowid
        c.execute('INSERT INTO notifications(user_id,kind,payload,created) VALUES(?,?,?,?)',(uid,'message',json.dumps({'space_id':'room','message_id':mid}),stamp))
    assert count(backend,uid,stamp)[0]==101
    assert count(backend,uid,stamp)[0]==101
    assert count(backend,uid,stamp+1)[0]==100
