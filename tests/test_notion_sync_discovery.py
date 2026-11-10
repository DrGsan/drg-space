import copy,json,time
from concurrent.futures import ThreadPoolExecutor
import httpx,pytest
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,ROOT,CHILD,NEW,page,rt
from app import main as m
from app.notion_sync.client import NotionError


def picker_fixture():
    c,s,provider,service,stamp=initialized();requests=[];original=provider.handle
    def respond(request):
        if request.url.path!='/v1/search':return original(request)
        data=json.loads(request.content);requests.append(data)
        if data.get('query')=='slow':time.sleep(.1)
        if data.get('start_cursor')=='next':return httpx.Response(200,json={'results':[page(NEW,'Next page')],'has_more':False})
        source={'id':NEW,'object':'data_source','title':rt('Database'),'parent':{'database_id':CHILD}}
        return httpx.Response(200,json={'results':[page(ROOT,'Test'),source,{**source,'id':ROOT}],'has_more':True,'next_cursor':'next'})
    service.connections.transport=httpx.MockTransport(respond)
    return c,s,service,stamp,requests


def test_picker_caches_metadata_and_paginates_without_fetching_page_bodies():
    c,s,service,stamp,requests=picker_fixture()
    result=service.picker.list(s['user_id'],'read')
    assert result=={'items':[{'id':ROOT,'title':'Test','kind':'page','emoji':None},{'id':CHILD,'title':'Database','kind':'database','emoji':None}],'cursor':'next'}
    result['items'][0]['title']='Modified consumer copy'
    assert service.picker.list(s['user_id'],'read')['items'][0]['title']=='Test'
    assert len(requests)==1
    assert service.picker.list(s['user_id'],'read',cursor='next')['items'][0]['id']==NEW
    assert len(requests)==2
    service.picker.list(s['user_id'],'read',query='Test');assert len(requests)==3
    stamp[0]+=301;service.picker.list(s['user_id'],'read');assert len(requests)==4


def test_picker_coalesces_concurrent_requests_and_invalidates_on_new_grant():
    c,s,service,stamp,requests=picker_fixture()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:service.picker.list(s['user_id'],'read','slow'),range(4)))
    assert len(requests)==1 and all(row==results[0] for row in results)
    with m.db() as db:
        grant=json.loads(db.execute('SELECT payload FROM notion_connections WHERE id=?',('read',)).fetchone()[0]);grant['access_token']='different-test-only-grant'
        db.execute('UPDATE notion_connections SET payload=? WHERE id=?',(json.dumps(grant),'read'))
    service.picker.list(s['user_id'],'read','slow');assert len(requests)==2


@pytest.mark.parametrize('guard',['account','role','connection','write'])
def test_cached_picker_never_bypasses_current_owner_and_grant_checks(guard):
    c,s,service,stamp,requests=picker_fixture();service.picker.list(s['user_id'],'read')
    uid=s['user_id']
    with m.db() as db:
        if guard=='account':uid='another-account'
        elif guard=='role':db.execute("UPDATE users SET role='admin' WHERE id=?",(uid,))
        elif guard=='connection':db.execute("UPDATE notion_connections SET status='paused'")
        else:db.execute("UPDATE notion_connections SET kind='write'")
    with pytest.raises(NotionError,match='notionAccessDenied'):service.picker.list(uid,'read')
    assert len(requests)==1


@pytest.mark.parametrize('status',[401,429])
def test_picker_preserves_authorization_and_shared_retry_after(status):
    c,s,service,stamp,requests=picker_fixture();service.picker.list(s['user_id'],'read')
    attempted=[]
    def denied(request):attempted.append(request);return httpx.Response(status,headers={'Retry-After':'45'})
    service.connections.transport=httpx.MockTransport(denied)
    with pytest.raises(NotionError):service.picker.list(s['user_id'],'read','uncached')
    with m.db() as db:connection=db.execute('SELECT status,cooldown FROM notion_connections WHERE id=?',('read',)).fetchone()
    if status==401:
        assert connection['status']=='reauthorize'
        with pytest.raises(NotionError):service.picker.list(s['user_id'],'read')
    else:
        assert connection['cooldown']>=int(stamp[0])+44
        assert service.picker.list(s['user_id'],'read')['items']
        with pytest.raises(NotionError,match='notionRateLimited'):service.picker.list(s['user_id'],'read','another')
    assert len(attempted)==1
