"""Cross-worker reconciliation and kernel socket-liveness regressions."""
import asyncio
import base64
import json
import multiprocessing
import os
import signal
import threading
import time
from types import SimpleNamespace

import pytest
from pycrdt import Doc, XmlElement, XmlFragment, XmlText, Map

from app.database import Database
from app.data_crypto import DataCipher
from app.http import HTTPError, WebSocketDisconnect
from app.modules.realtime import create_realtime_router
from app.multiprocess import ProcessCoordinator, PeerCollision, PeerLimit, channel_for
from app.notifications import create_notifications_router
from app.storage import StorageGate


def database(root, cipher=None):
    backend=Database(root, url='', cipher=cipher or DataCipher(enabled=False))
    backend.initialize()
    return backend


def coordinator(backend):
    return ProcessCoordinator(backend, backend.root, backend.cipher, StorageGate())


def child_lease(root, key, pipe):
    async def hold():
        worker=coordinator(database(root, DataCipher(key)))
        lease=await worker.register_peer('realtime','killed-user',('note','page'),777,{'presence':{'private':'cursor'}})
        pipe.send(lease.token)
        # Keep the FD locked until the test sends SIGKILL. No guessed timeout.
        await asyncio.to_thread(pipe.recv)
    asyncio.run(hold())


def test_registry_global_quota_collision_replacement_and_encrypted_metadata(tmp_path):
    backend=database(tmp_path, DataCipher.ephemeral())
    async def run():
        a,b=coordinator(backend),coordinator(backend)
        leases=[]
        try:
            for cid in range(12):
                leases.append(await (a if cid%2 else b).register_peer('realtime','private-user',('note','private-page'),cid,{'presence':{'name':'Private cursor'}}))
            with pytest.raises(PeerLimit):await a.register_peer('realtime','private-user',('chat','another'),13)
            with pytest.raises(PeerCollision):await a.register_peer('realtime','intruder',('note','private-page'),0)
            replacement=await a.register_peer('realtime','private-user',('note','private-page'),0)
            assert not await leases[0].is_current()
            await leases[0].close()
            assert await replacement.is_current()  # Old finally cannot remove it.
            assert len(await b.peers('realtime'))==12
            inbox=await b.register_peer('inbox','private-user',('inbox','private-user'),0)
            assert await inbox.is_current()  # Inbox has its own 12-socket scope.
            assert all(b'Private cursor' not in p.read_bytes() and b'private-user' not in p.read_bytes()
                       for p in a.root.glob('*.state'))
        finally:
            await a.stop();await b.stop()
        assert not list(a.root.glob('*.state'))
    asyncio.run(run())


def test_sigkill_releases_presence_without_pid_or_expiry_guess(tmp_path):
    cipher=DataCipher.ephemeral();backend=database(tmp_path,cipher)
    context=multiprocessing.get_context('spawn');parent,child=context.Pipe()
    process=context.Process(target=child_lease,args=(str(tmp_path),cipher.key,child))
    process.start()
    try:
        assert parent.poll(15)
        token=parent.recv()
        worker=coordinator(backend)
        async def inspect():return await worker.peers('realtime')
        assert [r['token'] for r in asyncio.run(inspect())]==[token]
        os.kill(process.pid,signal.SIGKILL);process.join(10)
        assert process.exitcode==-signal.SIGKILL
        assert asyncio.run(inspect())==[]
        assert not list(worker.root.glob('*.lease'))
    finally:
        if process.is_alive():process.kill();process.join(10)
        parent.close();child.close()


def test_invalidation_channels_isolate_storage_and_schema():
    assert channel_for('drg','one')!=channel_for('drg','two')
    assert channel_for('drg','one')!=channel_for('other','one')
    assert len(channel_for('drg','one'))<63
    assert 'one' not in channel_for('drg','one')


def test_cancel_all_during_registration_finishes_thread_and_releases_lease(tmp_path):
    backend=database(tmp_path);worker=coordinator(backend)
    entered,release=threading.Event(),threading.Event()
    register=worker._register
    def paused(*args):
        entered.set();assert release.wait(5)
        return register(*args)
    worker._register=paused
    async def run():
        pending=asyncio.create_task(worker.register_peer('realtime','user',('note','page'),1))
        assert await asyncio.to_thread(entered.wait,3)
        # Same shutdown pattern as AsyncRuntime.stop: every child Task receives
        # cancellation while its underlying disk operation remains in flight.
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():task.cancel()
        pending.cancel()  # Repeated cancellation must not interrupt cleanup.
        release.set()
        result=await asyncio.gather(pending,return_exceptions=True)
        assert isinstance(result[0],asyncio.CancelledError)
        assert not worker._leases
        assert not list(worker.root.glob('*.lease'))
        assert not list(worker.root.glob('*.state'))
    asyncio.run(run())


class FakeSocket:
    def __init__(self, uid):
        self.cookies={};self.headers={'origin':'http://testserver','test-uid':uid}
        self.client=SimpleNamespace(host='test');self.incoming=asyncio.Queue();self.outgoing=asyncio.Queue();self.code=None

    async def accept(self):pass
    async def receive_text(self):
        value=await self.incoming.get()
        if isinstance(value,Exception):raise value
        return value
    async def send_json(self,event):await self.outgoing.put(event)
    async def close(self,code=1000):
        if self.code is None:
            self.code=code;self.incoming.put_nowait(WebSocketDisconnect(code))
    def send(self,event):self.incoming.put_nowait(json.dumps(event))
    async def event(self,kind):
        async def find():
            while True:
                event=await self.outgoing.get()
                if event['type']==kind:return event
        return await asyncio.wait_for(find(),4)


def fail(status,message):raise HTTPError(status,message)


def seed():
    block=XmlElement('blockContainer',{'id':'paragraph-one'},[XmlElement('paragraph',{},[XmlText('Before')])])
    return Doc({'prosemirror':XmlFragment([XmlElement('blockGroup',{},[block])]),'meta':Map({'title':'Together'})})


def setup_rooms(backend):
    stamp=int(time.time())
    with backend.connection() as c:
        for uid in ('alice','bob'):
            c.execute('INSERT INTO users(id,login,password,role,created) VALUES(?,?,?,?,?)',(uid,uid,'test','user',stamp))
        c.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',('room','family','Family','alice',stamp))
        for uid in ('alice','bob'):c.execute('INSERT INTO space_members VALUES(?,?,?)',('room',uid,stamp))
        c.execute('INSERT INTO notes(id,user_id,space_id,title,body,created,updated) VALUES(?,?,?,?,?,?,?)',('page','alice','room','Together','Before',stamp,stamp))


def router(backend,worker,revoked):
    def auth(request):
        uid=request.headers['test-uid']
        if uid in revoked:fail(401,'Revoked')
        return {'user_id':uid,'login':uid,'csrf':'csrf','token':'session-'+uid}
    def csrf(request,expected):
        if request.headers['x-csrf-token']!=expected:fail(403,'Bad CSRF')
    guards=lambda module:auth
    protection=SimpleNamespace(check=lambda request:None,rate=lambda *args,**kwargs:None)
    return create_realtime_router(backend.connection,auth,guards,SimpleNamespace(check_csrf=csrf),
                                  protection,worker.gate,lambda:int(time.time()),fail,'http://testserver',coordinator=worker)


async def open_socket(router,kind,target,uid,cid):
    socket=FakeSocket(uid)
    handler=next(fn for route,fn in router.socket_routes if ('/notes/' if kind=='note' else '/chat/') in route)
    task=asyncio.create_task(handler(socket,target))
    socket.send({'type':'auth','csrf':'csrf','client_id':cid})
    return socket,task


def test_workers_reconcile_crdt_presence_restores_and_revoked_sessions(tmp_path):
    backend=database(tmp_path);setup_rooms(backend)
    async def run():
        a,b=coordinator(backend),coordinator(backend);revoked=set()
        ra,rb=router(backend,a,revoked),router(backend,b,revoked)
        await a.start();await b.start();tasks=[]
        try:
            first,t1=await open_socket(ra,'note','page','alice',11);tasks.append(t1)
            second,t2=await open_socket(rb,'note','page','bob',22);tasks.append(t2)
            initial=await first.event('init');await second.event('init')
            doc=seed();first.send({'type':'init','version':initial['page']['version'],'update':base64.b64encode(doc.get_update()).decode()})
            sync_a=await first.event('sync');sync_b=await second.event('sync')
            assert sync_a['epoch']==sync_b['epoch'] and sync_a['state']==sync_b['state']
            first.send({'type':'presence','state':{'cursor':None}})
            remote=await second.event('presence')
            assert remote['client_id']==11 and remote['state']['user']['name']=='alice'
            doc.get('meta',type=Map)['title']='Across workers'
            first.send({'type':'update','epoch':sync_a['epoch'],'seq':1,'update':base64.b64encode(doc.get_update()).decode()})
            ack=await first.event('ack');changed=await second.event('sync')
            merged=Doc({'prosemirror':XmlFragment(),'meta':Map()});merged.apply_update(base64.b64decode(changed['state']))
            assert merged.get('meta',type=Map)['title']=='Across workers' and changed['version']==ack['version']
            # Revocation is detected without needing another incoming message.
            revoked.add('alice');await asyncio.wait_for(t1,4)
            departed=await second.event('presence')
            assert departed=={'type':'presence','client_id':11,'state':None}
            # Replacing a canonical generation through HTTP closes old editors.
            with backend.connection() as c:c.execute('UPDATE collaborative_docs SET epoch=? WHERE note_id=?',('restored','page'))
            await asyncio.wait_for(t2,4)
            assert second.code==4409
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await a.stop();await b.stop()
    asyncio.run(run())


def test_chat_reconciliation_preserves_order_across_worker_publication_gaps(tmp_path):
    backend=database(tmp_path);setup_rooms(backend)
    async def run():
        a,b=coordinator(backend),coordinator(backend);ra,rb=router(backend,a,set()),router(backend,b,set())
        await a.start();await b.start()
        socket,task=await open_socket(rb,'chat','room','bob',22)
        try:
            await socket.event('ready')
            # Simulate HTTP commits handled by a worker with no local sockets.
            with backend.connection() as c:
                c.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',('room','alice','message-one','First',int(time.time())))
                c.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',('room','alice','message-two','Second',int(time.time())))
            one,two=await socket.event('message'),await socket.event('message')
            assert [one['message']['body'],two['message']['body']]==['First','Second']
            assert one['message']['id']<two['message']['id']
            # An old ID edited on another worker must arrive without a new ID.
            with backend.connection() as c:
                c.execute('UPDATE spaces SET chat_revision=1 WHERE id=?',('room',))
                c.execute('UPDATE messages SET body=?,edited=?,revision=1 WHERE id=?',('First edited',int(time.time()),one['message']['id']))
            changed=await socket.event('message_edited')
            assert changed['message']['id']==one['message']['id'] and changed['message']['body']=='First edited' and changed['message']['revision']==1
            with backend.connection() as c:
                c.execute('UPDATE spaces SET chat_revision=2 WHERE id=?',('room',))
                c.execute('UPDATE messages SET body=?,edited=?,revision=2 WHERE id=?',('First edited again',int(time.time()),one['message']['id']))
            assert (await socket.event('message_edited'))['message']['revision']==2
            with backend.connection() as c:c.execute('DELETE FROM space_members WHERE space_id=? AND user_id=?',('room','bob'))
            await asyncio.wait_for(task,4)
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            await a.stop();await b.stop()
    asyncio.run(run())


def test_publication_after_restore_cannot_adopt_a_new_generation_for_old_editors(tmp_path):
    backend=database(tmp_path);setup_rooms(backend);state=seed().get_update()
    with backend.connection() as c:c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?)',('page',state,'original',int(time.time())))
    async def run():
        worker=coordinator(backend);routes=router(backend,worker,set())
        socket,task=await open_socket(routes,'note','page','bob',22)
        try:
            initial=await socket.event('sync');assert initial['epoch']=='original'
            with backend.connection() as c:c.execute('UPDATE collaborative_docs SET epoch=? WHERE note_id=?',('restored','page'))
            # A share edit initializes/publishes the restored document before
            # the periodic watcher runs. Old local editors still must reset.
            await routes.publication_update('page',state,state,'restored',2,True)
            await asyncio.wait_for(task,3)
            assert socket.code==4409
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            await worker.stop()
    asyncio.run(run())


def test_ack_and_local_delta_cannot_hide_an_earlier_foreign_worker_commit(tmp_path):
    backend=database(tmp_path);setup_rooms(backend);state=seed().get_update()
    with backend.connection() as c:c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?)',('page',state,'original',int(time.time())))
    async def run():
        a,b=coordinator(backend),coordinator(backend)
        ra,rb=router(backend,a,set()),router(backend,b,set());tasks=[]
        # Leave reconciliation paused to deterministically deliver newer local
        # deltas/ACKs before the earlier committed foreign worker edit.
        first,t1=await open_socket(ra,'note','page','alice',11);tasks.append(t1)
        second,t2=await open_socket(rb,'note','page','bob',22);tasks.append(t2)
        observer,t3=await open_socket(ra,'note','page','bob',33);tasks.append(t3)
        try:
            for socket in (first,second,observer):await socket.event('sync')
            # Use identical canonical initial state with independent Yjs IDs.
            d1,d2=Doc({'prosemirror':XmlFragment(),'meta':Map()}),Doc({'prosemirror':XmlFragment(),'meta':Map()})
            for doc in (d1,d2):doc.apply_update(state)
            d2_before=d2.get_state()
            text=list(list(list(list(d2.get('prosemirror',type=XmlFragment).children)[0].children)[0].children)[0].children)[0]
            text.insert(0,'From B ')
            second.send({'type':'update','epoch':'original','seq':1,'update':base64.b64encode(d2.get_update(d2_before)).decode()})
            old_ack=await second.event('ack')
            d1_before=d1.get_state();d1.get('meta',type=Map)['title']='From A'
            first.send({'type':'update','epoch':'original','seq':1,'update':base64.b64encode(d1.get_update(d1_before)).decode()})
            new_ack=await first.event('ack');local_delta=await observer.event('update')
            assert old_ack['version']<new_ack['version']==local_delta['version']
            for worker in (a,b):
                for callback in tuple(worker._callbacks):await callback()
            for socket in (first,second,observer):
                canonical=await socket.event('sync')
                merged=Doc({'prosemirror':XmlFragment(),'meta':Map()});merged.apply_update(base64.b64decode(canonical['state']))
                assert merged.get('meta',type=Map)['title']=='From A'
                merged_text=list(list(list(list(merged.get('prosemirror',type=XmlFragment).children)[0].children)[0].children)[0].children)[0]
                assert str(merged_text)=='From B Before'
                assert canonical['version']==new_ack['version']
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await a.stop();await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('change',['none','session','epoch'])
def test_established_socket_waits_for_other_worker_exclusive_and_rechecks_access(tmp_path,change):
    backend=database(tmp_path);setup_rooms(backend);state=seed().get_update()
    with backend.connection() as c:c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?)',('page',state,'original',int(time.time())))
    async def run():
        worker=coordinator(backend);worker.gate=StorageGate(tmp_path)
        other=StorageGate(tmp_path);revoked=set();routes=router(backend,worker,revoked)
        socket,task=await open_socket(routes,'note','page','bob',22)
        try:
            await socket.event('sync')
            # Queue a write, then acquire another worker's exclusive startup /
            # restore lease before the socket writer gets to run.
            await routes.publication_update('page',state,state,'original',1,False)
            async with other.exclusive():
                socket.send({'type':'ping'})
                if change=='session':revoked.add('bob')
                elif change=='epoch':
                    with backend.connection() as c:c.execute('UPDATE collaborative_docs SET epoch=? WHERE note_id=?',('restored','page'))
                # Also exercise the 2-second watch path in the healthy case.
                await asyncio.sleep(2.1 if change=='none' else .1)
                assert not task.done() and socket.code is None and socket.outgoing.empty()
            if change=='none':
                await socket.event('update');await socket.event('pong')
                socket.send({'type':'ping'});await socket.event('pong')
                assert socket.code is None and not task.done()
            else:
                await asyncio.wait_for(task,3)
                assert socket.code==(4401 if change=='session' else 4409)
                assert socket.outgoing.empty()
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            await worker.stop()
    asyncio.run(run())


def test_inbox_reauthenticates_after_initial_handshake_wait_without_reading_private_payload(tmp_path):
    async def run():
        gate,other=StorageGate(tmp_path),StorageGate(tmp_path)
        accepted,waiting=asyncio.Event(),asyncio.Event();revoked=False;auth_calls=[];db_calls=[]
        class InboxSocket(FakeSocket):
            async def accept(self):accepted.set()
        def ready(request):
            auth_calls.append(request.headers['test-uid'])
            if revoked:fail(401,'Revoked session')
            return {'user_id':'bob','csrf':'csrf'}
        def private_db():
            db_calls.append('inbox')
            raise AssertionError('A revoked handshake must not read the private inbox.')
        acquire=gate.acquire_shared
        def track_wait():
            if other.exclusive_pending:waiting.set()
            return acquire()
        gate.acquire_shared=track_wait
        routes=create_notifications_router(private_db,ready,SimpleNamespace(check_csrf=lambda *args:None),
                                            lambda *args,**kwargs:None,gate,lambda:int(time.time()),fail,'http://testserver')
        socket=InboxSocket('bob');handler=routes.socket_routes[0][1]
        task=asyncio.create_task(handler(socket))
        try:
            await asyncio.wait_for(accepted.wait(),1)
            assert auth_calls==['bob'] and socket.outgoing.empty()
            async with other.exclusive():
                revoked=True
                socket.send({'type':'auth','csrf':'csrf'})
                await asyncio.wait_for(waiting.wait(),1)
                assert not task.done() and socket.code is None
                assert auth_calls==['bob'] and not db_calls and socket.outgoing.empty()
            await asyncio.wait_for(task,1)
            assert auth_calls==['bob','bob'] and not db_calls
            assert socket.code==4401 and socket.outgoing.empty()
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())


def test_chat_typing_and_receipts_reconcile_across_workers(tmp_path):
    backend=database(tmp_path);setup_rooms(backend)
    async def run():
        a,b=coordinator(backend),coordinator(backend);ra,rb=router(backend,a,set()),router(backend,b,set())
        await a.start();await b.start();tasks=[]
        try:
            first,t1=await open_socket(ra,'chat','room','alice',11);tasks.append(t1)
            second,t2=await open_socket(rb,'chat','room','bob',22);tasks.append(t2)
            await first.event('ready');await second.event('ready')
            first.send({'type':'presence','state':{'typing':True}})
            state=await second.event('presence');assert state['state']['user']['id']=='alice'
            first.send({'type':'presence','state':None});assert (await second.event('presence'))['state'] is None
            with backend.connection() as c:c.execute('INSERT INTO chat_reads VALUES(?,?,?)',('room','bob',12))
            event=await first.event('reads');assert event['reads']=={'bob':12}
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True);await a.stop();await b.stop()
    asyncio.run(run())
