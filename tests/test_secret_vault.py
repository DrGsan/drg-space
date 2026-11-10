"""Vault isolation, expiry, recovery boundaries and lossless moves."""
import json
from pathlib import Path

import pytest
from tests.test_security import owner, member, client, note, share, reset, m
from app.secret_vault import load, PREFIX

PASSWORD = 'Owner-password-123'
pytestmark=pytest.mark.skipif(not m.secret_vault.encrypted,reason='Vault activation requires encrypted server storage.')

@pytest.fixture(autouse=True)
def clean_vault_objects(reset):
    # The shared SQL fixture removes accounts; reconcile their former disk data
    # before each case so the audit covers only this case's operations.
    with m.storage_gate.exclusive_sync():m.secret_vault.reconcile()



def enable(c, codes, pin='1234'):
    r=c.post('/api/secret-vault/enable',json={'pin':pin,'code':codes[0]})
    assert r.status_code==200,r.text


def unlock(c, section='notes', pin='1234', mobile=False):
    r=c.post('/api/secret-vault/unlock',json={'pin':pin,'section':section,'mobile':mobile})
    assert r.status_code==200,r.text
    return {'x-vault-token':r.json()['token']},r.json()


def page(c, headers, title='Secret title'):
    r=c.post('/api/secret-vault/notes',json={'title':title,'body':'Secret body'},headers=headers)
    assert r.status_code==200,r.text
    return r.json()


def test_disabled_by_default_and_requires_encryption_and_current_2fa(monkeypatch):
    c,_,codes=owner()
    assert not c.get('/api/secret-vault').json()['enabled']
    monkeypatch.setattr(m.secret_vault,'encrypted',False)
    assert c.post('/api/secret-vault/enable',json={'pin':'1234','code':codes[0]}).status_code==409
    monkeypatch.setattr(m.secret_vault,'encrypted',True)
    assert c.post('/api/secret-vault/enable',json={'pin':'12345','code':codes[0]}).status_code==422
    assert c.post('/api/secret-vault/enable',json={'pin':'1234','code':'bad'}).status_code==403
    enable(c,codes)
    assert c.get('/api/secret-vault').json()['enabled']
    assert c.post('/api/secret-vault/enable',json={'pin':'123456','code':codes[1]}).status_code==409


def test_grants_are_scoped_to_account_session_and_section_and_revoke_on_logout():
    c,_,codes=owner();enable(c,codes)
    headers,_=unlock(c);n=page(c,headers)
    assert c.get('/api/secret-vault/notes/pages/'+n['id']).status_code==423
    assert c.get('/api/secret-vault/files/items',headers=headers).status_code==423
    guest,_=member(c)
    assert guest.get('/api/secret-vault/notes/pages/'+n['id'],headers=headers).status_code in (404,423)
    other=client();r=other.post('/api/login',json={'login':'owner','password':PASSWORD});other.headers['x-csrf-token']=r.json()['csrf']
    r=other.post('/api/2fa/verify',json={'code':codes[1]});other.headers['x-csrf-token']=r.json()['csrf']
    assert other.get('/api/secret-vault/notes/pages/'+n['id'],headers=headers).status_code==423
    assert c.post('/api/logout').status_code==200
    assert c.get('/api/secret-vault/notes/pages/'+n['id'],headers=headers).status_code==401


@pytest.mark.parametrize('mobile,ttl',[(False,1200),(True,300)])
def test_only_explicit_activity_extends_server_deadline(monkeypatch,mobile,ttl):
    c,_,codes=owner();enable(c,codes);headers,grant=unlock(c,mobile=mobile)
    assert grant['ttl']==ttl
    start=grant['expires']-ttl;monkeypatch.setattr(m,'now',lambda:start+ttl-1)
    if ttl>900:
        renewed=c.post('/api/refresh');assert renewed.status_code==200,renewed.text
        c.headers['x-csrf-token']=renewed.json()['csrf']
    assert c.get('/api/secret-vault/notes/items',headers=headers).status_code==200
    assert c.get('/api/notes').status_code==200
    monkeypatch.setattr(m,'now',lambda:start+ttl)
    assert c.get('/api/secret-vault/notes/items',headers=headers).status_code==423
    headers,grant=unlock(c,mobile=mobile)
    assert c.post('/api/secret-vault/notes/activity',headers=headers).json()['expires']==start+ttl*2
    assert c.post('/api/secret-vault/notes/lock',headers=headers).status_code==200
    assert c.get('/api/secret-vault/notes/items',headers=headers).status_code==423


def test_failed_pin_attempts_persist_and_block_all_grants_and_can_use_recovery_code():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c)
    for _ in range(4):
        assert c.post('/api/secret-vault/unlock',json={'pin':'9999','section':'notes'}).status_code==403
    assert c.post('/api/secret-vault/unlock',json={'pin':'999999','section':'files'}).status_code==429
    assert c.get('/api/secret-vault/notes/items',headers=headers).status_code==423
    assert c.post('/api/secret-vault/unlock',json={'pin':'1234','section':'notes'}).status_code==429
    assert c.post('/api/secret-vault/unblock',json={'code':codes[1]}).status_code==200
    assert c.post('/api/secret-vault/unblock',json={'code':codes[1]}).status_code==403
    unlock(c)


def test_pin_change_requires_old_pin_and_factor_revokes_all_grants():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c)
    assert c.put('/api/secret-vault/pin',json={'pin':'123456','old_pin':'1111','code':codes[1]}).status_code==403
    assert c.put('/api/secret-vault/pin',json={'pin':'123456','old_pin':'1234','code':'bad'}).status_code==403
    r=c.put('/api/secret-vault/pin',json={'pin':'123456','old_pin':'1234','code':codes[1]});assert r.status_code==200,r.text
    assert c.get('/api/secret-vault/notes/items',headers=headers).status_code==423
    unlock(c,pin='123456')


def test_factor_disable_admin_reset_and_existing_password_reset_ticket_are_blocked():
    c,_,_=owner();guest,_=member(c)
    with m.db() as db:
        uid=db.execute("SELECT id FROM users WHERE login='friend'").fetchone()[0]
    ticket=c.post('/api/admin/users/'+uid+'/password-reset',json={'admin_password':PASSWORD}).json()['url'].split('/')[-1]
    # Obtain a fresh factor code without knowing the helper's recovery codes.
    with m.db() as db:
        factor=m.cipher.decrypt(db.execute('SELECT totp FROM users WHERE id=?',(uid,)).fetchone()[0].encode()).decode()
        db.execute('UPDATE users SET last_counter=-1 WHERE id=?',(uid,))
    import pyotp
    assert guest.post('/api/secret-vault/enable',json={'pin':'1234','code':pyotp.TOTP(factor).at(m.now())}).status_code==200
    assert guest.post('/api/security/2fa/disable',json={'password':'Friend-password-123','code':'bad'}).status_code==409
    assert c.post('/api/admin/users/'+uid+'/reset-2fa',json={'admin_password':PASSWORD}).status_code==409
    assert client().post('/api/password-reset',json={'token':ticket,'password':'Another-password-456'}).status_code==409


def test_documents_are_absent_from_ordinary_routes_search_history_and_plain_envelope():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    uid=c.get('/api/me').json()['id']
    assert c.get('/api/notes/'+n['id']).status_code==404
    assert n['id'] not in c.get('/api/notes').text
    assert 'Secret title' not in c.get('/api/notes/search?q=Secret').text
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM note_revisions WHERE note_id=?',(n['id'],)).fetchone()
        value=db.execute('SELECT value FROM app_settings WHERE key=?',(PREFIX+uid,)).fetchone()[0]
        assert 'Secret title' not in value and 'Secret body' not in value
    assert c.get('/api/secret-vault').json()['backup_warning']


def test_file_encryption_is_separate_and_deletion_has_no_trash():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,'files')
    r=c.post('/api/secret-vault/files/upload?name=private.txt',content=b'never plaintext',headers=headers)
    assert r.status_code==200,r.text
    f=r.json();uid=c.get('/api/me').json()['id']
    assert c.get('/api/secret-vault/files/content/'+f['id'],headers=headers).content==b'never plaintext'
    assert f['id'] not in c.get('/api/files').text
    with m.db() as db:
        value=load(db,uid);identity=value['files'][f['id']]['object_id']
    path=Path(m.DATA)/'secret'/uid/'files'/identity
    assert b'never plaintext' not in path.read_bytes()
    assert c.delete('/api/secret-vault/files/items/'+f['id'],headers=headers).status_code==200
    assert not path.exists() and f['id'] not in c.get('/api/files/trash').text


def test_regular_note_tree_moves_in_and_out_with_no_history_or_shares():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c)
    root=note(c);child=c.post('/api/notes',json={'title':'Child','parent_id':root,'body':'child content'}).json()
    r=c.post('/api/secret-vault/notes/move-in',json={'id':root,'version':1},headers=headers)
    assert r.status_code==200,r.text
    assert c.get('/api/notes/'+root).status_code==404
    assert c.get('/api/notes/'+child['id']).status_code==404
    assert len(c.get('/api/secret-vault/notes/items',headers=headers).json()['items'])==2
    r=c.post('/api/secret-vault/notes/move-out',json={'id':root,'version':1},headers=headers)
    assert r.status_code==200,r.text
    assert c.get('/api/notes/'+root).status_code==200
    assert c.get('/api/notes/'+child['id']).json()['parent_id']==root


def test_active_publication_prevents_move_and_preserves_source():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);nid=note(c);token=share(c,nid,'public')
    assert c.post('/api/secret-vault/notes/move-in',json={'id':nid,'version':1},headers=headers).status_code==409
    assert c.get('/api/notes/'+nid).status_code==200
    assert client().get('/api/shared/'+token).status_code==200


def test_vault_destruction_needs_factor_but_not_pin():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);page(c,headers)
    assert c.request('DELETE','/api/secret-vault',json={'password':PASSWORD,'code':'bad'}).status_code==403
    r=c.request('DELETE','/api/secret-vault',json={'password':PASSWORD,'code':codes[1]});assert r.status_code==200,r.text
    assert not c.get('/api/secret-vault').json()['enabled']
    assert c.get('/api/secret-vault/notes/items',headers=headers).status_code==404


def test_all_http_surfaces_including_publications_and_errors_forbid_indexing():
    c,_,_=owner();token=share(c,note(c),'public')
    for path in ('/','/api/health','/api/notes','/share/'+token,'/api/shared/'+token,'/api/not-found'):
        response=client().get(path)
        assert 'noindex' in response.headers['x-robots-tag']


def test_file_tree_roundtrip_preserves_bytes_parentage_and_quota():
    from tests.test_files_trash import folder,upload,used
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,'files')
    root=folder(c,'Root');child=folder(c,'Child',root['id']);f=upload(c,'private.txt',b'private tree',child['id'])
    before=used(c)
    r=c.post('/api/secret-vault/files/move-in',json={'id':root['id'],'version':root['version']},headers=headers)
    assert r.status_code==200,r.text
    assert c.get('/api/files/'+f['id']+'/content').status_code==404
    assert c.get('/api/secret-vault/files/content/'+f['id'],headers=headers).content==b'private tree'
    r=c.post('/api/secret-vault/files/move-out',json={'id':root['id'],'version':root['version']},headers=headers)
    assert r.status_code==200,r.text
    assert c.get('/api/files/'+f['id']+'/content').content==b'private tree'
    assert used(c)==before


def test_note_attachment_roundtrip_keeps_links_and_removes_ordinary_access():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c)
    nid=note(c)
    attachment=c.post('/api/notes/'+nid+'/files?name=private.txt',content=b'private attachment').json()
    url=m.PUBLIC_URL+attachment['url']
    current=c.get('/api/notes/'+nid).json()
    r=c.put('/api/notes/'+nid,json={'title':current['title'],'body':'File '+url,'version':current['version']})
    assert r.status_code==200,r.text
    current=r.json()
    r=c.post('/api/secret-vault/notes/move-in',json={'id':nid,'version':current['version']},headers=headers)
    assert r.status_code==200,r.text
    assert c.get(attachment['url']).status_code==404
    assert c.get('/api/secret-vault/notes/content/'+attachment['id'],headers=headers).content==b'private attachment'
    saved=c.get('/api/secret-vault/notes/pages/'+nid,headers=headers).json()
    assert '/api/secret-vault/notes/content/' in saved['body']
    r=c.post('/api/secret-vault/notes/move-out',json={'id':nid,'version':saved['version']},headers=headers)
    assert r.status_code==200,r.text
    assert c.get(attachment['url']).content==b'private attachment'
    assert '/api/notes/'+nid+'/files/' in c.get('/api/notes/'+nid).json()['body']


def test_both_archive_types_exclude_vault_content_keys_and_grants_and_restore_refuses_loss():
    from app.personal_data import PersonalData
    from app.backups import BackupError
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);page(c,headers)
    uid=c.get('/api/me').json()['id']
    backups=m.admin_router.backups
    with backups.temp() as directory:
        metadata=backups.snapshot(directory)
        with backups.snapshot_database(directory) as snapshot:
            assert not snapshot.execute('SELECT 1 FROM app_settings WHERE key LIKE ?',('secret-vault:%',)).fetchone()
        with pytest.raises(BackupError,match='секретные хранилища'):
            backups.restore(directory,metadata)
    personal=PersonalData(m.db,m.DATA,lambda:m.now(),m.PUBLIC_URL,m.cipher)
    with personal.temp() as stage:
        snapshot=personal.snapshot(uid,stage)
        assert 'Secret title' not in snapshot.model_dump_json()
        assert 'secret-vault:' not in snapshot.model_dump_json()


def test_deletion_failure_revokes_content_and_keeps_retry_queue(monkeypatch):
    from app.secret_vault import VaultObjects
    from app.data_crypto import StorageError
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,'files')
    item=c.post('/api/secret-vault/files/upload?name=private.txt',content=b'private',headers=headers).json()
    original=VaultObjects.delete
    def failed(self,identity):raise StorageError('Temporary storage error')
    monkeypatch.setattr(VaultObjects,'delete',failed)
    assert c.delete('/api/secret-vault/files/items/'+item['id'],headers=headers).status_code>=400
    assert c.get('/api/secret-vault/files/content/'+item['id'],headers=headers).status_code==404
    uid=c.get('/api/me').json()['id']
    with m.db() as db:assert load(db,uid)['garbage']
    monkeypatch.setattr(VaultObjects,'delete',original)
    m.secret_vault.cleanup(uid)
    with m.db() as db:assert not load(db,uid)['garbage']


def test_native_download_is_scoped_and_streams_files_and_folders():
    import io
    from zipfile import ZipFile
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,'files')
    folder=c.post('/api/secret-vault/files/folders',json={'name':'Private'},headers=headers).json()
    f=c.post('/api/secret-vault/files/upload?name=private.txt&parent_id='+folder['id'],content=b'private stream',headers=headers).json()
    assert c.get('/api/secret-vault/files/download/'+f['id']).status_code==423
    r=c.post('/api/secret-vault/files/prepare-download/'+f['id'],headers=headers)
    assert r.status_code==200,r.text
    assert 'HttpOnly' in r.headers['set-cookie']
    cookie=c.cookies.get('drg_vault_download')
    assert c.get(r.json()['url']).content==b'private stream'
    c.cookies.set('drg_vault_download',cookie,path=r.json()['url'])
    assert c.get(r.json()['url']).status_code==423
    r=c.post('/api/secret-vault/files/prepare-download/'+folder['id'],headers=headers)
    with ZipFile(io.BytesIO(c.get(r.json()['url']).content)) as archive:
        assert archive.read('Private/private.txt')==b'private stream'
    assert c.post('/api/secret-vault/files/lock',headers=headers).status_code==200
    assert c.get(r.json()['url']).status_code==423


def test_s3_vault_namespace_encryption_and_permanent_version_removal(tmp_path):
    import io
    from app.secret_vault import VaultObjects,new_vault
    from app.file_store import FileStore
    from app.data_crypto import DataCipher
    class S3:
        def __init__(self):self.versions={};self.removed=[]
        def put_object(self,**kw):self.versions.setdefault(kw['Key'],[]).append((str(len(self.versions.get(kw['Key'],[]))),bytes(kw['Body'])))
        def get_object(self,**kw):return {'Body':io.BytesIO(self.versions[kw['Key']][-1][1])}
        def get_paginator(self,name):
            client=self
            class Pages:
                def paginate(self,**kw):
                    if name=='list_object_versions':yield {'Versions':[{'Key':key,'VersionId':version} for key,items in client.versions.items() if key.startswith(kw['Prefix']) for version,_ in items]}
                    elif name=='list_objects_v2':yield {'Contents':[{'Key':key} for key,items in client.versions.items() if items and key.startswith(kw['Prefix'])]}
                    else:yield {}
            return Pages()
        def delete_objects(self,**kw):
            for obj in kw['Delete']['Objects']:
                self.removed.append((obj['Key'],obj['VersionId']))
                self.versions[obj['Key']]=[(version,data) for version,data in self.versions[obj['Key']] if version!=obj['VersionId']]
            return {}
    client=S3();base=FileStore(tmp_path,DataCipher.ephemeral(),backend='s3',client=client,bucket='private',prefix='site',bind=False)
    value=new_vault('hash','factor');store=VaultObjects(tmp_path,'a'*32,value,base);identity='b'*32
    writer=store.writer(identity);writer.write(b'secret S3 body');writer.finish()
    assert '/secret/' in store.key(identity) and store.key(identity)!=base.key(identity)
    assert store.read(identity)==b'secret S3 body'
    assert b'secret S3 body' not in client.versions[store.key(identity)][0][1]
    writer=store.writer(identity);writer.write(b'new secret');writer.finish()
    store.delete(identity)
    assert len(client.removed)==2 and client.versions[store.key(identity)]==[]


def test_custom_note_icon_and_cover_survive_roundtrip_and_old_gc():
    from tests.test_widgets import image_bytes
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);nid=note(c)
    c.post('/api/notes/'+nid+'/assets/icon',content=image_bytes())
    c.post('/api/notes/'+nid+'/assets/cover',content=image_bytes())
    source=c.get('/api/notes/'+nid).json()
    before_icon=c.get(source['icon_url']).content;before_cover=c.get(source['cover_url']).content
    r=c.post('/api/secret-vault/notes/move-in',json={'id':nid,'version':source['version']},headers=headers)
    assert r.status_code==200,r.text
    inside=c.get('/api/secret-vault/notes/pages/'+nid,headers=headers).json()
    assert len(inside['decorations'])==2
    assert c.get('/api/secret-vault/notes/content/'+source['icon'][6:],headers=headers).content==before_icon
    r=c.post('/api/secret-vault/notes/move-out',json={'id':nid,'version':inside['version']},headers=headers)
    assert r.status_code==200,r.text
    restored=c.get('/api/notes/'+nid).json();m.files_router.cleanup()
    assert c.get(restored['icon_url']).content==before_icon
    assert c.get(restored['cover_url']).content==before_cover


def test_failed_transfer_readback_preserves_source_and_removes_staging(monkeypatch):
    from tests.test_files_trash import upload
    from app.secret_vault import VaultObjects
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,'files');source=upload(c,'source.txt',b'original body')
    original=VaultObjects.iter_read
    def corrupt(self,identity):
        for chunk in original(self,identity):yield chunk+b'corrupted'
    monkeypatch.setattr(VaultObjects,'iter_read',corrupt)
    r=c.post('/api/secret-vault/files/move-in',json={'id':source['id'],'version':source['version']},headers=headers)
    assert r.status_code==503,r.text
    assert c.get('/api/files/'+source['id']+'/content').content==b'original body'
    assert c.get('/api/secret-vault/files/items',headers=headers).json()['items']==[]


def test_database_roundtrip_keeps_records_views_formulas_and_starts_fresh_history():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c)
    root=c.post('/api/databases',json={'title':'Private database'}).json()['id']
    config=c.get('/api/databases/'+root).json()
    props=[{'id':'price','name':'Цена','type':'number'},{'id':'double','name':'Удвоенная','type':'formula','expression':'prop("Цена") * 2'}]
    views=[{'id':'table','name':'Таблица','type':'table'}]
    r=c.put('/api/databases/'+root,json={'properties':props,'views':views,'version':config['version']});assert r.status_code==200,r.text
    rid=c.post('/api/databases/'+root+'/records',json={'title':'First','properties':{'price':5}}).json()['id']
    with m.db() as db:
        db.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)',('old-revision',rid,'Old','old secret text',None,m.now()))
    version=c.get('/api/notes/'+root).json()['version']
    r=c.post('/api/secret-vault/notes/move-in',json={'id':root,'version':version},headers=headers);assert r.status_code==200,r.text
    assert c.get('/api/databases/'+root).status_code==404
    assert root not in c.get('/api/database-catalog').text
    with m.db() as db:
        for table in ('notes','note_databases','database_records','note_revisions','collaborative_docs'):
            assert not list(db.execute('SELECT * FROM '+table))
    r=c.get('/api/secret-vault/notes/databases/'+root,headers=headers);assert r.status_code==200,r.text
    data=r.json();assert data['properties']==props and data['views']==views
    assert data['records'][0]['properties']['double']==10
    r=c.patch('/api/secret-vault/notes/database-records/'+rid,json={'properties':{'price':7},'version':data['records'][0]['version']},headers=headers);assert r.status_code==200,r.text
    second=c.post('/api/secret-vault/notes/databases/'+root+'/records',json={'title':'Second','properties':{'price':10}},headers=headers);assert second.status_code==200,second.text
    title=c.patch('/api/secret-vault/notes/notes/'+root+'/database-title',json={'title':'Secret database','version':version},headers=headers);assert title.status_code==200,title.text
    r=c.post('/api/secret-vault/notes/move-out',json={'id':root,'version':title.json()['version']},headers=headers);assert r.status_code==200,r.text
    restored=c.get('/api/databases/'+root).json();assert restored['title']=='Secret database'
    assert sorted(r['properties']['double'] for r in restored['records'])==[14,20]
    assert c.get('/api/notes/'+rid).json()['parent_id']==root
    uid=c.get('/api/me').json()['id']
    with m.db() as db:
        assert not list(db.execute('SELECT * FROM note_revisions'))
        assert not load(db,uid)['notes']
    # Ordinary changes resume the normal history rule after moving out.
    current=c.get('/api/notes/'+rid).json()
    r=c.put('/api/notes/'+rid,json={'title':'Edited','body':'new ordinary text','version':current['version']});assert r.status_code==200,r.text
    with m.db() as db:assert db.execute('SELECT 1 FROM note_revisions WHERE note_id=?',(rid,)).fetchone()


def test_secret_database_catalog_and_relations_never_cross_personal_boundary():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c)
    root=note(c)
    ids=[c.post('/api/databases',json={'title':name,'parent_id':root}).json()['id'] for name in ('A','B')]
    config=c.get('/api/databases/'+ids[0]).json()
    properties=[{'id':'rel','name':'Связь','type':'relation','database_id':ids[1],'two_way':True,'multiple':True}]
    r=c.put('/api/databases/'+ids[0],json={'properties':properties,'views':[{'id':'table','name':'Таблица','type':'table'}],'version':config['version']});assert r.status_code==200,r.text
    a=c.post('/api/databases/'+ids[0]+'/records',json={'title':'A','properties':{}}).json()['id']
    b=c.post('/api/databases/'+ids[1]+'/records',json={'title':'B','properties':{}}).json()['id']
    assert c.post('/api/secret-vault/notes/move-in',json={'id':ids[0],'version':1},headers=headers).status_code==409
    assert c.post('/api/secret-vault/notes/move-in',json={'id':a,'version':1},headers=headers).status_code==409
    r=c.post('/api/secret-vault/notes/move-in',json={'id':root,'version':1},headers=headers);assert r.status_code==200,r.text
    data=c.get('/api/secret-vault/notes/databases/'+ids[0],headers=headers).json()
    assert len(c.get('/api/secret-vault/notes/database-catalog',headers=headers).json())==2
    r=c.patch('/api/secret-vault/notes/database-records/'+a,json={'properties':{'rel':[b]},'version':1},headers=headers);assert r.status_code==200,r.text
    inverse=c.get('/api/secret-vault/notes/databases/'+ids[1],headers=headers).json()
    back=next(p['id'] for p in inverse['properties'] if p['type']=='relation' and p['database_id']==ids[0]);assert inverse['records'][0]['properties'][back]==[a]
    assert c.post('/api/secret-vault/notes/move-out',json={'id':ids[0],'version':1},headers=headers).status_code==409
    assert c.post('/api/secret-vault/notes/databases',json={'title':'No workspace','space_id':'fake'},headers=headers).status_code==422
    assert c.get('/api/secret-vault/notes/databases/'+ids[0]).status_code==423
    # Permanent deletion removes both directions and IDs in remaining records.
    r=c.delete('/api/secret-vault/notes/items/'+b,headers=headers);assert r.status_code==200,r.text
    data=c.get('/api/secret-vault/notes/databases/'+ids[0],headers=headers).json();assert data['records'][0]['properties']['rel']==[]
    r=c.delete('/api/secret-vault/notes/items/'+ids[1],headers=headers);assert r.status_code==200,r.text
    data=c.get('/api/secret-vault/notes/databases/'+ids[0],headers=headers).json();assert data['properties']==[]


def test_backup_warning_enabled_even_if_empty_and_counts_notes_files_and_attachments():
    c,_,codes=owner();enable(c,codes)
    status=c.get('/api/secret-vault').json();assert status['backup_warning'] and status['counts']=={'notes':0,'files':0}
    nh,_=unlock(c);fh,_=unlock(c,'files');n=page(c,nh)
    assert c.post('/api/secret-vault/notes/upload?name=attachment.txt&parent_id='+n['id'],content=b'attachment',headers=nh).status_code==200
    assert c.post('/api/secret-vault/files/upload?name=file.txt',content=b'file',headers=fh).status_code==200
    assert c.post('/api/secret-vault/files/folders',json={'name':'Folder'},headers=fh).status_code==200
    status=c.get('/api/secret-vault').json();assert status['counts']=={'notes':1,'files':2}
    assert 'заметки: 1' in status['backup_warning'] and 'файлы: 2' in status['backup_warning']


def test_auto_lock_is_audited_once_client_or_background_and_expires_download_tickets(monkeypatch):
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,mobile=True);uid=c.get('/api/me').json()['id']
    assert c.post('/api/secret-vault/notes/auto-lock',headers=headers).status_code==200
    assert c.post('/api/secret-vault/notes/auto-lock',headers=headers).status_code==200
    with m.db() as db:assert sum(r[0] for r in db.execute("SELECT count FROM security_events WHERE action='vault-auto-locked'"))==1
    headers,grant=unlock(c,'files',mobile=True)
    f=c.post('/api/secret-vault/files/upload?name=private.txt',content=b'hello',headers=headers).json()
    assert c.post('/api/secret-vault/files/prepare-download/'+f['id'],headers=headers).status_code==200
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp+301)
    m.secret_vault.expire_grants(uid);m.secret_vault.expire_grants(uid)
    with m.db() as db:
        assert sum(r[0] for r in db.execute("SELECT count FROM security_events WHERE action='vault-auto-locked'"))==2
        value=load(db,uid);assert not value['grants'] and not value['downloads']
    assert c.get('/api/secret-vault/files/items',headers=headers).status_code==423


def test_secret_text_edit_removes_previous_blob_and_preserves_optimistic_version():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,'files');uid=c.get('/api/me').json()['id']
    f=c.post('/api/secret-vault/files/upload?name=private.txt',content=b'before',headers=headers).json()
    with m.db() as db:
        value=load(db,uid);store=m.secret_vault.objects(uid,value);old=value['files'][f['id']]['object_id']
    r=c.put('/api/secret-vault/files/'+f['id']+'/text',json={'text':'after','version':1},headers=headers);assert r.status_code==200,r.text
    assert c.get('/api/secret-vault/files/'+f['id']+'/text',headers=headers).json()['text']=='after'
    assert c.put('/api/secret-vault/files/'+f['id']+'/text',json={'text':'stale','version':1},headers=headers).status_code==409
    with m.db() as db:
        value=load(db,uid);assert store.ids()=={value['files'][f['id']]['object_id']}
        assert not value['garbage'] and old not in store.ids()


def test_all_file_lifecycle_operations_leave_only_referenced_objects_and_no_deleted_vault_directory():
    from app.vault_audit import inventory
    from tests.test_files_trash import folder,upload
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,'files');uid=c.get('/api/me').json()['id']
    root=folder(c,'Ordinary folder');f=upload(c,'original.txt',b'original',root['id'])
    with m.db() as db:original=db.execute('SELECT object_id FROM files WHERE id=?',(f['id'],)).fetchone()[0]
    assert c.post('/api/secret-vault/files/move-in',json={'id':root['id'],'version':root['version']},headers=headers).status_code==200
    assert not m.secret_vault.base.exists(original)
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM blob_gc').fetchone()
        value=load(db,uid);store=m.secret_vault.objects(uid,value);identity=value['files'][f['id']]['object_id']
    for name in ('renamed.txt','renamed-again.txt'):
        with m.db() as db:version=load(db,uid)['files'][f['id']]['version']
        r=c.put('/api/secret-vault/files/'+f['id'],json={'name':name,'parent_id':root['id'],'version':version},headers=headers);assert r.status_code==200,r.text
        assert store.ids()=={identity}
    other=c.post('/api/secret-vault/files/folders',json={'name':'Other'},headers=headers).json()
    r=c.put('/api/secret-vault/files/'+root['id'],json={'name':'Inside other','parent_id':other['id'],'version':1},headers=headers);assert r.status_code==200,r.text
    assert store.ids()=={identity}
    assert c.post('/api/secret-vault/files/move-out',json={'id':other['id'],'version':1},headers=headers).status_code==200
    assert not store.ids()
    assert c.get('/api/files/'+f['id']+'/content').content==b'original'
    version=c.get('/api/files?parent_id='+root['id']).json()['items'][0]['version']
    r=c.post('/api/secret-vault/files/move-in',json={'id':other['id'],'version':1},headers=headers);assert r.status_code==200,r.text
    with m.db() as db:
        report=inventory(db,m.secret_vault.base,m.DATA,m.now())
        assert report['objects']==report['files']==1
        assert not any(report[k] for k in ('missing','unreferenced','dangling_references','volume_strays'))
    r=c.delete('/api/secret-vault/files/items/'+other['id'],headers=headers);assert r.status_code==200,r.text
    assert not store.ids()
    r=c.request('DELETE','/api/secret-vault',json={'password':PASSWORD,'code':codes[1]});assert r.status_code==200,r.text
    assert not (Path(m.DATA)/'secret'/uid).exists()
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',(PREFIX+uid,)).fetchone()
        assert not db.execute('SELECT 1 FROM blob_gc').fetchone()


def test_failed_transfer_cleanup_is_durable_and_orphan_sweep_keeps_live_objects(monkeypatch):
    from app.secret_vault import VaultObjects,new_vault
    from app.data_crypto import StorageError
    from app.vault_audit import inventory
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);uid=c.get('/api/me').json()['id'];nid=note(c)
    assert c.post('/api/notes/'+nid+'/files?name=attached.txt',content=b'attached').status_code==200
    read=VaultObjects.iter_read;remove=VaultObjects.delete
    monkeypatch.setattr(VaultObjects,'iter_read',lambda *args:iter([b'bad readback']))
    monkeypatch.setattr(VaultObjects,'delete',lambda *args:(_ for _ in ()).throw(StorageError('Unavailable')))
    assert c.post('/api/secret-vault/notes/move-in',json={'id':nid,'version':1},headers=headers).status_code==503
    assert c.get('/api/notes/'+nid).status_code==200
    with m.db() as db:
        value=load(db,uid);assert value['garbage'] and not value['notes']
    monkeypatch.setattr(VaultObjects,'iter_read',read);monkeypatch.setattr(VaultObjects,'delete',remove)
    m.secret_vault.cleanup(uid)
    assert c.post('/api/secret-vault/notes/move-in',json={'id':nid,'version':1},headers=headers).status_code==200
    with m.db() as db:
        value=load(db,uid);store=m.secret_vault.objects(uid,value);live=store.ids()
    orphan='f'*32;writer=store.writer(orphan);writer.write(b'uncommitted');writer.finish()
    unknown=m.secret_vault.objects('e'*32,new_vault('',''));writer=unknown.writer('d'*32);writer.write(b'orphan vault');writer.finish()
    with m.db() as db:
        report=inventory(db,m.secret_vault.base,m.DATA,m.now());assert report['unreferenced']==2 and report['orphan_accounts']==1
    with m.storage_gate.exclusive_sync():m.secret_vault.reconcile()
    assert store.ids()==live and not (Path(m.DATA)/'secret'/('e'*32)).exists()
    with m.db() as db:
        report=inventory(db,m.secret_vault.base,m.DATA,m.now());assert not report['unreferenced'] and not report['orphan_accounts']


def test_space_pages_and_note_attachment_file_roots_cannot_be_moved_into_vault():
    c,_,codes=owner();enable(c,codes);nh,_=unlock(c);fh,_=unlock(c,'files')
    sid=c.post('/api/social/spaces',json={'name':'Space','template':'group'}).json()['id']
    nid=c.post('/api/notes',json={'title':'Space page','space_id':sid}).json()['id']
    assert c.post('/api/secret-vault/notes/move-in',json={'id':nid,'version':1},headers=nh).status_code==409
    assert c.post('/api/notes/'+nid+'/files?name=space-file.txt',content=b'private to space').status_code==200
    assert c.post('/api/secret-vault/files/move-in',json={'id':nid,'version':1},headers=fh).status_code==404
    assert c.get('/api/notes/'+nid).status_code==200


def test_file_copy_replacement_thumbnail_and_selection_zip_leave_no_old_objects():
    import io
    from zipfile import ZipFile
    from tests.test_widgets import image_bytes
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,'files');uid=c.get('/api/me').json()['id']
    original=c.post('/api/secret-vault/files/upload?name=a.txt',content=b'old',headers=headers).json()
    copied=c.post('/api/secret-vault/files/'+original['id']+'/duplicate',json={'version':1},headers=headers)
    assert copied.status_code==200,copied.text
    copied=copied.json();assert copied['name']=='a копия.txt'
    r=c.put('/api/secret-vault/files/'+original['id']+'/content?version=1',content=b'replaced',headers=headers);assert r.status_code==200,r.text
    assert c.put('/api/secret-vault/files/'+original['id']+'/content?version=1',content=b'stale',headers=headers).status_code==409
    picture=c.post('/api/secret-vault/files/upload?name=image.png',content=image_bytes(),headers=headers).json()
    thumb=c.get('/api/secret-vault/files/'+picture['id']+'/thumbnail',headers=headers);assert thumb.status_code==200,thumb.text
    assert thumb.headers['content-type']=='image/webp' and thumb.headers['cache-control']=='no-store'
    assert c.get('/api/secret-vault/files/'+picture['id']+'/thumbnail').status_code==423
    ticket=c.post('/api/secret-vault/files/prepare-selection',json={'ids':[original['id'],copied['id']]},headers=headers);assert ticket.status_code==200,ticket.text
    zipped=c.get(ticket.json()['url']);assert zipped.status_code==200,zipped.text
    with ZipFile(io.BytesIO(zipped.content)) as archive:
        assert archive.read('a.txt')==b'replaced' and archive.read('a копия.txt')==b'old'
    assert c.get(ticket.json()['url']).status_code==423
    with m.db() as db:
        value=load(db,uid);store=m.secret_vault.objects(uid,value)
        assert store.ids()=={item['object_id'] for item in value['files'].values() if item.get('object_id')}
        assert not value['garbage']


def test_versioned_s3_move_edit_delete_and_failed_multipart_leave_no_objects(monkeypatch):
    import io
    from tests.test_s3_multipart import MultipartS3,redirect_file_store
    from botocore.exceptions import ClientError
    from app.vault_audit import inventory
    class VersionedS3(MultipartS3):
        def __init__(self):super().__init__();self.versions={};self.markers={};self.counter=0
        def put_object(self,**kw):
            result=super().put_object(**kw);self.counter+=1
            self.versions.setdefault((kw['Bucket'],kw['Key']),{})[str(self.counter)]=bytes(kw['Body']);return result
        def complete_multipart_upload(self,**kw):
            result=super().complete_multipart_upload(**kw);self.counter+=1
            self.versions.setdefault((kw['Bucket'],kw['Key']),{})[str(self.counter)]=self.objects[(kw['Bucket'],kw['Key'])];return result
        def get_paginator(self,method):
            client=self
            class Pages:
                def paginate(self,**kw):
                    if method=='list_object_versions':yield {'Versions':[{'Key':key,'VersionId':version} for (bucket,key),items in client.versions.items() if bucket==kw['Bucket'] and key.startswith(kw['Prefix']) for version in items],'DeleteMarkers':[{'Key':key,'VersionId':version} for (bucket,key),items in client.markers.items() if bucket==kw['Bucket'] and key.startswith(kw['Prefix']) for version in items]}
                    elif method=='list_multipart_uploads':yield {'Uploads':[{'Key':upload['target'][1],'UploadId':identity} for identity,upload in client.uploads.items() if upload['target'][0]==kw['Bucket'] and upload['target'][1].startswith(kw['Prefix'])]}
                    else:yield {'Contents':[{'Key':key} for bucket,key in client.objects if bucket==kw['Bucket'] and key.startswith(kw['Prefix'])]}
            return Pages()
        def delete_objects(self,**kw):
            for item in kw['Delete']['Objects']:
                key=(kw['Bucket'],item['Key']);self.versions.get(key,{}).pop(item['VersionId'],None);self.markers.get(key,set()).discard(item['VersionId'])
                if not self.versions.get(key):self.objects.pop(key,None);self.versions.pop(key,None)
                if not self.markers.get(key):self.markers.pop(key,None)
            return {}
        def delete_object(self,**kw):
            super().delete_object(**kw);self.counter+=1;self.markers.setdefault((kw['Bucket'],kw['Key']),set()).add('m'+str(self.counter));return {'DeleteMarker':True}
        def head_object(self,**kw):
            if (kw['Bucket'],kw['Key']) not in self.objects:raise ClientError({'Error':{'Code':'NoSuchKey'},'ResponseMetadata':{'HTTPStatusCode':404}},'HeadObject')
            return {}
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c,'files');uid=c.get('/api/me').json()['id']
    remote=VersionedS3();redirect_file_store(monkeypatch,remote)
    for attr in ('backend','client','bucket','prefix'):monkeypatch.setattr(m.secret_vault.base,attr,getattr(m.files_router.store,attr))
    ordinary=c.post('/api/files/upload?name=ordinary.txt',content=b'ordinary').json()
    assert c.post('/api/secret-vault/files/move-in',json={'id':ordinary['id'],'version':1},headers=headers).status_code==200
    assert len(remote.objects)==len(remote.versions)==1
    assert c.put('/api/secret-vault/files/'+ordinary['id']+'/text',json={'text':'changed','version':1},headers=headers).status_code==200
    assert len(remote.objects)==len(remote.versions)==1
    assert c.post('/api/secret-vault/files/move-out',json={'id':ordinary['id'],'version':2},headers=headers).status_code==200
    assert len(remote.objects)==len(remote.versions)==1
    assert all('/secret/' not in key for bucket,key in remote.objects)
    assert c.post('/api/secret-vault/files/move-in',json={'id':ordinary['id'],'version':1},headers=headers).status_code==200
    assert len(remote.objects)==len(remote.versions)==1
    with m.db() as db:store=m.secret_vault.objects(uid,load(db,uid))
    writer=store.writer('c'*32);writer.write(b'orphan old version');writer.finish()
    remote.delete_object(Bucket=store.bucket,Key=store.key('c'*32))
    foreign_prefix=store.vault_prefix.rsplit('/',1)[0]+'/'+'d'*32+'/'+'e'*32
    remote.create_multipart_upload(Bucket=store.bucket,Key=foreign_prefix,ContentType='application/octet-stream')
    with m.storage_gate.exclusive_sync():m.secret_vault.reconcile()
    assert not remote.markers and not remote.uploads and len(remote.objects)==len(remote.versions)==1
    remote.fail='upload_part'
    body=b'x'*(17*1024**2+200)
    r=c.post('/api/secret-vault/files/upload?name=failed.bin',content=body,headers=headers);assert r.status_code==503,r.text
    assert not remote.uploads and len(remote.objects)==len(remote.versions)==1
    remote.fail=None
    with m.db() as db:
        report=inventory(db,m.secret_vault.base,m.DATA,m.now());assert report['objects']==1 and not report['unreferenced']
    assert c.delete('/api/secret-vault/files/items/'+ordinary['id'],headers=headers).status_code==200
    assert not remote.objects and not remote.versions and not remote.markers and not remote.uploads
    headers,_=unlock(c,'notes');n=page(c,headers);base='/api/secret-vault/notes/pages/'+n['id']
    for _ in range(2):
        result=c.post(base+'/assets/icon',content=Path('tests/fixtures/blue.png').read_bytes(),headers=headers)
        assert result.status_code==200,result.text
        assert len(remote.objects)==len(remote.versions)==1
    current=c.get(base,headers=headers).json()
    copied=c.post(base+'/duplicate',json={'version':current['version']},headers=headers)
    assert copied.status_code==200,copied.text
    assert len(remote.objects)==len(remote.versions)==2
    for identity in (n['id'],copied.json()['id']):assert c.delete('/api/secret-vault/notes/items/'+identity,headers=headers).status_code==200
    assert not remote.objects and not remote.versions and not remote.markers and not remote.uploads
    with m.db() as db:assert not db.execute('SELECT 1 FROM blob_gc').fetchone()


def test_secret_page_menu_metadata_images_copy_and_permanent_cleanup():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    base='/api/secret-vault/notes/pages/'+n['id']
    assert c.patch(base+'/meta',json={'version':1,'full_width':True},headers={}).status_code==423
    changed=c.patch(base+'/meta',json={'version':1,'full_width':True,'favorite':True,'icon':'🔑','cover':'sage'},headers=headers)
    assert changed.status_code==200,changed.text
    assert c.patch(base+'/meta',json={'version':1,'cover':''},headers=headers).status_code==409
    assert c.patch(base+'/meta',json={'version':2,'space_id':'other'},headers=headers).status_code==422
    image=Path('tests/fixtures/blue.png').read_bytes()
    uploaded=c.post(base+'/assets/cover',content=image,headers=headers);assert uploaded.status_code==200,uploaded.text
    original=c.get(base,headers=headers).json();aid=original['cover'][6:]
    assert original['favorite'] and original['full_width'] and len(original['decorations'])==1
    assert c.get('/api/secret-vault/notes/content/'+aid).status_code==423
    child=c.post('/api/secret-vault/notes',json={'title':'Child','parent_id':n['id'],'body':'/api/secret-vault/notes/content/'+aid},headers=headers).json()
    result=c.post(base+'/duplicate',json={'version':original['version']},headers=headers)
    assert result.status_code==200,result.text
    copied=result.json();assert copied['id']!=n['id'] and copied['cover']!=original['cover']
    assert not copied['favorite'] and copied['full_width']
    items=c.get('/api/secret-vault/notes/items',headers=headers).json()['items']
    children=[x for x in items if x.get('parent_id')==copied['id']];assert len(children)==1
    clone_child=c.get('/api/secret-vault/notes/pages/'+children[0]['id'],headers=headers).json()
    assert copied['cover'][6:] in clone_child['body'] and aid not in clone_child['body']
    assert 'Secret title' not in c.get('/api/notes').text
    # Deleting the source must not remove the cloned image or its child document.
    assert c.delete('/api/secret-vault/notes/items/'+n['id'],headers=headers).status_code==200
    assert c.get('/api/secret-vault/notes/content/'+copied['cover'][6:],headers=headers).status_code==200
    assert c.delete('/api/secret-vault/notes/items/'+copied['id'],headers=headers).status_code==200
    with m.db() as conn:
        uid=conn.execute('SELECT id FROM users WHERE login=?',('owner',)).fetchone()[0]
        value=load(conn,uid)
        assert not value['notes'] and not value['files'] and not value['garbage']
        assert not list(m.secret_vault.objects(uid,value).ids())


def test_replacing_secret_page_decoration_cleans_previous_object_and_moves_out():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    base='/api/secret-vault/notes/pages/'+n['id'];image=Path('tests/fixtures/blue.png').read_bytes()
    for _ in range(2):
        response=c.post(base+'/assets/icon',content=image,headers=headers);assert response.status_code==200,response.text
    current=c.get(base,headers=headers).json()
    with m.db() as conn:
        uid=conn.execute('SELECT id FROM users WHERE login=?',('owner',)).fetchone()[0];value=load(conn,uid)
        assert len(value['files'])==1 and not value['garbage']
        assert len(list(m.secret_vault.objects(uid,value).ids()))==1
    moved=c.post('/api/secret-vault/notes/move-out',json={'id':n['id'],'version':current['version']},headers=headers)
    assert moved.status_code==200,moved.text
    ordinary=c.get('/api/notes/'+n['id']).json()
    assert ordinary['icon'].startswith('image:')
    assert c.get(ordinary['icon_url']).status_code==200


def test_failed_secret_page_copy_rolls_back_files_and_catalog(monkeypatch):
    import app.vault_pages as pages
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    base='/api/secret-vault/notes/pages/'+n['id']
    assert c.post(base+'/assets/icon',content=Path('tests/fixtures/blue.png').read_bytes(),headers=headers).status_code==200
    current=c.get(base,headers=headers).json()
    def broken(*args):raise OSError('Simulated catalog failure')
    monkeypatch.setattr(pages,'save',broken)
    response=c.post(base+'/duplicate',json={'version':current['version']},headers=headers)
    assert response.status_code==500
    with m.db() as conn:
        uid=conn.execute('SELECT id FROM users WHERE login=?',('owner',)).fetchone()[0];value=load(conn,uid)
        assert len(value['notes'])==len(value['files'])==1
        assert len(list(m.secret_vault.objects(uid,value).ids()))==1


def test_secret_cover_catalog_uses_scoped_tokens_and_keeps_attribution(monkeypatch):
    import app.modules.note_assets as assets
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    uid=c.get('/api/me').json()['id'];base='/api/secret-vault/notes/pages/'+n['id']
    attribution={'author':'Example','license':'CC BY 4.0','source_url':'https://commons.wikimedia.org/wiki/Test'}
    def token(owner):return m.tokens.encode('note-cover','image',m.now()+1800,owner=owner,provider='commons',url='https://upload.wikimedia.org/test.png',attribution=attribution)
    fetched=[]
    async def fetch(url,**kwargs):fetched.append(url);return Path('tests/fixtures/blue.png').read_bytes(),'image/png',url
    monkeypatch.setattr(assets,'fetch_public',fetch)
    assert c.post(base+'/select-cover',json={'token':token('foreign')},headers=headers).status_code==403
    assert not fetched
    response=c.post(base+'/select-cover',json={'token':token(uid)},headers=headers)
    assert response.status_code==200,response.text
    current=c.get(base,headers=headers).json()
    assert current['decorations'][0]['payload']['attribution']==attribution
    assert len(fetched)==1
    assert c.get('/api/notes/'+n['id']+'/assets/'+current['cover'][6:]).status_code==404
    assert c.post('/api/secret-vault/notes/move-out',json={'id':n['id'],'version':current['version']},headers=headers).status_code==200
    assert c.get('/api/notes/'+n['id']).json()['cover_attribution']==attribution


def test_secret_image_failure_after_upload_discards_the_staged_object(monkeypatch):
    import app.vault_pages as pages
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    def broken(*args):raise OSError('Simulated catalog failure')
    monkeypatch.setattr(pages,'save',broken)
    result=c.post('/api/secret-vault/notes/pages/'+n['id']+'/assets/icon',content=Path('tests/fixtures/blue.png').read_bytes(),headers=headers)
    assert result.status_code==500
    with m.db() as conn:
        uid=conn.execute('SELECT id FROM users WHERE login=?',('owner',)).fetchone()[0];value=load(conn,uid)
        assert not value['files'] and not value['garbage'] and not list(m.secret_vault.objects(uid,value).ids())


def test_secret_database_page_copy_keeps_records_inside_the_new_database():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c)
    db=c.post('/api/secret-vault/notes/databases',json={'title':'Private database'},headers=headers).json()
    record=c.post('/api/secret-vault/notes/databases/'+db['id']+'/records',json={'title':'Private record','properties':{}},headers=headers)
    assert record.status_code==200,record.text
    base='/api/secret-vault/notes/pages/'+db['id'];current=c.get(base,headers=headers).json()
    assert c.patch(base+'/meta',json={'version':current['version'],'favorite':True},headers=headers).status_code==200
    # The shared database projection must retain private presentation metadata.
    assert c.post('/api/secret-vault/notes/databases/'+db['id']+'/records',json={'title':'Second record','properties':{}},headers=headers).status_code==200
    current=c.get(base,headers=headers).json();assert current['favorite']
    result=c.post(base+'/duplicate',json={'version':current['version']},headers=headers);assert result.status_code==200,result.text
    copy=result.json();copied_db=c.get('/api/secret-vault/notes/databases/'+copy['id'],headers=headers).json()
    assert {r['title'] for r in copied_db['records']}=={'Private record','Second record'}
    for row in copied_db['records']:
        copied_record=c.get('/api/secret-vault/notes/pages/'+row['note_id'],headers=headers).json()
        assert copied_record['parent_id']==copy['id'] and copied_record['record']['database_id']==copy['id']
    assert c.delete('/api/secret-vault/notes/items/'+db['id'],headers=headers).status_code==200
    assert len(c.get('/api/secret-vault/notes/databases/'+copy['id'],headers=headers).json()['records'])==2


def test_replaced_secret_image_survives_embedded_reference_then_is_erased():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    base='/api/secret-vault/notes/pages/'+n['id']
    assert c.post(base+'/assets/cover',content=Path('tests/fixtures/blue.png').read_bytes(),headers=headers).status_code==200
    current=c.get(base,headers=headers).json();aid=current['cover'][6:]
    linked=c.post('/api/secret-vault/notes',json={'title':'Image reference','body':'/api/secret-vault/notes/content/'+aid},headers=headers).json()
    assert c.patch(base+'/meta',json={'version':current['version'],'cover':''},headers=headers).status_code==200
    assert c.get('/api/secret-vault/notes/content/'+aid,headers=headers).status_code==200
    response=c.put('/api/secret-vault/notes/pages/'+linked['id'],json={'title':linked['title'],'body':'Reference removed','version':linked['version']},headers=headers)
    assert response.status_code==200,response.text
    assert c.get('/api/secret-vault/notes/content/'+aid,headers=headers).status_code==404
    with m.db() as conn:
        uid=conn.execute('SELECT id FROM users WHERE login=?',('owner',)).fetchone()[0];value=load(conn,uid)
        assert not value['files'] and not value['garbage'] and not list(m.secret_vault.objects(uid,value).ids())


def test_secret_subpages_use_atomic_common_links_and_permanent_pruning():
    from app.page_links import page_links
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    url='/api/secret-vault/notes/notes/'+n['id']+'/subpages'
    response=c.post(url,json={'title':'Private child','version':n['version'],'parent_blocks':[{'id':'keep','type':'paragraph','content':'Secret body'}]},headers=headers)
    assert response.status_code==200,response.text
    result=response.json();parent,child=result['parent'],result['page']
    assert page_links(parent['blocks'])=={child['id']} and parent['blocks'][0]['id']=='keep'
    assert c.post(url,json={'title':'Stale copy','version':n['version']},headers=headers).status_code==409
    assert c.delete('/api/secret-vault/notes/items/'+child['id'],headers=headers).status_code==200
    updated=c.get('/api/secret-vault/notes/pages/'+n['id'],headers=headers).json()
    assert not page_links(updated['blocks']) and 'Secret body' in updated['body']
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM notes').fetchone()
        assert not db.execute('SELECT 1 FROM note_revisions').fetchone()
        assert not db.execute('SELECT 1 FROM collaborative_docs').fetchone()


def test_secret_legacy_children_repair_and_slash_replacement_preserve_content():
    from app.page_links import page_links
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    child=c.post('/api/secret-vault/notes',json={'title':'Legacy child','parent_id':n['id']},headers=headers).json()
    url='/api/secret-vault/notes/notes/'+n['id']+'/subpages'
    repaired=c.post(url,json={'version':1,'repair_ids':[child['id']],'parent_blocks':[{'id':'text','type':'paragraph','content':'Keep content'},{'id':'slash','type':'paragraph','content':'/page'}]},headers=headers)
    assert repaired.status_code==200,repaired.text
    parent=repaired.json()['parent'];assert page_links(parent['blocks'])=={child['id']}
    created=c.post(url,json={'title':'Slash child','version':parent['version'],'replace_block_id':'slash'},headers=headers)
    assert created.status_code==200,created.text
    assert created.json()['parent']['blocks'][1]['props']['pageId']==created.json()['page']['id']
    assert created.json()['parent']['blocks'][0]['content']=='Keep content'


def test_vault_moves_prune_navigation_on_both_sides_without_leaking_secret_history():
    from app.page_links import page_links
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c)
    parent=c.post('/api/notes',json={'title':'Ordinary parent','blocks':[]}).json()
    response=c.post('/api/notes/'+parent['id']+'/subpages',json={'title':'Moving child','version':parent['version']});assert response.status_code==200,response.text
    child=response.json()['page'];outside=c.post('/api/notes',json={'title':'Stays ordinary'}).json()
    edited=c.put('/api/notes/'+child['id'],json={'title':child['title'],'version':child['version'],'blocks':[{'type':'paragraph','content':'Keep child text'},{'type':'pageLink','props':{'pageId':outside['id'],'title':'Outside'}}]}).json()
    r=c.post('/api/secret-vault/notes/move-in',json={'id':child['id'],'version':edited['version']},headers=headers);assert r.status_code==200,r.text
    assert not page_links(c.get('/api/notes/'+parent['id']).json()['blocks'])
    inside=c.get('/api/secret-vault/notes/pages/'+child['id'],headers=headers).json();assert not page_links(inside['blocks']) and 'Keep child text' in inside['body']
    secret=page(c,headers,'Stays secret')
    linked=c.put('/api/secret-vault/notes/pages/'+secret['id'],json={'title':secret['title'],'version':secret['version'],'blocks':[{'type':'pageLink','props':{'pageId':child['id'],'title':child['title']}}]},headers=headers);assert linked.status_code==200,linked.text
    changed=c.put('/api/secret-vault/notes/pages/'+child['id'],json={'title':child['title'],'version':inside['version'],'blocks':[{'type':'paragraph','content':'Keep child text'},{'type':'pageLink','props':{'pageId':secret['id'],'title':'Secret peer'}}]},headers=headers).json()
    r=c.post('/api/secret-vault/notes/move-out',json={'id':child['id'],'version':changed['version']},headers=headers);assert r.status_code==200,r.text
    ordinary=c.get('/api/notes/'+child['id']).json();assert not page_links(ordinary['blocks']) and 'Keep child text' in ordinary['body']
    assert not page_links(c.get('/api/secret-vault/notes/pages/'+secret['id'],headers=headers).json()['blocks'])
    with m.db() as db:assert not db.execute('SELECT 1 FROM note_revisions WHERE note_id=?',(child['id'],)).fetchone()


def test_maintenance_prunes_old_missing_secret_page_links_and_audit_detects_them():
    from app.vault_audit import inventory
    from app.page_links import page_links
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);n=page(c,headers)
    uid=c.get('/api/me').json()['id']
    with m.db() as db:
        from app.secret_vault import save
        value=load(db,uid);value['notes'][n['id']]['blocks']=[{'type':'paragraph','content':'Preserved'},{'type':'pageLink','props':{'pageId':'missing-page','title':'Gone'}}];save(db,uid,value)
    with m.db() as db:assert inventory(db,m.secret_vault.base,m.DATA,m.now())['dangling_references']==1
    m.secret_vault.cleanup(uid)
    current=c.get('/api/secret-vault/notes/pages/'+n['id'],headers=headers).json()
    assert not page_links(current['blocks']) and 'Preserved' in current['body']
    with m.db() as db:assert inventory(db,m.secret_vault.base,m.DATA,m.now())['dangling_references']==0


def test_removing_secret_owned_shortcut_erases_subtree_and_blobs_and_cannot_undo():
    from app.page_links import page_links
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);parent=page(c,headers)
    result=c.post('/api/secret-vault/notes/notes/'+parent['id']+'/subpages',json={'title':'Owned child','version':parent['version'],'parent_blocks':[{'id':'kept','type':'paragraph','content':'Preserve parent text'}]},headers=headers).json()
    parent,child=result['parent'],result['page']
    nested=c.post('/api/secret-vault/notes',json={'title':'Nested','parent_id':child['id']},headers=headers).json()
    assert c.post('/api/secret-vault/notes/upload?name=child.txt&parent_id='+child['id'],content=b'private child bytes',headers=headers).status_code==200
    assert c.post('/api/secret-vault/notes/pages/'+nested['id']+'/assets/icon',content=Path('tests/fixtures/blue.png').read_bytes(),headers=headers).status_code==200
    peer=page(c,headers,'Unrelated page')
    url='/api/secret-vault/notes/pages/'+parent['id']
    blocks=[parent['blocks'][0]]
    assert c.put(url,json={'title':parent['title'],'version':parent['version']-1,'blocks':blocks},headers=headers).status_code==409
    assert c.get('/api/secret-vault/notes/pages/'+child['id'],headers=headers).status_code==200
    saved=c.put(url,json={'title':parent['title'],'version':parent['version'],'blocks':blocks,'body':'Preserve parent text'},headers=headers);assert saved.status_code==200,saved.text
    assert not page_links(saved.json()['blocks'])
    for identity in (child['id'],nested['id']):assert c.get('/api/secret-vault/notes/pages/'+identity,headers=headers).status_code==404
    assert c.get('/api/secret-vault/notes/pages/'+peer['id'],headers=headers).status_code==200
    # The editor undo buffer contains blocks, never the permanently erased data.
    undone=c.put(url,json={'title':parent['title'],'version':saved.json()['version'],'blocks':parent['blocks']},headers=headers)
    assert undone.status_code==200,undone.text
    assert not page_links(undone.json()['blocks']) and 'Preserve parent text' in undone.json()['body']
    uid=c.get('/api/me').json()['id']
    with m.db() as db:
        value=load(db,uid)
        assert not value['files'] and not value['garbage'] and not list(m.secret_vault.objects(uid,value).ids())
        for table in ('notes','note_revisions','collaborative_docs'):assert not db.execute('SELECT 1 FROM '+table).fetchone()


def test_secret_shortcut_deletion_requires_last_owned_child_reference():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);parent=page(c,headers)
    child=c.post('/api/secret-vault/notes',json={'title':'Owned','parent_id':parent['id']},headers=headers).json();peer=page(c,headers,'Peer')
    url='/api/secret-vault/notes/pages/'+parent['id']
    links=[{'id':'one','type':'pageLink','props':{'pageId':child['id']}},{'id':'two','type':'pageLink','props':{'pageId':child['id']}},{'id':'peer','type':'pageLink','props':{'pageId':peer['id']}}]
    saved=c.put(url,json={'title':parent['title'],'version':parent['version'],'blocks':links},headers=headers).json()
    saved=c.put(url,json={'title':parent['title'],'version':saved['version'],'blocks':[links[1]]},headers=headers).json()
    assert c.get('/api/secret-vault/notes/pages/'+child['id'],headers=headers).status_code==200
    assert c.get('/api/secret-vault/notes/pages/'+peer['id'],headers=headers).status_code==200
    saved=c.put(url,json={'title':parent['title'],'version':saved['version'],'body':'Markdown only'},headers=headers).json()
    assert c.get('/api/secret-vault/notes/pages/'+child['id'],headers=headers).status_code==200


def test_secret_shortcut_deletion_preserves_shared_attachment_guard_and_rolls_back():
    c,_,codes=owner();enable(c,codes);headers,_=unlock(c);parent=page(c,headers)
    result=c.post('/api/secret-vault/notes/notes/'+parent['id']+'/subpages',json={'version':1,'parent_blocks':[]},headers=headers).json();parent,child=result['parent'],result['page']
    upload=c.post('/api/secret-vault/notes/upload?name=child.txt&parent_id='+child['id'],content=b'protected bytes',headers=headers).json()
    peer=c.post('/api/secret-vault/notes',json={'title':'Shared attachment reference','body':upload['url']},headers=headers);assert peer.status_code==200,peer.text
    url='/api/secret-vault/notes/pages/'+parent['id']
    result=c.put(url,json={'title':parent['title'],'version':parent['version'],'blocks':[]},headers=headers);assert result.status_code==409,result.text
    assert c.get(url,headers=headers).json()['version']==parent['version']
    assert c.get('/api/secret-vault/notes/pages/'+child['id'],headers=headers).status_code==200
    assert c.get(upload['url'],headers=headers).content==b'protected bytes'
