import json
import uuid
from collections import Counter
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,imported,ROOT,CHILD,NEW,page
from tests.test_notion_sync_partial import partial
from app import main as m
from app.database import Connection
from app.notion_sync.state import dump,enqueue


def test_new_pages_update_an_active_tree_and_repeated_delivery_does_not_double_count():
    c,s,p,service,stamp=initialized();rid,mapping=imported(service,s)
    p.pages[NEW]=page(NEW,'New child',ROOT);p.blocks[NEW]=[]
    p.blocks[ROOT].append({'id':NEW,'type':'child_page','child_page':{'title':'New child'},'has_children':True})
    with m.db() as db:enqueue(db,rid,ROOT,'pull',int(stamp[0]))
    assert service.work_once();result=service.status(s['user_id'])['roots'][0]
    assert result['summary']['pages']==3
    with m.db() as db:
        assert json.loads(db.execute('SELECT payload FROM notion_roots WHERE id=?',(rid,)).fetchone()[0])['summary']['pages']==3
    while service.work_once():pass
    assert len(c.get('/api/notes').json())==3
    with m.db() as db:enqueue(db,rid,ROOT,'pull',int(stamp[0]))
    assert service.work_once()
    assert service.status(s['user_id'])['roots'][0]['summary']['pages']==3


def test_old_cached_total_is_corrected_from_local_links_without_provider_requests_or_writes():
    c,s,p,service,stamp=initialized();rid,mapping=imported(service,s)
    with m.db() as db:
        row=db.execute('SELECT payload FROM notion_roots WHERE id=?',(rid,)).fetchone();value=json.loads(row[0]);value['summary']['pages']=1
        db.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(value),rid));cached=dump(value)
    p.status=503;before=len(p.requests)
    for _ in range(3):assert service.status(s['user_id'])['roots'][0]['summary']['pages']==2
    with m.db() as db:
        row=db.execute('SELECT * FROM notion_roots WHERE id=?',(rid,)).fetchone()
        assert service.describe(db,row)['summary']['pages']==2
        assert row['payload']==cached
    assert len(p.requests)==before


def test_initial_preview_and_pending_import_counts_keep_unpublished_pages_hidden():
    c,s,p,service,stamp=initialized();rid=service.preview(s['user_id'],'read',ROOT)['id'];assert service.work_once()
    preview=service.status(s['user_id'])['roots'][0];assert preview['summary']['pages']==2 and preview['status']=='ready'
    assert c.get('/api/notes').json()==[]
    service.confirm(s['user_id'],rid,preview['preview_hash'])
    assert service.status(s['user_id'])['roots'][0]['progress']=={'copied_pages':0,'total_pages':2}
    assert c.get('/api/notes').json()==[]


def test_stale_total_during_partial_import_does_not_claim_pending_pages_are_published():
    c,s,p,service,stamp,rid,mapping=partial()
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_roots WHERE id=?',(rid,)).fetchone()[0]);value['summary']['pages']=1
        db.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(value),rid))
    result=service.status(s['user_id'])['roots'][0]
    assert result['summary']['pages']==2 and result['progress']=={'copied_pages':1,'total_pages':2}
    assert len(c.get('/api/notes').json())==1


def test_status_counts_many_roots_with_one_grouped_query_not_one_query_per_root(monkeypatch):
    c,s,p,service,stamp=initialized();rid,mapping=imported(service,s)
    queries=Counter();execute=Connection._execute
    def counted(connection,sql,parameters=()):queries[' '.join(sql.split())]+=1;return execute(connection,sql,parameters)
    monkeypatch.setattr(Connection,'_execute',counted)
    service.status(s['user_id']);single=sum(queries.values())
    with m.db() as db:
        root=dict(db.execute('SELECT * FROM notion_roots WHERE id=?',(rid,)).fetchone());note=dict(db.execute('SELECT * FROM notes WHERE id=?',(mapping[ROOT],)).fetchone());link=dict(db.execute('SELECT * FROM notion_pages WHERE root_id=? LIMIT 1',(rid,)).fetchone())
        for i in range(20):
            remote=str(uuid.uuid5(uuid.NAMESPACE_URL,'count-root-'+str(i)));nr='count-root-'+str(i);nid='count-note-'+str(i)
            db.execute('INSERT INTO notes(id,user_id,title,body,blocks,created,updated) VALUES(?,?,?,?,?,?,?)',(nid,s['user_id'],note['title'],note['body'],note['blocks'],int(stamp[0]),int(stamp[0])))
            db.execute("INSERT INTO notion_roots(id,user_id,connection_id,notion_id,note_id,status,payload,created,updated) VALUES(?,?,?,?,?,'active',?,?,?)",(nr,s['user_id'],'read',remote,nid,root['payload'],int(stamp[0]),int(stamp[0])))
            db.execute("INSERT INTO notion_pages(id,user_id,root_id,notion_id,note_id,status,payload,updated) VALUES(?,?,?,?,?,'active',?,?)",('count-link-'+str(i),s['user_id'],nr,remote,nid,link['payload'],int(stamp[0])))
    queries.clear();before=len(p.requests);result=service.status(s['user_id'])
    assert len(result['roots'])==21 and sum(queries.values())==single
    assert all(r['summary']['pages']==(2 if r['id']==rid else 1) for r in result['roots'])
    assert len(p.requests)==before
