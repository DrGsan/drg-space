import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime,timezone
import httpx
import pytest
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,imported,ROOT,CHILD,NEW,page
from app import main as m
from app.notion_sync.client import NotionError
from app.notion_sync.events import receive,reconcile,route_unknown
from app.notion_sync.state import pause_after_restore

def signed(event):
    body=json.dumps(event).encode();return body,'sha256='+hmac.new(b'webhook-test',body,hashlib.sha256).hexdigest()

def test_signed_duplicate_and_aggregated_events_queue_once_and_do_no_network_in_handler():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    service.config=replace(service.config,read_webhook_secret='webhook-test')
    count=len(provider.requests)
    event={'id':'delivery','workspace_id':'workspace','entity':{'id':ROOT,'type':'page'},'type':'page.content_updated'}
    for _ in range(100):receive(service,'read',*signed(event))
    assert len(provider.requests)==count
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM notion_events').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==1
        assert db.execute('SELECT generation FROM notion_jobs').fetchone()[0]==1
    for i in range(100):receive(service,'read',*signed({**event,'id':str(i)}))
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==1
    stamp[0]+=3
    assert service.work_once();assert not service.work_once()
    assert len(provider.requests)-count==2

def test_unsigned_webhook_never_persists_or_queues_and_unknown_page_only_schedules_common_reconcile():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    service.config=replace(service.config,read_webhook_secret='webhook-test')
    event={'id':'new','workspace_id':'workspace','entity':{'id':'77777777-7777-4777-8777-777777777777','type':'page'},'type':'page.created'}
    with pytest.raises(NotionError):receive(service,'read',signed(event)[0],'sha256=invalid')
    with m.db() as db:assert not db.execute('SELECT 1 FROM notion_events').fetchone()
    receive(service,'read',*signed(event))
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM notion_jobs').fetchone()
        assert db.execute('SELECT pending FROM notion_events').fetchone()[0]==1

def test_unchanged_connection_reconciles_once_without_fetching_tree_and_sleeping_window_survives_new_service():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    current=datetime.fromtimestamp(stamp[0],timezone.utc).isoformat()
    with m.db() as db:db.execute('UPDATE notion_pages SET remote_stamp=?',(current,))
    for value in provider.pages.values():value['last_edited_time']=current
    original=provider.handle
    def respond(req):
        if req.url.path=='/v1/search':
            provider.requests.append((req.method,req.url.path))
            return httpx.Response(200,json={'results':list(provider.pages.values()),'has_more':False})
        return original(req)
    service.connections.transport=httpx.MockTransport(respond)
    before=len(provider.requests)
    assert reconcile(service);assert not reconcile(service)
    assert len(provider.requests)-before==1
    with m.db() as db:assert not db.execute('SELECT 1 FROM notion_jobs').fetchone()

def test_rescan_requested_during_search_preserves_rewound_watermark():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    original=provider.handle
    def respond(req):
        if req.url.path=='/v1/search':
            with m.db() as db:
                row=db.execute("SELECT payload FROM notion_connections WHERE id='read'").fetchone();value=json.loads(row[0])
                value['watermark']=42;value.pop('scan',None)
                db.execute("UPDATE notion_connections SET payload=?,next_scan=0 WHERE id='read'",(json.dumps(value),))
            return httpx.Response(200,json={'results':[],'has_more':False})
        return original(req)
    service.connections.transport=httpx.MockTransport(respond)
    started=int(stamp[0])
    assert reconcile(service)
    with m.db() as db:
        row=db.execute("SELECT payload,next_scan FROM notion_connections WHERE id='read'").fetchone()
        assert json.loads(row['payload'])['watermark']==42
        assert 'scan' not in json.loads(row['payload'])
        assert row['next_scan']==started+2


def test_restore_pauses_outbound_work_and_keeps_disconnected_copies_editable():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    with m.db() as db:
        db.execute("UPDATE notion_roots SET mode='duplex'")
        pause_after_restore(db)
        assert db.execute('SELECT mode,status FROM notion_roots').fetchone()[:]==('pull','paused')
        assert db.execute('SELECT status FROM notion_connections').fetchone()[0]=='paused'
    assert not service.work_once()
    root=service.status(s['user_id'])['roots'][0];service.action(s['user_id'],identity,'disconnect',root['version'])
    with m.db() as db:pause_after_restore(db)
    assert c.get('/api/notes/'+mapping[ROOT]).json()['role']=='editor'


def test_new_or_old_moved_page_is_discovered_without_search_index_and_repeated_hints_share_one_parent_lookup():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    service.config=replace(service.config,read_webhook_secret='webhook-test')
    provider.pages[NEW]=page(NEW,'Moved old page',ROOT);provider.pages[NEW]['last_edited_time']='2000-01-01T00:00:00Z';provider.blocks[NEW]=[]
    provider.blocks[ROOT].append({'id':NEW,'type':'child_page','child_page':{'title':'Moved old page'},'has_children':True})
    before=len(provider.requests)
    for i in range(100):receive(service,'read',*signed({'id':'new-'+str(i),'type':'page.created','workspace_id':'workspace','entity':{'id':NEW,'type':'page'}}))
    assert len(provider.requests)==before
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_events WHERE pending=1').fetchone()[0]==1
    stamp[0]+=3;assert route_unknown(service)
    assert len(provider.requests)-before==1
    while service.work_once():pass
    with m.db() as db:
        child=db.execute('SELECT note_id FROM notion_pages WHERE root_id=? AND notion_id=?',(identity,NEW)).fetchone()[0]
        assert db.execute('SELECT COUNT(*) FROM notion_pages WHERE notion_id=?',(NEW,)).fetchone()[0]==1
    assert c.get('/api/notes/'+child).json()['parent_id']==mapping[ROOT]
    assert not any(path=='/v1/search' for method,path in provider.requests[before:])


def test_verification_bootstrap_does_not_activate_unsigned_events_or_expose_token_to_members():
    from tests.test_security import member
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    token='secret_browser_test_verification'
    assert receive(service,'read',json.dumps({'verification_token':token}).encode(),'')=={'ok':True}
    assert not service.config.read_webhook_secret
    event={'id':'not-active','type':'page.content_updated','workspace_id':'workspace','entity':{'id':ROOT,'type':'page'}}
    body=json.dumps(event).encode();signature='sha256='+hmac.new(token.encode(),body,hashlib.sha256).hexdigest()
    with pytest.raises(NotionError,match='notionInvalidSignature'):receive(service,'read',body,signature)
    response=c.get('/api/admin/notion-sync/webhooks/read')
    assert response.status_code==200 and response.json()['pending']['token']==token
    assert response.headers['Cache-Control']=='no-store'
    guest,_=member(c)
    assert guest.get('/api/admin/notion-sync/webhooks/read').status_code==403
