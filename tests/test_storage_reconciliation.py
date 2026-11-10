import json

import pytest
from botocore.exceptions import ClientError

from app.data_crypto import MediaStore,StorageError
from app.file_store import FileStore
from app.storage_reconciliation import reconcile,inventory,summary,MISSING_KEY,MISSING_GRACE
from tests.test_storage_encryption import databases,seed,backup_service
from tests.test_s3_media import S3


def missing_file(db,identity='d'*32,fid='f'*16,created=1):
    with db.connection() as c:
        c.execute('INSERT INTO files(id,user_id,kind,name,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                  (fid,'a'*32,'file','Original filename',5,identity,created,created))
    return identity


def state(db):
    with db.connection() as c:
        row=c.execute('SELECT value FROM app_settings WHERE key=?',(MISSING_KEY,)).fetchone()
        return json.loads(row[0]) if row else {}


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_missing_records_retire_only_after_repeat_and_preserve_documents_and_quota(databases,engine):
    db=databases(engine);seed(db);objects=FileStore(db.root,db.cipher,backend='local');media=MediaStore(db.root,db.cipher)
    identity=missing_file(db);aid='b'*32;media.delete(aid);stamp=1000000
    with db.connection() as c:
        c.execute('INSERT INTO note_assets VALUES(?,?,?,?,?)',(aid,'n'*16,'cover','{}',1))
        c.execute('UPDATE notes SET icon=?,cover=? WHERE id=?',('image:'+aid,'image:'+aid,'n'*16))
        settings={'profile':{'display_name':'Original name','avatar_id':aid},'dashboard':{'instances':[{'id':'gallery','module_id':'media','config':{'items':[{'id':'one','asset_id':aid}],'selected':['one']}}]}}
        c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),'a'*32))
        c.execute('INSERT INTO file_shares(token,file_id,mode,created) VALUES(?,?,?,?)',('publication','f'*16,'public',1))
        c.execute('INSERT INTO file_share_sessions VALUES(?,?,?)',('grant','publication',stamp+999999))
        c.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)',('revision','n'*16,'Previous title','Keep original historical text',None,1))
    reconcile(db.connection,objects,media,stamp)
    assert set(state(db))=={'file:'+identity,'media:'+aid}
    reconcile(db.connection,objects,media,stamp+MISSING_GRACE-1)
    with db.connection() as c:assert c.execute('SELECT COUNT(*) FROM media').fetchone()[0]==1
    report=reconcile(db.connection,objects,media,stamp+MISSING_GRACE)
    assert report['file']['removed_records']==report['media']['removed_records']==1
    assert state(db)=={}
    with db.connection() as c:
        for table in ('media','note_assets','files','file_shares','file_share_sessions'):assert c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0,table
        note=c.execute('SELECT body,icon,cover FROM notes').fetchone()
        assert note['body']=='PRIVATE_BODY_MARKER / личный текст' and note['icon']=='📄' and note['cover']==''
        assert c.execute('SELECT body FROM note_revisions').fetchone()[0]=='Keep original historical text'
        settings=json.loads(c.execute('SELECT settings FROM users').fetchone()[0])
        assert settings['profile']=={'display_name':'Original name'}
        assert settings['dashboard']['instances'][0]['config']=={'items':[],'selected':[]}
        from app.quota import usage
        assert c.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',('a'*32,)).fetchone()[0]==usage(c,'a'*32)


def test_returned_objects_cancel_timer_and_fresh_uploads_are_not_candidates(databases):
    db=databases();seed(db);objects=FileStore(db.root,db.cipher);media=MediaStore(db.root,db.cipher);stamp=1000000
    identity=missing_file(db);fresh=missing_file(db,'e'*32,'g'*16,stamp)
    reconcile(db.connection,objects,media,stamp);assert set(state(db))=={'file:'+identity}
    writer=objects.writer(identity);writer.write(b'restored bytes');writer.finish()
    reconcile(db.connection,objects,media,stamp+1);assert not state(db)
    with db.connection() as c:assert c.execute('SELECT COUNT(*) FROM files').fetchone()[0]==2
    objects.delete(identity);reconcile(db.connection,objects,media,stamp+MISSING_GRACE)
    assert set(state(db))=={'file:'+identity,'file:'+fresh}
    assert all(v['first']==stamp+MISSING_GRACE for v in state(db).values())


def test_broken_file_row_without_object_identity_also_retires(databases):
    db=databases();seed(db);missing_file(db,identity=None)
    objects=FileStore(db.root,db.cipher);media=MediaStore(db.root,db.cipher)
    reconcile(db.connection,objects,media,1000000)
    assert set(state(db))=={'file:row:'+'f'*16}
    reconcile(db.connection,objects,media,1000000+MISSING_GRACE)
    with db.connection() as c:assert c.execute('SELECT COUNT(*) FROM files').fetchone()[0]==0


class ConfirmedS3(S3):
    failure=None
    fail_listing=False
    fail_delete=None
    def head_object(self,**kw):
        code,status=self.failure or ('NoSuchKey',404)
        if self.failure or kw['Key'] not in self.objects:
            raise ClientError({'Error':{'Code':code},'ResponseMetadata':{'HTTPStatusCode':status}},'HeadObject')
        return {}
    def delete_object(self,**kw):
        if kw['Key']==self.fail_delete:raise StorageError('Injected outage')
        return super().delete_object(**kw)
    def get_paginator(self,name):
        if self.fail_listing:raise StorageError('Injected incomplete listing')
        return super().get_paginator(name)


@pytest.mark.parametrize('failure',[('AccessDenied',403),('InternalError',500),('NoSuchBucket',404)])
def test_s3_failures_never_delete_records_or_advance_absence_timer(databases,failure):
    db=databases();seed(db);client=ConfirmedS3();objects=FileStore(db.root,db.cipher,backend='s3',client=client,bucket='private',bind=False);media=MediaStore(db.root,db.cipher,objects)
    identity=missing_file(db);stamp=1000000
    reconcile(db.connection,objects,media,stamp);before=state(db)
    client.failure=failure
    with pytest.raises(StorageError):reconcile(db.connection,objects,media,stamp+MISSING_GRACE)
    assert state(db)==before
    with db.connection() as c:assert c.execute('SELECT COUNT(*) FROM files').fetchone()[0]==1


def test_s3_listing_failure_and_referenced_objects_do_not_trigger_deletions(databases):
    db=databases();seed(db);client=ConfirmedS3();objects=FileStore(db.root,db.cipher,backend='s3',client=client,bucket='private',bind=False);media=MediaStore(db.root,db.cipher,objects)
    identity=missing_file(db);client.objects[objects.key('e'*32)]=b'orphan';client.objects['unrelated/other-project']=b'keep'
    client.fail_listing=True
    with pytest.raises(StorageError):reconcile(db.connection,objects,media,1000000)
    assert len(client.objects)==2 and not state(db)
    client.fail_listing=False;reconcile(db.connection,objects,media,1000000)
    assert client.objects=={'unrelated/other-project':b'keep'}
    # Even a stale empty listing cannot overrule a successful HEAD.
    client.head_object=lambda **kw:{}
    reconcile(db.connection,objects,media,1000000+MISSING_GRACE)
    assert not state(db)
    with db.connection() as c:assert c.execute('SELECT object_id FROM files').fetchone()[0]==identity


def test_unreferenced_deletion_failure_is_queued_without_blocking_other_objects(databases):
    db=databases();seed(db);client=ConfirmedS3();objects=FileStore(db.root,db.cipher,backend='s3',client=client,bucket='private',bind=False);media=MediaStore(db.root,db.cipher,objects)
    for identity in ('c'*32,'d'*32):client.objects[objects.key(identity)]=b'orphan'
    client.fail_delete=objects.key('c'*32)
    reconcile(db.connection,objects,media,1000000)
    assert set(client.objects)=={objects.key('c'*32)}
    with db.connection() as c:assert c.execute('SELECT id FROM blob_gc').fetchone()[0]=='c'*32


def test_probe_budget_rotates_and_missing_observations_are_not_backed_up(databases,monkeypatch):
    db=databases();seed(db);objects=FileStore(db.root,db.cipher);media=MediaStore(db.root,db.cipher)
    missing_file(db);missing_file(db,'e'*32,'g'*16)
    monkeypatch.setattr('app.storage_reconciliation.MAX_PROBES',1)
    reconcile(db.connection,objects,media,1000000);reconcile(db.connection,objects,media,1000001)
    assert len(state(db))==2
    # Restore the physical objects so a normal complete backup can be made.
    for identity in ('d'*32,'e'*32):
        writer=objects.writer(identity);writer.write(b'12345');writer.finish()
    service=backup_service(db)
    with service.temp() as directory:
        service.snapshot(directory)
        with service.snapshot_database(directory) as c:assert not c.execute('SELECT 1 FROM app_settings WHERE key=?',(MISSING_KEY,)).fetchone()
    assert len(state(db))==2
    before=summary(inventory(db.connection,objects,media))
    assert before['file']['missing']==before['file']['unreferenced']==0


def test_audit_runs_with_read_only_database_and_does_not_disclose_content(databases):
    from scripts.storage_audit import audit
    db=databases();seed(db);objects=FileStore(db.root,db.cipher);media=MediaStore(db.root,db.cipher)
    with db.connection() as c:
        c.quota_disabled=True;c.raw.execute('PRAGMA query_only=ON')
        result=audit(c,objects,media,db.root,1000000)
    assert result['storage']['media']['records']==result['storage']['media']['objects']==1
    assert result['dangling_foreign_keys']=={} and result['confirmed_missing_pending']==0
    assert 'PRIVATE_' not in json.dumps(result)
