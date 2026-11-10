"""Ordering, immutable document uploads and page-scoped attachment access."""
from tests.test_security import owner,member,client,reset,note,share
from tests.test_social import family,join
from tests.test_personal_data import export,restore
from tests.test_widgets import image_bytes
from app import main as m

def create(c,title,parent=None,scope=None):
    r=c.post('/api/notes',json={'title':title,'parent_id':parent,'space_id':scope});assert r.status_code==200,r.text
    return r.json()

def test_new_notes_append_and_moves_insert_at_exact_position_atomically():
    c,_,_=owner();g,_=member(c)
    a,b,d=[create(c,title) for title in ['A','B','D']]
    def ids():return [n['id'] for n in c.get('/api/notes').json() if n['parent_id'] is None]
    assert ids()==[a['id'],b['id'],d['id']]
    r=c.post('/api/notes/'+d['id']+'/move',json={'parent_id':None,'before_id':a['id'],'version':d['version']});assert r.status_code==200,r.text
    assert ids()==[d['id'],a['id'],b['id']]
    d=r.json();r=c.post('/api/notes/'+d['id']+'/move',json={'parent_id':None,'before_id':b['id'],'version':d['version']});assert r.status_code==200
    assert ids()==[a['id'],d['id'],b['id']]
    before=ids();assert c.post('/api/notes/'+a['id']+'/move',json={'parent_id':None,'before_id':'missing','version':a['version']}).status_code==409
    assert ids()==before
    child=create(c,'Child',a['id'])
    assert c.post('/api/notes/'+a['id']+'/move',json={'parent_id':child['id'],'version':a['version']}).status_code==422
    assert g.post('/api/notes/'+a['id']+'/move',json={'version':a['version']}).status_code==404
    assert create(c,'Last')['id']==ids()[-1]

def test_note_file_upload_access_sharing_lock_and_portable_archive():
    c,_,_=owner();g,_=member(c);nid=note(c);body=image_bytes()
    c.put('/api/modules',json={'enabled':{'files':False,'media':False}})
    r=c.post('/api/notes/'+nid+'/files?name=preview.png',content=body);assert r.status_code==200,r.text
    attachment=r.json();url=attachment['url'];assert c.get(url).content==body
    partial=c.get(url,headers={'Range':'bytes=0-9'});assert partial.status_code==206 and partial.content==body[:10]
    assert c.get(url,headers={'Range':'bytes=999999-'}).status_code==416
    assert g.get(url).status_code==404 and client().get(url).status_code==401
    page=c.get('/api/notes/'+nid).json();blocks=[{'id':'attachment','type':'image','props':{'url':'http://testserver'+url,'name':'preview.png'}}]
    r=c.put('/api/notes/'+nid,json={'title':'Files portable','body':'![image](http://testserver'+url+')','blocks':blocks,'version':page['version']});assert r.status_code==200,r.text
    token=share(c,nid,'public');public=client();shared='/api/shared/'+token+'/files/'+attachment['id']
    assert public.get(shared).content==body
    other=note(c);other_token=share(c,other,'public');assert public.get('/api/shared/'+other_token+'/files/'+attachment['id']).status_code==404
    archive=export(c);r=restore(c,archive);assert r.status_code==200,r.text
    imported=[p for p in c.get('/api/notes').json() if p['id']!=nid and p['title']=='Files portable'][0]
    imported_url=imported['blocks'][0]['props']['url'];assert nid not in imported_url
    assert c.get(imported_url.replace('http://testserver','')).content==body
    assert c.put('/api/notes/'+imported['id'],json={'title':imported['title'],'body':imported['body'],'blocks':imported['blocks'],'version':imported['version']}).status_code==200
    assert c.post('/api/notes/'+nid+'/lock',json={'mode':'lock'}).status_code==200
    assert c.post('/api/notes/'+nid+'/files?name=blocked.txt',content=b'x').status_code==423
    c.delete('/api/shares/'+token);assert public.get(shared).status_code==404

def test_shared_upload_uses_page_owners_folder_and_checks_editor_role():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);page=create(c,'Shared',scope=sid)
    r=g.post('/api/notes/'+page['id']+'/files?name=test.txt',content=b'shared file');assert r.status_code==200,r.text
    assert c.get(r.json()['url']).content==b'shared file'
    guest_id=g.get('/api/me').json()['id']
    with m.db() as db:
        db.execute('INSERT INTO note_permissions(note_id,user_id,role) VALUES(?,?,?)',(page['id'],guest_id,'viewer'))
    assert g.post('/api/notes/'+page['id']+'/files?name=no.txt',content=b'x').status_code==403

def test_chat_original_upload_preserves_bytes_and_suppresses_image_preview():
    from tests.test_chat_retention import send
    from tests.test_social import enabled
    c,_,_=owner();sid=family(c);enabled(c);body=image_bytes()
    result=c.post('/api/chat/'+sid+'/attachments?name=original.png&as_file=true',content=body)
    assert result.status_code==200,result.text
    f=result.json();assert f['kind']=='file' and f['mime']=='application/octet-stream'
    assert c.get(f['url']).content==body
    r=send(c,sid,files=[f]);assert r.status_code==200,r.text


def test_full_backup_restores_note_attachment_folder_and_encrypted_bytes():
    from tests.test_admin import backup,headers
    from app.file_store import file_store
    c,_,_=owner();nid=note(c)
    r=c.post('/api/notes/'+nid+'/files?name=keep.txt',content=b'Keep original bytes')
    assert r.status_code==200,r.text
    fid=r.json()['id'];archive=backup(c)
    restored=c.post('/api/admin/backups/restore',content=archive,headers=headers());assert restored.status_code==200,restored.text
    with m.db() as db:
        row=db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
        assert row['parent_id']==nid and db.execute('SELECT 1 FROM files WHERE id=?',(nid,)).fetchone()
    assert file_store(m.db,m.DATA).read(row['object_id'])==b'Keep original bytes'


def test_public_editor_cannot_disclose_an_unrelated_attachment_by_id():
    from tests.test_share_editing import put
    c,_,_=owner();source=note(c);target=note(c)
    f=c.post('/api/notes/'+source+'/files?name=secret.txt',content=b'private').json()
    token=share(c,target,'public',permission='edit');anonymous=client()
    # A bare identifier in text must never grant a public download.
    assert put(anonymous,token,text=f['id']).status_code==200
    assert anonymous.get('/api/shared/'+token+'/files/'+f['id']).status_code==404
    assert put(anonymous,token,text='http://testserver'+f['url']).status_code==422
