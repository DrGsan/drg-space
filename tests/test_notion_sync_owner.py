import pytest
import httpx
from urllib.parse import parse_qs,urlsplit
from tests.test_security import owner,member,reset
from tests.test_notion_sync_connections import CONFIG,session,grant
from tests.test_notion_sync_service import initialized,imported,ROOT
from app import main as m
from app.notion_sync.connections import Connections
from app.notion_sync.client import NotionError
from app.notion_sync import access


@pytest.mark.parametrize('role',['member','admin'])
def test_all_account_sync_routes_reject_non_owner_even_site_administrator(role):
    c,_,_=owner();guest,_=member(c)
    uid=guest.get('/api/me').json()['id']
    with m.db() as db:db.execute('UPDATE users SET role=? WHERE id=?',(role,uid))
    requests=[('get','',None),('get','/roots/fixture',None),('get','/roots/fixture/conflict',None),
              ('post','/oauth',{'kind':'read'}),('post','/pages',{'connection_id':'fixture'}),('post','/tree',{'connection_id':'fixture'}),('post','/preview',{'connection_id':'fixture','source':ROOT}),
              ('post','/roots/fixture/confirm',{'preview_hash':'a'*64}),('post','/roots/fixture/action',{'action':'pause','version':1}),
              ('post','/copy',{'note_id':'a'*16}),('post','/roots/fixture/experimental',{'enabled':False,'version':1}),
              ('post','/roots/fixture/resolve',{'note_id':'a'*16,'review_hash':'a'*64,'version':1})]
    for method,path,body in requests:
        response=getattr(guest,method)('/api/account/notion-sync'+path,**({'json':body} if body is not None else {}))
        assert response.status_code==403,(method,path,response.text)
    if role=='admin':assert guest.post('/api/admin/access/unlock',json={'password':'Friend-password-123'}).status_code==200
    assert guest.get('/api/admin/notion-sync/webhooks/read').status_code==403
    assert guest.delete('/api/admin/notion-sync/webhooks/read').status_code==403
    assert c.get('/api/account/notion-sync').status_code==200


@pytest.mark.parametrize('when',['before','during'])
def test_oauth_callback_rechecks_owner_role_before_and_after_provider_exchange(when):
    c,_,_=owner();s=session(c);requests=[]
    def respond(request):
        requests.append(request)
        if when=='during':
            with m.db() as db:db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
        return httpx.Response(200,json=grant())
    service=Connections(m.db,CONFIG,m.DATA,transport=httpx.MockTransport(respond))
    state=parse_qs(urlsplit(service.start(s)).query)['state'][0]
    if when=='before':
        with m.db() as db:db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
    with pytest.raises(NotionError):service.complete(state,'provider-code')
    assert len(requests)==(0 if when=='before' else 1)
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_connections').fetchone()[0]==0


def test_role_revocation_stops_provider_calls_and_keeps_existing_copy_readonly():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    client=service.connections.client('read')
    access.write_enabled()
    with m.db() as db:
        db.execute("UPDATE notion_roots SET mode='duplex' WHERE id=?",(identity,))
        db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
    before=len(provider.requests)
    with pytest.raises(NotionError) as error:service.connections.client('read')
    assert error.value.status==403 and len(provider.requests)==before
    try:
        with pytest.raises(NotionError) as error:client.page(ROOT)
        assert error.value.status==403 and len(provider.requests)==before
    finally:client.close()
    page=c.get('/api/notes/'+mapping[ROOT]).json()
    assert page['notion_sync']['status']=='paused' and page['notion_sync']['read_only'] and page['role']=='viewer'
    assert page['title']=='Root'
