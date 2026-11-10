"""Query budgets and wakeup behavior, using real encrypted data and sockets."""
import asyncio
from contextlib import contextmanager
import json
import time
from types import SimpleNamespace

import pytest

from app.database import Database
from app.data_crypto import DataCipher
from app.multiprocess import ProcessCoordinator
from app.notifications import create_notifications_router, unread_count
from app.storage import StorageGate
from tests.test_multiprocess_realtime import FakeSocket, fail
from tests.test_storage_encryption import databases


class Counted:
    def __init__(self, connection, statements):
        self.connection,self.statements=connection,statements
    def execute(self, query, parameters=()):
        self.statements.append(query)
        return self.connection.execute(query,parameters)
    def __getattr__(self,key):return getattr(self.connection,key)


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_thousand_family_notices_count_without_profiles_or_per_row_queries(databases,engine,monkeypatch):
    backend=databases(engine);stamp=int(time.time());uid='a'*32
    settings={'modules':{'chat':True,'family_photos':True}}
    with backend.connection() as c:
        c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',(uid,'owner','test','owner',json.dumps(settings),stamp))
        for i in range(1000):
            c.execute('INSERT INTO notifications(user_id,kind,payload,created) VALUES(?,?,?,?)',
                      (uid,'family_message',json.dumps({'family_group_id':'-1001','space_id':'telegram:-1001','message_id':i+1,'actor_id':uid,'preview':'Private preview'}),stamp))
    def forbidden(*args,**kwargs):raise AssertionError('Counting must never build actor profiles or preview text.')
    monkeypatch.setattr('app.notifications.actor',forbidden)
    monkeypatch.setattr('app.people_names.mention_text',forbidden)
    with backend.connection() as raw:
        queries=[];c=Counted(raw,queries)
        assert unread_count(c,uid,stamp)==1000
        assert len(queries)==3,queries
        # The next transaction/view must see revoked module rights immediately.
        settings['modules']['family_photos']=False
        c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
        queries.clear()
        assert unread_count(c,uid,stamp)==0 and len(queries)==3


def test_idle_inbox_uses_signals_recovery_and_independent_revocation_watchdog(tmp_path):
    backend=Database(tmp_path,url='',cipher=DataCipher.ephemeral());backend.initialize()
    stamp=int(time.time());uid='b'*32;statements=[];revoked=False
    with backend.connection() as c:
        c.execute('INSERT INTO users(id,login,password,role,created) VALUES(?,?,?,?,?)',(uid,'bob','test','user',stamp))
    @contextmanager
    def db():
        with backend.connection() as c:yield Counted(c,statements)
    async def run():
        nonlocal revoked
        worker=ProcessCoordinator(backend,tmp_path,backend.cipher,StorageGate())
        # Model a healthy LISTEN transport without starting a periodic SQLite
        # fallback. The real cross-worker transport is covered by Gunicorn.
        worker.listening=True
        def ready(request):
            if revoked:fail(401,'revoked')
            return {'user_id':uid,'csrf':'csrf','token':'test'}
        routes=create_notifications_router(db,ready,SimpleNamespace(check_csrf=lambda *args:None),lambda *args,**kwargs:None,
                                           worker.gate,lambda:int(time.time()),fail,'http://testserver',coordinator=worker)
        socket=FakeSocket(uid);task=asyncio.create_task(routes.socket_routes[0][1](socket))
        socket.send({'type':'auth','csrf':'csrf'})
        try:
            await socket.event('ready');statements.clear()
            socket.send({'type':'ping'});await asyncio.sleep(1.1)
            assert statements==[], 'Idle pings/watchdog must not rebuild the inbox.'
            with backend.connection() as c:
                c.execute('INSERT INTO notifications(user_id,kind,payload,created) VALUES(?,?,?,?)',(uid,'session','{}',stamp))
            for callback in tuple(worker._change_callbacks):await callback()
            event=await socket.event('notifications');assert event['unread']==1
            # Lost LISTEN falls back to polling, including changes made during
            # the gap, even without an incoming client message.
            worker.listening=False
            with backend.connection() as c:c.execute('UPDATE notifications SET read=1 WHERE user_id=?',(uid,))
            assert (await socket.event('notifications'))['unread']==0
            worker.listening=True;statements.clear();revoked=True
            await asyncio.wait_for(task,2)
            assert socket.code==4401 and not statements
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            assert not worker._change_callbacks
            await worker.stop()
    asyncio.run(run())


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_empty_mutations_do_not_generate_self_sustaining_invalidations(databases,engine):
    backend=databases(engine)
    with backend.connection() as c:
        c.execute('DELETE FROM push_deliveries WHERE available<?',(int(time.time()),))
        assert not c.realtime_dirty
        c.execute('INSERT INTO app_settings VALUES(?,?)',('test-change','{}'))
        assert c.realtime_dirty


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_federation_retry_bookkeeping_does_not_wake_its_dispatcher(databases,engine):
    backend=databases(engine)
    with backend.connection() as c:
        c.execute('INSERT INTO federation_revocations VALUES(?,?,?,?,?)',('peer','actor','device','',1))
        assert c.realtime_dirty
    with backend.connection() as c:
        c.execute('UPDATE federation_revocations SET created=? WHERE peer_id=?',(2,'peer'))
        assert not c.realtime_dirty
