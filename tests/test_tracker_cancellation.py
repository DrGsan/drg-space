"""Cancellation preserves work and is never counted as successful completion."""
import json
import pytest
from tests.test_security import owner, member, reset
from tests.test_task_lists import friend
from tests.test_tracker import ok, enable, project, issue
from tests.test_storage_encryption import databases, seed, backup_service
from app import tracker

def path(item):
    return '/api/tracker/issues/'+item['id']+'?include_cancelled=true'

def test_cancel_reopen_history_progress_and_legacy_projection():
    c,_,_=owner();enable(c);p=project(c)
    epic=issue(c,p,kind='epic');task=issue(c,p,parent_id=epic['id'],description='Keep description')
    child=issue(c,p,parent_id=task['id'],status='work')
    done=issue(c,p,parent_id=epic['id'],status='done')
    ok(c.post('/api/tracker/issues/'+task['id']+'/comments',json={'body':'Keep comment'}))
    changed=ok(c.patch(path(task),json={'version':1,'status':'cancelled','cancellation_reason':'No longer needed'}))
    assert changed['status']=='cancelled' and changed['completed_at'] is None
    assert changed['description']=='Keep description' and changed['parent_id']==epic['id']
    assert changed['comments'][0]['body']=='Keep comment'
    assert ok(c.get(path(child)))['status']=='work'
    progress=ok(c.get(path(epic)))['progress']
    assert progress=={'total':1,'done':1,'cancelled':1}
    modern=ok(c.get('/api/tracker/projects/'+p['id']+'/issues?include_cancelled=true'))
    assert len(modern['items'])==4 and modern['view_counts']['cancelled']==1 and modern['view_counts']['done']==1
    legacy=ok(c.get('/api/tracker/projects/'+p['id']+'/issues'))
    assert len(legacy['items'])==sum(legacy['view_counts'].values())==3
    assert 'cancelled' not in legacy['view_counts']
    assert child['id'] in {i['id'] for i in legacy['items']}
    assert c.get('/api/tracker/issues/'+task['id']).status_code==409
    assert c.patch('/api/tracker/issues/'+task['id'],json={'version':2,'status':'done'}).status_code==409
    assert c.patch(path(task),json={'version':1,'status':'work'}).status_code==409
    reopened=ok(c.patch(path(task),json={'version':2,'status':'work'}))
    assert reopened['status']=='work' and not reopened.get('cancellation_reason')
    assert any(h['payload'].get('cancellation_reason',{}).get('after')=='No longer needed' for h in reopened['history'])
    assert ok(c.get(path(epic)))['progress']=={'total':2,'done':1,'cancelled':0}
    assert ok(c.patch(path(task),json={'version':3,'status':'cancelled'}))['status']=='cancelled'
    assert c.patch(path(task),json={'version':4,'cancellation_reason':'x'*2001}).status_code==422

def test_cancel_and_reopen_require_current_editor_permission():
    c,_,_=owner();g,_=member(c);friend(c,g);enable(c);enable(g);p=project(c);task=issue(c,p)
    uid=ok(g.get('/api/me'))['id']
    ok(c.post('/api/tracker/projects/'+p['id']+'/members',json={'actor_id':uid,'role':'viewer'}))
    ok(g.post('/api/tracker/projects/'+p['id']+'/accept'))
    assert g.patch(path(task),json={'version':1,'status':'cancelled'}).status_code==403
    ok(c.post('/api/tracker/projects/'+p['id']+'/members',json={'actor_id':uid,'role':'editor'}))
    changed=ok(g.patch(path(task),json={'version':1,'status':'cancelled'}))
    ok(c.post('/api/tracker/projects/'+p['id']+'/members',json={'actor_id':uid,'role':'viewer'}))
    assert g.patch(path(task),json={'version':changed['version'],'status':'work'}).status_code==403

@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_cancelled_issue_and_reason_survive_encrypted_full_backup(databases,engine):
    source=databases(engine);seed(source);uid='a'*32
    with source.connection() as c:
        p=tracker.create_project(c,tracker.Project(name='Keep project',key='KEEP'),uid,100)
        item=tracker.create_issue(c,p['id'],tracker.Issue(title='Keep cancelled',status='cancelled',cancellation_reason='PRIVATE_REASON'),uid,101)
    service=backup_service(source)
    with service.temp() as directory:
        manifest=service.snapshot(directory)
        archive=service.encrypt(directory,manifest,'Test-backup-password-123').read_bytes()
    target=databases(engine);restored=backup_service(target)
    with restored.temp() as directory:
        metadata=restored.prepare(archive,'Test-backup-password-123',directory);restored.restore(directory,metadata)
    with target.connection() as c:
        row=tracker.issue_row(c,item['id'])
        assert row['status']=='cancelled' and row['completed_at'] is None
        assert json.loads(row['payload'])['cancellation_reason']=='PRIVATE_REASON'

def test_legacy_four_status_full_backup_restores_into_expanded_schema(databases,monkeypatch):
    import app.database as module
    source=databases();seed(source);service=backup_service(source)
    with service.temp() as directory:
        with monkeypatch.context() as old:
            old.setattr(module,'SCHEMA',module.SCHEMA.replace("'done','cancelled'","'done'"))
            metadata=service.snapshot(directory)
        metadata['schema']=module.TRACKER_LEGACY_SCHEMA_HASH
        archive=service.encrypt(directory,metadata,'Test-backup-password-123').read_bytes()
    target=databases();restored=backup_service(target)
    with restored.temp() as directory:
        prepared=restored.prepare(archive,'Test-backup-password-123',directory)
        assert restored.restore(directory,prepared)=='owner'
