"""Consent, expiry, privacy and cancellation for shared reminder lists."""
import json
from tests.test_security import owner, member, reset
from tests.test_task_lists import ok, friend, listing, add, all_tasks, stamp, renew
from app import main as m
from app.retention import cleanup


def invite(c, g, shared):
    uid=ok(g.get('/api/me'))['id']
    result=ok(c.post('/api/task-lists/'+shared['id']+'/members',json={'user_id':uid}))
    item=next(item for item in result['invitations'] if item['recipient_id']==uid)
    return '/api/task-lists/'+shared['id']+'/invitations/'+item['id']


def test_invitation_in_bell_grants_nothing_until_recipient_accepts():
    c,_,_=owner();g,_=member(c);other,_=member(c,'stranger');friend(c,g)
    shared=listing(c);task=add(c,list_id=shared['id'],notes='Private until accepted')
    path=invite(c,g,shared)
    assert shared['id'] not in {item['id'] for item in ok(g.get('/api/task-lists'))}
    assert task['id'] not in all_tasks(g)
    assert g.patch('/api/tasks/'+task['id'],json={'done':True}).status_code==404
    assert g.post('/api/tasks',json={'title':'No access','list_id':shared['id']}).status_code==404
    notice=next(item for item in ok(g.get('/api/notifications'))['items'] if item['kind']=='task_invitation')
    assert notice['payload']['pending'] and notice['payload']['list_name']=='Список'
    assert other.post(path+'/accept').status_code==404
    assert c.post(path+'/accept').status_code==404
    assert other.delete(path).status_code==404
    ok(g.post(path+'/accept'))
    assert task['id'] in all_tasks(g)
    notice=next(item for item in ok(g.get('/api/notifications'))['items'] if item['kind']=='task_invitation')
    assert not notice['payload']['pending']
    assert g.post(path+'/accept').status_code==404


def test_duplicate_cancel_decline_resend_and_deleted_list():
    c,_,_=owner();g,_=member(c);friend(c,g);shared=listing(c)
    path=invite(c,g,shared);assert invite(c,g,shared)==path
    assert len([item for item in ok(g.get('/api/notifications'))['items'] if item['kind']=='task_invitation'])==1
    ok(c.delete(path));assert g.post(path+'/accept').status_code==404
    next_path=invite(c,g,shared);assert next_path!=path
    ok(g.delete(next_path));assert g.post(next_path+'/accept').status_code==404
    third=invite(c,g,shared);ok(c.delete('/api/task-lists/'+shared['id']))
    # Deletion is recoverable for 24 hours: an invitation cannot grant access
    # during that period, but survives together with the list for restoration.
    assert g.post(third+'/accept').status_code==409
    with m.db() as db:
        assert db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?',('task-invitation:'+shared['id']+':%',)).fetchone()
    ok(c.post('/api/task-lists/'+shared['id']+'/restore'))
    ok(g.post(third+'/accept'))


def test_expiry_is_seven_days_and_friendship_revocation_blocks_acceptance(monkeypatch):
    clock=stamp('2026-10-28T10:00:00+00:00');monkeypatch.setattr(m,'now',lambda:clock)
    c,_,_=owner();g,_=member(c);sid=friend(c,g);shared=listing(c)
    path=invite(c,g,shared)
    clock+=6*86400
    renew(c);renew(g)
    clock+=86400
    renew(c);renew(g)
    assert g.post(path+'/accept').status_code==404
    cleanup(m.db,clock)
    with m.db() as db:assert not db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?',('task-invitation:%',)).fetchone()
    path=invite(c,g,shared);ok(g.delete('/api/social/spaces/'+sid))
    assert g.post(path+'/accept').status_code==404


def test_parent_waits_for_child_full_day_and_completion_can_restart(monkeypatch):
    clock=stamp('2026-10-28T23:59:00+00:00');monkeypatch.setattr(m,'now',lambda:clock)
    c,_,_=owner();parent=add(c,done=True);child=add(c,parent_id=parent['id'],done=True)
    # Defensive legacy data: the child was completed later than its parent.
    with m.db() as db:
        db.execute('UPDATE tasks SET done=1 WHERE id=?',(parent['id'],))
        db.execute('UPDATE task_details SET completed_at=? WHERE task_id=?',(clock-86400,parent['id']))
    cleanup(m.db,clock);assert set(all_tasks(c))=={parent['id'],child['id']}
    clock+=120;assert child['id'] in all_tasks(c)  # Crossing midnight is irrelevant.
    ok(c.patch('/api/tasks/'+child['id'],json={'done':False}))
    ok(c.patch('/api/tasks/'+child['id'],json={'done':True}))
    clock+=86400-1;renew(c);assert child['id'] in all_tasks(c)
    clock+=1;assert child['id'] not in all_tasks(c)
    assert parent['id'] in all_tasks(c)  # Reopening the child reopened its parent.


def test_list_ids_with_underscores_cannot_match_another_lists_invitations():
    c,_,_=owner();g,_=member(c);friend(c,g)
    uid=ok(c.get('/api/me'))['id']
    with m.db() as db:
        for lid in ['list_one01','listXone01']:
            db.execute('INSERT INTO task_lists(id,owner_id,payload,created,updated) VALUES(?,?,?,?,?)',
                       (lid,uid,json.dumps({'name':lid}),m.now(),m.now()))
    shared={'id':'listXone01'};path=invite(c,g,shared)
    disguised=path.replace('listXone01','list_one01')
    assert g.post(disguised+'/accept').status_code==404
    ok(c.delete('/api/task-lists/list_one01'))
    ok(g.post(path+'/accept'))
    assert 'listXone01' in {item['id'] for item in ok(g.get('/api/task-lists'))}


def test_legacy_deep_archive_keeps_structure_and_remains_editable():
    from app.modules.tasks import validate_task_archive
    c,_,_=owner();root=add(c);child=add(c,parent_id=root['id'])
    uid=ok(c.get('/api/me'))['id'];parent=child['id']
    with m.db() as db:
        for depth in range(3,6):
            tid='legacy_depth_'+str(depth)
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',(tid,uid,'Legacy level '+str(depth),'',0,m.now()))
            db.execute('INSERT INTO task_details(task_id,list_id,parent_id,payload) VALUES(?,?,?,?)',(tid,root['list_id'],parent,'{}'))
            parent=tid
        validate_task_archive(db,lambda:(_ for _ in ()).throw(AssertionError('Legacy archive rejected')))
    changed=ok(c.patch('/api/tasks/'+parent,json={'notes':'Edited after upgrade'}))
    assert changed['parent_id']=='legacy_depth_4' and changed['notes']=='Edited after upgrade'
    assert c.post('/api/tasks',json={'title':'Too deep','parent_id':parent}).status_code==422
    assert len(all_tasks(c))==5
