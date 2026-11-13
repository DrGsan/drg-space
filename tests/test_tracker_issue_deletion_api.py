"""Public boundaries cannot read, mutate or export a deleted issue branch."""
import json
import pytest

from tests.test_security import owner,member,reset
from tests.test_task_lists import friend
from tests.test_tracker import enable,project,issue,ok
from app import main as m,tracker
from app import tracker_issue_lifecycle as lifecycle
from app.tracker_portability import snapshot


def deletion(c, item, **override):
    detail=ok(c.get('/api/tracker/issues/'+item['id']+'?include_cancelled=true'))
    return c.request('DELETE','/api/tracker/issues/'+item['id'],json={'version':detail['version'],'project_version':detail['delete_scope']['project_version'],'confirmation':detail['key'],**override})


def test_confirmed_branch_delete_hides_every_boundary_and_restores_complete_state():
    c,_,_=owner();enable(c);p=project(c);parent=issue(c,p,kind='epic');child=issue(c,p,parent_id=parent['id'],status='done')
    file=ok(c.post('/api/tracker/issues/'+child['id']+'/attachments?name=evidence.txt',content=b'PRIVATE_EVIDENCE'))
    ok(c.post('/api/tracker/issues/'+child['id']+'/comments',json={'body':'Kept comment'}))
    before=ok(c.get('/api/tracker/issues/'+parent['id']))
    assert before['delete_scope']['count']==2
    assert c.request('DELETE','/api/tracker/issues/'+parent['id'],json={'version':True,'project_version':1,'confirmation':before['key']}).status_code==422
    assert deletion(c,parent,confirmation='wrong').status_code==422
    assert deletion(c,parent,project_version=before['delete_scope']['project_version']-1).status_code==409
    value=ok(deletion(c,parent))['deletion'];assert value['delete_at']-value['created']==7*86400
    for item in (parent,child):
        assert c.get('/api/tracker/issues/'+item['id']+'?include_cancelled=true').status_code==404
        assert c.patch('/api/tracker/issues/'+item['id'],json={'version':1,'title':'No resurrection'}).status_code==404
        assert c.post('/api/tracker/issues/'+item['id']+'/comments',json={'body':'Blocked'}).status_code==404
    assert c.get('/api/tracker/issues/'+child['id']+'/attachments/'+file['id']).status_code==404
    assert c.get('/api/files/'+file['id']+'/content').status_code==404
    assert c.post('/api/tracker/projects/'+p['id']+'/issues',json={'title':'Cannot join deleted parent','parent_id':parent['id']}).status_code==404
    assert ok(c.get('/api/tracker/projects/'+p['id']+'/issues'))['items']==[]
    recovery=ok(c.get('/api/tracker/projects/'+p['id']+'/deleted-issues'));row=recovery['items'][0]
    assert len(recovery['items'])==1 and row['count']==2
    restored=ok(c.post('/api/tracker/issues/'+parent['id']+'/restore',json={'version':row['version'],'project_version':recovery['project_version']}))
    assert restored['progress']=={'total':1,'done':1,'cancelled':0}
    detail=ok(c.get('/api/tracker/issues/'+child['id']));assert detail['comments'][0]['body']=='Kept comment'
    assert c.get('/api/tracker/issues/'+child['id']+'/attachments/'+file['id']).content==b'PRIVATE_EVIDENCE'


def test_project_version_prevents_deleting_an_unseen_new_descendant():
    c,_,_=owner();enable(c);p=project(c);parent=issue(c,p)
    before=ok(c.get('/api/tracker/issues/'+parent['id']))
    child=issue(c,p,parent_id=parent['id'])
    response=c.request('DELETE','/api/tracker/issues/'+parent['id'],json={'version':before['version'],'project_version':before['delete_scope']['project_version'],'confirmation':before['key']})
    assert response.status_code==409
    assert c.get('/api/tracker/issues/'+child['id']).status_code==200
    assert ok(deletion(c,parent))['deletion']


def test_current_editor_role_controls_deletion_recovery_and_retained_exports():
    c,_,_=owner();g,_=member(c);friend(c,g);enable(c);enable(g);p=project(c);item=issue(c,p)
    guest=ok(g.get('/api/me'))['id'];uid=ok(c.get('/api/me'))['id']
    ok(c.post('/api/tracker/projects/'+p['id']+'/members',json={'actor_id':guest,'role':'viewer'}));ok(g.post('/api/tracker/projects/'+p['id']+'/accept'))
    assert 'delete_scope' not in ok(g.get('/api/tracker/issues/'+item['id']))
    detail=ok(c.get('/api/tracker/issues/'+item['id']))
    payload={'version':detail['version'],'project_version':detail['delete_scope']['project_version'],'confirmation':detail['key']}
    assert g.request('DELETE','/api/tracker/issues/'+item['id'],json=payload).status_code==403
    assert g.get('/api/tracker/projects/'+p['id']+'/deleted-issues').status_code==403
    ok(deletion(c,item))
    recovery=ok(c.get('/api/tracker/projects/'+p['id']+'/deleted-issues'));row=recovery['items'][0]
    assert g.post('/api/tracker/issues/'+item['id']+'/restore',json={'version':row['version'],'project_version':recovery['project_version']}).status_code==403
    with m.db() as db:
        assert snapshot(db,uid,m.now())[0]['issues']
        assert snapshot(db,guest,m.now())[0]['issues']==[]
    ok(c.delete('/api/tracker/projects/'+p['id']+'/members/'+guest))
    assert g.get('/api/tracker/projects/'+p['id']+'/deleted-issues').status_code==404


def test_expiry_uses_actual_object_queue_without_leftover_files_or_metadata():
    c,_,_=owner();enable(c);p=project(c);item=issue(c,p)
    file=ok(c.post('/api/tracker/issues/'+item['id']+'/attachments?name=proof.txt',content=b'AUDIT_BYTES'))
    with m.db() as db:identity=db.execute('SELECT object_id FROM files WHERE id=?',(file['id'],)).fetchone()[0]
    deadline=ok(deletion(c,item))['deletion']['delete_at']
    with m.db() as db:
        db.execute('BEGIN IMMEDIATE');lifecycle.purge(db,deadline)
        assert not db.execute('SELECT 1 FROM files WHERE id IN (?,?)',(item['id'],file['id'])).fetchone()
        assert not db.execute('SELECT 1 FROM tracker_history WHERE issue_id=?',(item['id'],)).fetchone()
        assert not lifecycle.records(db,p['id'])
    m.files_router.cleanup()
    assert not m.files_router.store.exists(identity)
    with m.db() as db:assert not db.execute('SELECT 1 FROM blob_gc WHERE id=?',(identity,)).fetchone()


def test_workspace_recovery_and_final_detach_preserve_the_remaining_issue_clock(monkeypatch):
    from tests.test_people_groups import workspace
    from app.workspace_lifecycle import destroy
    c,_,_=owner();enable(c);sid=workspace(c,'organization');p=project(c,space_id=sid);item=issue(c,p)
    initial=ok(deletion(c,item))['deletion']
    ok(c.delete('/api/social/spaces/'+sid))
    with m.db() as db:assert lifecycle.pending(db,item['id'])['paused_at'] is not None
    ok(c.post('/api/social/spaces/'+sid+'/restore'))
    with m.db() as db:
        value=lifecycle.pending(db,item['id']);assert value['paused_at'] is None
        assert value['delete_at']-value['created']==7*86400+value['paused_seconds']
    ok(c.delete('/api/social/spaces/'+sid))
    with m.db() as db:
        before=lifecycle.pending(db,item['id']);future=before['paused_at']+31*86400
        lifecycle.purge(db,future)
        assert tracker.issue_row(db,item['id'],include_deleted=True)
        destroy(db,sid,future)
        after=lifecycle.pending(db,item['id'])
        assert after['paused_at'] is None and after['delete_at']-future==before['delete_at']-before['paused_at']
        assert tracker.project_row(db,p['id'])['space_id'] is None
        tracker.validate_archive(db,lambda:pytest.fail('Workspace deletion corrupted issue clocks'))
