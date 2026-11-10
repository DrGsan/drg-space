import io
import json
from zipfile import ZipFile
import pytest
from tests.test_security import owner,member,reset,client
from tests.test_widgets import enable
from app import main as m
from app.quota import usage


def prepare(c,folders=None,parent=None,name='Local folder'):
    r=c.post('/api/files/folder-import',json={'name':name,'parent_id':parent,'folders':folders or []});assert r.status_code==200,r.text
    return r.json()

def send(c,batch,name='hello.txt',body=b'hello',parent=None):
    return c.post('/api/files/upload?name='+name+'&parent_id='+(parent or batch['root']['id']),content=body,headers={'X-Folder-Upload-Token':batch['upload_token']})


def test_directory_tree_empty_folders_files_zip_and_no_overwrites():
    c,_,_=owner();enable(c,'files');existing=c.post('/api/files/folders',json={'name':'Destination'}).json()['id']
    b=prepare(c,['nested/deep','empty'],existing)
    assert set(b['folders'])=={'','nested','nested/deep','empty'}
    assert send(c,b).status_code==200
    assert send(c,b,'code.py',b'print(42)',b['folders']['nested/deep']).status_code==200
    assert c.get('/api/files?parent_id='+existing).json()['items'][0]['id']==b['root']['id']
    with ZipFile(io.BytesIO(c.get('/api/files/download.zip?ids='+b['root']['id']).content)) as z:
        assert z.read('Destination/Local folder/hello.txt')==b'hello'
        assert z.read('Destination/Local folder/nested/deep/code.py')==b'print(42)'
        assert 'Destination/Local folder/empty/' in z.namelist()
    again=prepare(c,[],existing);assert again['root']['name']=='Local folder (2)'
    assert c.get('/api/files?parent_id='+b['root']['id']).json()['items']
    uid=c.get('/api/me').json()['id']
    with m.db() as d:assert d.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()[0]==usage(d,uid)


@pytest.mark.parametrize('path',['../escape','a/../../b','/absolute','a/','a//b','a\\b','C:/drive','a/'+('b/'*32)+'c','a/\x00bad'])
def test_malicious_directory_paths_never_create_rows(path):
    c,_,_=owner();enable(c,'files')
    r=c.post('/api/files/folder-import',json={'name':'Folder','folders':[path]});assert r.status_code==422,r.text
    assert c.get('/api/files').json()['items']==[]


def test_foreign_target_and_scoped_upload_grant_revocation_and_expiry(monkeypatch):
    c,_,_=owner();g,_=member(c);enable(c,'files');enable(g,'files');foreign=g.post('/api/files/folders',json={'name':'Private'}).json()['id']
    assert c.post('/api/files/folder-import',json={'name':'Escape','parent_id':foreign}).status_code==404
    batch=prepare(c);outside=c.post('/api/files/folders',json={'name':'Outside'}).json()['id']
    assert send(g,batch).status_code==403
    assert send(c,batch,parent=outside).status_code==403
    assert send(c,batch,parent=foreign).status_code==404
    raw=batch['upload_token'];batch['upload_token']=raw[:-5]+'bogus';assert send(c,batch).status_code==401;batch['upload_token']=raw
    original=m.now;monkeypatch.setattr(m,'now',lambda:original()+3601)
    # Refresh access, preserving the same account session, before checking grant expiry.
    r=c.post('/api/refresh');assert r.status_code==200;c.headers['X-CSRF-Token']=r.json()['csrf']
    assert send(c,batch).status_code==401
    renewed=c.post('/api/files/folder-import/'+batch['root']['id']+'/renew');assert renewed.status_code==200,renewed.text
    batch.update(renewed.json());assert send(c,batch).status_code==200
    assert g.post('/api/files/folder-import/'+batch['root']['id']+'/renew').status_code==401


def test_folder_quota_failure_rolls_back_entire_tree_and_file_quota_rolls_back_blob():
    c,_,_=owner();enable(c,'files');uid=c.get('/api/me').json()['id']
    with m.db() as d:
        used=d.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()[0];d.execute('UPDATE user_limits SET limit_bytes=? WHERE user_id=?',(used,uid))
    assert c.post('/api/files/folder-import',json={'name':'Too large','folders':['a/b','empty']}).status_code==413
    assert c.get('/api/files').json()['items']==[]
    with m.db() as d:d.execute('UPDATE user_limits SET limit_bytes=NULL WHERE user_id=?',(uid,))
    batch=prepare(c)
    with m.db() as d:
        used=d.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()[0];d.execute('UPDATE user_limits SET limit_bytes=? WHERE user_id=?',(used,uid))
    before=set((m.DATA/'files').iterdir());assert send(c,batch).status_code==413
    assert set((m.DATA/'files').iterdir())==before
    assert c.get('/api/files?parent_id='+batch['root']['id']).json()['items']==[]


def test_directory_and_plain_uploads_accept_more_than_30_files():
    c,_,_=owner();enable(c,'files');batch=prepare(c)
    for i in range(35):assert send(c,batch,'file'+str(i)+'.txt').status_code==200
    assert len(c.get('/api/files?parent_id='+batch['root']['id']).json()['items'])==35
    for i in range(35):
        r=c.post('/api/files/upload?name=plain'+str(i)+'.txt',content=b'')
        assert r.status_code==200,r.text


def test_folder_starts_use_a_short_window_without_extending_it_on_rejection(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,_,_=owner();enable(c,'files')
    for i in range(30):prepare(c,name='Folder'+str(i))
    assert len(c.get('/api/files').json()['items'])==30
    stamp+=45
    blocked=c.post('/api/files/folder-import',json={'name':'Blocked'})
    assert blocked.status_code==429 and blocked.headers['retry-after']=='15'
    assert len(c.get('/api/files').json()['items'])==30
    stamp+=15
    prepare(c,name='After pause')
    with m.db() as d:assert d.execute('SELECT COUNT(*) FROM security_bans').fetchone()[0]==0


@pytest.mark.parametrize('batch_upload',[False,True])
def test_file_rate_exhaustion_rejects_before_writing_and_recovers(monkeypatch,batch_upload):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,_,_=owner();enable(c,'files');batch=prepare(c);uid=c.get('/api/me').json()['id']
    action='folder-file-upload' if batch_upload else 'file-upload'
    key=m.protection.key('testclient',action,uid)
    with m.db() as d:
        for _ in range(600):d.execute('INSERT INTO throttle VALUES(?,?)',(key,stamp))
    before=set((m.DATA/'files').iterdir())
    request=lambda:send(c,batch) if batch_upload else c.post('/api/files/upload?name=plain.txt',content=b'hello')
    stamp+=50;blocked=request()
    assert blocked.status_code==429 and blocked.headers['retry-after']=='10'
    assert set((m.DATA/'files').iterdir())==before
    assert c.get('/api/files?parent_id='+batch['root']['id']).json()['items']==[]
    stamp+=10;assert request().status_code==200


def test_size_limit_applies_to_plain_folder_and_streamed_bodies(monkeypatch):
    from app.modules import files
    from app.http import Request
    c,_,_=owner();enable(c,'files');batch=prepare(c)
    monkeypatch.setattr(files,'MAX_FILE_SIZE',32)
    for send_file in (lambda body:c.post('/api/files/upload?name=plain.bin',content=body),lambda body:send(c,batch,'nested.bin',body)):
        assert send_file(b'a'*32).status_code==200
        before=set((m.DATA/'files').iterdir())
        assert send_file(b'a'*33).status_code==413
        assert set((m.DATA/'files').iterdir())==before
        async def too_large(self):
            yield b'a'*16
            yield b'b'*17
        with monkeypatch.context() as patch:
            patch.setattr(Request,'stream',too_large)
            assert send_file(b'').status_code==413
            assert set((m.DATA/'files').iterdir())==before


def test_default_size_limit_is_ten_gib():
    from app.modules.files import MAX_FILE_SIZE
    assert MAX_FILE_SIZE==10*1024**3


def test_directory_input_is_bounded_and_creates_nothing_when_over_limit():
    c,_,_=owner();enable(c,'files')
    assert c.post('/api/files/folder-import',json={'name':'Big','folders':['folder'+str(i) for i in range(2048)]}).status_code==422
    assert c.get('/api/files').json()['items']==[]


def test_folder_grant_is_bound_to_the_device_and_renewal_checks_ownership():
    c,_,codes=owner();g,_=member(c);enable(c,'files');enable(g,'files');batch=prepare(c)
    assert g.post('/api/files/folder-import/'+batch['root']['id']+'/renew').status_code==404
    another=client();r=another.post('/api/login',json={'login':'owner','password':'Owner-password-123'});assert r.status_code==200;another.headers['X-CSRF-Token']=r.json()['csrf']
    r=another.post('/api/2fa/verify',json={'code':codes[0]});assert r.status_code==200;another.headers['X-CSRF-Token']=r.json()['csrf']
    assert send(another,batch).status_code==403
    grant=another.post('/api/files/folder-import/'+batch['root']['id']+'/renew');assert grant.status_code==200
    batch.update(grant.json());assert send(another,batch).status_code==200


def test_admin_size_handles_disappearing_sqlite_shared_memory_files(monkeypatch):
    from types import SimpleNamespace
    from app.database import Database
    class Main:
        def is_file(self):return True
        def stat(self):return SimpleNamespace(st_size=4096)
    class Gone:
        def is_file(self):return True
        def stat(self):raise FileNotFoundError('Transient SQLite SHM disappeared.')
    backend=SimpleNamespace(kind='sqlite',root=SimpleNamespace(glob=lambda pattern:[Main(),Gone()]))
    assert Database.physical_bytes(backend)==4096
