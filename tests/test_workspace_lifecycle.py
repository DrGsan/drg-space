import json
import pytest
from tests.test_security import owner, member, reset, note, client, share
from app import main as m
from app import workspace_lifecycle as lifecycle
from tests.test_admin import proof, backup, headers
from tests.test_people_groups import workspace, invite
from tests.test_social import shared, enabled
from tests.test_workspace_teams import config, update, audience, T1
from tests.test_widgets import image_bytes


def test_shared_documents_and_attachments_survive_account_deletion_without_old_author():
    c, _, _ = owner(); employee, _ = member(c)
    sid = workspace(c, 'organization'); invite(c, employee, sid, 'friend')
    old = employee.get('/api/me').json()['id']; new = c.get('/api/me').json()['id']
    private = note(employee); root = shared(employee, sid); child = shared(employee, sid, root['id'])
    attachment = employee.post('/api/notes/' + root['id'] + '/files?name=keep.txt', content=b'keep this').json()
    image = employee.post('/api/notes/' + child['id'] + '/assets/cover', content=image_bytes())
    assert image.status_code == 200, image.text
    with m.db() as db:db.execute('UPDATE user_limits SET limit_bytes=used_bytes WHERE user_id=?',(new,))
    assert c.post('/api/admin/users/delete', json=proof(user_id=old, confirmation='friend')).status_code == 200
    from tests.deletion_helpers import finish_account
    finish_account(old)
    assert c.get('/api/notes/' + private).status_code == 404
    for page in (root, child):
        response = c.get('/api/notes/' + page['id']); assert response.status_code == 200, response.text
        with m.db() as db: assert db.execute('SELECT user_id FROM notes WHERE id=?', (page['id'],)).fetchone()[0] == new
    assert c.get('/api/notes/' + root['id'] + '/files/' + attachment['id']).content == b'keep this'
    assert c.get(image.json()['cover_url']).status_code == 200
    with m.db() as db:
        for table in ('notes', 'media', 'files'): assert not db.execute('SELECT 1 FROM ' + table + ' WHERE user_id=?', (old,)).fetchone()
        lifecycle.validate_archive(db, lambda: pytest.fail('invalid lifecycle data'))


def test_owner_account_requires_explicit_transfer_before_deletion():
    c, _, _ = owner(); employee, _ = member(c)
    sid = workspace(employee, 'organization'); uid = employee.get('/api/me').json()['id']
    assert c.post('/api/admin/users/delete', json=proof(user_id=uid, confirmation='friend')).status_code == 409
    assert employee.get('/api/me').status_code == 200
    invite(employee, c, sid, 'owner')
    value = config(employee, sid)
    assert employee.post(f'/api/social/spaces/{sid}/ownership', json={'revision': value['revision'], 'user_id': c.get('/api/me').json()['id']}).status_code == 200
    assert c.post('/api/admin/users/delete', json=proof(user_id=uid, confirmation='friend')).status_code == 200
    assert c.get('/api/social').json()['spaces'][0]['id'] == sid


def test_work_page_creator_obeys_viewer_and_revocation_and_family_keeps_original_rules():
    c, _, _ = owner(); employee, _ = member(c); uid = employee.get('/api/me').json()['id']
    sid = workspace(c, 'organization'); invite(c, employee, sid, 'friend'); page = shared(employee, sid)
    audience(c, page['id'], members={uid: 'viewer'})
    assert employee.get('/api/notes/' + page['id']).json()['role'] == 'viewer'
    assert employee.patch('/api/notes/' + page['id'], json={'icon': '⭐'}).status_code == 403
    audience(c, page['id'], members={})
    assert employee.get('/api/notes/' + page['id']).status_code == 404
    family = workspace(c, 'family'); invite(c, employee, family, 'friend'); personal_shared = shared(employee, family)
    assert c.put('/api/notes/' + personal_shared['id'] + '/permissions', json={'user_id': uid, 'role': 'viewer'}).status_code == 422


@pytest.mark.parametrize('template,seconds', [('organization', 30*86400), ('project', 7*86400), ('family', 86400), ('group', 86400)])
def test_scheduled_deletion_blocks_all_entries_and_can_be_cancelled_then_purged(template, seconds, monkeypatch):
    c, _, _ = owner(); employee, _ = member(c); sid = workspace(c, template); invite(c, employee, sid, 'friend')
    page = shared(c, sid); image = c.post('/api/notes/' + page['id'] + '/assets/cover', content=image_bytes()).json()
    file = c.post('/api/notes/' + page['id'] + '/files?name=gone.txt', content=b'gone').json()
    with m.db() as db:object_id=db.execute('SELECT object_id FROM files WHERE id=?',(file['id'],)).fetchone()[0]
    token = share(c, page['id'], 'public'); enabled(c)
    c.post(f'/api/chat/{sid}/messages', json={'body': 'retained until deadline', 'client_id': 'lifecycle-message-123'})
    assert employee.delete('/api/social/spaces/' + sid).status_code == 403
    started = m.now(); result = c.delete('/api/social/spaces/' + sid); assert result.status_code == 200, result.text
    end = result.json()['deletion']['delete_at']; assert end-started == seconds
    assert c.delete('/api/social/spaces/' + sid).json()['deletion']['delete_at'] == end
    assert employee.post('/api/social/spaces/' + sid + '/restore').status_code == 403
    assert c.get('/api/social').json()['spaces'][0]['deletion']['delete_at'] == end
    for account in (c, employee):
        for url in ('/api/notes/' + page['id'], '/api/notes/' + page['id'] + '/files/' + file['id'], image['cover_url']): assert account.get(url).status_code == 404, url
        assert account.post('/api/notes', json={'space_id': sid, 'title': 'blocked'}).status_code == 423
        assert account.get(f'/api/chat/{sid}/messages').status_code == 423
    assert client().get('/api/shared/' + token).status_code == 404
    assert c.post('/api/social/spaces/' + sid + '/restore').status_code == 200
    assert c.get('/api/notes/' + page['id']).status_code == 200
    assert client().get('/api/shared/' + token).status_code == 200
    c.delete('/api/social/spaces/' + sid)
    with m.db() as db:
        lifecycle.purge(db, end-1)
        assert db.execute('SELECT 1 FROM spaces WHERE id=?', (sid,)).fetchone()
    with m.db() as db:
        value=lifecycle.pending(db,sid);value['delete_at']=m.now()-1;value['created']=value['delete_at']-seconds
        from app.workspace_management import write
        write(db,lifecycle.DELETE_PREFIX+sid,value)
    assert c.post('/api/social/spaces/' + sid + '/restore').status_code == 409
    from app.retention import cleanup
    cleanup(m.db, m.now())
    with m.db() as db:
        for table, field, identity in [('spaces','id',sid),('notes','space_id',sid),('messages','space_id',sid),('files','id',file['id'])]: assert not db.execute(f'SELECT 1 FROM {table} WHERE {field}=?', (identity,)).fetchone()
        assert not db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?', ('%'+sid+'%',)).fetchone()
        assert not db.execute('SELECT 1 FROM media WHERE id=?', (image['cover'].removeprefix('image:'),)).fetchone()
    m.files_router.cleanup()
    assert not m.admin_router.backups.objects.exists(object_id)
    assert not m.admin_router.backups.media.exists(image['cover'].removeprefix('image:'))
    assert not c.get('/api/social').json()['spaces']


def test_single_use_invite_register_existing_user_revoke_and_demoted_sender():
    c, _, _ = owner(); employee, _ = member(c); outsider, _ = member(c, 'outsider')
    sid = workspace(c, 'organization'); invite(c, employee, sid, 'friend'); uid = employee.get('/api/me').json()['id']
    assert employee.post(f'/api/social/spaces/{sid}/invite-links').status_code == 403
    value = config(c, sid); value['admins'] = [uid]; update(c, sid, value)
    link = employee.post(f'/api/social/spaces/{sid}/invite-links').json(); token = link['url'].split('/')[-1]
    newcomer = client(); response = newcomer.post('/api/register', json={'login':'new-worker','password':'New-worker-password-123','invite':token})
    assert response.status_code == 200, response.text
    new_uid = newcomer.get('/api/me').json()['id']
    with m.db() as db: assert db.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?', (sid,new_uid)).fetchone()
    assert outsider.post('/api/social/invite-links/'+token+'/accept').status_code == 404
    link = c.post(f'/api/social/spaces/{sid}/invite-links').json(); token = link['url'].split('/')[-1]
    assert outsider.get('/api/social/invite-links/'+token).json()['name']
    assert outsider.post('/api/social/invite-links/'+token+'/accept').status_code == 200
    link = c.post(f'/api/social/spaces/{sid}/invite-links').json(); token = link['url'].split('/')[-1]
    assert c.delete(f'/api/social/spaces/{sid}/invite-links/'+link['id']).status_code == 200
    assert client().post('/api/register',json={'login':'cancelled','password':'New-worker-password-123','invite':token}).status_code == 403
    link = employee.post(f'/api/social/spaces/{sid}/invite-links').json(); token = link['url'].split('/')[-1]
    value = config(c,sid); value['admins'] = []; update(c,sid,value)
    assert outsider.get('/api/social/invite-links/'+token).status_code == 404


def test_offboarding_preview_revokes_teams_grants_invites_and_optionally_public_links():
    c, _, _ = owner(); employee, _ = member(c); sid = workspace(c, 'organization'); invite(c,employee,sid,'friend'); uid = employee.get('/api/me').json()['id']
    page = shared(employee,sid); token = share(c,page['id'],'public')
    value = config(c,sid); value['teams']={T1:{'name':'dev','members':[uid]}}; update(c,sid,value)
    assert employee.get(f'/api/social/spaces/{sid}/members/{uid}/offboarding').status_code == 403
    preview = c.get(f'/api/social/spaces/{sid}/members/{uid}/offboarding').json()
    assert preview['teams']==['dev'] and preview['notes']==1 and preview['publications']==1
    assert c.delete(f'/api/social/spaces/{sid}/members/{uid}?revoke_publications=true').status_code == 200
    assert employee.get('/api/notes/'+page['id']).status_code == 404
    assert c.get('/api/notes/'+page['id']).status_code == 200
    assert client().get('/api/shared/'+token).status_code == 404
    assert employee.get(f'/api/social/spaces/{sid}/audit').status_code == 404
    events=c.get(f'/api/social/spaces/{sid}/audit').json()['events']
    assert any(e['event']=='member_removed' and e['details']['publications_revoked'] for e in events)


def test_pending_deletion_and_audit_survive_full_backup_restore():
    c, _, _ = owner(); sid=workspace(c,'project'); page=shared(c,sid)
    deadline=c.delete('/api/social/spaces/'+sid).json()['deletion']['delete_at']
    saved=backup(c)
    assert c.post('/api/social/spaces/'+sid+'/restore').status_code == 200
    assert c.post('/api/admin/backups/restore',content=saved,headers=headers()).status_code == 200
    with m.db() as db:
        assert lifecycle.pending(db,sid)['delete_at']==deadline
        lifecycle.validate_archive(db,lambda:pytest.fail('invalid archive'))


def test_pending_friendship_hides_tasks_and_alerts_and_cancellation_restores_them():
    from tests.test_task_lists import friend,listing,add,all_tasks,share
    c,_,_=owner();g,_=member(c);sid=friend(c,g);uid=g.get('/api/me').json()['id']
    task_list=listing(c)
    share(c,g,task_list['id'],uid)
    task=add(c,list_id=task_list['id']);enabled(c)
    assert c.post(f'/api/chat/{sid}/messages',json={'body':'private','client_id':'freeze-alert-123'}).status_code==200
    assert task['id'] in all_tasks(g)
    assert any(n['kind']=='message' for n in g.get('/api/notifications').json()['items'])
    result=c.delete('/api/social/spaces/'+sid);assert result.status_code==200
    assert result.json()['deletion']['delete_at']-result.json()['deletion']['created']==86400
    assert task['id'] not in all_tasks(g)
    assert g.patch('/api/task-lists/'+task_list['id'],json={'name':'blocked'}).status_code==404
    assert g.get('/api/task-lists/friends').json()==[]
    assert not any(n['kind']=='message' for n in g.get('/api/notifications').json()['items'])
    assert c.post('/api/social/spaces/'+sid+'/restore').status_code==200
    assert task['id'] in all_tasks(g)
    assert any(n['kind']=='message' for n in g.get('/api/notifications').json()['items'])


@pytest.mark.parametrize('version',[1,2])
def test_old_personal_backup_formats_still_validate(version):
    from tests.test_admin import BACKUP_PASSWORD
    c,_,_=owner();note(c);service=m.admin_router.backups
    with service.temp() as stage:
        metadata=service.snapshot(stage);metadata['version']=version
        saved=service.encrypt(stage,metadata,BACKUP_PASSWORD).read_bytes()
    with service.temp() as stage:
        restored=service.prepare(saved,BACKUP_PASSWORD,stage)
        assert restored['version']==version


@pytest.mark.parametrize('template',['organization','project'])
def test_offboarding_transfers_only_target_work_documents_and_assets(template):
    c,_,_=owner();employee,_=member(c);uid=employee.get('/api/me').json()['id'];owner_id=c.get('/api/me').json()['id']
    sid=workspace(c,template);other=workspace(c,'organization');invite(c,employee,sid,'friend');invite(c,employee,other,'friend')
    page=shared(employee,sid);outside=shared(employee,other);private=note(employee)
    file=employee.post('/api/notes/'+page['id']+'/files?name=handover.txt',content=b'company file').json()
    cover=employee.post('/api/notes/'+page['id']+'/assets/cover',content=image_bytes()).json()
    assert c.delete(f'/api/social/spaces/{sid}/members/{uid}').status_code==200
    assert employee.get('/api/notes/'+page['id']).status_code==404
    assert employee.get('/api/notes/'+outside['id']).status_code==200
    assert employee.get('/api/notes/'+private).status_code==200
    assert c.get(file['url']).content==b'company file' and c.get(cover['cover_url']).status_code==200
    with m.db() as db:
        assert db.execute('SELECT user_id FROM notes WHERE id=?',(page['id'],)).fetchone()[0]==owner_id
        assert db.execute('SELECT user_id FROM files WHERE id=?',(file['id'],)).fetchone()[0]==owner_id
        assert db.execute('SELECT user_id FROM notes WHERE id=?',(outside['id'],)).fetchone()[0]==uid
