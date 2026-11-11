"""Real PostgreSQL reuse, rollback, bounded concurrency and reconnects."""
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg import sql
import pytest

from app.database import Database
from app.data_crypto import DataCipher


@pytest.fixture
def backend(tmp_path):
    url=os.getenv('DRG_TEST_DATABASE_URL')
    if not url:pytest.skip('Requires disposable PostgreSQL test access')
    schema='drg_pool_'+secrets.token_hex(8)
    backend=Database(tmp_path,url=url,schema=schema,cipher=DataCipher.ephemeral())
    backend.initialize();backend.start_pool()
    try:yield backend
    finally:
        backend.close_pool()
        with psycopg.connect(url,autocommit=True) as raw:
            raw.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_reuse_resets_session_and_rolls_back_failed_transaction(backend):
    backend._pool.resize(1,1)
    with backend.connection() as c:
        first=c.raw.info.backend_pid
        c.raw.execute("SET application_name='changed-by-previous-borrower'")
    with pytest.raises(RuntimeError):
        with backend.connection() as c:
            assert c.raw.info.backend_pid==first
            assert c.raw.execute('SHOW application_name').fetchone()[0]=='drg-space'
            assert c.raw.execute('SHOW search_path').fetchone()[0]==backend.schema+', pg_catalog'
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',('pool-rollback','private'))
            raise RuntimeError('abort')
    with backend.connection() as c:
        assert not c.execute('SELECT 1 FROM app_settings WHERE key=?',('pool-rollback',)).fetchone()


def test_parallel_borrowers_are_bounded_and_do_not_share_connections(backend):
    mutex=threading.Lock();release=threading.Event();full=threading.Event()
    active=set();pids=set();peak=[0]
    def borrow(_):
        with backend.connection() as c:
            pid=c.raw.info.backend_pid
            with mutex:
                assert pid not in active
                active.add(pid);pids.add(pid);peak[0]=max(peak[0],len(active))
                if len(active)==4:full.set()
            assert release.wait(8)
            c.raw.execute('SELECT 1')
            with mutex:active.remove(pid)
    with ThreadPoolExecutor(max_workers=8) as executor:
        jobs=[executor.submit(borrow,index) for index in range(8)]
        try:assert full.wait(5)
        finally:release.set()
        for job in jobs:job.result(timeout=10)
    assert peak[0]==4 and len(pids)<=4


def test_dead_connection_replaced_and_drain_discards_idle_sessions(backend):
    backend._pool.resize(1,1)
    with backend.connection() as c:first=c.raw.info.backend_pid
    with psycopg.connect(backend.url,autocommit=True) as admin:
        admin.execute('SELECT pg_terminate_backend(%s)',(first,))
    with backend.connection() as c:
        second=c.raw.info.backend_pid
        assert second!=first
    backend.drain_pool()
    with backend.connection() as c:assert c.raw.info.backend_pid!=second
