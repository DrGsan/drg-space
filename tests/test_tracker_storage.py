"""Tracker content remains encrypted and survives a cross-key full restore."""
import json
import base64
import io
from PIL import Image
import pytest
from tests.test_security import reset
from tests.test_storage_encryption import databases, seed, backup_service
from app import tracker
from app.data_crypto import DataCipher
from app.personal_data import PersonalData


def avatar():
    output=io.BytesIO();Image.new('RGB',(256,256),'purple').save(output,format='PNG')
    return base64.b64encode(output.getvalue()).decode()


@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_encrypted_tracker_backup_keeps_completed_tree_history_and_comments(databases, engine):
    source = databases(engine); seed(source); uid = 'a'*32
    with source.connection() as c:
        project = tracker.create_project(c, tracker.Project(name='PRIVATE_TRACKER_PROJECT', key='WORK',avatar=avatar()), uid, 100)
        group=tracker.create_group(c,project['id'],tracker.Group(name='PRIVATE_GROUP',description='PRIVATE_GROUP_BODY'),uid,100)
        parent = tracker.create_issue(c, project['id'], tracker.Issue(title='PRIVATE_TRACKER_PARENT', kind='epic',group_id=group['id']), uid, 101)
        child = tracker.create_issue(c, project['id'], tracker.Issue(title='PRIVATE_TRACKER_CHILD', description='PRIVATE_TRACKER_BODY', parent_id=parent['id'], status='done',merge_request_url='https://git.example/project/pulls/17'), uid, 102)
        tracker.add_comment(c, child['id'], tracker.Comment(body='PRIVATE_TRACKER_COMMENT'), uid, 103)
        raw = c.raw.execute('SELECT payload FROM tracker_issues').fetchone()[0]
        if engine == 'postgres': assert raw.startswith(DataCipher.TEXT) and 'PRIVATE_' not in raw
        tracker.validate_archive(c, lambda: pytest.fail('Invalid source tracker'))
    if engine == 'sqlite': assert b'PRIVATE_TRACKER' not in source.path.read_bytes()
    service = backup_service(source)
    with service.temp() as directory:
        manifest = service.snapshot(directory)
        archive = service.encrypt(directory, manifest, 'Tracker-backup-password-123').read_bytes()
    target = databases(engine); restored = backup_service(target)
    with restored.temp() as directory:
        metadata = restored.prepare(archive, 'Tracker-backup-password-123', directory)
        assert restored.restore(directory, metadata) == 'owner'
    with target.connection() as c:
        row = tracker.issue_row(c, child['id'])
        assert row['group_id']==group['id']
        assert tracker.serialize_group(c,tracker.group_row(c,group['id']),True)['description']=='PRIVATE_GROUP_BODY'
        assert tracker.serialize_project(c,tracker.project_row(c,project['id']),uid,104)['avatar_version']==project['avatar_version']
        assert json.loads(row['payload'])['merge_request_url'].endswith('/17')
        assert row['status'] == 'done' and row['completed_at'] == 102 and row['parent_id'] == parent['id']
        assert json.loads(row['payload'])['description'] == 'PRIVATE_TRACKER_BODY'
        assert c.execute('SELECT body FROM tracker_comments WHERE issue_id=?', (child['id'],)).fetchone()[0] == 'PRIVATE_TRACKER_COMMENT'
        tracker.validate_archive(c, lambda: pytest.fail('Invalid restored tracker'))


@pytest.mark.parametrize('engine', ['sqlite','postgres'])
@pytest.mark.parametrize('backend',['local','s3'])
def test_personal_archive_copies_projects_without_members_or_foreign_identity(databases, engine, backend, monkeypatch):
    if backend=='s3':
        import boto3
        from tests.test_tracker_attachment_scope import Remote
        client=Remote();monkeypatch.setattr(boto3,'client',lambda *a,**kw:client)
        monkeypatch.setenv('S3_BUCKET','private')
    monkeypatch.setenv('FILE_STORAGE',backend)
    source=databases(engine);seed(source);uid='a'*32
    from app.modules.tracker import store_attachment
    from app.file_store import FileStore
    objects=FileStore(source.root,source.cipher); identity='b'*32
    writer=objects.writer(identity);writer.write(b'Portable tracker attachment');writer.finish()
    with source.connection() as c:
        p=tracker.create_project(c,tracker.Project(name='Portable project',key='PORT',avatar=avatar()),uid,100)
        group=tracker.create_group(c,p['id'],tracker.Group(name='Portable group',description='Group notes'),uid,100)
        parent=tracker.create_issue(c,p['id'],tracker.Issue(title='Parent',kind='epic',group_id=group['id']),uid,101)
        child=tracker.create_issue(c,p['id'],tracker.Issue(title='Completed',description='Retained description',parent_id=parent['id'],status='done',merge_request_url='https://git.example/project/merge_requests/17'),uid,102)
        tracker.add_comment(c,child['id'],tracker.Comment(body='Portable comment'),uid,103)
        attachment=store_attachment(c,tracker.issue_row(c,child['id']),uid,'','portable.txt','text/plain',27,identity,104)
    service=PersonalData(source.connection,source.root,lambda:123,'https://old.example')
    with service.temp() as directory:
        content=service.snapshot(uid,directory)
        assert len(content.tracker_projects)==1 and len(content.tracker_projects[0].issues)==2
        portable=content.model_dump_json()
        assert uid not in portable and 'assignee_peer":""' in portable
        target=databases(engine);seed(target)
        other=PersonalData(target.connection,target.root,lambda:124,'https://new.example')
        # The import only needs tracker content; seed's media is exercised by
        # the existing complete personal archive suite.
        content.notes=[];content.revisions=[];content.tasks=[];content.task_lists=[];content.media=[];content.files=[f for f in content.files if f.id in (child['id'],attachment['id'])];content.note_assets=[];content.databases=[];content.records=[];content.comments=[]
        content.settings={}
        archive=service.encrypt(content,'Tracker-personal-password-123',directory).read_bytes()
        with other.temp() as prepared:
            imported=other.prepare(archive,'Tracker-personal-password-123',prepared)
            result=other.restore(uid,imported,prepared,False)
        assert result['tracker_projects']==1 and result['tracker_issues']==2 and result['tracker_groups']==1
    with target.connection() as c:
        migrated=c.execute('SELECT * FROM tracker_issues WHERE status=?',('done',)).fetchone()
        assert migrated['group_id']!=group['id'] and tracker.group_row(c,migrated['group_id'])['project_id']==migrated['project_id']
        assert json.loads(migrated['payload'])['merge_request_url'].endswith('/17')
        assert tracker.serialize_project(c,tracker.project_row(c,migrated['project_id']),uid,124)['avatar_version']==p['avatar_version']
        assert migrated['id']!=child['id'] and migrated['completed_at']==102
        assert tracker.issue_row(c,migrated['parent_id'])['kind']=='epic'
        assert c.execute('SELECT body FROM tracker_comments WHERE issue_id=?',(migrated['id'],)).fetchone()[0]=='Portable comment'
        assert c.execute('SELECT COUNT(*) FROM tracker_members').fetchone()[0]==0
        file=c.execute('SELECT f.* FROM files f JOIN tracker_attachments a ON a.id=f.id WHERE a.issue_id=?',(migrated['id'],)).fetchone()
        assert file['parent_id']==migrated['id'] and file['name']=='portable.txt'
        assert b''.join(FileStore(target.root,target.cipher).iter_read(file['object_id']))==b'Portable tracker attachment'
        assert FileStore(target.root,target.cipher).scoped('tracker-attachments').exists_here(file['object_id'])
        assert not FileStore(target.root,target.cipher).exists_here(file['object_id'])
