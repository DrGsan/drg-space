"""Hierarchy discovery and transaction-scoped duplicate prevention."""
import copy,json
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from tests.test_notion_sync_service import initialized,imported,page,ROOT,CHILD,NEW
from app import main as m
from app.notion_sync.client import NotionError
from tests.test_security import reset

DB='66666666-6666-4666-8666-666666666666'
DS='77777777-7777-4777-8777-777777777777'


def tree_fixture():
    c,s,p,service,stamp=initialized();calls=[];original=p.handle
    def provider(req):
        calls.append((req.method,req.url.path));path=req.url.path
        if path=='/v1/search':return httpx.Response(200,json={'results':[page(CHILD,'Same',ROOT),page(ROOT,'IT'),{'id':DS,'object':'data_source','title':[],'parent':{'database_id':DB}}],'has_more':False})
        if path=='/v1/databases/'+DB:return httpx.Response(200,json={'id':DB,'object':'database','title':[{'plain_text':'Notes'}],'parent':{'page_id':ROOT},'data_sources':[{'id':DS}]})
        if path=='/v1/data_sources/'+DS+'/query':return httpx.Response(200,json={'results':[page(NEW,'Same')|{'parent':{'type':'data_source_id','data_source_id':DS,'database_id':DB}}],'has_more':False})
        return original(req)
    service.connections.transport=httpx.MockTransport(provider)
    return c,s,p,service,stamp,calls


def test_tree_builds_multilevel_paths_and_single_source_database_without_bodies():
    c,s,p,service,stamp,calls=tree_fixture()
    result=service.picker.tree(s['user_id'],'read');items={x['id']:x for x in result['items']}
    assert set(items)=={ROOT,CHILD,DB,NEW}
    assert items[NEW]['parent_id']==DB and items[NEW]['path']==['IT','Notes']
    assert items[CHILD]['parent_id']==ROOT and items[ROOT]['has_children']
    assert items[NEW]['title']==items[CHILD]['title']=='Same'
    assert len(calls)==3 and not any('/blocks/' in path for _,path in calls)
    result['items'].clear()
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:service.picker.tree(s['user_id'],'read'),range(4)))
    assert all(len(r['items'])==4 for r in results) and len(calls)==3
    service.picker.tree(s['user_id'],'read',True);assert len(calls)==3
    stamp[0]+=31;service.picker.tree(s['user_id'],'read',True);assert len(calls)==6


def test_current_link_overlay_is_not_cached_and_preserves_metadata():
    c,s,p,service,stamp,calls=tree_fixture();service.picker.tree(s['user_id'],'read')
    root=service.preview(s['user_id'],'read',CHILD)
    before=len(calls);items={x['id']:x for x in service.picker.tree(s['user_id'],'read')['items']}
    assert items[ROOT]['linked']['relation']=='contains'
    assert items[CHILD]['linked']['relation']=='included'
    assert 'linked' not in items[NEW] and len(calls)==before
    service.action(s['user_id'],root['id'],'cancel',root['version'])
    assert not any('linked' in x for x in service.picker.tree(s['user_id'],'read')['items'])


@pytest.mark.parametrize('first,second',[(ROOT,CHILD),(CHILD,ROOT)])
def test_pending_selection_reserves_both_ancestor_orders_and_creates_no_notes(first,second):
    c,s,p,service,stamp=initialized();service.preview(s['user_id'],'read',first)
    with pytest.raises(NotionError,match='notionSelectionOverlap'):service.preview(s['user_id'],'read',second)
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM notion_roots').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==0


def test_same_titles_do_not_conflict_and_local_sort_does_not_change_source_parent():
    c,s,p,service,stamp=initialized();p.pages[NEW]=page(NEW,'Root');p.blocks[NEW]=[]
    service.preview(s['user_id'],'read',ROOT);service.preview(s['user_id'],'read',NEW)
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_roots').fetchone()[0]==2


def test_parallel_ancestor_selections_only_reserve_one_tree():
    c,s,p,service,stamp=initialized()
    def choose(identity):
        try:return service.preview(s['user_id'],'read',identity)['id']
        except NotionError as e:return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(choose,[ROOT,CHILD]))
    assert result.count('notionSelectionOverlap')==1
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_roots').fetchone()[0]==1


def test_repeat_same_root_is_idempotent_and_moved_new_child_checks_live_ancestors():
    c,s,p,service,stamp=initialized();root,mapping=imported(service,s)
    p.pages[NEW]=page(NEW,'New child',CHILD);p.blocks[NEW]=[]
    assert service.preview(s['user_id'],'read',ROOT)['id']==root
    with pytest.raises(NotionError,match='notionSelectionOverlap'):service.preview(s['user_id'],'read',NEW)
    p.pages[NEW]['parent']={'workspace':True}
    assert service.preview(s['user_id'],'read',NEW)['id']!=root


def test_tree_revalidates_owner_and_grant_even_with_populated_cache():
    c,s,p,service,stamp,calls=tree_fixture();service.picker.tree(s['user_id'],'read')
    with m.db() as db:db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
    with pytest.raises(NotionError,match='notionAccessDenied'):service.picker.tree(s['user_id'],'read')
    assert len(calls)==3


def test_confirmation_rechecks_a_page_moved_inside_an_existing_tree():
    c,s,p,service,stamp=initialized();imported(service,s)
    p.pages[NEW]=page(NEW,'Independent');p.blocks[NEW]=[]
    candidate=service.preview(s['user_id'],'read',NEW)
    assert service.work_once()
    with m.db() as db:ready=service.describe(db,service.owned(db,candidate['id'],s['user_id']))
    assert ready['status']=='ready'
    p.pages[NEW]['parent']={'page_id':CHILD}
    with pytest.raises(NotionError,match='notionSelectionOverlap'):service.confirm(s['user_id'],candidate['id'],ready['preview_hash'])
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==2


def test_shared_page_with_inaccessible_parent_remains_selectable_without_expanding_access():
    c,s,p,service,stamp=initialized()
    p.pages[ROOT]['parent']={'page_id':NEW}
    result=service.preview(s['user_id'],'read',ROOT)
    assert result['status']=='scanning'
    assert not any(method!='GET' for method,_ in p.requests)


def test_unavailable_old_copy_does_not_claim_current_sync_coverage():
    c,s,p,service,stamp,calls=tree_fixture();root,mapping=imported(service,s)
    with m.db() as db:db.execute("UPDATE notion_pages SET status='unavailable' WHERE notion_id=?",(CHILD,))
    # Move out of IT in the metadata response, preserving the old local copy.
    original=service.connections.transport
    def moved(req):
        response=original.handle_request(req)
        if req.url.path=='/v1/search':
            data=response.json()
            for raw in data['results']:
                if raw['id']==CHILD:raw['parent']={'workspace':True}
            return httpx.Response(200,json=data)
        return response
    service.connections.transport=httpx.MockTransport(moved)
    items={x['id']:x for x in service.picker.tree(s['user_id'],'read')['items']}
    assert items[CHILD]['linked']['relation']=='reserved'
    assert c.get('/api/notes/'+mapping[CHILD]).status_code==200


def test_workspace_scope_does_not_reserve_unrelated_connection():
    c,s,p,service,stamp=initialized();imported(service,s)
    with m.db() as db:
        value=db.execute("SELECT payload FROM notion_connections WHERE id='read'").fetchone()[0]
        db.execute("INSERT INTO notion_connections(id,user_id,kind,workspace_id,bot_id,payload,created,updated) VALUES(?,?,'read',?,?,?,?,?)",('other',s['user_id'],'another-workspace','bot',value,int(stamp[0]),int(stamp[0])))
    assert service.preview(s['user_id'],'other',ROOT)['connection_id']=='other'


def test_tree_bad_parent_cycle_stops_bounded_without_requesting_content():
    c,s,p,service,stamp,calls=tree_fixture();original=service.connections.transport
    def cyclic(req):
        response=original.handle_request(req)
        if req.url.path=='/v1/search':
            data=response.json()
            for value in data['results']:
                if value['id']==ROOT:value['parent']={'page_id':CHILD}
            return httpx.Response(200,json=data)
        return response
    service.connections.transport=httpx.MockTransport(cyclic)
    with pytest.raises(NotionError,match='notionInvalidResponse'):service.picker.tree(s['user_id'],'read')
    assert len(calls)==3 and not any('/blocks/' in path for _,path in calls)
