import json
from urllib.parse import urlsplit,parse_qs
import httpx
import pytest
from tests.test_security import owner,member,reset
from app import main as m
from app.notion_sync.config import NotionConfig
from app.notion_sync.client import NotionError
from app.notion_sync.connections import Connections

CONFIG=NotionConfig(True,'read-app','read-secret',redirect_uri='https://site.example/api/notion-sync/oauth/callback')

@pytest.fixture(autouse=True)
def isolated_connection_budget():
    with m.db() as db:db.execute('DELETE FROM notion_budgets')

def session(c):
    uid=c.get('/api/me').json()['id']
    with m.db() as db:return dict(db.execute("SELECT * FROM sessions WHERE user_id=? AND stage='full'",(uid,)).fetchone())

def grant(kind='read'):
    return {'access_token':'test-private-'+kind,'refresh_token':'refresh-private-'+kind,'workspace_id':'workspace','bot_id':'bot-'+kind,'workspace_name':'Private workspace','expires_in':3600}

def connect(service,s,kind='read'):
    url=service.start(s,kind)
    state=parse_qs(urlsplit(url).query)['state'][0]
    assert service.complete(state,'provider-code')=='web'
    with m.db() as db:return dict(db.execute('SELECT * FROM notion_connections WHERE user_id=? AND kind=?',(s['user_id'],'read')).fetchone())

def test_oauth_state_single_use_session_bound_and_tokens_not_in_authorize_url():
    c,_,_=owner();s=session(c);requests=[]
    def respond(req):requests.append(req);return httpx.Response(200,json=grant())
    service=Connections(m.db,CONFIG,m.DATA,transport=httpx.MockTransport(respond))
    url=service.start(s);args=parse_qs(urlsplit(url).query);token=args['state'][0]
    assert args['client_id']==['read-app'] and 'secret' not in url
    with m.db() as db:
        row=db.execute('SELECT * FROM notion_oauth_states').fetchone()
        assert row['id']!=token and row['session_token']==s['token']
    assert service.complete(token,'code')=='web'
    with pytest.raises(NotionError):service.complete(token,'code')
    assert len(requests)==1

def test_logout_during_provider_exchange_cannot_install_token_and_no_db_transaction_spans_http():
    c,_,_=owner();s=session(c)
    def respond(req):
        with m.db() as db:db.execute('DELETE FROM sessions WHERE token=?',(s['token'],))
        return httpx.Response(200,json=grant())
    service=Connections(m.db,CONFIG,m.DATA,transport=httpx.MockTransport(respond))
    token=parse_qs(urlsplit(service.start(s)).query)['state'][0]
    with pytest.raises(NotionError):service.complete(token,'code')
    with m.db() as db:assert not db.execute('SELECT 1 FROM notion_connections').fetchone()

def test_reauthorization_updates_same_connection_without_enabling_two_way():
    c,_,_=owner();s=session(c)
    service=Connections(m.db,CONFIG,m.DATA,transport=httpx.MockTransport(lambda _:httpx.Response(200,json=grant())))
    first=connect(service,s);second=connect(service,s)
    assert first['id']==second['id']
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_connections').fetchone()[0]==1


def test_old_write_oauth_request_uses_the_single_primary_application():
    c,_,_=owner();s=session(c)
    service=Connections(m.db,CONFIG,m.DATA,transport=httpx.MockTransport(lambda _:httpx.Response(200,json=grant())))
    url=service.start(s,'write');args=parse_qs(urlsplit(url).query)
    assert args['client_id']==['read-app']
    service.complete(args['state'][0],'provider-code')
    with m.db() as db:assert db.execute('SELECT kind FROM notion_connections').fetchone()[0]=='read'

def test_shared_budget_cooldown_and_write_recheck_after_wait():
    c,_,_=owner();s=session(c);stamp=[m.now()];requests=[];waits=[]
    def respond(req):requests.append(req);return httpx.Response(200,json=grant('write') if '/oauth/token' in str(req.url) else {'id':'bot'})
    def wait(seconds):waits.append(seconds);stamp[0]+=seconds
    service=Connections(m.db,CONFIG,m.DATA,clock=lambda:stamp[0],transport=httpx.MockTransport(respond),wait=wait)
    row=connect(service,s,'write');requests.clear()
    client=service.client(row['id'])
    try:
        with pytest.raises(NotionError,match='notionWriteForbidden'):client.request('PATCH','/v1/pages/11111111-1111-4111-8111-111111111111',{})
        assert not requests
        client.request('GET','/v1/users/me');client.request('GET','/v1/users/me')
        assert waits and .35<waits[-1]<.37
        with m.db() as db:db.execute('UPDATE notion_connections SET cooldown=? WHERE id=?',(int(stamp[0])+120,row['id']))
        with pytest.raises(NotionError) as error:client.request('GET','/v1/users/me')
        assert error.value.delay>=119 and len(requests)==2
    finally:client.close()
