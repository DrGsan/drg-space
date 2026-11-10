import copy
import json
import httpx
import pytest
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,imported,ROOT,page,rt
from app import main as m
from app.notion_sync.state import enqueue

DATABASE='88888888-8888-4888-8888-888888888888'
SOURCE='99999999-9999-4999-8999-999999999999'
RECORD='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
NEW='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'


@pytest.mark.parametrize('kind',['page','database','data_source'])
@pytest.mark.parametrize('flag',['archived','in_trash'])
def test_trashed_source_stops_before_fetching_content_or_descendants(kind,flag):
    from app.notion_sync.snapshot import read_subtree
    from app.notion_sync.client import NotionError
    class NoContentRequests:
        def __getattr__(self,name):raise AssertionError('Deleted source requested '+name)
    with pytest.raises(NotionError) as error:
        read_subtree(NoContentRequests(),ROOT,root_kind=kind,root_raw={'id':ROOT,'object':kind,flag:True})
    assert error.value.status==404


def test_data_source_schema_values_new_rows_and_row_edits_sync_without_fetching_unchanged_record_bodies():
    c,s,provider,service,stamp=initialized();original=provider.handle
    provider.blocks[ROOT].append({'id':DATABASE,'type':'child_database','child_database':{'title':'Purchases'}})
    record=page(RECORD,'Coffee',SOURCE);record['parent']={'type':'data_source_id','data_source_id':SOURCE}
    record['properties'].update({'Cost':{'id':'num','type':'number','number':12},'State':{'id':'state','type':'select','select':{'name':'New'}},'Total':{'id':'calc','type':'formula','formula':{'type':'number','number':24}}})
    provider.pages[RECORD]=record;provider.blocks[RECORD]=[];rows=[record]
    data={'object':'data_source','id':SOURCE,'title':rt('Purchases'),'parent':{'type':'database_id','database_id':DATABASE},'last_edited_time':'2026-11-01T00:00:00Z','properties':{'Name':{'id':'title','type':'title','title':{}},'Cost':{'id':'num','type':'number','number':{}},'State':{'id':'state','type':'select','select':{'options':[{'name':'New'}]}},'Total':{'id':'calc','type':'formula','formula':{'expression':'Cost * 2'}}}}
    def respond(request):
        path=request.url.path
        if path=='/v1/databases/'+DATABASE:
            provider.requests.append((request.method,path));return httpx.Response(200,json={'object':'database','id':DATABASE,'title':rt('Purchases'),'parent':{'type':'page_id','page_id':ROOT},'data_sources':[{'id':SOURCE,'name':'Purchases'}]})
        if path=='/v1/data_sources/'+SOURCE:
            provider.requests.append((request.method,path));return httpx.Response(200,json=data)
        if path=='/v1/data_sources/'+SOURCE+'/query':
            provider.requests.append((request.method,path));return httpx.Response(200,json={'results':copy.deepcopy(rows),'has_more':False})
        return original(request)
    service.connections.transport=httpx.MockTransport(respond)
    identity,mapping=imported(service,s)
    database=c.get('/api/databases/'+mapping[SOURCE]).json()
    keys={field['name']:field['id'] for field in database['properties']}
    assert database['role']=='viewer'
    assert database['records'][0]['properties'][keys['Cost']]==12
    assert database['records'][0]['properties'][keys['Total']]=='24'
    assert c.get('/api/notes/'+mapping[RECORD]).json()['parent_id']==mapping[SOURCE]
    with m.db() as db:enqueue(db,identity,SOURCE,'pull',int(stamp[0]))
    before=len(provider.requests);assert service.work_once()
    # Metadata for the source and its database verifies the path to ROOT;
    # view listing and the record query never fetch unchanged record bodies.
    assert len(provider.requests)-before==4
    new=copy.deepcopy(record);new['id']=NEW;rows.append(new);provider.pages[NEW]=new;provider.blocks[NEW]=[]
    with m.db() as db:enqueue(db,identity,SOURCE,'pull',int(stamp[0]))
    before=len(provider.requests);assert service.work_once()
    while service.work_once():pass
    assert not any(path=='/v1/blocks/'+RECORD+'/children' for method,path in provider.requests[before:])
    with m.db() as db:new_note=db.execute('SELECT note_id FROM notion_pages WHERE notion_id=?',(NEW,)).fetchone()[0]
    assert len(c.get('/api/databases/'+mapping[SOURCE]).json()['records'])==2
    record['properties']['Cost']['number']=27;record['last_edited_time']='2026-11-01T00:00:01Z'
    with m.db() as db:enqueue(db,identity,RECORD,'pull',int(stamp[0]))
    before=len(provider.requests);assert service.work_once()
    assert len(provider.requests)-before==4
    actual=c.get('/api/databases/'+mapping[SOURCE]).json()
    assert next(row for row in actual['records'] if row['note_id']==mapping[RECORD])['properties'][keys['Cost']]==27
    data['properties']['State']['select']['options']=[{'name':'Done'}]
    for item in rows:item['properties']['State']['select']['name']='Done'
    with m.db() as db:enqueue(db,identity,SOURCE,'pull',int(stamp[0]))
    before=len(provider.requests);assert service.work_once()
    actual=c.get('/api/databases/'+mapping[SOURCE]).json()
    assert all(row['properties'][keys['State']]=='Done' for row in actual['records'])
    assert len(provider.requests)-before==4


@pytest.mark.parametrize('volatile',[False,True,'files'])
def test_yielded_initial_scan_advances_past_completed_database_records(volatile):
    c,s,provider,service,stamp=initialized();original=provider.handle;service.batch_seconds=0
    provider.blocks[ROOT].append({'id':DATABASE,'type':'child_database','child_database':{'title':'Library'}})
    records=[page(RECORD,'First',SOURCE),page(NEW,'Second',SOURCE)]
    query_count=[0]
    if volatile:
        for item in records:item['properties']['Dynamic']=({'id':'dynamic','type':'files','files':[{'type':'file','file':{'url':'https://files.example/attachment.pdf?signature=initial'}}]} if volatile=='files' else {'id':'dynamic','type':'formula','formula':{'type':'string','string':'initial'}})
    for record in records:
        record['parent']={'type':'data_source_id','data_source_id':SOURCE}
        provider.pages[record['id']]=record;provider.blocks[record['id']]=[]
    def respond(request):
        path=request.url.path
        if path=='/v1/databases/'+DATABASE:
            provider.requests.append((request.method,path));return httpx.Response(200,json={'object':'database','id':DATABASE,'title':rt('Library'),'parent':{'type':'page_id','page_id':ROOT},'data_sources':[{'id':SOURCE}]})
        if path=='/v1/data_sources/'+SOURCE:
            fields={'Name':{'id':'title','type':'title','title':{}}}
            if volatile:fields['Dynamic']=({'id':'dynamic','type':'files','files':{}} if volatile=='files' else {'id':'dynamic','type':'formula','formula':{'expression':'now()'}})
            provider.requests.append((request.method,path));return httpx.Response(200,json={'object':'data_source','id':SOURCE,'title':rt('Library'),'parent':{'type':'database_id','database_id':DATABASE},'properties':fields})
        if path=='/v1/data_sources/'+SOURCE+'/query':
            query_count[0]+=1;result=copy.deepcopy(records)
            if volatile:
                for item in result:
                    if volatile=='files':item['properties']['Dynamic']['files'][0]['file']['url']='https://files.example/attachment.pdf?signature=query-'+str(query_count[0])
                    else:item['properties']['Dynamic']['formula']['string']='query-'+str(query_count[0])
            provider.requests.append((request.method,path));return httpx.Response(200,json={'results':result,'has_more':False})
        return original(request)
    service.connections.transport=httpx.MockTransport(respond)
    preview=service.preview(s['user_id'],'read',ROOT)
    for _ in range(10):
        assert service.work_once()
        result=service.status(s['user_id'])['roots'][0]
        if result['status']=='ready':break
        stamp[0]+=2
    assert result['status']=='ready',result
    assert result['summary']['pages']==6
    assert provider.requests.count(('GET','/v1/databases/'+DATABASE))==1
    assert provider.requests.count(('GET','/v1/data_sources/'+SOURCE))==1
    if volatile:
        with m.db() as db:saved=json.loads(db.execute('SELECT payload FROM notion_staging WHERE root_id=? AND notion_id=?',(preview['id'],RECORD)).fetchone()[0])
        assert saved['values']['n_'+b'dynamic'.hex()]==('https://files.example/attachment.pdf?signature=query-1' if volatile=='files' else 'query-1')
        assert query_count[0]>=3
    for record in records:assert provider.requests.count(('GET','/v1/blocks/'+record['id']+'/children'))==1
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM notion_staging WHERE root_id=?',(preview['id'],)).fetchone()[0]==6
        assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==0


@pytest.mark.parametrize('page_status',[400,404])
def test_database_root_is_resolved_after_page_endpoint_rejects_database_id(page_status):
    c,s,provider,service,stamp=initialized();original=provider.handle
    def respond(request):
        path=request.url.path
        if path=='/v1/pages/'+DATABASE:return httpx.Response(page_status,json={'code':'validation_error' if page_status==400 else 'object_not_found'})
        if path=='/v1/databases/'+DATABASE:return httpx.Response(200,json={'object':'database','id':DATABASE,'title':rt('Notes'),'data_sources':[{'id':SOURCE,'name':'Notes'}]})
        if path=='/v1/data_sources/'+SOURCE:return httpx.Response(200,json={'object':'data_source','id':SOURCE,'title':rt('Notes'),'parent':{'type':'database_id','database_id':DATABASE},'properties':{'Name':{'id':'title','type':'title','title':{}}}})
        if path=='/v1/data_sources/'+SOURCE+'/query':return httpx.Response(200,json={'results':[],'has_more':False})
        return original(request)
    service.connections.transport=httpx.MockTransport(respond)
    root=service.preview(s['user_id'],'read',DATABASE);assert service.work_once()
    ready=service.status(s['user_id'])['roots'][0];assert ready['status']=='ready'
    assert ready['summary']['pages']==2 and ready['summary']['databases']==1
    # Resuming a checkpoint from an older candidate repairs its empty database
    # container and schema-derived title automatically, without editing notes.
    from app.notion_sync.snapshot import read_subtree
    with m.db() as db:cached={r['notion_id']:json.loads(r['payload']) for r in db.execute('SELECT notion_id,payload FROM notion_staging WHERE root_id=?',(root['id'],))}
    cached[DATABASE]['blocks']=[];cached[SOURCE]['title']='Notion';changed=[]
    client=service.connections.client('read')
    try:repaired=read_subtree(client,DATABASE,cached=cached,root_kind='database',checkpoint=lambda page:changed.append(page.id))
    finally:client.close()
    assert repaired[DATABASE].blocks[0]['id']==SOURCE and repaired[SOURCE].title=='Notes'
    assert set(changed)=={DATABASE,SOURCE}
    service.confirm(s['user_id'],root['id'],ready['preview_hash'])
    while service.work_once():pass
    active=service.status(s['user_id'])['roots'][0];assert active['status']=='active'
    note=c.get('/api/notes/'+active['note_id']).json()
    assert note['kind']=='database' and note['blocks']==[]
    assert note['navigation_aliases']==[active['note_id']]
    target=note['id']
    assert c.get('/api/databases/'+target).status_code==200
    source=c.get('/api/notes/'+target).json()
    assert source['notion_sync']['source_url']=='https://www.notion.so/'+DATABASE.replace('-','')
    assert 'source_notion_id' not in source['notion_sync']
