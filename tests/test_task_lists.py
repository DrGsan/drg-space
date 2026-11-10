"""Reminder authorization, tree invariants, retention and portable storage."""
import json
from datetime import datetime
import pytest
from tests.test_security import owner, member, reset
from app import main as m
from app.retention import cleanup
from app.modules.tasks import validate_task_archive

def ok(response):
    assert response.status_code==200,response.text
    return response.json()

def add(c, title='Дело', **fields): return ok(c.post('/api/tasks',json={'title':title,**fields}))
def listing(c, **fields): return ok(c.post('/api/task-lists',json={'name':'Список',**fields}))
def all_tasks(c): return {item['id']:item for item in ok(c.get('/api/tasks'))}
def friend(c,g,name='friend'):
    rid=ok(c.post('/api/social/friends',json={'login':name}))['id']
    return ok(g.post('/api/social/requests/'+rid+'/accept'))['space_id']
def share(sender, recipient, lid, uid):
    result=ok(sender.post('/api/task-lists/'+lid+'/members',json={'user_id':uid}))
    invitation=next(item for item in result['invitations'] if item['recipient_id']==uid)
    ok(recipient.post('/api/task-lists/'+lid+'/invitations/'+invitation['id']+'/accept'))
    return next(item for item in ok(sender.get('/api/task-lists')) if item['id']==lid)

def renew(c):
    c.headers['x-csrf-token']=ok(c.post('/api/refresh'))['csrf']

def stamp(value): return int(datetime.fromisoformat(value).timestamp())

def test_default_list_and_legacy_task_backfill_preserve_completion_time(monkeypatch):
    clock=stamp('2026-10-13T10:00:00+00:00');monkeypatch.setattr(m,'now',lambda:clock)
    c,_,_=owner();uid=ok(c.get('/api/me'))['id']
    with m.db() as db:
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('legacy_task_001',uid,'Старое дело','2026-09-01',1,1))
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
        settings['_task_completed_at']={'legacy_task_001':clock}
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    lists=ok(c.get('/api/task-lists'));assert len(lists)==1
    assert lists[0]['id']=='default_'+uid and lists[0]['name']=='Напоминания'
    assert lists[0]['is_default'] and lists[0]['can_manage'] and not lists[0]['retain_completed']
    task=all_tasks(c)['legacy_task_001'];assert task['completed_at']==clock and task['list_id']==lists[0]['id']
    assert task['notes']=='' and task['parent_id'] is None
    clock+=86400;cleanup(m.db,clock)
    with m.db() as db:assert not db.execute('SELECT 1 FROM tasks WHERE id=?',('legacy_task_001',)).fetchone()

def test_undated_fields_partial_updates_and_input_validation():
    c,_,_=owner()
    task=add(c,day=None,notes='Купить для дома',url='https://example.com/a',tags=['#Дом','дом'],flagged=True,priority=3)
    assert task['day']=='' and task['time']=='' and task['tags']==['Дом']
    changed=ok(c.patch('/api/tasks/'+task['id'],json={'day':'2026-10-18','time':'09:30','version':task['version']}))
    assert changed['notes']==task['notes'] and changed['time']=='09:30' and changed['ok']
    assert c.patch('/api/tasks/'+task['id'],json={'title':'Конфликт','version':task['version']}).status_code==409
    assert all_tasks(c)[task['id']]['title']=='Дело'
    for bad in [{'day':'2026-02-30'},{'time':'25:00'},{'priority':True},{'flagged':'true'},
                {'notes':'x'*5001},{'url':'javascript:alert(1)'},{'url':'https://u:p@example.com'},
                {'url':'https://example.com/\n'},{'tags':['x'*41]},{'tags':['']},{'repeat':'daily'}]:
        assert c.post('/api/tasks',json={'title':'Недопустимо',**bad}).status_code==422,bad
    assert c.patch('/api/tasks/'+task['id'],json={'day':None}).status_code==422
    changed=ok(c.patch('/api/tasks/'+task['id'],json={'day':None,'time':''}));assert changed['day']==''

def test_completion_tree_cycles_depth_and_manual_subtree_deletion(monkeypatch):
    clock=stamp('2026-10-13T10:00:00+00:00');monkeypatch.setattr(m,'now',lambda:clock)
    c,_,_=owner();root=add(c,'Родитель');child=add(c,'Поддело',parent_id=root['id'])
    assert c.post('/api/tasks',json={'title':'Внук','parent_id':child['id']}).status_code==422
    grandchild=child
    assert c.patch('/api/tasks/'+root['id'],json={'parent_id':grandchild['id']}).status_code==422
    ok(c.patch('/api/tasks/'+root['id'],json={'done':True,'version':root['version']}))
    tasks=all_tasks(c);assert all(item['done']==1 and item['completed_at']==clock for item in tasks.values())
    clock+=30;ok(c.patch('/api/tasks/'+root['id'],json={'done':True}))
    assert all(item['completed_at']==clock-30 for item in all_tasks(c).values())
    ok(c.patch('/api/tasks/'+grandchild['id'],json={'done':False}))
    assert all(not item['done'] and item['completed_at'] is None for item in all_tasks(c).values())
    ok(c.patch('/api/tasks/'+root['id'],json={'done':True}))
    added=add(c,'Новое поддело',parent_id=root['id']);assert all_tasks(c)[root['id']]['done']==0
    assert c.patch('/api/tasks/'+root['id'],json={'parent_id':child['id']}).status_code==422
    ok(c.delete('/api/tasks/'+child['id']))
    assert set(all_tasks(c))=={root['id'],added['id']}

def test_shared_lists_require_membership_and_active_friendship():
    c,_,_=owner();g,_=member(c);stranger,_=member(c,'stranger')
    uid=ok(g.get('/api/me'))['id'];shared=listing(c)
    assert c.post('/api/task-lists/'+shared['id']+'/members',json={'user_id':uid}).status_code==422
    sid=friend(c,g)
    assert ok(c.get('/api/task-lists/friends'))[0]['id']==uid
    assert shared['id'] not in {item['id'] for item in ok(g.get('/api/task-lists'))}
    shared=share(c,g,shared['id'],uid)
    assert len(shared['members'])==2 and shared['shared']
    task=add(g,'Общее дело',list_id=shared['id'])
    with m.db() as db: assert db.execute('SELECT user_id FROM tasks WHERE id=?',(task['id'],)).fetchone()[0]==shared['owner_id']
    assert task['id'] in all_tasks(c) and task['id'] in all_tasks(g)
    assert stranger.patch('/api/tasks/'+task['id'],json={'done':True}).status_code==404
    assert g.patch('/api/task-lists/'+shared['id'],json={'name':'Нельзя'}).status_code==403
    ok(g.patch('/api/tasks/'+task['id'],json={'notes':'Обновление друга','version':task['version']}))
    deletion=ok(g.delete('/api/social/spaces/'+sid))
    assert task['id'] not in all_tasks(g) and task['id'] in all_tasks(c)
    assert g.patch('/api/tasks/'+task['id'],json={'done':True}).status_code==404
    assert g.post('/api/tasks',json={'title':'Нельзя','list_id':shared['id']}).status_code==404
    with m.db() as db:
        from app.workspace_lifecycle import purge
        purge(db,deletion['deletion']['delete_at'])
    friend(c,g)
    assert task['id'] not in all_tasks(g)
    with m.db() as db:assert not db.execute('SELECT 1 FROM task_list_members WHERE list_id=? AND user_id=?',(shared['id'],uid)).fetchone()

def test_move_subtree_between_lists_validates_scope_and_updates_owner():
    c,_,_=owner();g,_=member(c);friend(c,g)
    uid=ok(g.get('/api/me'))['id'];target=listing(c)
    share(c,g,target['id'],uid)
    root=add(g,'Свой родитель');child=add(g,'Свой ребёнок',parent_id=root['id'])
    another=add(c,'Чужой родитель')
    assert g.patch('/api/tasks/'+root['id'],json={'parent_id':another['id']}).status_code==422
    moved=ok(g.patch('/api/tasks/'+root['id'],json={'list_id':target['id'],'version':root['version']}))
    assert moved['parent_id'] is None and moved['list_id']==target['id']
    tasks=all_tasks(c);assert tasks[child['id']]['parent_id']==root['id'] and tasks[child['id']]['list_id']==target['id']
    with m.db() as db:
        assert {row[0] for row in db.execute('SELECT user_id FROM tasks WHERE id IN (?,?)',(root['id'],child['id']))}=={target['owner_id']}
    private=listing(c)
    assert g.patch('/api/tasks/'+root['id'],json={'list_id':private['id']}).status_code==404

def test_retention_waits_full_day_across_timezones_and_keeps_retention_switch_when_disabled(monkeypatch):
    clock=stamp('2026-10-13T13:59:00+00:00');monkeypatch.setattr(m,'now',lambda:clock)
    c,_,_=owner();g,_=member(c);friend(c,g)
    c.headers['x-time-zone']='Asia/Vladivostok';g.headers['x-time-zone']='America/Los_Angeles'
    shared=listing(c);kept=listing(c,retain_completed=True)
    share(c,g,shared['id'],ok(g.get('/api/me'))['id'])
    gone=add(g,'Общее завершённое',list_id=shared['id'],done=True,day=None)
    saved=add(c,'Сохраняемое завершённое',list_id=kept['id'],done=True)
    pending=add(c,'Невыполненное просроченное',day='2020-01-01')
    ok(c.put('/api/modules',json={'enabled':{'tasks':False}}))
    assert c.get('/api/task-lists').status_code==403
    assert c.get('/api/task-lists/friends').status_code==403
    clock+=61;cleanup(m.db,clock)
    with m.db() as db: assert db.execute('SELECT 1 FROM tasks WHERE id=?',(gone['id'],)).fetchone()
    clock+=86400-61;cleanup(m.db,clock)
    renew(c)
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM tasks WHERE id=?',(gone['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM tasks WHERE id=?',(saved['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM tasks WHERE id=?',(pending['id'],)).fetchone()
    ok(c.put('/api/modules',json={'enabled':{'tasks':True}}))
    assert set(all_tasks(c))=={saved['id'],pending['id']}
    latest=next(item for item in ok(c.get('/api/task-lists')) if item['id']==kept['id'])
    ok(c.patch('/api/task-lists/'+kept['id'],json={'retain_completed':False,'version':latest['version']}))
    assert set(all_tasks(c))=={pending['id']}

def test_automatic_parent_deletion_waits_for_unfinished_child():
    c,_,_=owner();parent=add(c);child=add(c,parent_id=parent['id'])
    with m.db() as db:
        db.execute('UPDATE tasks SET done=1 WHERE id=?',(parent['id'],))
        db.execute('UPDATE task_details SET completed_at=? WHERE task_id=?',(m.now()-86400,parent['id']))
    cleanup(m.db,m.now());tasks=all_tasks(c)
    assert parent['id'] in tasks and tasks[child['id']]['parent_id']==parent['id']

def test_list_delete_and_version_conflict_leave_no_orphaned_tasks():
    c,_,_=owner();default=ok(c.get('/api/task-lists'))[0]
    assert c.delete('/api/task-lists/'+default['id']).status_code==409
    custom=listing(c);task=add(c,list_id=custom['id']);add(c,list_id=custom['id'],parent_id=task['id'])
    changed=ok(c.patch('/api/task-lists/'+custom['id'],json={'name':'Новое','version':custom['version']}))
    assert changed['name']=='Новое'
    assert c.patch('/api/task-lists/'+custom['id'],json={'name':'Конфликт','version':custom['version']}).status_code==409
    deletion=ok(c.delete('/api/task-lists/'+custom['id']))['deletion']
    assert all_tasks(c)=={}
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM task_details').fetchone()[0]==2
        from app.task_list_deletion import purge
        purge(db,deletion['delete_at'])
    with m.db() as db: assert not db.execute('SELECT 1 FROM task_details').fetchone()


def test_recovery_preserves_members_tree_completed_retention_and_owner_access(monkeypatch):
    clock=[m.now()]; monkeypatch.setattr(m,'now',lambda:clock[0])
    c,_,_=owner();g,_=member(c);friend(c,g)
    uid=ok(g.get('/api/me'))['id'];custom=listing(c);share(c,g,custom['id'],uid)
    parent=add(c,list_id=custom['id']);child=add(c,list_id=custom['id'],parent_id=parent['id'],done=True)
    first=ok(c.delete('/api/task-lists/'+custom['id']))['deletion']
    assert ok(c.delete('/api/task-lists/'+custom['id']))['deletion']==first
    assert g.post('/api/task-lists/'+custom['id']+'/restore').status_code==403
    assert c.patch('/api/tasks/'+parent['id'],json={'title':'Changed'}).status_code==409
    assert c.post('/api/tasks',json={'title':'Blocked','list_id':custom['id']}).status_code==409
    assert c.patch('/api/task-lists/'+custom['id'],json={'name':'Blocked'}).status_code==409
    assert custom['id'] not in {r['id'] for r in ok(g.get('/api/task-lists'))}
    assert all_tasks(c)==all_tasks(g)=={}
    pending=next(r for r in ok(c.get('/api/task-lists')) if r['id']==custom['id'])
    assert not pending['can_manage'] and pending['deletion']==first
    clock[0]+=23*3600
    renew(c);renew(g)
    assert all_tasks(c)=={}
    with m.db() as db: assert db.execute('SELECT 1 FROM tasks WHERE id=?',(child['id'],)).fetchone()
    restored=ok(c.post('/api/task-lists/'+custom['id']+'/restore'))
    assert restored['deletion'] is None and len(restored['members'])==2
    tasks=all_tasks(g)
    assert tasks[child['id']]['parent_id']==parent['id']
    assert tasks[child['id']]['completed_at']==child['completed_at']+23*3600
    assert c.patch('/api/tasks/'+child['id'],json={'version':child['version'],'title':'Stale'}).status_code==409
    second=ok(c.delete('/api/task-lists/'+custom['id']))['deletion']
    clock[0]=second['delete_at']
    renew(c)
    assert c.post('/api/task-lists/'+custom['id']+'/restore').status_code in (404,409)
    assert custom['id'] not in {r['id'] for r in ok(c.get('/api/task-lists'))}
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM tasks WHERE id=?',(parent['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM task_list_members WHERE list_id=?',(custom['id'],)).fetchone()

def test_full_archive_validation_rejects_cross_owner_parent_and_plaintext_forgery():
    c,_,_=owner();g,_=member(c);mine=add(c);foreign=add(g)
    with m.db() as db:
        validate_task_archive(db,lambda:pytest.fail('Valid reminder data rejected'))
        db.execute('UPDATE task_details SET parent_id=? WHERE task_id=?',(foreign['id'],mine['id']))
        with pytest.raises(ValueError): validate_task_archive(db,lambda:(_ for _ in ()).throw(ValueError('invalid')))
        db.execute('UPDATE task_details SET parent_id=NULL,payload=? WHERE task_id=?',(json.dumps({'priority':True}),mine['id']))
        with pytest.raises(ValueError): validate_task_archive(db,lambda:(_ for _ in ()).throw(ValueError('invalid')))

def test_reminder_payloads_are_charged_to_list_owner_and_widget_access_is_scoped():
    from app.quota import usage
    c,_,_=owner();g,_=member(c);friend(c,g);uid=ok(c.get('/api/me'))['id'];guest=ok(g.get('/api/me'))['id']
    custom=listing(c);share(c,g,custom['id'],guest)
    task=add(g,list_id=custom['id'],notes='x'*5000)
    with m.db() as db:
        assert db.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()[0]==usage(db,uid)
        assert db.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',(guest,)).fetchone()[0]==usage(db,guest)
        assert json.loads(db.execute('SELECT payload FROM task_details WHERE task_id=?',(task['id'],)).fetchone()[0])['notes']=='x'*5000
    layout=ok(g.get('/api/dashboard/layout'));widget=next(item for item in layout['instances'] if item['module_id']=='tasks')
    widget['config']={'view':'list','list_id':custom['id']};layout=ok(g.put('/api/dashboard/layout',json=layout))
    ok(c.delete('/api/task-lists/'+custom['id']+'/members/'+guest))
    assert g.put('/api/dashboard/layout',json=layout).status_code==404
    assert next(item for item in ok(g.get('/api/dashboard/layout'))['instances'] if item['module_id']=='tasks')['config']['list_id']==custom['id']
    widget=next(item for item in layout['instances'] if item['module_id']=='tasks')
    widget['config']['view']='all'
    saved=ok(g.put('/api/dashboard/layout',json=layout))
    assert next(item for item in saved['instances'] if item['module_id']=='tasks')['config']=={'view':'all','list_id':None}

def test_shared_default_is_never_the_viewers_default_and_unfriend_revokes_both_directions():
    c,_,_=owner();g,_=member(c);sid=friend(c,g)
    owner_id=ok(c.get('/api/me'))['id'];guest_id=ok(g.get('/api/me'))['id']
    mine=ok(c.get('/api/task-lists'))[0];theirs=ok(g.get('/api/task-lists'))[0]
    share(c,g,mine['id'],guest_id)
    share(g,c,theirs['id'],owner_id)
    for client,own_default in ((c,mine),(g,theirs)):
        defaults=[item for item in ok(client.get('/api/task-lists')) if item['is_default']]
        assert [item['id'] for item in defaults]==[own_default['id']]
        assert add(client)['list_id']==own_default['id']
    deletion=ok(c.delete('/api/social/spaces/'+sid))
    with m.db() as db:
        from app.workspace_lifecycle import purge
        purge(db,deletion['deletion']['delete_at'])
    friend(c,g)
    assert len(ok(c.get('/api/task-lists')))==1 and len(ok(g.get('/api/task-lists')))==1
    assert len(all_tasks(c))==1 and len(all_tasks(g))==1

def test_deleted_account_removes_owned_lists_but_preserves_other_owners_shared_tasks():
    from tests.test_admin import proof
    from app.quota import usage
    c,_,_=owner();g,_=member(c);friend(c,g)
    guest=ok(g.get('/api/me'))['id'];owner_id=ok(c.get('/api/me'))['id']
    shared=listing(c);share(c,g,shared['id'],guest)
    shared_task=add(g,'Остаётся у владельца списка',list_id=shared['id'])
    personal=add(g,'Удаляется вместе с аккаунтом');custom=listing(g);add(g,list_id=custom['id'])
    ok(c.post('/api/admin/users/delete',json=proof(user_id=guest,confirmation='friend')))
    from tests.deletion_helpers import finish_account
    finish_account(guest)
    assert set(all_tasks(c))=={shared_task['id']}
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM task_lists WHERE owner_id=?',(guest,)).fetchone()
        assert not db.execute('SELECT 1 FROM task_list_members WHERE user_id=?',(guest,)).fetchone()
        assert not db.execute('SELECT 1 FROM task_details WHERE task_id=?',(personal['id'],)).fetchone()
        assert db.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',(owner_id,)).fetchone()[0]==usage(db,owner_id)

@pytest.mark.parametrize('legacy_without_stamp',[False,True])
def test_completing_parent_preserves_earlier_child_completion_and_retention(monkeypatch,legacy_without_stamp):
    clock=stamp('2026-10-13T23:59:00+00:00');monkeypatch.setattr(m,'now',lambda:clock)
    c,_,_=owner();c.headers['x-time-zone']='UTC';custom=listing(c,retain_completed=True)
    parent=add(c,'Родитель',list_id=custom['id'])
    earlier=add(c,'Сделано вчера',day='2026-10-13',list_id=custom['id'],parent_id=parent['id'],done=True)
    pending=add(c,'Сделано сегодня',list_id=custom['id'],parent_id=parent['id'])
    first_completion=clock
    if legacy_without_stamp:
        with m.db() as db:
            db.execute('UPDATE task_details SET completed_at=NULL WHERE task_id=?',(earlier['id'],))
            settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(custom['owner_id'],)).fetchone()[0])
            settings.get('_task_completed_at',{}).pop(earlier['id'],None)
            db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),custom['owner_id']))
        first_completion=clock+120
    clock+=120;parent_completion=clock
    changed=ok(c.patch('/api/tasks/'+parent['id'],json={'done':True,'version':parent['version']}))
    tasks=all_tasks(c)
    assert tasks[earlier['id']]['completed_at']==first_completion
    assert tasks[parent['id']]['completed_at']==tasks[pending['id']]['completed_at']==parent_completion
    clock+=30;ok(c.patch('/api/tasks/'+parent['id'],json={'done':True,'version':changed['version']}))
    tasks=all_tasks(c)
    assert tasks[earlier['id']]['completed_at']==first_completion
    assert tasks[parent['id']]['completed_at']==tasks[pending['id']]['completed_at']==parent_completion
    ok(c.patch('/api/task-lists/'+custom['id'],json={'retain_completed':False,'version':custom['version']}))
    assert earlier['id'] in all_tasks(c)
    clock=parent_completion+86400-1
    renew(c)
    tasks=all_tasks(c)
    if legacy_without_stamp: assert earlier['id'] in tasks
    else: assert earlier['id'] not in tasks
    clock+=1
    assert all_tasks(c)=={}
