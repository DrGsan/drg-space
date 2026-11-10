import copy
import json
import httpx
import pytest
from tests.test_security import reset

from app import main as m
from app.notion_sync.client import NotionClient,NotionError
from app.notion_sync.state import enqueue
from app.notion_sync.views import convert
from tests.test_notion_sync_service import initialized,imported,ROOT,page,rt
from tests.test_notion_sync_databases import DATABASE,SOURCE,RECORD,NEW

VIEW='cccccccc-cccc-4ccc-8ccc-cccccccccccc'
ARCHIVE='dddddddd-dddd-4ddd-8ddd-dddddddddddd'


@pytest.fixture(autouse=True)
def isolated_view_budget():
    yield
    with m.db() as db:db.execute('DELETE FROM notion_budgets')


def test_read_view_endpoints_do_not_expand_write_capabilities():
    mutations=[];requests=[]
    def respond(request):requests.append(request);return httpx.Response(200,json={})
    client=NotionClient('test',before_request=mutations.append,transport=httpx.MockTransport(respond))
    try:
        client.request('GET','/v1/views?database_id='+DATABASE)
        client.request('GET','/v1/views/'+VIEW)
        assert mutations==[False,False]
        for method,path in [('POST','/v1/views'),('PATCH','/v1/views/'+VIEW),('DELETE','/v1/views/'+VIEW)]:
            with pytest.raises(NotionError,match='notionWriteForbidden'):client.request(method,path,{})
        assert len(requests)==2
    finally:client.close()


def test_live_list_views_filters_sorts_visible_fields_and_changes_without_row_edits():
    c,s,provider,service,stamp=initialized();original=provider.handle
    provider.blocks[ROOT].append({'id':DATABASE,'type':'child_database','child_database':{'title':'Notes'}})
    properties={'Название':{'id':'title','type':'title','title':{}},'Тег':{'id':'%2CTu%25','type':'multi_select','multi_select':{'options':[{'name':'Архив'}]}}}
    data={'object':'data_source','id':SOURCE,'title':rt('Notes'),'parent':{'type':'database_id','database_id':DATABASE},'properties':properties,'last_edited_time':'2026-11-01T00:00:00Z'}
    records=[]
    for identity,name,tags in [(RECORD,'Current',[]),(NEW,'Archived',[{'name':'Архив'}])]:
        item=page(identity,name,SOURCE);item['parent']={'type':'data_source_id','data_source_id':SOURCE};item['properties']['Тег']={'id':'%2CTu%25','type':'multi_select','multi_select':tags}
        records.append(item);provider.pages[identity]=item;provider.blocks[identity]=[]
    views=[{'id':identity,'object':'view','parent':{'database_id':DATABASE},'data_source_id':SOURCE,'name':name,'type':'list','filter':{'or':[{'property':',Tu%','multi_select':{operation:'Архив'}}]},'sorts':[{'property':'title','direction':'ascending'}],'configuration':{'type':'list','properties':[{'property_id':',Tu%','visible':True}]}} for identity,name,operation in [(VIEW,'Заметки','does_not_contain'),(ARCHIVE,'Архив','contains')]]
    def respond(request):
        path=request.url.path
        if path=='/v1/databases/'+DATABASE:
            value={'object':'database','id':DATABASE,'title':rt('Notes'),'parent':{'type':'page_id','page_id':ROOT},'data_sources':[{'id':SOURCE,'name':'Notes'}]}
        elif path=='/v1/data_sources/'+SOURCE:value=data
        elif path=='/v1/data_sources/'+SOURCE+'/query':value={'results':records,'has_more':False}
        elif path=='/v1/search':value={'results':[],'has_more':False}
        elif path=='/v1/views':value={'results':[{'object':'view','id':view['id']} for view in views],'has_more':False}
        elif path.startswith('/v1/views/'):
            value=next(view for view in views if path.endswith(view['id']))|{'request_id':str(len(provider.requests))}
        else:return original(request)
        provider.requests.append((request.method,path));return httpx.Response(200,json=copy.deepcopy(value))
    service.connections.transport=httpx.MockTransport(respond)
    identity,mapping=imported(service,s)
    result=c.get('/api/databases/'+mapping[SOURCE]).json();actual=result['views'];tag=result['properties'][0]['id']
    assert c.get('/api/notes/'+mapping[SOURCE]).json()['title']=='Notes'
    assert [v['name'] for v in actual]==['Заметки','Архив'] and all(v['type']=='list' for v in actual)
    assert actual[0]['filter']=={'property':tag,'operator':'not_contains','value':'Архив'}
    assert actual[1]['filter']['operator']=='contains'
    assert actual[0]['sort']=={'property':'title','direction':'asc'} and actual[0]['visible']==[tag]
    views[0]['name']='Действующие';before=len(provider.requests)
    with m.db() as db:enqueue(db,identity,SOURCE,'pull',int(stamp[0]))
    assert service.work_once()
    assert c.get('/api/databases/'+mapping[SOURCE]).json()['views'][0]['name']=='Действующие'
    assert len(provider.requests)-before==6
    assert not any('/blocks/' in path for method,path in provider.requests[before:])
    version=c.get('/api/notes/'+mapping[SOURCE]).json()['version']
    with m.db() as db:enqueue(db,identity,SOURCE,'pull',int(stamp[0]))
    assert service.work_once()
    assert c.get('/api/notes/'+mapping[SOURCE]).json()['version']==version
    with m.db() as db:
        saved=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[SOURCE],)).fetchone()[0])
        assert saved['source']['views'][0]['filter']==views[0]['filter']
    from app.notion_sync.copy import independent
    copied=independent(service,s['user_id'],mapping[ROOT])
    copied_root=c.get('/api/notes/'+copied['note_id']).json()
    copied_container=c.get('/api/notes/'+copied_root['blocks'][-1]['props']['pageId']).json()
    assert copied_container['kind']=='database'
    copied_source=copied_container['id']
    copied_database=c.get('/api/databases/'+copied_source).json()
    assert copied_database['views'][0]['name']=='Действующие' and copied_database['views'][0]['filter']==actual[0]['filter']
    assert copied_database['role']=='editor'
    from app.notion_sync.events import reconcile,receive,route_unknown
    views[0]['name']='Периодическое обновление'
    assert reconcile(service)
    assert not reconcile(service)
    with m.db() as db:
        assert [(r['entity_id'],r['direction']) for r in db.execute('SELECT entity_id,direction FROM notion_jobs')]==[(DATABASE,'pull')]
    assert service.work_once()
    assert c.get('/api/databases/'+mapping[SOURCE]).json()['views'][0]['name']=='Периодическое обновление'
    from dataclasses import replace
    from tests.test_notion_sync_events import signed
    service.config=replace(service.config,read_webhook_secret='webhook-test')
    views[0]['name']='Обновление по событию';before=len(provider.requests)
    receive(service,'read',*signed({'id':'view-change','workspace_id':'workspace','entity':{'id':VIEW,'type':'view'},'type':'view.updated'}))
    assert len(provider.requests)==before
    stamp[0]+=3;assert route_unknown(service);assert service.work_once()
    assert c.get('/api/databases/'+mapping[SOURCE]).json()['views'][0]['name']=='Обновление по событию'



def test_unsupported_view_settings_are_reported_and_source_is_not_modified():
    raw={'id':VIEW,'type':'list','name':'Complex','filter':{'and':[{'property':'title','title':{'contains':'A'}},{'property':'title','title':{'contains':'B'}}]},'sorts':[{'property':'title','direction':'ascending'},{'property':'title','direction':'descending'}]}
    before=copy.deepcopy(raw)
    views,issues=convert([raw],{'Name':{'id':'title','type':'title'}})
    assert len(views)==1 and len(issues)==1 and issues[0]['reason']=='notionViewSettingsUnsupported'
    assert raw==before and not views[0].get('filter') and not views[0].get('sort')
