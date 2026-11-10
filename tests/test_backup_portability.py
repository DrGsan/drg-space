import asyncio
import base64
import hashlib
import json
import sqlite3
import tempfile
import threading
from contextlib import contextmanager,closing
from pathlib import Path
import pyotp
import pytest
from cryptography.fernet import Fernet
from tests.test_security import owner,member,reset,note
from tests.test_widgets import enable,image_bytes
from tests.test_admin import backup,BACKUP_PASSWORD,headers
from app import main as m
from app.backups import Backups,BackupError
from app.storage import StorageGate,StorageMiddleware,blocking


def test_restore_to_fresh_schema_with_new_server_key_preserves_passwords_and_totp():
    c,secret,_=owner();g,_=member(c);enable(g,'media')
    asset=g.post('/api/media',content=image_bytes()).json()
    listing=c.post('/api/task-lists',json={'name':'Recoverable trip'}).json()
    reminder=c.post('/api/tasks',json={'title':'Keep in backup','list_id':listing['id']}).json()
    deletion=c.delete('/api/task-lists/'+listing['id']).json()['deletion']
    layout = [{'id':'columns','type':'columnList','children':[{'id':'column','type':'column','props':{'width':0.5},'children':[{'id':'bookmark','type':'paragraph','props':{'linkDisplay':'bookmark','title':'Saved title','description':'Saved description','url':''},'content':[{'type':'link','href':'https://example.org','content':[{'type':'text','text':'Saved title','styles':{}}]}],'children':[]}]}]}]
    page=c.post('/api/notes',json={'title':'Portable layout','blocks':layout}).json()
    body=backup(c)
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);(root/'media').mkdir();destination=root/'site.sqlite3'
        with sqlite3.connect(destination) as target:
            from app.schema import SCHEMA
            target.executescript(SCHEMA)
            target.execute("INSERT INTO users(id,login,password,role,created) VALUES('temporary','newadmin',?,'owner',1)",(m.ph.hash('new-server-password'),))
        @contextmanager
        def db():
            connection=sqlite3.connect(destination);connection.row_factory=sqlite3.Row;connection.execute('PRAGMA foreign_keys=ON')
            try:yield connection;connection.commit()
            except BaseException:connection.rollback();raise
            finally:connection.close()
        new_key=Fernet.generate_key();new_cipher=Fernet(new_key)
        service=Backups(db,root,new_cipher,new_key.decode(),m.now)
        with service.temp() as stage:
            manifest=service.prepare(body,BACKUP_PASSWORD,stage)
            assert service.restore(stage,manifest)=='owner'
        with db() as restored:
            assert restored.execute('SELECT 1 FROM tasks WHERE id=?',(reminder['id'],)).fetchone()
            assert json.loads(restored.execute('SELECT value FROM app_settings WHERE key=?',('task-list-deletion:'+listing['id'],)).fetchone()[0])==deletion
            assert json.loads(restored.execute('SELECT blocks FROM notes WHERE id=?',(page['id'],)).fetchone()[0]) == page['blocks']
        with db() as restored:
            user=restored.execute("SELECT * FROM users WHERE login='owner'").fetchone()
            assert new_cipher.decrypt(user['totp'].encode()).decode()==secret
            assert m.ph.verify(user['password'],'Owner-password-123')
            assert not restored.execute("SELECT 1 FROM users WHERE login='newadmin'").fetchone()
            assert restored.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
            with pytest.raises(__import__('cryptography').fernet.InvalidToken):m.cipher.decrypt(user['totp'].encode())
        assert (root/'media'/asset['id']).read_bytes()==image_bytes()


def test_portable_restore_rekeys_security_history_purges_expired_records_and_clears_emergency_grants(monkeypatch):
    from app.http import Request
    from app.schema import SCHEMA
    from app.security_monitor import SecurityMonitor,MODE_KEY

    captured_at=m.now();monkeypatch.setattr(m,'now',lambda:captured_at)
    admin,_,_=owner();uid=admin.get('/api/me').json()['id']
    clock=[captured_at-364*86400]
    monitor=SecurityMonitor(m.db,lambda:clock[0],m.protection)
    def event(ip,kind='refresh_replay',**values):
        request=Request({'client':(ip,12345),'path':'/api/login','headers':[(b'user-agent',b'Backup regression browser')],'method':'POST'})
        identity=monitor.record(request,kind,action='login' if kind=='refresh_replay' else 'ip-ban',
                                user_id=uid,outcome='blocked' if kind=='refresh_replay' else 'success',**values)
        assert identity is not None
        return identity

    # The older incident is valid when backed up, but expires while the archive
    # is stored. Restore must apply its own retention using destination time.
    old_event=event('203.0.113.10')
    clock[0]=captured_at
    recent_event=event('203.0.113.11')
    admin_event=event('203.0.113.12','admin_action',details={'reason':'Проверена серия входов','target_ip':'203.0.113.11'})
    with m.db() as source:
        old_incident=source.execute('SELECT incident_id FROM security_events WHERE id=?',(old_event,)).fetchone()[0]
        recent_incident=source.execute('SELECT incident_id FROM security_events WHERE id=?',(recent_event,)).fetchone()[0]
        source.execute("UPDATE security_incidents SET status='reviewed' WHERE id=?",(recent_incident,))
        retained_events={row['id']:dict(row) for row in source.execute('SELECT * FROM security_events WHERE id IN (?,?)',(recent_event,admin_event))}
        retained_incident=dict(source.execute('SELECT * FROM security_incidents WHERE id=?',(recent_incident,)).fetchone())
        session=dict(source.execute("SELECT s.token,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.user_id=? AND s.stage='full'",(uid,)).fetchone())
        assert source.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]>0
        monitor.set_mode('lockdown',session,0,'Исследование инцидента',source)
    old_subject_key=monitor.subject_key('owner')
    assert all(row['subject_key']==old_subject_key for row in retained_events.values())
    assert retained_incident['subject_key']==old_subject_key
    body=backup(admin)

    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);destination=root/'site.sqlite3'
        with sqlite3.connect(destination) as target:target.executescript(SCHEMA)
        @contextmanager
        def db():
            connection=sqlite3.connect(destination);connection.row_factory=sqlite3.Row;connection.execute('PRAGMA foreign_keys=ON')
            try:yield connection;connection.commit()
            except BaseException:connection.rollback();raise
            finally:connection.close()
        restored_at=captured_at+2*86400
        new_secret='independent-restore-server-secret-for-security-history-12345'
        monkeypatch.setenv('APP_SECRET',new_secret)
        new_key=base64.urlsafe_b64encode(hashlib.sha256(new_secret.encode()).digest())
        service=Backups(db,root,Fernet(new_key),new_key.decode(),lambda:restored_at)
        with service.temp() as stage:
            manifest=service.prepare(body,BACKUP_PASSWORD,stage)
            with service.snapshot_database(stage) as archived:
                assert archived.execute('SELECT COUNT(*) FROM security_events WHERE id IN (?,?)',(old_event,recent_event)).fetchone()[0]==2
                assert archived.execute('SELECT COUNT(*) FROM security_incidents WHERE id IN (?,?)',(old_incident,recent_incident)).fetchone()[0]==2
                assert json.loads(archived.execute('SELECT value FROM app_settings WHERE key=?',(MODE_KEY,)).fetchone()[0])['mode']=='lockdown'
            assert service.restore(stage,manifest)=='owner'

        restored_monitor=SecurityMonitor(db,lambda:restored_at,None)
        new_subject_key=restored_monitor.subject_key(' OWNER ')
        assert new_subject_key!=old_subject_key
        with db() as restored:
            for identity,original in retained_events.items():
                row=dict(restored.execute('SELECT * FROM security_events WHERE id=?',(identity,)).fetchone())
                assert row['subject_key']==new_subject_key
                assert {key:value for key,value in row.items() if key!='subject_key'}=={key:value for key,value in original.items() if key!='subject_key'}
            incident=dict(restored.execute('SELECT * FROM security_incidents WHERE id=?',(recent_incident,)).fetchone())
            assert incident['subject_key']==new_subject_key
            assert {key:value for key,value in incident.items() if key!='subject_key'}=={key:value for key,value in retained_incident.items() if key!='subject_key'}
            assert restored.execute('SELECT COUNT(*) FROM security_events WHERE subject_key=? AND id IN (?,?)',(new_subject_key,recent_event,admin_event)).fetchone()[0]==2
            assert restored.execute('SELECT COUNT(*) FROM security_incidents WHERE subject_key=? AND id=?',(new_subject_key,recent_incident)).fetchone()[0]==1
            assert not restored.execute('SELECT 1 FROM security_events WHERE id=?',(old_event,)).fetchone()
            assert not restored.execute('SELECT 1 FROM security_incidents WHERE id=?',(old_incident,)).fetchone()
            assert not restored.execute('SELECT 1 FROM app_settings WHERE key=?',(MODE_KEY,)).fetchone()
            for table in ('sessions','refresh_tokens','session_info','session_presence'):
                assert restored.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
        assert restored_monitor.get_mode()['mode']=='normal'
        assert restored_monitor.get_mode()['allowed_session']==''


def test_invalid_database_and_path_traversal_are_rejected_before_live_changes():
    from zipfile import ZipFile
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from app.backups import MAGIC,key
    import io,os
    c,_,_=owner()
    service=Backups(m.db,m.DATA,m.cipher,base64.urlsafe_b64encode(hashlib.sha256(m.APP_SECRET.encode()).digest()).decode(),m.now)
    def seal(raw):
        salt=os.urandom(16);nonce=os.urandom(12);header=MAGIC+salt+nonce
        return header+AESGCM(key(BACKUP_PASSWORD,salt)).encrypt(nonce,raw,header)
    for metadata,name,database in [({'format':'drg-site','version':1,'files':[]},'../escape',b'bad'),({'format':'drg-site','version':1,'files':[],'schema':'wrong'},None,b'bad')]:
        output=io.BytesIO()
        with ZipFile(output,'w') as z:
            z.writestr('manifest.json',json.dumps(metadata));z.writestr('site.sqlite3',database)
            if name:z.writestr(name,'escape')
        with service.temp() as stage:
            with pytest.raises(BackupError):service.prepare(seal(output.getvalue()),BACKUP_PASSWORD,stage)
        assert c.get('/api/me').status_code==200
    assert not (m.DATA/'escape').exists()


@pytest.mark.parametrize('corruption',['page_cycle','unsafe_blocks'])
def test_authenticated_but_invalid_content_cannot_replace_live_data(corruption):
    c,_,_=owner();nid=note(c)
    service=Backups(m.db,m.DATA,m.cipher,base64.urlsafe_b64encode(hashlib.sha256(m.APP_SECRET.encode()).digest()).decode(),m.now)
    with service.temp() as stage:
        manifest=service.snapshot(stage)
        with service.snapshot_database(stage,write=True) as database:
            with database:
                if corruption=='page_cycle':database.execute('UPDATE notes SET parent_id=id WHERE id=?',(nid,))
                else:database.execute('UPDATE notes SET blocks=? WHERE id=?',(json.dumps([{'type':'paragraph','content':[{'type':'link','href':'javascript:alert(1)'}]}]),nid))
        encrypted=service.encrypt(stage,manifest,BACKUP_PASSWORD).read_bytes()
    response=c.post('/api/admin/backups/restore',content=encrypted,headers=headers())
    assert response.status_code==422
    original=c.get('/api/notes/'+nid).json();assert original['parent_id'] is None and original['blocks'] is None
    assert c.get('/api/me').status_code==200


def test_storage_exclusion_waits_for_existing_requests_and_blocks_new_requests():
    from types import SimpleNamespace
    from app.http import Response
    async def run():
        gate=StorageGate();started=asyncio.Event();finish=asyncio.Event();exclusive=asyncio.Event()
        async def handler(request):
            started.set();await finish.wait();return Response()
        middleware=StorageMiddleware(gate)
        request=asyncio.create_task(middleware.handle(SimpleNamespace(url=SimpleNamespace(path='/api/notes')),handler,None))
        await started.wait()
        async def snapshot():
            async with gate.exclusive():exclusive.set()
        worker=asyncio.create_task(snapshot());await asyncio.sleep(0)
        assert gate.exclusive_pending and not exclusive.is_set()
        blocked=await middleware.handle(SimpleNamespace(url=SimpleNamespace(path='/api/tasks')),handler,None)
        assert blocked.status_code==503 and blocked.headers['Retry-After']=='5'
        finish.set();await request;await worker;assert exclusive.is_set() and not gate.exclusive_pending
    asyncio.run(run())


def test_cancelled_restore_worker_keeps_exclusion_until_disk_work_finishes():
    async def run():
        gate=StorageGate();started=threading.Event();finish=threading.Event()
        def disk_work():started.set();finish.wait(2)
        async def work():
            async with gate.exclusive():await blocking(disk_work)
        task=asyncio.create_task(work());await asyncio.to_thread(started.wait,2);task.cancel();await asyncio.sleep(0)
        assert gate.exclusive_pending
        finish.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert not gate.exclusive_pending
    asyncio.run(run())
