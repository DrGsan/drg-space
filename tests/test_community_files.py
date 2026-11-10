import io
import json
from zipfile import ZipFile
from tests.test_security import owner,member,client,reset,note
from tests.test_widgets import enable,image_bytes
from app import main as m
from app.quota import usage
from app.retention import cleanup


def connect(c,g):
    q=c.post('/api/social/friends',json={'login':'friend'}).json()['id']
    sid=g.post('/api/social/requests/'+q+'/accept').json()['space_id']
    return sid

def upload(c,name='test.md',body=b'hello',parent=None):
    url='/api/files/upload?name='+name+('&parent_id='+parent if parent else '')
    r=c.post(url,content=body);assert r.status_code==200,r.text
    return r.json()


def test_download_head_checks_permissions_without_reading_file_bytes(monkeypatch):
    from app.file_store import FileStore
    c,_,_=owner();file=upload(c)
    shared=c.post('/api/files/'+file['id']+'/shares',json={'mode':'public'}).json()
    def no_read(*args):raise AssertionError('HEAD must not read file data')
    monkeypatch.setattr(FileStore,'iter_read',no_read)
    paths=['/api/files/'+file['id']+'/content','/api/files/download.zip?ids='+file['id'],
           '/api/shared-files/'+shared['token']+'/'+file['id']+'/content',
           '/api/shared-files/'+shared['token']+'/download.zip']
    for path in paths:
        response=c.head(path)
        assert response.status_code==200,response.text
        assert response.content==b''
        assert response.headers['content-disposition'].startswith('attachment;')
    assert client().head('/api/files/'+file['id']+'/content').status_code==401
    assert c.delete('/api/file-shares/'+shared['token']).status_code==200
    assert c.head(paths[2]).status_code==404

def test_invitation_in_global_inbox_without_opening_social():
    c,_,_=owner();g,_=member(c)
    q=c.post('/api/social/friends',json={'login':'friend'});assert q.status_code==200,q.text
    inbox=g.get('/api/notifications').json();assert inbox['unread']==1
    item=inbox['items'][0];assert item['kind']=='invitation' and item['payload']['request_id']==q.json()['id']
    assert item['payload']['pending']
    assert c.patch('/api/notifications/'+str(item['id'])).status_code==404
    assert g.post('/api/social/requests/'+q.json()['id']+'/accept').status_code==200
    assert not g.get('/api/notifications').json()['items'][0]['payload']['pending']
    assert g.post('/api/notifications/read-all').status_code==200
    assert g.get('/api/notifications').json()['unread']==0

def test_message_alerts_deduplicate_and_disappear_when_membership_revoked():
    c,_,_=owner();g,_=member(c);sid=connect(c,g);enable(c,'chat')
    data={'body':'Private message preview','client_id':'unique-client-123'}
    a=c.post('/api/chat/'+sid+'/messages',json=data);assert a.status_code==200,a.text
    c.post('/api/chat/'+sid+'/messages',json=data)
    messages=[n for n in g.get('/api/notifications').json()['items'] if n['kind']=='message']
    assert len(messages)==1 and messages[0]['payload']['preview']=='Private message preview'
    assert c.delete('/api/social/spaces/'+sid).status_code==200
    assert not [n for n in g.get('/api/notifications').json()['items'] if n['kind']=='message']

def test_notifications_expire_after_a_week_and_session_alert_is_only_after_auth():
    c,_,codes=owner();g,_=member(c)
    uid=c.get('/api/me').json()['id']
    other=client();r=other.post('/api/login',json={'login':'owner','password':'Owner-password-123'});assert r.status_code==200
    other.headers['x-csrf-token']=r.json()['csrf']
    assert not [n for n in c.get('/api/notifications').json()['items'] if n['kind']=='session']
    r=other.post('/api/2fa/verify',json={'code':codes[0]});assert r.status_code==200
    alerts=[n for n in c.get('/api/notifications').json()['items'] if n['kind']=='session'];assert len(alerts)==1
    with m.db() as d:d.execute('UPDATE notifications SET created=? WHERE user_id=?',(m.now()-7*86400,uid))
    cleanup(m.db,m.now())
    assert c.get('/api/notifications').json()['items']==[]

def test_presence_and_contact_avatars_do_not_expose_strangers():
    c,_,_=owner();g,_=member(c);connect(c,g)
    assert g.post('/api/presence').status_code==200
    people=c.get('/api/presence').json()['users'];p=next(p for p in people if p['login']=='friend');assert p['online']
    aid=g.post('/api/account/avatar',content=image_bytes()).json()['avatar_id']
    url='/api/users/'+p['id']+'/avatar?v='+aid
    assert c.get(url).status_code==200
    stranger,_=member(c,'stranger');assert stranger.get(url).status_code==404
    with m.db() as d:d.execute('UPDATE user_presence SET last_seen=? WHERE user_id=?',(m.now()-90000,p['id']))
    p=next(p for p in c.get('/api/presence').json()['users'] if p['login']=='friend');assert not p['online'] and p['last_seen'] is None

def test_file_private_tree_text_version_download_and_zip():
    c,_,_=owner();g,_=member(c);enable(c,'files');enable(g,'files')
    folder=c.post('/api/files/folders',json={'name':'Documents'}).json()
    a=upload(c,parent=folder['id']);b=upload(c,'second.txt',b'world',folder['id'])
    assert g.get('/api/files/'+a['id']+'/content').status_code==404
    assert c.get('/api/files/'+a['id']+'/content').content==b'hello'
    assert c.get('/api/files/'+a['id']+'/text').json()['text']=='hello'
    edited=c.put('/api/files/'+a['id']+'/text',json={'text':'updated','version':1});assert edited.status_code==200,edited.text
    assert c.put('/api/files/'+a['id']+'/text',json={'text':'stale','version':1}).status_code==409
    with ZipFile(io.BytesIO(c.get('/api/files/download.zip?ids='+folder['id']).content)) as z:
        assert z.read('Documents/test.md')==b'updated' and z.read('Documents/second.txt')==b'world'
    partial=c.get('/api/files/download.zip?ids='+b['id'])
    with ZipFile(io.BytesIO(partial.content)) as z:assert [n for n in z.namelist() if not n.endswith('/')]==['Documents/second.txt']
    uid=c.get('/api/me').json()['id']
    with m.db() as d:objects=[row[0] for row in d.execute('SELECT object_id FROM files WHERE user_id=? AND object_id IS NOT NULL',(uid,))]
    if m.data_cipher.enabled:assert all(b'updated' not in (m.DATA/'files'/identity).read_bytes() for identity in objects)
    assert c.delete('/api/files/'+folder['id']).status_code==200
    assert c.get('/api/files').json()['items']==[]


def test_file_quota_atomic_on_upload_notes_and_replacements_and_admin_control():
    c,_,_=owner();g,_=member(c);enable(g,'files')
    uid=g.get('/api/me').json()['id'];quota=g.get('/api/account/storage').json();assert quota['limit_bytes']==1024**3
    initial=quota['used_bytes']
    r=c.put('/api/admin/users/'+uid+'/quota',json={'admin_password':'Owner-password-123','limit_bytes':initial+200});assert r.status_code==200,r.text
    assert g.put('/api/admin/users/'+uid+'/quota',json={'admin_password':'Friend-password-123','limit_bytes':None}).status_code==403
    allowed=upload(g,body=b'a'*50)
    before=m.files_router.store.ids()
    assert g.post('/api/files/upload?name=huge.txt',content=b'b'*500).status_code==413
    assert m.files_router.store.ids()==before
    assert g.post('/api/notes',json={'title':'Large','body':'x'*500}).status_code==413
    assert g.put('/api/files/'+allowed['id']+'/text',json={'text':'z'*1000,'version':1}).status_code==413
    assert g.get('/api/files/'+allowed['id']+'/content').content==b'a'*50
    with m.db() as d:expected_usage=usage(d,uid)
    assert g.get('/api/account/storage').json()['used_bytes']==expected_usage
    c.put('/api/admin/users/'+uid+'/quota',json={'admin_password':'Owner-password-123','limit_bytes':0})
    assert g.delete('/api/files/'+allowed['id']).status_code==200
    assert g.get('/api/me').status_code==200
    c.put('/api/admin/users/'+uid+'/quota',json={'admin_password':'Owner-password-123','limit_bytes':None})
    assert upload(g,body=b'c'*1000)['size']==1000


def test_file_share_read_edit_password_expiry_and_folder_boundary():
    c,_,_=owner();enable(c,'files');folder=c.post('/api/files/folders',json={'name':'Shared'}).json();a=upload(c,parent=folder['id']);private=upload(c,'private.txt',b'PRIVATE')
    pub=c.post('/api/files/'+folder['id']+'/shares',json={'mode':'password','password':'shared-password','permission':'edit','duration':13,'unit':'minutes'});assert pub.status_code==200,pub.text
    token=pub.json()['token'];prefix='/api/shared-files/'+token;guest=client()
    assert guest.get(prefix).status_code==423
    assert guest.post(prefix+'/unlock',json={'password':'shared-password'}).status_code==200
    assert guest.get(prefix+'/'+a['id']+'/content').content==b'hello'
    assert guest.get(prefix+'/'+private['id']+'/content').status_code==404
    grant=guest.post(prefix+'/edit-session');assert grant.status_code==200,grant.text
    guest.headers['x-csrf-token']=grant.json()['csrf']
    assert guest.put(prefix+'/'+a['id']+'/text',json={'text':'Changed by friend','version':1}).status_code==200
    assert c.get('/api/files/'+a['id']+'/content').content==b'Changed by friend'
    assert guest.post(prefix+'/upload?parent_id='+folder['id']+'&name=new.txt',content=b'new').status_code==200
    assert guest.post(prefix+'/upload?parent_id='+private['id']+'&name=bad.txt',content=b'bad').status_code==404
    with m.db() as d:d.execute('UPDATE file_shares SET expires=? WHERE token=?',(m.now()-1,token))
    assert guest.get(prefix+'/'+a['id']+'/content').status_code==404
    assert guest.put(prefix+'/'+a['id']+'/text',json={'text':'Expired edit','version':2}).status_code==404


def test_named_file_shares_and_read_only_cannot_start_editor():
    c,_,_=owner();g,_=member(c);other,_=member(c,'other');enable(c,'files');a=upload(c)
    token=c.post('/api/files/'+a['id']+'/shares',json={'mode':'users','allowed':['friend']}).json()['token'];prefix='/api/shared-files/'+token
    assert client().get(prefix).status_code==401
    assert g.get(prefix).status_code==200
    assert other.get(prefix).status_code==403
    assert g.post(prefix+'/edit-session').status_code==403
    assert c.delete('/api/file-shares/'+token).status_code==200
    assert g.get(prefix).status_code==404


def test_foreground_heartbeat_remains_online_beyond_fifteen_minutes_limit(monkeypatch):
    c,_,_=owner();g,_=member(c);connect(c,g);stamp=m.now()
    # Previous limit (12 / 900 s) stopped a normal client after six minutes.
    for step in range(25):
        monkeypatch.setattr(m,'now',lambda step=step:stamp+step*30)
        response=g.post('/api/presence');assert response.status_code==200,response.text
    p=next(p for p in c.get('/api/presence').json()['users'] if p['login']=='friend');assert p['online']
    monkeypatch.setattr(m,'now',lambda:stamp+24*30+91)
    p=next(p for p in c.get('/api/presence').json()['users'] if p['login']=='friend');assert not p['online'] and p['last_seen']


def test_logout_removes_online_immediately_but_keeps_last_seen_and_other_active_sessions():
    from tests.test_account_security import other
    c,_,codes=owner();g,_=member(c);connect(c,g)
    second=other(codes)
    def status():return next(p for p in g.get('/api/presence').json()['users'] if p['login']=='owner')
    assert c.post('/api/presence').status_code==200
    assert status()['online']
    assert c.post('/api/logout').status_code==200
    assert not status()['online'] and status()['last_seen']
    # An unused login cannot keep the account online; an active one can.
    assert second.post('/api/presence').status_code==200
    third=other(codes[1:])
    assert third.post('/api/presence').status_code==200
    assert third.post('/api/logout').status_code==200
    assert status()['online']
    current=next(s['id'] for s in second.get('/api/security/sessions').json() if s['current'])
    assert second.delete('/api/security/sessions/'+current).status_code==200
    assert not status()['online'] and status()['last_seen']


def test_expired_presence_session_cannot_appear_online(monkeypatch):
    c,_,_=owner();g,_=member(c);connect(c,g)
    assert g.post('/api/presence').status_code==200
    uid=g.get('/api/me').json()['id']
    with m.db() as db:db.execute('UPDATE sessions SET expires=? WHERE user_id=?',(m.now()-1,uid))
    assert not next(p for p in c.get('/api/presence').json()['users'] if p['id']==uid)['online']
