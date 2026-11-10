"""Local navigation order is independent of source content and publication time."""
from app import main as m
from app.notion_sync.state import enqueue
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,imported,ROOT,CHILD,NEW,page,paragraph
from tests.test_notion_sync_presentation import database
from tests.test_notion_sync_databases import SOURCE


def test_append_offsets_are_batched_and_ignore_deleted_notes(monkeypatch):
    from app.database import Connection
    from app.notion_sync.service import next_positions
    c,s,provider,service,stamp=initialized()
    c.post('/api/notes',json={'title':'Kept'})
    removed=c.post('/api/notes',json={'title':'Deleted'})
    assert c.delete('/api/notes/'+removed.json()['id']).status_code==200
    queries=[];execute=Connection._execute
    def counted(connection,sql,params=()):queries.append(sql);return execute(connection,sql,params)
    monkeypatch.setattr(Connection,'_execute',counted)
    with m.db() as db:
        positions=next_positions(db,s['user_id'],[None,*('parent-'+str(i) for i in range(450))])
    assert positions[None]==1 and len(positions)==451
    assert sum('MAX(position)' in sql for sql in queries)==3
    assert len(provider.requests)==0


def roots(c):return [n for n in c.get('/api/notes?catalog=true').json() if n['parent_id'] is None and n['space_id'] is None]


def test_new_sync_roots_append_after_local_notes_and_previous_syncs():
    c,s,provider,service,stamp=initialized()
    first=c.post('/api/notes',json={'title':'First local'}).json()
    second=c.post('/api/notes',json={'title':'Second local'}).json()
    identity,mapping=imported(service,s)
    assert [n['id'] for n in roots(c)]==[first['id'],second['id'],mapping[ROOT]]
    provider.pages[NEW]=page(NEW,'Second sync');provider.blocks[NEW]=[paragraph('Independent source')]
    preview=service.preview(s['user_id'],'read',NEW);assert service.work_once()
    ready=next(r for r in service.status(s['user_id'])['roots'] if r['id']==preview['id'])
    confirmed=service.confirm(s['user_id'],preview['id'],ready['preview_hash'])
    while service.work_once():pass
    assert [n['id'] for n in roots(c)]==[first['id'],second['id'],mapping[ROOT],confirmed['note_id']]


def test_new_child_appends_and_manual_sibling_order_survives_remote_updates():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    old=c.get('/api/notes/'+mapping[CHILD]).json()
    provider.pages[NEW]=page(NEW,'New child',ROOT);provider.blocks[NEW]=[paragraph('New child body')]
    provider.blocks[ROOT].append({'id':NEW,'type':'child_page','child_page':{'title':'New child'}})
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    while service.work_once():pass
    siblings=[n for n in c.get('/api/notes?catalog=true').json() if n['parent_id']==mapping[ROOT]]
    assert siblings[0]['id']==old['id'] and siblings[1]['title']=='New child'
    assert siblings[0]['notion_sync']['can_reorder']
    moved=c.post('/api/notes/'+old['id']+'/move',json={'parent_id':mapping[ROOT],'before_id':None,'version':siblings[0]['version']})
    assert moved.status_code==200,moved.text
    assert [n['title'] for n in c.get('/api/notes?catalog=true').json() if n['parent_id']==mapping[ROOT]]==['New child','Child']
    provider.blocks[CHILD]=[paragraph('Updated in Notion')]
    with m.db() as db:enqueue(db,identity,CHILD,'pull',int(stamp[0]))
    while service.work_once():pass
    assert [n['title'] for n in c.get('/api/notes?catalog=true').json() if n['parent_id']==mapping[ROOT]]==['New child','Child']
    current=c.get('/api/notes/'+old['id']).json()
    assert 'Updated in Notion' in current['body']
    assert c.post('/api/notes/'+old['id']+'/move',json={'parent_id':None,'version':current['version']}).status_code==423
    assert all(method=='GET' for method,path in provider.requests)


def test_collapsed_database_uses_visible_position_for_catalog_and_full_list():
    c,s,provider,service,stamp,identity,mapping,raw=database()
    local=c.post('/api/notes',json={'title':'Local neighbour'}).json()
    shown=c.get('/api/notes/'+mapping[SOURCE]).json()
    moved=c.post('/api/notes/'+shown['id']+'/move',json={'parent_id':None,'before_id':None,'version':shown['version']})
    assert moved.status_code==200,moved.text
    for path in ('/api/notes?catalog=true','/api/notes'):
        rows=[n for n in c.get(path).json() if n['parent_id'] is None]
        assert [n['id'] for n in rows]==[local['id'],shown['id']]
        assert rows[-1]['position']>rows[0]['position']
