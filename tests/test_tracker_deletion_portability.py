"""Recovery deadlines survive encrypted backups without leaking access grants."""
import json
import pytest

from tests.test_security import reset
from tests.test_storage_encryption import databases, seed, backup_service
from app import tracker, tracker_lifecycle
from app.personal_data import PersonalData
from app.file_store import FileStore
from app.modules.tracker import store_attachment

ATTACHMENT = b'Recovered project attachment'


def pending_project(source):
    uid='a'*32
    identity='d'*32
    writer=FileStore(source.root,source.cipher).writer(identity)
    writer.write(ATTACHMENT);writer.finish()
    with source.connection() as c:
        project=tracker.create_project(c,tracker.Project(name='Recoverable private project',key='KEEP'),uid,100)
        group=tracker.create_group(c,project['id'],tracker.Group(name='Keep group'),uid,101)
        parent=tracker.create_issue(c,project['id'],tracker.Issue(title='Parent',kind='epic',group_id=group['id']),uid,102)
        child=tracker.create_issue(c,project['id'],tracker.Issue(title='Completed child',parent_id=parent['id'],status='done'),uid,103)
        tracker.add_comment(c,child['id'],tracker.Comment(body='Keep comment'),uid,104)
        store_attachment(c,tracker.issue_row(c,child['id']),uid,'','recovered.txt','text/plain',len(ATTACHMENT),identity,104)
        row=tracker.project_row(c,project['id'])
        deadline=tracker_lifecycle.schedule(c,project['id'],uid,row['version'],project['name'],105)
    return uid,project,parent,child,deadline


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_full_cross_key_backup_retains_deadline_and_all_work(databases,engine):
    source=databases(engine);seed(source)
    uid,project,parent,child,deadline=pending_project(source)
    service=backup_service(source)
    with service.temp() as directory:
        manifest=service.snapshot(directory)
        body=service.encrypt(directory,manifest,'Project-recovery-backup-123').read_bytes()
    target=databases(engine);restored=backup_service(target)
    with restored.temp() as directory:
        metadata=restored.prepare(body,'Project-recovery-backup-123',directory)
        assert restored.restore(directory,metadata)=='owner'
    with target.connection() as c:
        assert tracker_lifecycle.pending(c,project['id'])==deadline
        assert tracker.projects(c,uid,123)==[]
        assert tracker_lifecycle.deleted_projects(c,uid)[0]['id']==project['id']
        assert tracker.issue_row(c,child['id'])['parent_id']==parent['id']
        assert tracker.issue_row(c,child['id'])['status']=='done'
        assert c.execute('SELECT body FROM tracker_comments WHERE issue_id=?',(child['id'],)).fetchone()[0]=='Keep comment'
        version=tracker.project_row(c,project['id'])['version']
        restored_project=tracker_lifecycle.restore(c,project['id'],uid,version,123)
        assert restored_project['id']==project['id'] and restored_project['counts']['done']==1
        stored=c.execute('SELECT f.object_id FROM files f JOIN tracker_attachments a ON a.id=f.id WHERE a.issue_id=?',(child['id'],)).fetchone()
        assert b''.join(FileStore(target.root,target.cipher).iter_read(stored['object_id']))==ATTACHMENT


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_personal_archive_keeps_owned_pending_content_without_timer(databases,engine):
    source=databases(engine);seed(source)
    uid,project,parent,child,deadline=pending_project(source)
    service=PersonalData(source.connection,source.root,lambda:123,'https://old.example')
    with service.temp() as directory:
        content=service.snapshot(uid,directory)
        assert len(content.tracker_projects)==1
        portable=content.tracker_projects[0]
        assert portable.name=='Recoverable private project' and len(portable.groups)==1
        assert {item.status for item in portable.issues}=={'todo','done'}
        assert portable.issues[1].comments[0].body=='Keep comment'
        assert len(portable.issues[1].attachments)==1
        serialized=portable.model_dump_json()
        assert uid not in serialized and 'delete_at' not in serialized
        content.notes=[];content.revisions=[];content.tasks=[];content.task_lists=[];content.media=[];content.note_assets=[];content.databases=[];content.records=[];content.comments=[];content.settings={}
        body=service.encrypt(content,'Pending-project-personal-123',directory).read_bytes()
    target=databases(engine);seed(target)
    imported=PersonalData(target.connection,target.root,lambda:124,'https://new.example')
    with imported.temp() as directory:
        prepared=imported.prepare(body,'Pending-project-personal-123',directory)
        result=imported.restore(uid,prepared,directory,False)
        assert result['tracker_projects']==1 and result['tracker_issues']==2
    with target.connection() as c:
        projects=tracker.projects(c,uid,124)
        assert len(projects)==1 and projects[0]['counts']['done']==1
        assert tracker_lifecycle.pending(c,projects[0]['id']) is None
        stored=c.execute('SELECT f.object_id FROM files f JOIN tracker_attachments a ON a.id=f.id').fetchone()
        assert b''.join(FileStore(target.root,target.cipher).iter_read(stored['object_id']))==ATTACHMENT
    with source.connection() as c:
        assert tracker_lifecycle.pending(c,project['id'])==deadline
        assert tracker_lifecycle.deleted_projects(c,uid)[0]['id']==project['id']


def test_backup_validation_refuses_forged_deadline_and_orphan_marker(databases):
    source=databases();seed(source)
    _,project,_,_,deadline=pending_project(source)
    def invalid():raise ValueError('Invalid recovery metadata')
    with source.connection() as c:
        c.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(deadline | {'delete_at':deadline['delete_at']+1}),tracker_lifecycle.PREFIX+project['id']))
        with pytest.raises(ValueError):tracker.validate_archive(c,invalid)
        c.execute('DELETE FROM app_settings WHERE key=?',(tracker_lifecycle.PREFIX+project['id'],))
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',(tracker_lifecycle.PREFIX+'z'*16,json.dumps(deadline)))
        with pytest.raises(ValueError):tracker.validate_archive(c,invalid)


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_restoring_old_backup_does_not_restart_deletion_deadline(databases,engine):
    source=databases(engine);seed(source)
    uid,project,_,_,deadline=pending_project(source)
    service=backup_service(source)
    with service.temp() as directory:
        manifest=service.snapshot(directory)
        body=service.encrypt(directory,manifest,'Project-expired-backup-123').read_bytes()
    target=databases(engine);restored=backup_service(target)
    restored.now=lambda:deadline['delete_at']+1
    with restored.temp() as directory:
        metadata=restored.prepare(body,'Project-expired-backup-123',directory)
        assert restored.restore(directory,metadata)=='owner'
    with target.connection() as c:
        assert not c.execute('SELECT 1 FROM tracker_projects WHERE id=?',(project['id'],)).fetchone()
        assert not c.execute('SELECT 1 FROM tracker_issues WHERE project_id=?',(project['id'],)).fetchone()
        assert tracker_lifecycle.pending(c,project['id']) is None
