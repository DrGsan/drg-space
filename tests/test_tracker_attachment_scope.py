"""Physical isolation, legacy relocation and Files access boundaries."""
import pytest
from app.data_crypto import DataCipher,StorageError
from app.file_store import FileStore
from tests.test_storage_reconciliation import ConfirmedS3
from tests.test_security import owner,reset
from tests.test_tracker import enable,project,issue,ok
from tests.test_tracker_deletion import attachment
from app import main as m
from tests.test_storage_encryption import databases,seed,backup_service


class Remote(ConfirmedS3):
    def get_object(self,**kw):
        self.head_object(**kw)
        return super().get_object(**kw)


@pytest.mark.parametrize('backend',['local','s3'])
def test_isolated_namespace_relocation_rollback_and_shared_reference(tmp_path,backend):
    client=Remote();store=FileStore(tmp_path,DataCipher.ephemeral(),backend=backend,client=client,bucket='private')
    tasks=store.scoped('tracker-attachments');identity='a'*32;body=b'Private task evidence'*100000
    writer=store.writer(identity);writer.write(body);expected=writer.finish()
    assert not tasks.exists_here(identity) and tasks.read(identity,maximum=len(body))==body
    assert tasks.relocate_from('files',identity,keep_source=True)==expected
    assert store.exists_here(identity) and tasks.exists_here(identity)
    assert store.ids()==tasks.ids()=={identity}
    assert tasks.relocate_from('files',identity)==expected
    assert not store.exists_here(identity) and tasks.exists_here(identity)
    assert tasks.relocate_from('files',identity)==expected  # retry after source removal
    assert store.read(identity,maximum=len(body))==body
    # Roll back on an isolated copy, preserving the same database identity.
    assert store.relocate_from('tracker-attachments',identity)==expected
    assert store.exists_here(identity) and not tasks.exists_here(identity)
    tasks.relocate_from('files',identity)
    store.delete(identity);assert not store.ids()


def test_s3_outage_does_not_fall_back_to_other_namespace_or_local_disk(tmp_path):
    client=Remote();store=FileStore(tmp_path,DataCipher.ephemeral(),backend='s3',client=client,bucket='private')
    identity='b'*32;tasks=store.scoped('tracker-attachments')
    writer=tasks.writer(identity);writer.write(b'Object exists in task prefix');writer.finish()
    client.failure=('AccessDenied',403)
    with pytest.raises(StorageError):store.read(identity)
    with pytest.raises(StorageError):store.exists(identity)
    assert not list((tmp_path/'files').iterdir())


def test_failed_relocation_preserves_legacy_bytes(tmp_path):
    client=Remote();store=FileStore(tmp_path,DataCipher.ephemeral(),backend='s3',client=client,bucket='private')
    identity='c'*32;writer=store.writer(identity);writer.write(b'Keep original');writer.finish()
    tasks=store.scoped('tracker-attachments');original=client.get_object
    def corrupt_target(**kw):
        result=original(**kw)
        if '/tracker-attachments/' in kw['Key']:
            import io
            result['Body']=io.BytesIO(b'corrupt')
        return result
    client.get_object=corrupt_target
    with pytest.raises(StorageError):tasks.relocate_from('files',identity)
    assert store.read(identity)==b'Keep original'
    assert store.exists_here(identity) and not tasks.exists_here(identity)


@pytest.mark.parametrize('backend',['local','s3'])
@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_full_restore_isolates_legacy_task_objects_without_archive_format_change(databases,monkeypatch,backend,engine):
    from app import tracker
    from app.modules.tracker import store_attachment
    if backend=='s3':
        import boto3
        client=Remote();monkeypatch.setattr(boto3,'client',lambda *a,**kw:client)
        monkeypatch.setenv('S3_BUCKET','private')
    monkeypatch.setenv('FILE_STORAGE',backend)
    source=databases(engine);seed(source);uid='a'*32
    legacy=FileStore(source.root,source.cipher);identity='e'*32
    writer=legacy.writer(identity);writer.write(b'Legacy encrypted evidence');writer.finish()
    with source.connection() as c:
        p=tracker.create_project(c,tracker.Project(name='Archive',key='ARCH'),uid,100)
        task=tracker.create_issue(c,p['id'],tracker.Issue(title='Evidence'),uid,101)
        held=store_attachment(c,tracker.issue_row(c,task['id']),uid,'','evidence.txt','text/plain',25,identity,102)
    service=backup_service(source)
    with service.temp() as directory:
        metadata=service.snapshot(directory);archive=service.encrypt(directory,metadata,'Task-archive-password-123').read_bytes()
    target=databases(engine);restorer=backup_service(target)
    with restorer.temp() as directory:
        prepared=restorer.prepare(archive,'Task-archive-password-123',directory);restorer.restore(directory,prepared)
    with target.connection() as c:
        row=c.execute('SELECT * FROM files WHERE id=?',(held['id'],)).fetchone()
        assert row['name']=='evidence.txt'
        objects=FileStore(target.root,target.cipher)
        assert objects.scoped('tracker-attachments').exists_here(row['object_id'])
        assert not objects.exists_here(row['object_id'])
        assert objects.read(row['object_id'])==b'Legacy encrypted evidence'
        tracker.validate_archive(c,lambda:pytest.fail('Invalid restored tracker'))


def test_active_tracker_files_are_hidden_and_cannot_be_mutated_through_files():
    c,_,_=owner();enable(c);p=project(c);task=issue(c,p);held=attachment(c,task)
    folder=ok(c.post('/api/files/folders',json={'name':'Ordinary folder'}))
    with m.db() as db:
        row=db.execute('SELECT object_id,version FROM files WHERE id=?',(held['id'],)).fetchone()
        identity,version=row['object_id'],row['version']
        # Simulate a previous version placing a linked file in an ordinary tree.
        db.execute('UPDATE files SET parent_id=? WHERE id=?',(folder['id'],held['id']))
    from app.file_store import file_store
    objects=file_store(m.db,m.DATA)
    assert objects.scoped('tracker-attachments').exists_here(identity)
    assert not objects.exists_here(identity)
    for path in ('/content','/text','/shares'):
        assert c.get('/api/files/'+held['id']+path).status_code==404
    assert c.patch('/api/files/'+held['id'],json={'name':'Changed','version':version}).status_code==404
    assert c.post('/api/files/move',json={'items':[{'id':held['id'],'version':version}],'parent_id':None}).status_code==404
    assert c.delete('/api/files/'+held['id']).status_code==404
    # Recursive deletion of an ordinary parent must leave the task attachment alive.
    ok(c.delete('/api/files/'+folder['id']))
    with m.db() as db:
        assert db.execute('SELECT parent_id FROM files WHERE id=?',(held['id'],)).fetchone()[0] is None
    assert c.get('/api/tracker/issues/'+task['id']+'/attachments/'+held['id']).content==b'PRIVATE_RECEIPT'
    ok(c.delete('/api/files/'+folder['id']+'/permanent'))
    with m.db() as db:
        row=db.execute('SELECT parent_id,deleted FROM files WHERE id=?',(held['id'],)).fetchone()
        assert row['parent_id'] is None and row['deleted'] is None
    assert objects.exists(identity)


def test_storage_audit_accepts_and_counts_separate_tracker_prefix(databases,monkeypatch):
    import boto3
    from app import tracker
    from app.data_crypto import MediaStore
    from app.modules.tracker import store_attachment
    from scripts.storage_audit import audit
    client=Remote();monkeypatch.setattr(boto3,'client',lambda *a,**kw:client)
    monkeypatch.setenv('FILE_STORAGE','s3');monkeypatch.setenv('S3_BUCKET','private')
    backend=databases();seed(backend);uid='a'*32
    store=FileStore(backend.root,backend.cipher);identity='f'*32
    writer=store.scoped('tracker-attachments').writer(identity);writer.write(b'Evidence');writer.finish()
    with backend.connection() as c:
        project=tracker.create_project(c,tracker.Project(name='Audit',key='AUDIT'),uid,100)
        task=tracker.create_issue(c,project['id'],tracker.Issue(title='Attachment'),uid,101)
        store_attachment(c,tracker.issue_row(c,task['id']),uid,'','evidence.txt','text/plain',8,identity,102)
        report=audit(c,store,MediaStore(backend.root,backend.cipher,store),backend.root,200)
    assert report['s3']['unexpected_object_keys']==0
    assert report['storage']['file']['records']==report['storage']['file']['objects']==1
    assert report['storage']['file']['unreferenced']==0
