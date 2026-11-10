"""Provider access gates and retry semantics require no real Notion account."""
import httpx
import pytest
from app.notion_sync.config import NotionConfig
from app.notion_sync.client import NotionClient,NotionError,page_id

ID='11111111-1111-4111-8111-111111111111'

def test_optional_configuration_never_requires_credentials_or_enables_write(monkeypatch):
    for name in ('NOTION_SYNC_ENABLED','NOTION_OAUTH_CLIENT_ID','NOTION_OAUTH_CLIENT_SECRET','NOTION_OAUTH_REDIRECT_URI'):
        monkeypatch.delenv(name,raising=False)
    config=NotionConfig.from_env('https://example.com')
    assert not config.configured and not config.write_configured
    config=NotionConfig(True,'read','secret',redirect_uri='https://example.com/api/notion-sync/oauth/callback')
    assert config.configured and config.write_configured
    assert config.credentials('read')==config.credentials('write')
    assert not NotionConfig(True,'read','secret',redirect_uri='http://example.com/callback').configured


def test_read_client_rejects_all_content_writes_before_sending_but_allows_query_posts():
    requests=[]
    def respond(request):requests.append(request);return httpx.Response(200,json={'results':[],'has_more':False})
    client=NotionClient('private-test-token',transport=httpx.MockTransport(respond))
    try:
        for method,path in [('POST','/v1/pages'),('PATCH','/v1/pages/'+ID),('PATCH','/v1/blocks/'+ID),('PATCH','/v1/blocks/'+ID+'/children'),('PATCH','/v1/pages/'+ID+'/markdown'),('DELETE','/v1/blocks/'+ID),('POST','/v1/file_uploads')]:
            with pytest.raises(NotionError,match='notionWriteForbidden'):client.request(method,path,{})
        assert requests==[]
        client.request('POST','/v1/search',{})
        client.request('POST','/v1/data_sources/'+ID+'/query',{})
        assert len(requests)==2
    finally:client.close()


def test_readonly_and_uncertain_write_errors_are_not_retried_or_leaked():
    requests=[]
    def respond(request):requests.append(request);return httpx.Response(503,json={'message':'secret provider response with credentials'})
    for write in (False,True):
        client=NotionClient('private-test-token',write=write,transport=httpx.MockTransport(respond))
        try:
            with pytest.raises(NotionError) as error:client.request('PATCH' if write else 'GET','/v1/blocks/'+ID,{} if write else None)
            assert error.value.code==('notionWriteUncertain' if write else 'notionUnavailable')
            assert 'secret' not in str(error.value) and 'private-test-token' not in str(error.value)
        finally:client.close()
    assert len(requests)==2


def test_provider_retry_after_is_preserved_for_durable_queue():
    client=NotionClient('test',transport=httpx.MockTransport(lambda _:httpx.Response(429,headers={'Retry-After':'3600'})))
    try:
        with pytest.raises(NotionError) as error:client.page(ID)
        assert error.value.delay==3600
    finally:client.close()


def test_page_url_resolves_uuid_without_accepting_arbitrary_host_request():
    assert page_id('https://www.notion.so/Test-'+ID.replace('-','')+'?pvs=4')==ID
    with pytest.raises(ValueError):page_id('https://internal.example/latest')

def test_synced_copies_follow_the_original_with_one_read_and_no_content_mutations():
    original='22222222-2222-4222-8222-222222222222';copy_id='33333333-3333-4333-8333-333333333333';text_id='44444444-4444-4444-8444-444444444444';requests=[]
    shared={'id':original,'type':'synced_block','has_children':True,'synced_block':{'synced_from':None}}
    duplicate={'id':copy_id,'type':'synced_block','has_children':False,'synced_block':{'synced_from':{'block_id':original}}}
    def respond(req):
        requests.append((req.method,req.url.path));items=[shared,duplicate] if req.url.path=='/v1/blocks/'+ID+'/children' else [{'id':text_id,'type':'paragraph','has_children':False,'paragraph':{'rich_text':[]}}]
        return httpx.Response(200,json={'results':items,'has_more':False})
    client=NotionClient('test',transport=httpx.MockTransport(respond))
    try:
        tree=client.tree(ID)
        assert tree[0]['children']==tree[1]['children'] and all(b['synced_resolved'] for b in tree)
        tree[0]['children'][0]['type']='quote'
        assert tree[1]['children'][0]['type']=='paragraph'
        assert requests==[('GET','/v1/blocks/'+ID+'/children'),('GET','/v1/blocks/'+original+'/children')]
    finally:client.close()

@pytest.mark.parametrize('status',[403,404])
def test_unavailable_synced_original_keeps_a_visible_fallback_without_new_permissions(status):
    original='22222222-2222-4222-8222-222222222222'
    def respond(req):
        if req.url.path=='/v1/blocks/'+ID+'/children':return httpx.Response(200,json={'results':[{'id':ID,'type':'synced_block','synced_block':{'synced_from':{'block_id':original}}}],'has_more':False})
        return httpx.Response(status)
    client=NotionClient('test',transport=httpx.MockTransport(respond))
    try:
        from app.notion_sync.blocks import convert
        blocks,issues,_=convert(client.tree(ID),source_id=ID)
        assert blocks[0]['content'][0]['href'].startswith('https://www.notion.so/')
        assert any(i['reason']=='notionSourceUnavailable' for i in issues)
    finally:client.close()

def test_synced_cycles_do_not_loop_and_expanded_content_obeys_the_page_budget():
    requests=[]
    def cycle(req):
        requests.append(req.url.path)
        return httpx.Response(200,json={'results':[{'id':ID,'type':'synced_block','synced_block':{'synced_from':{'block_id':ID}}}],'has_more':False})
    client=NotionClient('test',transport=httpx.MockTransport(cycle))
    try:
        assert client.tree(ID)[0]['synced_unavailable']
        assert len(requests)==1
    finally:client.close()

    class Large(NotionClient):
        def children(self,identity):
            if identity==ID:return [{'id':ID,'type':'synced_block','synced_block':{'synced_from':{'block_id':'22222222-2222-4222-8222-222222222222'}}}]
            return [{'id':str(i),'type':'paragraph'} for i in range(2000)]
    client=Large('test')
    try:
        with pytest.raises(NotionError,match='notionPageTooLarge'):client.tree(ID)
    finally:client.close()


@pytest.mark.parametrize('status',[403,404])
def test_repeated_synced_copies_share_one_failed_original_read(status):
    original='22222222-2222-4222-8222-222222222222';calls=[]
    def respond(req):
        calls.append(req.url.path)
        if req.url.path=='/v1/blocks/'+ID+'/children':
            return httpx.Response(200,json={'results':[{'id':str(i),'type':'synced_block','synced_block':{'synced_from':{'block_id':original}}} for i in range(20)],'has_more':False})
        return httpx.Response(status)
    client=NotionClient('test',transport=httpx.MockTransport(respond))
    try:
        assert all(block['synced_unavailable'] for block in client.tree(ID))
        assert calls==['/v1/blocks/'+ID+'/children','/v1/blocks/'+original+'/children']
    finally:client.close()
