import asyncio
import socket
import httpx
import pytest
from app.notion_sync import assets
from app.notion_sync.asset_errors import AssetError,classify
from tests.test_security import reset


@pytest.mark.parametrize('status,reason',[(403,'notionAssetDenied'),(404,'notionAssetMissing'),(410,'notionAssetMissing'),(429,'notionAssetBusy'),(503,'notionAssetBusy'),(500,'notionAssetUnavailable')])
def test_attachment_failures_do_not_revoke_notion_access_or_leak_urls(status,reason):
    response=httpx.Response(status,request=httpx.Request('GET','https://example.org/file?secret=private-signature'),headers={'Retry-After':'120'})
    failure=classify(httpx.HTTPStatusError('private provider body',request=response.request,response=response))
    assert failure.code=='notionAssetUnavailable' and failure.status==502
    assert failure.params=={'reason':reason}
    assert 'private' not in str(failure)
    if status in (429,503):assert failure.delay==120


def test_timeout_and_dns_are_distinct_without_parsing_localized_messages():
    assert classify(httpx.ConnectTimeout('private')).params['reason']=='notionAssetTimeout'
    try:
        try:raise socket.gaierror(socket.EAI_NONAME,'private hostname')
        except OSError as cause:raise ValueError('localized DNS message') from cause
    except ValueError as error:failure=classify(error)
    assert failure.params['reason']=='notionAssetDns' and 'private' not in str(failure)


def test_current_cdn_failure_is_not_replaced_by_obsolete_host_gone(monkeypatch):
    calls=[]
    async def fetch(url,**kwargs):
        calls.append(url)
        if 'dzeninfra' in url:raise httpx.ConnectTimeout('private timeout')
        response=httpx.Response(410,request=httpx.Request('GET',url))
        raise httpx.HTTPStatusError('private body',request=response.request,response=response)
    monkeypatch.setattr(assets,'fetch_public',fetch)
    with pytest.raises(AssetError) as failure:asyncio.run(assets.download('https://avatars.mds.yandex.net/get-zen_doc/123/image?secret=signature',1024))
    assert len(calls)==2 and failure.value.params['reason']=='notionAssetTimeout'
    assert failure.value.params['host']=='avatars.dzeninfra.ru'
    assert 'signature' not in str(failure.value)


def test_working_cdn_is_tried_before_obsolete_host(monkeypatch):
    calls=[]
    async def fetch(url,**kwargs):calls.append(url);return b'image',url,'image/png'
    monkeypatch.setattr(assets,'fetch_public',fetch)
    assert asyncio.run(assets.download('https://avatars.mds.yandex.net/get-zen_doc/123/image',1024))==b'image'
    assert len(calls)==1 and 'avatars.dzeninfra.ru' in calls[0]


def test_file_retries_stop_after_five_preserve_progress_and_manual_retry_completes():
    from tests.test_notion_sync_file_progress import pending_files,retry
    from tests.test_security import m
    from app.notion_sync.asset_retry import MAX_ATTEMPTS
    c,s,p,service,stamp,rid,downloads=pending_files()
    for _ in range(MAX_ATTEMPTS-1):retry(service,stamp,rid)
    state=service.status(s['user_id'])['roots'][0]
    failure=state['asset_failures'][0]
    assert failure['blocked'] and failure['attempts']==5 and failure['file']=='missing.pdf'
    assert state['mode']=='pull' and state['status']=='importing'
    assert 'fingerprint' not in failure and 'block_id' not in failure
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_jobs WHERE root_id=?',(rid,)).fetchone()[0]==0
    before=len(downloads);stamp[0]+=7*86400;assert not service.work_once()
    assert len(downloads)==before and downloads.count('good.pdf')==1
    service.fetch=lambda url,limit:(downloads.append(url.rsplit('/',1)[-1]) or b'recovered file')
    service.action(s['user_id'],rid,'retry_assets',state['version']);assert service.work_once()
    final=service.status(s['user_id'])['roots'][0]
    assert final['status']=='active' and not final['asset_failures'] and not final['error']
    assert downloads.count('good.pdf')==1 and len(c.get('/api/notes').json())==1


def test_source_event_restarts_blocked_import_even_when_refresh_flag_was_already_set():
    from tests.test_notion_sync_file_progress import pending_files,retry
    from tests.test_security import m
    from app.notion_sync.state import refresh_import
    from tests.test_notion_sync_service import ROOT
    c,s,p,service,stamp,rid,downloads=pending_files()
    with m.db() as db:refresh_import(db,rid,ROOT,int(stamp[0]))
    for _ in range(4):retry(service,stamp,rid)
    assert service.status(s['user_id'])['roots'][0]['asset_failures'][0]['blocked']
    with m.db() as db:refresh_import(db,rid,ROOT,int(stamp[0]))
    assert service.work_once()
    failure=service.status(s['user_id'])['roots'][0]['asset_failures'][0]
    assert failure['attempts']==1 and not failure['blocked']


def test_retry_api_enforces_owner_csrf_and_current_version_without_sending_to_notion(monkeypatch):
    from tests.test_notion_sync_file_progress import pending_files
    from tests.test_security import m,member
    c,s,p,service,stamp,rid,downloads=pending_files()
    monkeypatch.setattr(m.notion_sync_service,'config',service.config)
    state=service.status(s['user_id'])['roots'][0];request={'action':'retry_assets','version':state['version']}
    guest,_=member(c)
    assert guest.post('/api/account/notion-sync/roots/'+rid+'/action',json=request).status_code==403
    csrf=c.headers.pop('x-csrf-token')
    assert c.post('/api/account/notion-sync/roots/'+rid+'/action',json=request).status_code==403
    c.headers['x-csrf-token']=csrf;before=len(p.requests)
    response=c.post('/api/account/notion-sync/roots/'+rid+'/action',json=request)
    assert response.status_code==200,response.text
    assert c.post('/api/account/notion-sync/roots/'+rid+'/action',json=request).status_code==409
    assert len(p.requests)==before


def test_expired_notional_upload_is_not_downloaded_and_keeps_offset_semantics():
    from app.notion_sync.assets import check_expiry
    value={'type':'file','file':{'expiry_time':'2026-11-08T12:00:00+03:00'}}
    from datetime import datetime,timezone
    stamp=datetime(2026,11,8,9,tzinfo=timezone.utc).timestamp()
    check_expiry(value,stamp-1)
    with pytest.raises(AssetError) as failure:check_expiry(value,stamp)
    assert failure.value.params['reason']=='notionAssetLinkExpired'


@pytest.mark.parametrize('kind',['icon','cover'])
def test_decorations_stop_after_five_and_explicit_retry_recovers_without_losing_content(kind):
    import io
    from PIL import Image
    from tests.test_notion_sync_service import initialized,imported,ROOT
    from tests.test_security import m
    c,s,p,service,stamp=initialized();downloads=[]
    p.pages[ROOT][kind]={'type':'external','external':{'url':'https://files.example/decoration.png'}}
    def fail(*args):downloads.append(1);raise AssetError('notionAssetTimeout')
    service.fetch=fail;rid,mapping=imported(service,s)
    for _ in range(4):
        with m.db() as db:stamp[0]=db.execute('SELECT MIN(due) FROM notion_jobs WHERE root_id=?',(rid,)).fetchone()[0]+1
        assert service.work_once()
    assert len(downloads)==5
    state=service.status(s['user_id'])['roots'][0]
    assert state['status']=='active' and state['has_asset_errors']
    assert any(i['reason']=='notion'+kind.title()+'RetryStopped' for i in state['summary']['issues'])
    assert c.get('/api/notes/'+mapping[ROOT]).json()['notion_sync'][kind+'_warning']=='notion'+kind.title()+'RetryStopped'
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_jobs WHERE root_id=?',(rid,)).fetchone()[0]==0
    body=c.get('/api/notes/'+mapping[ROOT]).json()['body'];assert 'Hello' in body
    image=io.BytesIO();Image.new('RGB',(32,32),(20,30,40)).save(image,format='PNG')
    service.fetch=lambda *_:image.getvalue()
    service.action(s['user_id'],rid,'retry_assets',state['version']);assert service.work_once()
    note=c.get('/api/notes/'+mapping[ROOT]).json();assert note['body']==body and note[kind].startswith('image:')
    assert not service.status(s['user_id'])['roots'][0]['has_asset_errors']
