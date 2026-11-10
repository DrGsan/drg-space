import io
import json
import tempfile
from zipfile import ZipFile
import pytest
from tests.test_security import owner,member,reset,note
from app import main as m
from app.archive_stream import ArchiveWriter,open_archive
from app.quota import usage
from tests.test_security import owner,member,reset,note
from tests.test_widgets import enable,image_bytes
from tests.test_personal_data import export,restore,unseal,seal,headers,PREFIX
from tests.test_admin import backup,headers as admin_headers
from tests.test_community_files import upload,connect
from tests.test_database_features import database,configure,record


def test_personal_archive_isolates_files_and_remaps_relations_and_page_assets():
    c,_,_=owner();g,_=member(c);enable(c,'files');enable(g,'files')
    folder=c.post('/api/files/folders',json={'name':'Documents'}).json();upload(c,'mine.txt',b'PERSONAL_OWN_CONTENT',folder['id']);upload(g,'foreign.txt',b'FOREIGN_PRIVATE_DATA')
    nid=note(c);icon=c.post('/api/notes/'+nid+'/assets/icon',content=image_bytes()).json()['icon']
    products=database(c,'Products');orders=database(c,'Orders')
    assert configure(c,products,[{'id':'price','name':'Price','type':'number'}]).status_code==200
    first=record(c,products,'Product',{'price':7})
    assert configure(c,orders,[{'id':'link','name':'Product','type':'relation','database_id':products,'two_way':True},{'id':'total','name':'Total','type':'rollup','relation_id':'link','property':'price','aggregate':'sum'}]).status_code==200
    record(c,orders,'Order',{'link':[first]})
    body=export(c);manifest,objects=unseal(body)
    assert [f['name'] for f in manifest['data']['files']]==['Documents','mine.txt']
    assert b'FOREIGN_PRIVATE_DATA' not in b''.join(objects.values())
    assert c.post(PREFIX+'preview',content=body,headers=headers()).json()['summary']['files']==1
    r=restore(c,body);assert r.status_code==200,r.text
    tree=c.get('/api/notes').json();new=[n for n in tree if n['title']=='Orders' and n['id']!=orders][0]
    data=c.get('/api/databases/'+new['id']).json();assert data['records'][0]['properties']['total']==7
    assert data['properties'][0]['database_id']!=products and data['records'][0]['properties']['link']!=[first]
    duplicates=[n for n in tree if n['id']!=nid and n['icon'].startswith('image:')]
    assert duplicates and duplicates[0]['icon']!=icon and c.get(duplicates[0]['icon_url']).status_code==200
    uid=c.get('/api/me').json()['id']
    with m.db() as d:
        assert d.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()[0]==usage(d,uid)


def test_shared_note_decoration_exported_but_foreign_private_files_excluded():
    c,_,_=owner();g,_=member(c);sid=connect(c,g)
    n=c.post('/api/notes',json={'title':'Shared','body':'Shared body','space_id':sid}).json()['id']
    c.post('/api/notes/'+n+'/assets/cover',content=image_bytes())
    body=export(g,'Friend-password-123');manifest,_=unseal(body)
    shared=next(x for x in manifest['data']['notes'] if x['id']==n)
    assert shared['source_shared'] and shared['cover'].startswith('image:')
    r=restore(g,body,'Friend-password-123');assert r.status_code==200,r.text
    copied=next(x for x in g.get('/api/notes').json() if x['id']!=n and x['title']=='Shared')
    assert copied['space_id'] is None and g.get(copied['cover_url']).status_code==200


def test_full_restore_files_quota_notifications_and_page_images():
    c,_,_=owner();enable(c,'files');uid=c.get('/api/me').json()['id'];f=upload(c,body=b'FULL_ARCHIVE_CONTENT')
    nid=note(c);c.post('/api/notes/'+nid+'/assets/cover',content=image_bytes())
    assert c.put('/api/admin/users/'+uid+'/quota',json={'admin_password':'Owner-password-123','limit_bytes':5*1024**3}).status_code==200
    body=backup(c);assert c.delete('/api/files/'+f['id']).status_code==200
    r=c.post('/api/admin/backups/restore',content=body,headers=admin_headers());assert r.status_code==200,r.text
    with m.db() as d:
        assert d.execute('SELECT limit_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()[0]==5*1024**3
        assert d.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()[0]==usage(d,uid)
        obj=d.execute('SELECT object_id FROM files WHERE id=?',(f['id'],)).fetchone()[0]
        from app.file_store import file_store
        assert file_store(m.db,m.DATA).read(obj)==b'FULL_ARCHIVE_CONTENT'
        assert d.execute('SELECT COUNT(*) FROM note_assets WHERE note_id=?',(nid,)).fetchone()[0]==1


def test_page_duplicate_copies_decoration_and_permanent_removal_releases_it():
    c,_,_=owner();nid=note(c);icon=c.post('/api/notes/'+nid+'/assets/icon',content=image_bytes()).json()['icon']
    r=c.post('/api/notes/'+nid+'/duplicate');assert r.status_code==200,r.text
    clone=r.json();assert clone['icon']!=icon and c.get(clone['icon_url']).status_code==200
    c.delete('/api/notes/'+nid);assert c.delete('/api/notes/'+nid+'/permanent').status_code==200
    assert c.get(clone['icon_url']).status_code==200
    with m.db() as d:assert not d.execute('SELECT 1 FROM media WHERE id=?',(icon[6:],)).fetchone()


def test_stream_archive_authenticates_all_frames_and_never_stages_plaintext(tmp_path):
    from app.personal_data import MAGIC
    path=tmp_path/'test.encrypted';writer=ArchiveWriter(path,'Archive-password-123',MAGIC)
    marker=b'PRIVATE_ARCHIVE_TEST_CONTENT';content=marker*100000
    with ZipFile(writer,'w') as z:z.writestr('large.txt',content)
    writer.finish();assert marker not in path.read_bytes()
    spool=open_archive(path,'Archive-password-123',MAGIC,tmp_path)
    with ZipFile(spool) as z:assert z.read('large.txt')==content
    for page in spool.root.iterdir():assert marker not in page.read_bytes()
    body=path.read_bytes()
    from app.backups import BackupError
    for bad in (body[:-1],body+b'garbage',body[:100]+bytes([body[100]^1])+body[101:]):
        with pytest.raises(BackupError):open_archive(bad,'Archive-password-123',MAGIC,tmp_path)


def test_legacy_personal_archive_still_imports_with_new_optional_fields_absent():
    c,_,_=owner();note(c);manifest,objects=unseal(export(c))
    manifest['data'].pop('files');manifest['data'].pop('note_assets')
    r=restore(c,seal(manifest,objects));assert r.status_code==200,r.text

from tests.test_storage_encryption import databases,seed,backup_service

def test_portable_backup_moves_encrypted_s3_objects_to_a_local_volume(databases,monkeypatch):
    import boto3
    class S3:
        def __init__(self):self.objects={}
        def put_object(self,**kw):self.objects[kw['Key']]=kw['Body'];return {}
        def get_object(self,**kw):return {'Body':io.BytesIO(self.objects[kw['Key']])}
        def delete_object(self,**kw):self.objects.pop(kw['Key'],None);return {}
        def get_paginator(self,name):
            parent=self
            class Pages:
                def paginate(self,**kw):yield {'Contents':[{'Key':key} for key in parent.objects if key.startswith(kw['Prefix'])]}
            return Pages()
    client=S3();monkeypatch.setattr(boto3,'client',lambda *a,**kw:client)
    monkeypatch.setenv('FILE_STORAGE','s3');monkeypatch.setenv('S3_BUCKET','private-test')
    source=databases();seed(source);service=backup_service(source);identity='c'*32
    writer=service.objects.writer(identity);writer.write(b'S3_PRIVATE_CONTENT');writer.finish()
    with source.connection() as c:c.execute('INSERT INTO files(id,user_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',('f'*16,'a'*32,'file','private.txt','text/plain',18,identity,1,1))
    password='Portable-archive-password-123'
    with service.temp() as directory:
        meta=service.snapshot(directory);archive=service.encrypt(directory,meta,password).read_bytes()
    assert b'S3_PRIVATE_CONTENT' not in next(iter(client.objects.values()))
    monkeypatch.setenv('FILE_STORAGE','local');target=databases();target_service=backup_service(target)
    with target_service.temp() as directory:
        metadata=target_service.prepare(archive,password,directory);target_service.restore(directory,metadata)
    with target.connection() as c:restored=c.execute('SELECT object_id FROM files').fetchone()[0]
    assert target_service.objects.read(restored)==b'S3_PRIVATE_CONTENT'
    assert restored!=identity and b'S3_PRIVATE_CONTENT' not in (target.root/'files'/restored).read_bytes()

def test_deleted_relation_target_does_not_break_personal_or_full_archives():
    c,_,_=owner();target=database(c,'Deleted target');source=database(c,'Surviving database');rid=record(c,target,'Deleted record',{})
    assert configure(c,source,[{'id':'link','name':'Link','type':'relation','database_id':target},{'id':'total','name':'Total','type':'rollup','relation_id':'link','property':'title','aggregate':'count'}]).status_code==200
    record(c,source,'Survivor',{'link':[rid]});c.delete('/api/notes/'+target);c.delete('/api/notes/'+target+'/permanent')
    body=export(c);manifest,_=unseal(body)
    d=next(x for x in manifest['data']['databases'] if x['note_id']==source)
    assert d['properties'][0]['type']=='text' and d['properties'][1]['type']=='formula'
    assert restore(c,body).status_code==200
    full=backup(c);assert c.post('/api/admin/backups/restore',content=full,headers=admin_headers()).status_code==200


def test_duplicate_database_remaps_dependencies_to_copied_records():
    c,_,_=owner();db=database(c);props=[{'id':'before','name':'Before','type':'dependency'}];assert configure(c,db,props).status_code==200
    first=record(c,db,'First',{});second=record(c,db,'Second',{'before':[first]})
    clone=c.post('/api/notes/'+db+'/duplicate');assert clone.status_code==200,clone.text
    data=c.get('/api/databases/'+clone.json()['id']).json();copied_first=next(r for r in data['records'] if r['title']=='First')['note_id'];copied_second=next(r for r in data['records'] if r['title']=='Second')
    assert copied_first!=first and copied_second['properties']['before']==[copied_first]
    assert restore(c,export(c)).status_code==200
