import base64
import json
from tests.test_security import owner,member,client,reset,note,share,enroll
from tests.test_widgets import image_bytes,enable,add
from app import main as m

ADMIN_PASSWORD='Owner-password-123'
BACKUP_PASSWORD='пароль-резервной-копии-123'

def proof(**kwargs):return {'admin_password':ADMIN_PASSWORD,**kwargs}
def headers(password=BACKUP_PASSWORD,confirmation='RESTORE'):
    return {'x-admin-password-b64':base64.b64encode(ADMIN_PASSWORD.encode()).decode(),'x-backup-password-b64':base64.b64encode(password.encode()).decode(),'x-restore-confirmation':confirmation}
def uid(c,login='friend'):
    return next(u['id'] for u in c.get('/api/admin/users').json()['users'] if u['login']==login)
def backup(c):
    r=c.post('/api/admin/backups/export',json=proof(backup_password=BACKUP_PASSWORD));assert r.status_code==200,r.text
    return r.content

def test_admin_label_permissions_and_size_accounting():
    c,_,_=owner();g,_=member(c)
    assert c.get('/api/me').json()['role_label']=='Владелец'
    nid=note(g);enable(g,'media');asset=g.post('/api/media',content=image_bytes()).json()
    info=c.get('/api/admin/users').json();friend=next(row for row in info['users'] if row['login']=='friend')
    assert friend['notes']==1 and friend['media_bytes']==len(image_bytes()) and friend['total_bytes']==friend['data_bytes']+friend['media_bytes']
    assert friend['total_bytes']>len(image_bytes());assert info['database_bytes']>0
    assert not any('password' in row or 'totp' in row for row in info['users'])
    user=friend['id']
    for method,url,body in [('get','/api/admin/users',None),('put','/api/admin/users/'+user+'/password',proof(password='New-password-12345')),('post','/api/admin/users/'+user+'/reset-2fa',proof()),('post','/api/admin/users/delete',proof(user_id=user,confirmation='friend')),('post','/api/admin/backups/export',proof(backup_password=BACKUP_PASSWORD))]:
        r=getattr(g,method)(url,**({'json':body} if body else {}));assert r.status_code==403,url
    assert g.post('/api/admin/backups/restore',content=b'wrong',headers=headers()).status_code==403
    assert client().get('/api/admin/users').status_code==401


def test_password_change_and_2fa_reset_revoke_every_old_session():
    c,_,_=owner();g,_=member(c);user=uid(c)
    g2=client();r=g2.post('/api/login',json={'login':'friend','password':'Friend-password-123'});assert r.status_code==200
    g2.headers['x-csrf-token']=r.json()['csrf']
    assert c.put('/api/admin/users/'+user+'/password',json=proof(password='Replacement-password-123')).status_code==200
    assert g.get('/api/me').status_code==401 and g2.get('/api/me').status_code==401
    assert g.post('/api/refresh').status_code==401
    login=client();assert login.post('/api/login',json={'login':'friend','password':'Friend-password-123'}).status_code==401
    r=login.post('/api/login',json={'login':'friend','password':'Replacement-password-123'});assert r.status_code==200 and r.json()['needs_2fa']
    assert c.post('/api/admin/users/'+user+'/reset-2fa',json=proof()).status_code==200
    login.headers['x-csrf-token']=r.json()['csrf'];assert login.get('/api/me').status_code==401
    with m.db() as d:
        assert d.execute('SELECT totp FROM users WHERE id=?',(user,)).fetchone()[0] is None
        assert d.execute('SELECT COUNT(*) FROM recovery_codes WHERE user_id=?',(user,)).fetchone()[0]==0
    fresh=client();r=fresh.post('/api/login',json={'login':'friend','password':'Replacement-password-123'});assert r.json()['needs_enrollment']
    fresh.headers['x-csrf-token']=r.json()['csrf'];assert fresh.get('/api/notes').status_code==403
    enroll(fresh,'Replacement-password-123');assert fresh.get('/api/notes').status_code==200


def test_admin_password_csrf_confirmation_and_self_protection():
    c,_,_=owner();g,_=member(c);user=uid(c);administrator=uid(c,'owner')
    assert c.post('/api/admin/users/delete',json=proof(user_id=administrator,confirmation='owner')).status_code==409
    assert c.post('/api/admin/users/delete',json=proof(user_id=user,confirmation='wrong')).status_code==422
    assert c.put('/api/admin/users/'+user+'/password',json={'admin_password':'incorrect','password':'Replacement-password-123'}).status_code==403
    assert g.get('/api/me').status_code==200
    assert c.put('/api/admin/users/'+user+'/password',json=proof(password='short')).status_code==422
    del c.headers['x-csrf-token']
    assert c.post('/api/admin/users/delete',json=proof(user_id=user,confirmation='friend')).status_code==403
    assert c.post('/api/admin/backups/export',json=proof(backup_password=BACKUP_PASSWORD)).status_code==403
    assert c.post('/api/admin/backups/restore',content=b'wrong',headers=headers()).status_code==403


def test_delete_removes_all_owned_rows_publications_invites_and_files():
    c,_,_=owner();g,_=member(c);nid=note(g);token=share(g,nid,'public')
    restricted=share(c,note(c),'users',allowed=['friend','owner'])
    g.put('/api/notes/'+nid,json={'title':'History','body':'Previous version'})
    g.post('/api/notes',json={'title':'Child','parent_id':nid})
    task=g.post('/api/tasks',json={'title':'Private task','day':'2026-10-11'});assert task.status_code==200
    enable(g,'media');asset=g.post('/api/media',content=image_bytes()).json();add(g,'media',{'items':[{'id':'image','asset_id':asset['id']}],'selected':['image']})
    file=m.DATA/'media'/asset['id'];assert file.is_file();user=uid(c)
    assert c.post('/api/admin/users/delete',json=proof(user_id=user,confirmation='friend')).status_code==200
    from tests.deletion_helpers import finish_account
    finish_account(user)
    assert not file.exists();assert g.get('/api/me').status_code==401;assert client().get('/api/shared/'+token).status_code==404
    with m.db() as d:
        for table,field in [('users','id'),('notes','user_id'),('tasks','user_id'),('sessions','user_id'),('recovery_codes','user_id'),('media','user_id')]:assert d.execute('SELECT COUNT(*) FROM '+table+' WHERE '+field+'=?',(user,)).fetchone()[0]==0
        assert d.execute('SELECT COUNT(*) FROM note_revisions WHERE note_id=?',(nid,)).fetchone()[0]==0
        assert d.execute('SELECT COUNT(*) FROM shares WHERE note_id=?',(nid,)).fetchone()[0]==0
        assert d.execute("SELECT COUNT(*) FROM invites WHERE login='friend'").fetchone()[0]==0
    assert c.get('/api/me').status_code==200
    with m.db() as d:assert json.loads(d.execute('SELECT allowed FROM shares WHERE token=?',(restricted,)).fetchone()[0])==['owner']


def test_full_encrypted_restore_preserves_all_users_and_invalidates_sessions():
    c,secret,_=owner();g,_=member(c);nid=note(g);token=share(g,nid,'public');user=uid(c)
    g.put('/api/notes/'+nid,json={'title':'Changed','body':'Saved history'})
    g.post('/api/tasks',json={'title':'Saved task','day':'2026-10-11'})
    enable(g,'media');asset=g.post('/api/media',content=image_bytes(True)).json();add(g,'media',{'items':[{'id':'gif','asset_id':asset['id']}],'selected':['gif']})
    c.put('/api/appearance',json={'theme':'rose','mode':'dark'})
    body=backup(c);assert b'Saved history' not in body and b'friend' not in body
    assert not list(m.DATA.glob('.drg-backup-work-*'))
    assert c.post('/api/admin/users/delete',json=proof(user_id=user,confirmation='friend')).status_code==200
    c.put('/api/appearance',json={'theme':'sage','mode':'light'})
    r=c.post('/api/admin/backups/restore',content=body,headers=headers());assert r.status_code==200,r.text
    assert r.json()['login']=='owner';assert c.get('/api/me').status_code==401
    assert client().get('/api/shared/'+token).json()['body']=='Saved history'
    stored=(m.DATA/'media'/asset['id']).read_bytes()
    assert m.data_cipher.open(stored,'media:'+asset['id'])==image_bytes(True)
    if m.data_cipher.enabled:assert stored!=image_bytes(True)
    with m.db() as d:
        assert d.execute('SELECT COUNT(*) FROM users').fetchone()[0]==2
        assert d.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
        assert d.execute('SELECT COUNT(*) FROM refresh_tokens').fetchone()[0]==0
        assert d.execute('SELECT COUNT(*) FROM note_revisions').fetchone()[0]==1
        owner_row=d.execute("SELECT * FROM users WHERE login='owner'").fetchone();assert m.cipher.decrypt(owner_row['totp'].encode()).decode()==secret
        assert json.loads(owner_row['settings'])['appearance']['theme']=='rose'
        assert d.execute('SELECT COUNT(*) FROM tasks WHERE user_id=?',(user,)).fetchone()[0]==1
    assert not list(m.DATA.glob('.drg-backup-work-*'))


def test_invalid_backup_password_damage_size_and_confirmation_leave_data_unchanged():
    c,_,_=owner();nid=note(c);body=backup(c)
    for content,h in [(body,headers(password='incorrect-password')), (body[:-1]+bytes([body[-1]^1]),headers()), (b'bad file',headers()),(body,headers(confirmation='wrong'))]:
        r=c.post('/api/admin/backups/restore',content=content,headers=h);assert r.status_code==422,r.text
        assert c.get('/api/notes/'+nid).status_code==200
    from app.backups import MAX_BACKUP
    assert c.post('/api/admin/backups/restore',content=b'x',headers=headers()|{'content-length':str(__import__('app.archive_stream',fromlist=['maximum_upload']).maximum_upload(MAX_BACKUP)+1)}).status_code==413
    assert c.get('/api/notes/'+nid).status_code==200


def test_self_reset_requires_reenrollment_and_does_not_remove_admin_role():
    c,_,_=owner();administrator=uid(c,'owner')
    r=c.post('/api/admin/users/'+administrator+'/reset-2fa',json=proof());assert r.status_code==200 and r.json()['current']
    assert c.get('/api/me').status_code==401
    g=client();r=g.post('/api/login',json={'login':'owner','password':ADMIN_PASSWORD});assert r.json()['needs_enrollment']
    g.headers['x-csrf-token']=r.json()['csrf'];assert g.get('/api/admin/users').status_code==403
    enroll(g);assert g.get('/api/admin/users').status_code==423
    assert g.post('/api/admin/access/unlock',json={'password':ADMIN_PASSWORD}).status_code==200
    assert g.get('/api/admin/users').status_code==200
    assert g.get('/api/me').json()['role_label']=='Владелец'


def test_storage_breakdown_counts_uploads_once_and_identifies_note_content():
    c,_,_=owner();g,_=member(c);nid=note(g)
    assert g.put('/api/notes/'+nid,json={'title':'Заметка 📝','body':'Первый текст'}).status_code==200
    assert g.put('/api/notes/'+nid,json={'title':'Заметка 📝','body':'Вторая версия'}).status_code==200
    file=g.post('/api/files/upload?name=movie.mp4',content=b'video-content')
    assert file.status_code==200,file.text
    enable(g,'media');assert g.post('/api/media',content=image_bytes()).status_code==200
    info=c.get('/api/admin/users').json();user=next(row for row in info['users'] if row['login']=='friend')
    assert info['file_bytes']==len(b'video-content') and info['media_bytes']==len(image_bytes())
    assert info['site_total_bytes']==info['database_bytes']+info['file_bytes']+info['media_bytes']
    assert info['note_content_bytes']==sum(row['note_content_bytes'] for row in info['users'])
    with m.db() as d:
        current=d.execute('SELECT title,body,blocks,icon,cover FROM notes WHERE id=?',(nid,)).fetchone()
        history=d.execute('SELECT title,body,blocks FROM note_revisions WHERE note_id=?',(nid,)).fetchall()
        expected=sum(len(value.encode()) for row in [current,*history] for value in row if isinstance(value,str))
    assert user['note_content_bytes']==expected>len('Вторая версия'.encode())
    assert user['total_bytes']==user['data_bytes']+user['file_bytes']+user['media_bytes']
