"""Published revisions stay live while independent initial imports are pending."""
import copy
import json
from dataclasses import replace
from datetime import datetime,timezone

import httpx
import pytest

from tests.test_security import reset
from tests.test_notion_sync_service import initialized,ROOT,CHILD,NEW,IMAGE,page,paragraph,rt
from tests.test_notion_sync_databases import DATABASE,SOURCE,RECORD
from tests.test_notion_sync_events import signed
from app import main as m
from app.notion_sync.client import NotionError
from app.notion_sync.events import receive,reconcile
from app.notion_sync.state import enqueue


def partial():
    c,s,provider,service,stamp=initialized()
    provider.blocks[CHILD]=[paragraph('Original child'),{'id':IMAGE,'type':'file','file':{'type':'external','external':{'url':'https://files.example/pending.pdf'}}}]
    def unavailable(*_):raise NotionError('notionAssetUnavailable',502)
    service.fetch=unavailable
    identity=service.preview(s['user_id'],'read',ROOT)['id'];assert service.work_once()
    status=service.status(s['user_id'])['roots'][0]
    service.confirm(s['user_id'],identity,status['preview_hash']);assert service.work_once()
    service.config=replace(service.config,read_webhook_secret='webhook-test')
    with m.db() as db:mapping={r['notion_id']:r['note_id'] for r in db.execute('SELECT notion_id,note_id FROM notion_pages WHERE root_id=?',(identity,))}
    return c,s,provider,service,stamp,identity,mapping


def delivery(service,entity,identity='changed'):
    receive(service,'read',*signed({'id':identity,'workspace_id':'workspace','entity':{'id':entity,'type':'page'},'type':'page.content_updated'}))


def test_published_webhook_runs_before_bulk_import_and_keeps_failed_page_pending():
    c,s,provider,service,stamp,identity,mapping=partial()
    assert len(c.get('/api/notes').json())==1
    provider.blocks[ROOT][0]=paragraph('Live update during initial transfer')
    delivery(service,ROOT);stamp[0]+=3
    before=len(provider.requests);assert service.work_once()
    assert 'Live update during initial transfer' in c.get('/api/notes/'+mapping[ROOT]).json()['body']
    assert not any(CHILD in path for method,path in provider.requests[before:])
    with m.db() as db:assert db.execute('SELECT status FROM notion_pages WHERE notion_id=?',(CHILD,)).fetchone()[0]=='importing'
    assert service.work_once()
    root=service.status(s['user_id'])['roots'][0]
    assert root['status']=='importing' and root['error']=='notionAssetUnavailable'
    service.action(s['user_id'],identity,'pause',root['version'])
    before=len(provider.requests);delivery(service,ROOT,'paused-update')
    assert len(provider.requests)==before and not service.work_once()
    with m.db() as db:assert not db.execute('SELECT 1 FROM notion_jobs').fetchone()


@pytest.mark.parametrize('channel',['webhook','poll'])
def test_pending_edits_refresh_before_first_publication_without_duplicate_jobs(channel):
    c,s,provider,service,stamp,identity,mapping=partial()
    provider.blocks[CHILD][0]=paragraph('Changed after preview')
    provider.pages[CHILD]['last_edited_time']=datetime.fromtimestamp(stamp[0]+1,timezone.utc).isoformat()
    before=len(provider.requests)
    if channel=='webhook':
        delivery(service,CHILD)
        delivery(service,CHILD,'another-hint')
        assert len(provider.requests)==before
    else:
        original=provider.handle
        def respond(request):
            if request.url.path=='/v1/search':return httpx.Response(200,json={'results':[provider.pages[CHILD]],'has_more':False})
            return original(request)
        service.connections.transport=httpx.MockTransport(respond)
        assert reconcile(service);assert not reconcile(service)
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE notion_id=?',(CHILD,)).fetchone()[0])
        assert value['refresh_required']
        assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==1
    service.fetch=lambda *_:b'private attachment bytes'
    assert service.work_once()
    result=c.get('/api/notes/'+mapping[CHILD]).json()
    assert 'Changed after preview' in result['body']
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    with m.db() as db:assert not json.loads(db.execute('SELECT payload FROM notion_pages WHERE notion_id=?',(CHILD,)).fetchone()[0]).get('refresh_required')


def test_dirty_unpublished_page_moved_outside_selected_tree_is_not_copied():
    c,s,provider,service,stamp,identity,mapping=partial()
    provider.pages[CHILD]['parent']={'type':'workspace','workspace':True}
    delivery(service,CHILD);before=len(provider.requests)
    assert service.work_once()
    status=service.status(s['user_id'])['roots'][0]
    assert status['status']=='importing'
    assert any(item['reason']=='notionSourceUnavailable' for item in status['summary']['issues'])
    assert provider.requests[before:]==[('GET','/v1/pages/'+CHILD)]
    assert len(c.get('/api/notes').json())==1


def test_partial_discovery_adds_new_pages_and_updates_progress_total():
    c,s,provider,service,stamp,identity,mapping=partial()
    provider.pages[NEW]=page(NEW,'New page',ROOT);provider.blocks[NEW]=[paragraph('New child body')]
    provider.blocks[ROOT].append({'id':NEW,'type':'child_page','child_page':{'title':'New page'}})
    delivery(service,ROOT);stamp[0]+=3;assert service.work_once()
    status=service.status(s['user_id'])['roots'][0]
    assert status['summary']['pages']==3 and status['progress']=={'copied_pages':1,'total_pages':3}
    while service.work_once():pass
    status=service.status(s['user_id'])['roots'][0]
    assert status['progress']=={'copied_pages':2,'total_pages':3}
    assert any(n['title']=='New page' for n in c.get('/api/notes').json())


def test_partial_database_catches_up_old_titles_and_refreshes_pending_schema(monkeypatch):
    from app.notion_sync import snapshot
    c,s,provider,service,stamp=initialized();original=provider.handle
    provider.blocks[ROOT].append({'id':DATABASE,'type':'child_database','child_database':{'title':'Actual database'}})
    data={'object':'data_source','id':SOURCE,'title':rt('Actual database'),'parent':{'type':'database_id','database_id':DATABASE},'properties':{'Name':{'id':'title','type':'title','title':{}},'Cost':{'id':'cost','type':'number','number':{}}}}
    records=[]
    for identity in (RECORD,NEW):
        record=page(identity,identity[:8],SOURCE);record['parent']={'type':'data_source_id','data_source_id':SOURCE}
        record['properties']['Cost']={'id':'cost','type':'number','number':1}
        records.append(record);provider.pages[identity]=record;provider.blocks[identity]=[paragraph('Initial record body')]
    def respond(request):
        path=request.url.path
        if path=='/v1/databases/'+DATABASE:value={'id':DATABASE,'object':'database','title':rt('Actual database'),'parent':{'type':'page_id','page_id':ROOT},'data_sources':[{'id':SOURCE,'name':'Actual database'}]}
        elif path=='/v1/data_sources/'+SOURCE:value=data
        elif path=='/v1/data_sources/'+SOURCE+'/query':value={'results':records,'has_more':False}
        elif path=='/v1/search':value={'results':[],'has_more':False}
        else:return original(request)
        provider.requests.append((request.method,path));return httpx.Response(200,json=copy.deepcopy(value))
    service.connections.transport=httpx.MockTransport(respond)
    # Reproduce the old parser through its real checkpoint, not a manual edit
    # to the copied note/hash which would correctly trigger a conflict.
    title=snapshot.title
    with monkeypatch.context() as patch:
        patch.setattr(snapshot,'title',lambda raw:'Notion' if raw.get('object')=='data_source' else title(raw))
        root=service.preview(s['user_id'],'read',ROOT);assert service.work_once()
    ready=service.status(s['user_id'])['roots'][0];service.confirm(s['user_id'],root['id'],ready['preview_hash'])
    with m.db() as db:db.execute('UPDATE notion_jobs SET due=? WHERE entity_id=?',(int(stamp[0])+3600,RECORD))
    while service.work_once():pass
    with m.db() as db:mapping={r['notion_id']:r['note_id'] for r in db.execute('SELECT notion_id,note_id FROM notion_pages')}
    with m.db() as db:assert db.execute('SELECT title FROM notes WHERE id=?',(mapping[SOURCE],)).fetchone()[0]=='Notion'
    data['properties']['Cost']={'id':'cost','type':'rich_text','rich_text':{}}
    for record in records:record['properties']['Cost']={'id':'cost','type':'rich_text','rich_text':rt('fresh value')}
    provider.blocks[RECORD]=[paragraph('Latest pending body')]
    before=len(provider.requests);assert reconcile(service);assert service.work_once()
    assert c.get('/api/notes/'+mapping[SOURCE]).json()['title']=='Actual database'
    with m.db() as db:assert db.execute('SELECT title FROM notes WHERE id=?',(mapping[SOURCE],)).fetchone()[0]=='Actual database'
    assert not any('/blocks/' in path for method,path in provider.requests[before:])
    assert service.status(s['user_id'])['roots'][0]['status']=='importing'
    with m.db() as db:enqueue(db,root['id'],RECORD,'import',int(stamp[0]))
    before=len(provider.requests);assert service.work_once()
    result=c.get('/api/notes/'+mapping[RECORD]).json()
    assert 'Latest pending body' in result['body']
    database=c.get('/api/databases/'+mapping[SOURCE]).json()
    actual=next(r for r in database['records'] if r['note_id']==mapping[RECORD])
    assert actual['properties'][database['properties'][0]['id']]=='fresh value'
    assert ('POST','/v1/data_sources/'+SOURCE+'/query') not in provider.requests[before:]
    assert service.status(s['user_id'])['roots'][0]['status']=='active'


def test_trashed_pending_child_does_not_pause_published_root_or_publish_empty_placeholder():
    c,s,provider,service,stamp,identity,mapping=partial()
    assert service.work_once()  # first file failure; future attempts refresh source
    provider.pages[CHILD]['in_trash']=True;stamp[0]+=3600
    assert service.work_once()
    root=service.status(s['user_id'])['roots'][0]
    assert root['status']=='importing' and root['mode']=='pull'
    assert any(item['reason']=='notionSourceUnavailable' and item['page']=='Child' for item in root['summary']['issues'])
    assert len(c.get('/api/notes').json())==1
    with m.db() as db:
        pending=db.execute('SELECT status,payload FROM notion_pages WHERE root_id=? AND notion_id=?',(identity,CHILD)).fetchone()
        assert pending['status']=='importing' and json.loads(pending['payload'])['source_unavailable']
        assert not db.execute('SELECT 1 FROM notion_jobs WHERE root_id=?',(identity,)).fetchone()
    before=len(provider.requests);stamp[0]+=86400;assert not service.work_once();assert len(provider.requests)==before
    # Access restoration must re-read metadata, not trust the cached initial page.
    provider.pages[CHILD]['in_trash']=False;service.fetch=provider.fetch;delivery(service,CHILD,'restored-access')
    assert service.work_once()
    assert any(CHILD in path and path.startswith('/v1/pages/') for method,path in provider.requests[before:])
    root=service.status(s['user_id'])['roots'][0]
    assert root['status']=='active' and not any(item['reason']=='notionSourceUnavailable' for item in root['summary']['issues'])
    assert len(c.get('/api/notes').json())==2


def test_disconnect_retains_published_copy_and_discards_only_unpublished_unavailable_placeholder():
    c,s,provider,service,stamp,identity,mapping=partial()
    assert service.work_once();provider.pages[CHILD]['in_trash']=True;stamp[0]+=3600;assert service.work_once()
    root=service.status(s['user_id'])['roots'][0]
    result=service.action(s['user_id'],identity,'disconnect',root['version'])
    assert result['status']=='detached'
    assert c.get('/api/notes/'+mapping[ROOT]).status_code==200
    with m.db() as db:
        assert db.execute('SELECT 1 FROM notes WHERE id=?',(mapping[CHILD],)).fetchone() is None
        assert not db.execute('SELECT 1 FROM notion_staging WHERE root_id=?',(identity,)).fetchone()
