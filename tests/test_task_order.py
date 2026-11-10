"""Atomic reminder movement, authorization, concurrency and preservation."""
import hashlib
import json
from tests.test_task_lists import ok, add, listing, all_tasks, friend, share, owner, member, reset
from app import main as m
from app.modules.tasks import validate_task_archive


def revision(c, lid):
    rows=sorted((r for r in all_tasks(c).values() if r['list_id']==lid),key=lambda r:r['id'])
    value=[[r['id'],r['parent_id'],r['position'],r['version']] for r in rows]
    return hashlib.sha256(json.dumps(value,separators=(',',':')).encode()).hexdigest()


def move(c, task, parent=None, before=None, token=None):
    return c.post('/api/tasks/'+task['id']+'/move',json={'revision':token or revision(c,task['list_id']),'parent_id':parent,'before_id':before})


def order(c, lid, parent=None):
    return [r['title'] for r in sorted(all_tasks(c).values(),key=lambda r:(r['position'],r['created'],r['id'])) if r['list_id']==lid and r['parent_id']==parent]


def test_reorder_nest_extract_subtree_and_noop_preserve_content():
    c,_,_=owner(); l=listing(c,retain_completed=True)
    a=add(c,'A',list_id=l['id'],notes='Keep notes',url='https://example.com',tags=['home'],day='2027-01-20',time='09:30',priority=3,flagged=True)
    b=add(c,'B',list_id=l['id']); child=add(c,'Child',list_id=l['id'],parent_id=a['id']); other=add(c,'Other',list_id=l['id'],parent_id=b['id'])
    assert order(c,l['id'])==['A','B']
    ok(move(c,b,before=a['id'])); assert order(c,l['id'])==['B','A']
    ok(move(c,child,parent=b['id'],before=other['id']));assert order(c,l['id'],b['id'])==['Child','Other']
    ok(move(c,child,before=b['id']));assert order(c,l['id'])==['Child','B','A']
    token=revision(c,l['id']);ok(move(c,child,before=b['id']));assert revision(c,l['id'])==token
    ok(move(c,b));assert order(c,l['id'])==['Child','A','B'];assert all_tasks(c)[other['id']]['parent_id']==b['id']
    current=all_tasks(c)[a['id']]
    for key in ['title','notes','url','tags','day','time','priority','flagged','created','done','completed_at']:
        assert current[key]==a[key]
    with m.db() as db: validate_task_archive(db,lambda: (_ for _ in ()).throw(AssertionError('Invalid archive')))


def test_invalid_trees_targets_and_cross_list_are_atomic():
    c,_,_=owner();l=listing(c);l2=listing(c)
    a=add(c,'A',list_id=l['id']);b=add(c,'B',list_id=l['id']);child=add(c,'Child',list_id=l['id'],parent_id=a['id']);foreign=add(c,'Elsewhere',list_id=l2['id'])
    for task,parent,before in [(a,a['id'],None),(a,child['id'],None),(a,b['id'],None),(b,child['id'],None),(b,foreign['id'],None),(b,None,foreign['id']),(b,None,child['id']),(b,None,b['id'])]:
        previous=all_tasks(c)
        assert move(c,task,parent,before).status_code==422
        assert all_tasks(c)==previous


def test_stale_revision_rejects_edits_creates_deletes_and_moves():
    c,_,_=owner();l=listing(c);a=add(c,'A',list_id=l['id']);b=add(c,'B',list_id=l['id'])
    for mutate in [lambda:ok(c.patch('/api/tasks/'+b['id'],json={'notes':'Updated'})),lambda:add(c,'New',list_id=l['id']),lambda:ok(move(c,b,before=a['id'])),lambda:ok(c.delete('/api/tasks/'+b['id']))]:
        token=revision(c,l['id']);mutate();before=all_tasks(c)
        assert move(c,a,token=token).status_code==409
        assert all_tasks(c)==before


def test_shared_members_can_move_but_strangers_revoked_and_deleted_cannot():
    c,_,_=owner();g,_=member(c);stranger,_=member(c,'stranger');friend(c,g)
    lid=listing(c)['id'];share(c,g,lid,ok(g.get('/api/me'))['id'])
    a=add(c,'A',list_id=lid);b=add(c,'B',list_id=lid)
    ok(move(g,b,before=a['id']));assert order(c,lid)==['B','A']
    token=revision(c,lid)
    assert move(stranger,a,token=token).status_code==404
    ok(c.delete('/api/task-lists/'+lid+'/members/'+ok(g.get('/api/me'))['id']))
    assert move(g,a,token=token).status_code==404
    ok(c.delete('/api/task-lists/'+lid))
    assert move(c,a,token=token).status_code==409


def test_existing_patch_and_new_tasks_keep_manual_order_and_completion_rules():
    c,_,_=owner();l=listing(c,retain_completed=True)
    a=add(c,'A',list_id=l['id']);b=add(c,'B',list_id=l['id']);child=add(c,'Child',list_id=l['id'],parent_id=a['id'])
    ok(move(c,b,before=a['id'])); add(c,'C',list_id=l['id'])
    assert order(c,l['id'])==['B','A','C']
    ok(c.patch('/api/tasks/'+b['id'],json={'title':'Renamed'}));assert order(c,l['id'])==['Renamed','A','C']
    ok(c.patch('/api/tasks/'+b['id'],json={'done':True}))
    ok(move(c,child,parent=b['id']));assert not all_tasks(c)[b['id']]['done']
    ok(c.patch('/api/tasks/'+child['id'],json={'parent_id':None}));assert order(c,l['id'])==['Renamed','A','C','Child']


def test_legacy_records_and_minimal_old_payloads_are_not_rewritten_on_read():
    c,_,_=owner();l=listing(c);a=add(c,'A',list_id=l['id']);b=add(c,'B',list_id=l['id'])
    with m.db() as db:
        for index,task in enumerate((a,b)):
            db.execute('UPDATE task_details SET payload=? WHERE task_id=?',('{}',task['id']))
        from app.task_order import PREFIX
        db.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+l['id'],))
        for index,task in enumerate((a,b)):
            db.execute('UPDATE tasks SET created=? WHERE id=?',(index+1,task['id']))
    assert order(c,l['id'])==['A','B']
    with m.db() as db: assert db.execute('SELECT payload FROM task_details WHERE task_id=?',(a['id'],)).fetchone()[0]=='{}'
    ok(move(c,b,before=a['id']));assert order(c,l['id'])==['B','A']


def test_old_server_payload_reader_and_archive_validation_still_work_after_moves():
    import subprocess,types,sys
    c,_,_=owner();l=listing(c);a=add(c,'A',list_id=l['id']);b=add(c,'B',list_id=l['id'])
    ok(move(c,b,before=a['id']))
    old=types.ModuleType('drg_previous_tasks');sys.modules[old.__name__]=old
    try:
        from pathlib import Path
        source=(Path(__file__).parent/'fixtures/legacy_tasks.py').read_text()
        exec(compile(source,'previous_tasks.py','exec'),old.__dict__)
        with m.db() as db:
            for task in (a,b):assert old.serialize_task(old._task_row(db,task['id']))['title']==task['title']
            old.validate_task_archive(db,lambda: (_ for _ in ()).throw(AssertionError('Old format reader failed')))
    finally:sys.modules.pop(old.__name__,None)


def test_manual_order_personal_archive_remaps_positions_and_parents():
    from tests.test_personal_data import export,unseal,seal,restore
    c,_,_=owner();g,_=member(c);l=listing(c);a=add(c,'A',list_id=l['id']);b=add(c,'B',list_id=l['id']);child=add(c,'Child',list_id=l['id'],parent_id=a['id'])
    ok(move(c,b,before=a['id']));manifest,files=unseal(export(c))
    ok(restore(g,seal(manifest,files),'Friend-password-123'))
    copied=next(r for r in ok(g.get('/api/task-lists')) if r['name']==l['name'])
    assert order(g,copied['id'])==['B','A']
    new_a=next(r for r in all_tasks(g).values() if r['title']=='A')
    assert order(g,copied['id'],new_a['id'])==['Child']


def test_order_metadata_is_pruned_on_delete_and_list_cleanup():
    from app import task_order
    c,_,_=owner();l=listing(c);a=add(c,'A',list_id=l['id']);b=add(c,'B',list_id=l['id']);ok(move(c,b,before=a['id']))
    ok(c.delete('/api/tasks/'+b['id']))
    with m.db() as db:assert b['id'] not in task_order.positions(db,l['id'])
    with m.db() as db:
        db.execute('DELETE FROM tasks WHERE id=?',(a['id'],));db.execute('DELETE FROM task_lists WHERE id=?',(l['id'],));task_order.purge(db)
        assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',(task_order.PREFIX+l['id'],)).fetchone()


def test_legacy_deeper_tree_can_reorder_in_place_but_cannot_gain_new_depth():
    c,_,_=owner();l=listing(c);root=add(c,'Root',list_id=l['id']);child=add(c,'Child',list_id=l['id'],parent_id=root['id']);a=add(c,'Legacy A',list_id=l['id']);b=add(c,'Legacy B',list_id=l['id'])
    with m.db() as db:
        for task in (a,b):db.execute('UPDATE task_details SET parent_id=? WHERE task_id=?',(child['id'],task['id']))
    ok(move(c,b,parent=child['id'],before=a['id']));assert order(c,l['id'],child['id'])==['Legacy B','Legacy A']
    assert move(c,root,parent=child['id']).status_code==422
    ok(move(c,b));assert all_tasks(c)[b['id']]['parent_id'] is None


def test_long_list_moves_use_batched_versions_and_keep_every_record(monkeypatch):
    from app import task_order
    from app.database import Connection
    c,_,_=owner();l=listing(c)
    ids=['bulk_'+str(index).zfill(11) for index in range(550)]
    with m.db() as db:
        for index,identity in enumerate(ids):
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',(identity,l['owner_id'],'Bulk '+str(index),'',0,index+1))
            db.execute('INSERT INTO task_details(task_id,list_id,payload) VALUES(?,?,?)',(identity,l['id'],'{}'))
        task_order.save(db,l['id'],{identity:index+1 for index,identity in enumerate(ids)})
    updates=[];original=Connection.execute
    def execute(self,query,parameters=()):
        if query.startswith('UPDATE task_details SET version=version+1 WHERE task_id IN'):updates.append(len(parameters))
        return original(self,query,parameters)
    monkeypatch.setattr(Connection,'execute',execute)
    task=all_tasks(c)[ids[-1]];ok(move(c,task,before=ids[0]))
    assert updates==[200,200,150]
    assert order(c,l['id'])==['Bulk 549']+['Bulk '+str(index) for index in range(549)]
