import json
import pytest
from tests.test_security import owner,member,reset,note,client,share
from tests.test_admin import proof,backup
from app import main as m
from app.account_lifecycle import pending,purge
from app.retention import purge_trash,TRASH_SECONDS

def schedule(c,user):
    uid=user.get('/api/me').json()['id']
    result=c.post('/api/admin/users/delete',json=proof(user_id=uid,confirmation='friend'))
    assert result.status_code==200,result.text
    return uid,result.json()['deletion']

def test_account_waits_freezes_writes_and_can_be_restored():
    c,_,_=owner();user,_=member(c);nid=note(user)
    uid,deletion=schedule(c,user)
    assert deletion['delete_at']-deletion['created']==86400
    assert user.get('/api/me').json()['account_deletion']==deletion
    assert user.get('/api/notes/'+nid).status_code==200
    for method,path,body in [('post','/api/notes',{'title':'blocked'}),('delete','/api/notes/'+nid,None),('put','/api/account/profile',{'display_name':'Blocked'}),('post','/api/tasks',{'title':'blocked','day':'2026-10-20'})]:
        response=getattr(user,method)(path,**({'json':body} if body is not None else {}))
        assert response.status_code==423,response.text
        assert response.headers['x-account-deletion']=='pending'
    again=c.post('/api/admin/users/delete',json=proof(user_id=uid,confirmation='friend'))
    assert again.json()['deletion']==deletion
    with m.db() as db:
        purge(db,deletion['delete_at']-1)
        assert db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone()
    assert user.post('/api/account/deletion/restore').status_code==200
    assert user.get('/api/me').json()['account_deletion'] is None
    assert user.put('/api/account/profile',json={'display_name':'Restored'}).status_code==200
    with m.db() as db:
        purge(db,deletion['delete_at'])
        assert db.execute('SELECT 1 FROM notes WHERE id=?',(nid,)).fetchone()

def test_self_delete_checks_password_confirmation_csrf_and_owner():
    c,_,_=owner();user,_=member(c)
    data={'password':'Friend-password-123','confirmation':'friend'}
    assert user.post('/api/account/deletion',json={**data,'password':'wrong'}).status_code==403
    assert user.post('/api/account/deletion',json={**data,'confirmation':'wrong'}).status_code==422
    assert c.post('/api/account/deletion',json={'password':'Owner-password-123','confirmation':'owner'}).status_code==409
    assert user.post('/api/account/deletion',json=data,headers={'x-csrf-token':'wrong'}).status_code==403
    result=user.post('/api/account/deletion',json=data);assert result.status_code==200,result.text
    assert user.post('/api/account/deletion',json=data).json()==result.json()
    assert user.get('/api/me').json()['account_deletion']==result.json()['deletion']

def test_account_deleted_only_at_deadline_and_admin_can_cancel():
    c,_,_=owner();user,_=member(c);nid=note(user)
    uid,deletion=schedule(c,user)
    result=c.post('/api/admin/users/restore',json=proof(user_id=uid,confirmation='friend'));assert result.status_code==200,result.text
    assert user.get('/api/me').json()['account_deletion'] is None
    uid,deletion=schedule(c,user)
    with m.db() as db:purge(db,deletion['delete_at'])
    assert user.get('/api/me').status_code==401
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone()
        assert not db.execute('SELECT 1 FROM notes WHERE id=?',(nid,)).fetchone()
        assert pending(db,uid) is None


def test_every_note_keeps_30_days_in_trash_and_can_be_deleted_immediately():
    c,_,_=owner();nid=note(c)
    assert c.delete('/api/notes/'+nid).status_code==200
    row=c.get('/api/notes?trash=true').json()[0]
    with m.db() as db:
        purge_trash(db,row['deleted']+86400)
        assert db.execute('SELECT 1 FROM notes WHERE id=?',(nid,)).fetchone()
        purge_trash(db,row['deleted']+TRASH_SECONDS-1)
        assert db.execute('SELECT 1 FROM notes WHERE id=?',(nid,)).fetchone()
    assert c.post('/api/notes/'+nid+'/restore').status_code==200
    assert c.delete('/api/notes/'+nid).status_code==200
    assert c.delete('/api/notes/'+nid+'/permanent').status_code==200


def test_3_0_short_deadlines_are_ignored_without_changing_trash_dates():
    c,_,_=owner();root=note(c)
    child=c.post('/api/notes',json={'title':'child','parent_id':root}).json()['id']
    c.delete('/api/notes/'+root)
    with m.db() as db:
        stamp=db.execute('SELECT deleted FROM notes WHERE id=?',(root,)).fetchone()[0]
        for nid in (root,child):
            db.execute('INSERT INTO app_settings(key,value) VALUES (?,?)',('note-deletion:'+nid,json.dumps({'version':1,'created':stamp,'delete_at':stamp+86400})))
        purge_trash(db,stamp+86400)
        assert len(db.execute('SELECT id FROM notes WHERE deleted=?',(stamp,)).fetchall())==2
        assert not db.execute('SELECT key FROM app_settings WHERE key LIKE ?',('note-deletion:%',)).fetchone()
        purge_trash(db,stamp+TRASH_SECONDS)
        assert not db.execute('SELECT 1 FROM notes WHERE id IN (?,?)',(root,child)).fetchone()


def test_restore_cannot_win_at_the_exact_deadline():
    from app.account_lifecycle import cancel
    from app.http import HTTPError
    c,_,_=owner();user,_=member(c);uid,deletion=schedule(c,user)
    with m.db() as db:
        with pytest.raises(HTTPError):cancel(db,uid,deletion['delete_at'],m.fail)
        assert pending(db,uid)==deletion


def test_pending_account_can_sign_in_again_to_cancel():
    import pyotp
    c,_,_=owner();user,_=member(c);uid,deletion=schedule(c,user)
    assert user.post('/api/logout').status_code==200
    login=user.post('/api/login',json={'login':'friend','password':'Friend-password-123'})
    assert login.status_code==200,login.text
    user.headers['x-csrf-token']=login.json()['csrf']
    with m.db() as db:
        # Use a fresh, unconsumed time step for this isolated login.
        row=db.execute('SELECT totp FROM users WHERE id=?',(uid,)).fetchone()
        secret=m.cipher.decrypt(row[0].encode()).decode()
        db.execute('UPDATE users SET last_counter=-1 WHERE id=?',(uid,))
    verified=user.post('/api/2fa/verify',json={'code':pyotp.TOTP(secret).at(m.now())})
    assert verified.status_code==200,verified.text
    user.headers['x-csrf-token']=verified.json()['csrf']
    assert user.get('/api/me').json()['account_deletion']==deletion
    assert user.post('/api/account/deletion/restore').status_code==200


def test_backup_keeps_pending_account_and_note_and_rejects_downgraded_format():
    from tests.test_admin import BACKUP_PASSWORD
    from app.backups import BackupError
    c,_,_=owner();user,_=member(c);nid=note(user);page=note(c);c.delete('/api/notes/'+page)
    uid,deletion=schedule(c,user);service=m.admin_router.backups
    with service.temp() as stage:
        metadata=service.snapshot(stage);assert metadata['version']==4
        saved=service.encrypt(stage,metadata,BACKUP_PASSWORD).read_bytes()
    with service.temp() as stage:
        assert service.prepare(saved,BACKUP_PASSWORD,stage)['version']==4
    with service.temp() as stage:
        metadata=service.snapshot(stage);metadata['version']=3
        saved=service.encrypt(stage,metadata,BACKUP_PASSWORD).read_bytes()
    with service.temp() as stage:
        with pytest.raises(BackupError):service.prepare(saved,BACKUP_PASSWORD,stage)


def test_anonymous_edit_links_cannot_change_frozen_personal_data():
    from tests.test_community_files import upload
    from tests.test_widgets import enable
    from tests.test_share_editing import draft
    c,_,_=owner();user,_=member(c);nid=note(user);enable(user,'files')
    token=share(user,nid,'public',permission='edit');reader=client()
    page=reader.get('/api/shared/'+token).json();file=upload(user)
    link=user.post('/api/files/'+file['id']+'/shares',json={'mode':'public','permission':'edit'}).json()['token'];prefix='/api/shared-files/'+link
    grant=reader.post(prefix+'/edit-session').json();reader.headers['x-csrf-token']=grant['csrf']
    uid,deletion=schedule(c,user)
    assert reader.get('/api/shared/'+token).json()['permission']=='view'
    assert reader.put('/api/shared/'+token,json=draft(page),headers={'x-share-edit-token':page['edit_token']}).status_code==423
    assert reader.get(prefix).json()['permission']=='view'
    assert reader.put(prefix+'/'+file['id']+'/text',json={'text':'blocked','version':1}).status_code==423
    assert reader.post(prefix+'/edit-session').status_code==423
    assert reader.get(prefix+'/'+file['id']+'/content').content==b'hello'
    assert user.post('/api/account/deletion/restore').status_code==200
    assert reader.get('/api/shared/'+token).json()['permission']=='edit'
    assert reader.put(prefix+'/'+file['id']+'/text',json={'text':'restored','version':1}).status_code==200


def test_pending_member_cannot_receive_workspace_ownership():
    from tests.test_people_groups import workspace,invite
    from tests.test_workspace_teams import config
    c,_,_=owner();user,_=member(c);other,_=member(c,'other')
    sid=workspace(c,'organization');invite(c,user,sid,'friend')
    uid,deletion=schedule(c,user);value=config(c,sid)
    payload={'user_id':uid,'revision':value['revision']}
    assert other.post('/api/social/spaces/'+sid+'/ownership',json=payload).status_code==404
    assert c.post('/api/social/spaces/'+sid+'/ownership',json=payload).status_code==409
    with m.db() as db:assert db.execute('SELECT owner_id FROM spaces WHERE id=?',(sid,)).fetchone()[0]!=uid


def test_full_backup_from_3_0_restores_short_deadlines_as_normal_trash():
    from tests.test_admin import BACKUP_PASSWORD
    c,_,_=owner();nid=note(c);c.delete('/api/notes/'+nid)
    with m.db() as db:
        stamp=db.execute('SELECT deleted FROM notes WHERE id=?',(nid,)).fetchone()[0]
        db.execute('INSERT INTO app_settings(key,value) VALUES (?,?)',('note-deletion:'+nid,json.dumps({'version':1,'created':stamp,'delete_at':stamp+86400})))
    service=m.admin_router.backups
    with service.temp() as stage:
        metadata=service.snapshot(stage);saved=service.encrypt(stage,metadata,BACKUP_PASSWORD).read_bytes()
    with service.temp() as stage:
        assert service.prepare(saved,BACKUP_PASSWORD,stage)['version']==4
        with service.snapshot_database(stage,write=True) as source:
            purge_trash(source,stamp+86400)
            assert source.execute('SELECT deleted FROM notes WHERE id=?',(nid,)).fetchone()[0]==stamp
            purge_trash(source,stamp+TRASH_SECONDS)
            assert not source.execute('SELECT 1 FROM notes WHERE id=?',(nid,)).fetchone()
