"""Project isolation, durable completion, imports and concurrent edits."""
import json
import pytest
from tests.test_security import owner, member, reset
from tests.test_task_lists import friend, listing, add
from app import main as m
from app.retention import cleanup
from app.tracker import validate_archive


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def enable(c):
    ok(c.put('/api/modules', json={'enabled': {'tracker': True}}))


def project(c, **fields):
    return ok(c.post('/api/tracker/projects', json={'name': 'PRIVATE_PROJECT', 'key': 'DRG', **fields}))


def issue(c, p, **fields):
    return ok(c.post('/api/tracker/projects/' + p['id'] + '/issues', json={'title': 'PRIVATE_ISSUE', **fields}))


def test_module_is_opt_in_and_does_not_change_reminders():
    c, _, _ = owner()
    assert not ok(c.get('/api/modules'))['enabled']['tracker']
    assert c.get('/api/tracker').status_code == 403
    enable(c); p = project(c); task = issue(c, p)
    assert task['key'] == 'DRG-1' and task['status'] == 'todo'
    assert ok(c.get('/api/tasks')) == []
    assert ok(c.get('/api/tracker/projects/' + p['id']))['retain_completed'] is True
    assert c.delete('/api/tracker/issues/' + task['id']).status_code in (404, 405)


def test_project_isolation_strict_fields_tree_and_conflicts():
    c, _, _ = owner(); guest, _ = member(c); enable(c); enable(guest)
    p = project(c); task = issue(c, p, description='PRIVATE_DESCRIPTION')
    assert ok(guest.get('/api/tracker'))['projects'] == []
    for path in ['/api/tracker/projects/' + p['id'], '/api/tracker/projects/' + p['id'] + '/issues', '/api/tracker/issues/' + task['id']]:
        assert guest.get(path).status_code == 404
    assert guest.patch('/api/tracker/issues/' + task['id'], json={'version': 1, 'status': 'done'}).status_code == 404
    other = project(c, key='OTHER')
    assert c.post('/api/tracker/projects/' + other['id'] + '/issues', json={'title': 'Wrong parent', 'parent_id': task['id']}).status_code == 422
    assert c.patch('/api/tracker/issues/' + task['id'], json={'version': 1, 'parent_id': task['id']}).status_code == 422
    changed = ok(c.patch('/api/tracker/issues/' + task['id'], json={'version': 1, 'status': 'done'}))
    assert changed['version'] == 2 and changed['completed_at'] is not None
    assert c.patch('/api/tracker/issues/' + task['id'], json={'version': 1, 'title': 'Lost change'}).status_code == 409
    assert ok(c.get('/api/tracker/issues/' + task['id']))['title'] == 'PRIVATE_ISSUE'
    assert c.patch('/api/tracker/issues/' + task['id'], json={'version': 2, 'status': None}).status_code == 422
    assert c.patch('/api/tracker/issues/' + task['id'], json={'version': 2, 'priority': True}).status_code == 422
    assert c.post('/api/tracker/projects/' + p['id'] + '/issues', json={'title': 'Bad', 'url': 'javascript:alert(1)'}).status_code == 422


def test_completed_issues_and_comments_survive_yearly_retention(monkeypatch):
    c, _, _ = owner(); enable(c); p = project(c)
    task = issue(c, p, status='done')
    ok(c.post('/api/tracker/issues/' + task['id'] + '/comments', json={'body': 'Retained comment'}))
    monkeypatch.setattr(m, 'now', lambda: task['completed_at'] + 366 * 86400)
    cleanup(m.db, m.now())
    with m.db() as db:
        row = db.execute('SELECT * FROM tracker_issues WHERE id=?', (task['id'],)).fetchone()
        assert row['status'] == 'done' and row['completed_at'] == task['completed_at']
        assert db.execute('SELECT COUNT(*) FROM tracker_comments WHERE issue_id=?', (task['id'],)).fetchone()[0] == 1


def test_invitation_consent_viewer_permissions_and_revocation():
    c, _, _ = owner(); g, _ = member(c); friend(c, g); enable(c); enable(g)
    uid = ok(g.get('/api/me'))['id']; p = project(c); task = issue(c, p)
    ok(c.post('/api/tracker/projects/' + p['id'] + '/members', json={'actor_id': uid, 'role': 'viewer'}))
    catalog = ok(g.get('/api/tracker'))
    assert catalog['projects'][0]['pending'] and 'counts' not in catalog['projects'][0]
    assert g.get('/api/tracker/issues/' + task['id']).status_code == 404
    ok(g.post('/api/tracker/projects/' + p['id'] + '/accept'))
    assert ok(g.get('/api/tracker/issues/' + task['id']))['id'] == task['id']
    assert g.patch('/api/tracker/issues/' + task['id'], json={'version': 1, 'status': 'done'}).status_code == 403
    assert g.post('/api/tracker/issues/' + task['id'] + '/comments', json={'body': 'No edit'}).status_code == 403
    ok(c.delete('/api/tracker/projects/' + p['id'] + '/members/' + uid))
    assert g.get('/api/tracker/issues/' + task['id']).status_code == 404


def test_import_preserves_complete_tree_dates_payloads_and_is_idempotent():
    c, _, _ = owner(); enable(c)
    source = listing(c, name='DrG Space (Баги)', retain_completed=True)
    root = add(c, 'Root', list_id=source['id'], notes='Group description')
    child = add(c, 'Completed child', list_id=source['id'], parent_id=root['id'], notes='All notes', tags=['tag'], priority=3, flagged=True, url='https://example.com', day='2026-10-10', time='12:30')
    child = ok(c.patch('/api/tasks/' + child['id'], json={'done': True}))
    unrelated = add(c, 'Unrelated reminder')
    payload = {'list_id': source['id'], 'name': 'DrG Space', 'key': 'DRG'}
    p = ok(c.post('/api/tracker/import-reminders', json=payload))
    migrated = ok(c.get('/api/tracker/issues/' + child['id']))
    assert migrated['description'] == 'All notes' and migrated['parent_id'] == root['id']
    assert migrated['status'] == 'done' and migrated['completed_at'] == child['completed_at']
    assert migrated['created'] == child.get('created') and migrated['due'] == '2026-10-10' and migrated['time'] == '12:30'
    assert migrated['priority'] == 3 and migrated['flagged'] and migrated['tags'] == ['tag']
    assert migrated['url'] == 'https://example.com'
    assert ok(c.get('/api/tracker/issues/' + root['id']))['kind'] == 'task'
    assert {item['id'] for item in ok(c.get('/api/tasks'))} == {unrelated['id']}
    assert source['id'] not in {item['id'] for item in ok(c.get('/api/task-lists'))}
    assert ok(c.post('/api/tracker/import-reminders', json=payload))['id'] == p['id']
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM tracker_issues WHERE project_id=?', (p['id'],)).fetchone()[0] == 2
        validate_archive(db, lambda: pytest.fail('Invalid tracker archive'))


def test_import_validation_failure_keeps_original_list():
    c, _, _ = owner(); enable(c); p = project(c)
    source = listing(c); task = add(c, list_id=source['id'])
    result = c.post('/api/tracker/import-reminders', json={'list_id': source['id'], 'name': 'Duplicate', 'key': 'DRG'})
    assert result.status_code == 409
    assert task['id'] in {row['id'] for row in ok(c.get('/api/tasks'))}
    assert len(ok(c.get('/api/tracker'))['projects']) == 1


def test_scoped_attachment_requires_current_access_and_keeps_content():
    c, _, _ = owner(); g, _ = member(c); enable(c); enable(g); p = project(c); task = issue(c, p)
    path = '/api/tracker/issues/' + task['id'] + '/attachments'
    attachment = ok(c.post(path + '?name=proof.txt', content=b'PRIVATE_ATTACHMENT', headers={'Content-Type': 'application/octet-stream'}))
    response = c.get(path + '/' + attachment['id'])
    assert response.status_code == 200 and response.content == b'PRIVATE_ATTACHMENT'
    assert g.get(path + '/' + attachment['id']).status_code == 404
    assert attachment['id'] in {item['id'] for item in ok(c.get('/api/tracker/issues/' + task['id']))['attachments']}


def test_workspace_revoke_and_deletion_preserve_completed_work_privately():
    from tests.test_people_groups import workspace, invite
    from app.workspace_lifecycle import destroy
    c, _, _ = owner(); g, _ = member(c); enable(c); enable(g)
    sid = workspace(c, 'organization'); invite(c, g, sid, 'friend')
    uid = ok(g.get('/api/me'))['id']; p = project(c, space_id=sid)
    task = issue(c, p, status='done')
    assert g.get('/api/tracker/issues/'+task['id']).status_code == 200
    ok(c.delete('/api/tracker/projects/'+p['id']+'/members/'+uid))
    assert g.get('/api/tracker/issues/'+task['id']).status_code == 404
    with m.db() as db: destroy(db, sid, m.now())
    current = ok(c.get('/api/tracker/projects/'+p['id']))
    assert current['space_id'] is None and not current['shared']
    assert ok(c.get('/api/tracker/issues/'+task['id']))['status'] == 'done'
    assert g.get('/api/tracker/issues/'+task['id']).status_code == 404


def test_project_owner_transfer_keeps_files_and_old_owner_access():
    c, _, _ = owner(); g, _ = member(c); friend(c,g); enable(c); enable(g)
    uid = ok(g.get('/api/me'))['id']; p=project(c); task=issue(c,p,status='done')
    attachment=ok(c.post('/api/tracker/issues/'+task['id']+'/attachments?name=keep.txt',content=b'keep'))
    ok(c.post('/api/tracker/projects/'+p['id']+'/members',json={'actor_id':uid}))
    ok(g.post('/api/tracker/projects/'+p['id']+'/accept'))
    changed=ok(c.post('/api/tracker/projects/'+p['id']+'/ownership',json={'actor_id':uid}))
    assert changed['owner_id']==uid and changed['role']=='manager'
    assert ok(g.get('/api/tracker/projects/'+p['id']))['role']=='owner'
    assert g.get('/api/tracker/issues/'+task['id']+'/attachments/'+attachment['id']).content==b'keep'
    with m.db() as db:
        assert db.execute('SELECT user_id FROM files WHERE id=?',(attachment['id'],)).fetchone()[0]==uid


@pytest.mark.parametrize('template',['organization','group'])
def test_departing_member_projects_stay_in_workspace_with_files(template):
    from tests.test_people_groups import workspace, invite
    c, _, _=owner();g,_=member(c);enable(c);enable(g)
    sid=workspace(c,template);invite(c,g,sid,'friend')
    uid=ok(g.get('/api/me'))['id'];new_owner=ok(c.get('/api/me'))['id']
    p=project(c,space_id=sid)
    ok(c.post('/api/tracker/projects/'+p['id']+'/ownership',json={'actor_id':uid}))
    task=issue(g,p,status='done',assignee_id=uid)
    file=ok(g.post('/api/tracker/issues/'+task['id']+'/attachments?name=common.txt',content=b'common work'))
    with m.db() as db:
        db.execute("INSERT INTO tracker_members(project_id,actor_id,peer_id,role,state,created) VALUES(?,?,?,'editor','active',?)",(p['id'],'e'*32,'f'*32,m.now()))
        db.execute('UPDATE user_limits SET limit_bytes=used_bytes WHERE user_id=?',(new_owner,))
    ok(c.delete('/api/social/spaces/'+sid+'/members/'+uid))
    assert g.get('/api/tracker/issues/'+task['id']).status_code==404
    result=ok(c.get('/api/tracker/projects/'+p['id']))
    assert result['owner_id']==new_owner and result['space_id']==sid
    assert c.get('/api/tracker/issues/'+task['id']+'/attachments/'+file['id']).content==b'common work'
    with m.db() as db:
        assert db.execute('SELECT user_id FROM files WHERE id=?',(file['id'],)).fetchone()[0]==new_owner
        assert not db.execute("SELECT 1 FROM tracker_members WHERE project_id=? AND peer_id!='' AND state!='revoked'",(p['id'],)).fetchone()
        db.execute('UPDATE user_limits SET limit_bytes=NULL WHERE user_id=?',(new_owner,))
    changed=ok(c.patch('/api/tracker/issues/'+task['id'],json={'version':1,'status':'review'}))
    assert changed['assignee_id']==uid
    assert c.patch('/api/tracker/issues/'+task['id'],json={'version':changed['version'],'assignee_id':uid}).status_code==422


def test_groups_are_containers_and_moves_keep_tree_history_and_versions():
    c, _, _ = owner(); guest, _ = member(c); enable(c); enable(guest); p=project(c)
    base='/api/tracker/projects/'+p['id']
    group=ok(c.post(base+'/groups',json={'name':'iOS','description':'Retained group notes'}))
    assert not {'status','priority','assignee_id','kind'} & group.keys()
    assert guest.get('/api/tracker/groups/'+group['id']).status_code==404
    assert c.post(base+'/groups',json={'name':'Wrong','status':'done'}).status_code==422
    parent=issue(c,p,title='Ordinary parent',group_id=group['id'])
    child=issue(c,p,parent_id=parent['id'],status='done',merge_request_url='https://git.example/team/repo/pulls/12')
    assert child['group_id']==group['id'] and parent['kind']=='task'
    assert c.patch('/api/tracker/issues/'+child['id'],json={'version':1,'group_id':None}).status_code==422
    changed=ok(c.patch('/api/tracker/issues/'+parent['id'],json={'version':1,'group_id':None}))
    moved=ok(c.get('/api/tracker/issues/'+child['id']))
    assert changed['group_id'] is None and moved['group_id'] is None and moved['version']==2
    assert moved['status']=='done' and moved['parent_id']==parent['id'] and moved['merge_request_url'].endswith('/12')
    assert any('group_id' in h['payload'] for h in moved['history'])
    assert c.patch('/api/tracker/issues/'+child['id'],json={'version':1,'title':'Stale'}).status_code==409
    assert ok(c.get(base+'/issues?group_id='+group['id']))['items']==[]
    assert len(ok(c.get(base+'/issues?group_id='))['items'])==2
    updated=ok(c.patch('/api/tracker/groups/'+group['id'],json={'version':1,'name':'Native','description':'More notes'}))
    assert updated['description']=='More notes' and 'description' not in str(updated['history'])
    assert c.patch('/api/tracker/groups/'+group['id'],json={'version':1,'name':'Stale'}).status_code==409
    other=project(c,key='OTHER'); foreign=ok(c.post('/api/tracker/projects/'+other['id']+'/groups',json={'name':'Foreign'}))
    assert c.patch('/api/tracker/issues/'+parent['id'],json={'version':2,'group_id':foreign['id']}).status_code==404
    with m.db() as db:validate_archive(db,lambda:pytest.fail('Invalid groups'))


def test_project_avatar_and_editable_key_preserve_issue_identity_and_acl():
    import base64,io
    from PIL import Image
    c, _, _=owner();g,_=member(c);enable(c);enable(g)
    photo=io.BytesIO();Image.new('RGB',(100,180),'purple').save(photo,format='PNG')
    p=project(c,avatar=base64.b64encode(photo.getvalue()).decode());task=issue(c,p)
    assert p['has_avatar'] and 'avatar' not in p
    response=c.get(p['avatar_url']);assert response.status_code==200 and response.headers['content-type']=='image/webp'
    assert Image.open(io.BytesIO(response.content)).size==(256,256)
    assert g.get(p['avatar_url']).status_code==404
    # An omitted avatar keeps the exact canonical bytes on edit and backups.
    current=ok(c.get('/api/tracker/projects/'+p['id']))
    edited=ok(c.patch('/api/tracker/projects/'+p['id'],json={'version':current['version'],'name':p['name'],'key':'APP'}))
    again=ok(c.get('/api/tracker/issues/'+task['id']))
    assert again['key']=='APP-1' and again['id']==task['id'] and again['version']==task['version']
    assert edited['avatar_version']==p['avatar_version'] and c.get(edited['avatar_url']).content==response.content
    project(c,key='OTHER')
    assert c.patch('/api/tracker/projects/'+p['id'],json={'version':edited['version'],'name':p['name'],'key':'OTHER'}).status_code==409
    assert c.patch('/api/tracker/issues/'+task['id'],json={'version':1,'merge_request_url':'javascript:alert(1)'}).status_code==422
    assert c.post('/api/tracker/projects',json={'name':'Bad','key':'BAD','avatar':base64.b64encode(b'not an image').decode()}).status_code==422
    removed=ok(c.patch('/api/tracker/projects/'+p['id'],json={'version':edited['version'],'name':p['name'],'avatar':''}))
    assert not removed['has_avatar'] and c.get(edited['avatar_url']).status_code==404
