"""Permissions, opt-in discovery and streaming isolation of the optional bridge."""
import json
import httpx
import pytest
from tests.test_security import owner, member, client, reset
from app import main as m

G1,G2='-1001','-1002'
EXPORT='export:abcdef0123456789'
FILE='a'*32


@pytest.fixture
def connected(monkeypatch):
    bridge=m.family_photos
    for key,value in {'url':'http://archive.internal:8090','token':'service-secret-'*4,
                      'cached':None,'checked_at':0,'ever_available':False,'permission_confirmed':None}.items():monkeypatch.setattr(bridge,key,value)
    monkeypatch.setattr(bridge,'reaction_confirmed',{},raising=False)
    monkeypatch.setattr(bridge,'reaction_cursor','',raising=False)
    sent=[]
    def handler(request):
        assert request.headers['authorization']=='Bearer '+bridge.token
        path=request.url.path
        if path=='/v1/status':return httpx.Response(200,json={'service':'family-photo','api_version':1,'worker_online':True,'can_send':True,'bot_messaging':1})
        if path=='/v1/groups':return httpx.Response(200,json={'items':[{'id':G1,'title':'Семья 1','kind':'supergroup','can_send':True},{'id':G2,'title':'Семья 2','kind':'supergroup','can_send':True},
            {'id':EXPORT,'title':'Выгрузка','kind':'export','can_send':False}]})
        if path.endswith('/sync'):return httpx.Response(200,json={'state':'active','pending':2,'batch':{'total':3,'completed':1},'current':{'name':'family.mp4'},'cleanup':{'pending':12}})
        if path=='/v1/site-permissions':return httpx.Response(200,json={'ok':True,'revision':json.loads(request.content)['revision']})
        if path.endswith('/site-sends') and request.method=='POST':
            sent.append(json.loads(request.content)|{'actor_id':request.url.params['actor_id']});return httpx.Response(202,json={'id':'c'*32,'state':'queued','message_id':None})
        if path.endswith('/outbox'):
            assert request.url.params['actor_id']!='owner'
            return httpx.Response(200,json={'items':[]})
        if path.endswith('/unread'):return httpx.Response(200,json={'unread':0})
        if path.endswith('/media/'+FILE):return httpx.Response(206,content=b'video-range',headers={'Content-Type':'video/mp4','Content-Range':'bytes 0-10/11','Accept-Ranges':'bytes'})
        if path.endswith('/media'):return httpx.Response(200,json={'items':[{'id':FILE,'kind':'photo','has_thumbnail':True}],'more':False})
        if path.endswith('/media-index'):return httpx.Response(200,json={'items':[{'key':'2018-06','count':20,'start':1527811200,'end':1530403200}],'timezone':request.url.params.get('timezone'),'version':'index-test'})
        if path.endswith('/messages'):return httpx.Response(200,json={'items':[{'id':1,'text':'Привет','media':[{'id':FILE,'has_thumbnail':True}]}],'more':False})
        return httpx.Response(404,json={'detail':'not found'})
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(bridge,'client',lambda timeout=5:httpx.Client(base_url=bridge.url+'/',transport=transport,headers={'Authorization':'Bearer '+bridge.token}))
    return bridge,sent


def enable(c):
    response=c.put('/api/modules',json={'enabled':{'family_photos':True}})
    assert response.status_code==200,response.text


def grant(c,uid,groups,enabled=True):
    return c.put('/api/family-photos/admin/users/'+uid,json={'admin_password':'Owner-password-123','enabled':enabled,'groups':groups})


def test_worker_reuses_control_client_and_keeps_stream_clients_separate(connected,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    bridge,_=connected;created=[];timeouts=[]
    def handler(request):
        timeouts.append(request.extensions['timeout']['read'])
        return httpx.Response(200,json={'items':[]})
    def factory(timeout=5):
        client=httpx.Client(base_url=bridge.url+'/',transport=httpx.MockTransport(handler))
        created.append(client);return client
    monkeypatch.setattr(bridge,'client',factory)
    bridge.start_http()
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _:bridge.fetch('v1/groups'),range(20)))
        bridge.bot_fetch('v1/site-permissions','PUT',{})
        assert len(created)==1 and not created[0].is_closed
        assert 5 in timeouts and 60 in timeouts
        with bridge.client(timeout=30) as stream:
            assert stream is not created[0]
        assert created[1].is_closed and not created[0].is_closed
    finally:bridge.close_http()
    assert created[0].is_closed
    bridge.start_http()
    try:bridge.fetch('v1/groups');assert len(created)==3
    finally:bridge.close_http()


def test_no_config_hides_module_and_forbids_forged_enable(monkeypatch):
    monkeypatch.setattr(m.family_photos,'url','')
    c,_,_=owner()
    assert not any(e['id']=='family_photos' for e in c.get('/api/modules').json()['catalog'])
    assert c.put('/api/modules',json={'enabled':{'family_photos':True}}).status_code==403
    assert c.get('/api/family-photos/groups').status_code==404


def test_month_index_is_scoped_and_pagination_window_is_validated(connected):
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    assert grant(c,uid,[G1]).status_code==200
    data=guest.get('/api/family-photos/groups/'+G1+'/media-index?timezone=Europe/Moscow')
    assert data.status_code==200
    assert data.json()['timezone']=='Europe/Moscow'
    assert guest.get('/api/family-photos/groups/'+G2+'/media-index').status_code==403
    assert guest.get('/api/family-photos/groups/'+G1+'/media-index?timezone=invalid-zone').status_code==422
    assert guest.get('/api/family-photos/groups/'+G1+'/media?until=bad').status_code==422
    assert guest.get('/api/family-photos/groups/'+G1+'/media-version?until=9999999999999').status_code==422
    assert guest.get('/api/family-photos/groups/'+G1+'/media?until=1530403200').status_code==200
    assert grant(c,uid,[],False).status_code==200
    assert guest.get('/api/family-photos/groups/'+G1+'/media-index').status_code==403

@pytest.mark.parametrize('reported,expected',[('1.0.0','1.0.0'),(None,None),({'invalid':1},None)])
def test_service_version_is_optional_and_independent_of_protocol(connected,monkeypatch,reported,expected):
    bridge,_=connected
    def handler(request):
        return httpx.Response(200,json={'service':'family-photo','api_version':1,'service_version':reported})
    monkeypatch.setattr(bridge,'client',lambda timeout=5:httpx.Client(base_url=bridge.url+'/',transport=httpx.MockTransport(handler)))
    status=bridge.status()
    assert status['available'] is True
    assert status.get('service_version')==expected


def test_discovery_opt_in_and_no_user_self_grant(connected):
    c,_,_=owner();guest,_=member(c)
    initial=c.get('/api/modules').json()
    assert initial['enabled']['family_photos'] is False
    assert c.get('/api/family-photos/groups').status_code==403
    assert not any(e['id']=='family_photos' for e in guest.get('/api/modules').json()['catalog'])
    assert guest.put('/api/modules',json={'enabled':{'family_photos':True}}).status_code==403
    enable(c)
    assert len(c.get('/api/family-photos/groups').json()['items'])==3
    assert guest.get('/api/family-photos/admin').status_code==403


def test_per_group_grants_cover_gallery_history_original_thumbnail_and_send(connected):
    bridge,sent=connected
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    assert grant(c,uid,[G1]).status_code==200
    enable(guest)
    assert [g['id'] for g in guest.get('/api/family-photos/groups').json()['items']]==[G1]
    for suffix in ('media','messages','outbox?actor_id=owner','media/'+FILE,'media/'+FILE+'?thumbnail=1'):
        assert guest.get('/api/family-photos/groups/'+G1+'/'+suffix).status_code in {200,206}
        assert guest.get('/api/family-photos/groups/'+G2+'/'+suffix).status_code==403
    response=guest.get('/api/family-photos/groups/'+G1+'/media/'+FILE,headers={'Range':'bytes=0-10'})
    assert response.content==b'video-range' and response.headers['cache-control']=='no-store'
    assert response.headers['content-range']=='bytes 0-10/11'
    photo=guest.get('/api/family-photos/groups/'+G1+'/media').json()['items'][0]
    assert photo['url'].startswith('/api/family-photos/') and 'archive.internal' not in json.dumps(photo)
    assert guest.post('/api/family-photos/groups/'+G1+'/messages',json={'text':'Привет','client_id':'denied'}).status_code==403
    assert c.put('/api/family-photos/admin/users/'+uid,json={'enabled':True,'groups':[G1],'write_groups':[G1]}).status_code==200
    assert guest.post('/api/family-photos/groups/'+G1+'/messages',json={'text':'Привет','client_id':'request-1'}).status_code==200
    assert sent[0]['actor_id']==uid and sent[0]['author']=='friend'
    assert guest.post('/api/family-photos/groups/'+G2+'/messages',json={'text':'Привет','client_id':'request-2'}).status_code==403
    assert guest.post('/api/family-photos/groups/'+G1+'/messages',json={'text':'Привет','client_id':'request-3','author':'owner'}).status_code==422
    assert grant(c,uid,[],False).status_code==200
    assert guest.get('/api/family-photos/groups/'+G1+'/media/'+FILE).status_code==403
    assert guest.get('/api/family-photos/groups').status_code==403


def test_grant_enables_module_and_revoke_removes_settings_without_touching_other_modules(connected):
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    assert guest.put('/api/modules',json={'enabled':{'notes':False}}).status_code==200
    response=grant(c,uid,[G2,G1,G1])
    assert response.json()=={'ok':True,'enabled':True,'groups':[G1,G2],'write_groups':[]}
    assert guest.get('/api/modules').json()['enabled']['family_photos'] is True
    assert len(guest.get('/api/family-photos/groups').json()['items'])==2
    assert guest.get('/api/me').json()['settings']['modules']=={'notes':False,'family_photos':True}

    # Old selections submitted by an open admin form must also be discarded.
    response=grant(c,uid,[G1,G2],False)
    assert response.json()=={'ok':True,'enabled':False,'groups':[],'write_groups':[]}
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
    assert 'family_photo_access' not in settings
    assert settings['modules']=={'notes':False}
    public=guest.get('/api/me').json()['settings']
    assert 'family_photo_access' not in public and public['modules']=={'notes':False}
    modules=guest.get('/api/modules').json()
    assert 'family_photos' not in modules['enabled']
    assert all(item['id']!='family_photos' for item in modules['catalog'])
    saved=next(u for u in c.get('/api/family-photos/admin').json()['users'] if u['id']==uid)
    assert saved['enabled'] is False and saved['groups']==[]
    assert saved['profile']['id']==uid and saved['profile']['login']==saved['login']
    assert 'password' not in saved['profile'] and 'settings' not in saved['profile']
    assert guest.put('/api/modules',json={'enabled':{'family_photos':True}}).status_code==403
    assert guest.put('/api/modules',json={'enabled':{'family_photos':False}}).status_code==200
    assert guest.get('/api/me').json()['settings']['modules']=={'notes':False}
    assert grant(c,uid,[],False).status_code==200
    assert grant(c,uid,[G2]).status_code==200
    assert guest.get('/api/modules').json()['enabled']['family_photos'] is True


def test_user_can_hide_module_without_revoking_grant_and_admin_can_reenable_it(connected):
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    assert grant(c,uid,[G1]).status_code==200
    response=guest.put('/api/modules',json={'enabled':{'family_photos':False}})
    assert response.status_code==200 and response.json()['enabled']['family_photos'] is False
    settings=guest.get('/api/me').json()['settings']
    assert 'family_photos' not in settings['modules']
    assert settings['family_photo_access']=={'enabled':True,'groups':[G1],'write_groups':[]}
    assert guest.get('/api/family-photos/groups').status_code==403
    assert grant(c,uid,[G2]).status_code==200
    assert [g['id'] for g in guest.get('/api/family-photos/groups').json()['items']]==[G2]


def test_switch_checks_latest_grant_even_if_request_started_before_revocation(connected,monkeypatch):
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    assert grant(c,uid,[G1]).status_code==200
    original=m.session
    def concurrent_revoke(request):
        session=original(request)
        with m.db() as db:
            settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
            settings.pop('family_photo_access',None);settings['modules'].pop('family_photos',None)
            db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
        return session
    with monkeypatch.context() as patch:
        patch.setattr(m,'session',concurrent_revoke)
        assert guest.put('/api/modules',json={'enabled':{'family_photos':True}}).status_code==403
    settings=guest.get('/api/me').json()['settings']
    assert 'family_photo_access' not in settings and 'family_photos' not in settings['modules']


def test_revocation_during_outage_clears_legacy_disabled_grants(connected,monkeypatch):
    bridge,_=connected
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    settings=guest.get('/api/me').json()['settings']
    settings.update(family_photo_access={'enabled':False,'groups':[G1]},modules={'family_photos':False,'chat':False})
    with m.db() as db:db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    monkeypatch.setattr(bridge,'status',lambda:{'configured':True,'available':False})
    saved=next(u for u in c.get('/api/family-photos/admin').json()['users'] if u['id']==uid)
    assert saved['enabled'] is False and saved['groups']==[]
    def unavailable(*args,**kwargs):raise AssertionError('Revocation must not call the archive')
    monkeypatch.setattr(bridge,'fetch',unavailable)
    assert grant(c,uid,[G1],False).status_code==200
    settings=guest.get('/api/me').json()['settings']
    assert 'family_photo_access' not in settings and settings['modules']=={'chat':False}


def test_grant_requires_admin_password_known_groups_and_csrf(connected):
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    assert grant(c,uid,['-999']).status_code==422
    assert grant(c,uid,[]).status_code==422
    payload={'admin_password':'wrong','enabled':True,'groups':[G1]}
    assert c.put('/api/family-photos/admin/users/'+uid,json=payload).status_code==403
    assert guest.put('/api/family-photos/admin/users/'+uid,json=payload).status_code==403
    del c.headers['x-csrf-token']
    assert grant(c,uid,[G1]).status_code==403


def test_transient_outage_keeps_existing_switch_and_does_not_break_site(connected,monkeypatch):
    bridge,_=connected
    c,_,_=owner();enable(c)
    monkeypatch.setattr(bridge,'status',lambda:{'configured':True,'available':False})
    catalog=c.get('/api/modules').json()
    assert catalog['enabled']['family_photos'] and not catalog['catalog'][-1]['available']
    assert c.get('/api/notes').status_code==200
    assert c.put('/api/modules',json={'enabled':{'notes':False,'family_photos':True}}).status_code==200


def test_fresh_unreachable_service_is_not_offered(connected,monkeypatch):
    bridge,_=connected
    monkeypatch.setattr(bridge,'status',lambda:{'configured':True,'available':False})
    c,_,_=owner()
    assert not any(e['id']=='family_photos' for e in c.get('/api/modules').json()['catalog'])
    assert c.put('/api/modules',json={'enabled':{'family_photos':True}}).status_code==503


def test_unread_requires_current_session_grant_and_both_modules(connected):
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    path='/api/family-photos/unread'
    assert client().get(path).status_code==401
    assert guest.get(path).status_code==403
    assert grant(c,uid,[G1]).status_code==200
    assert guest.get(path).json()=={'unread':0}
    guest.put('/api/modules',json={'enabled':{'chat':False}})
    assert guest.get(path).status_code==403
    guest.put('/api/modules',json={'enabled':{'chat':True,'family_photos':False}})
    assert guest.get(path).status_code==403
    assert grant(c,uid,[G1]).status_code==200
    assert guest.get(path).status_code==200
    assert grant(c,uid,[],False).status_code==200
    assert guest.get(path).status_code==403


def test_chat_toggle_is_independent_of_gallery_access(connected):
    c,_,_=owner();enable(c)
    c.put('/api/modules',json={'enabled':{'chat':False}})
    assert c.get('/api/family-photos/groups/'+G1+'/media').status_code==200
    assert c.get('/api/family-photos/groups/'+G1+'/messages').status_code==403


def test_imported_archive_obeys_group_grants_and_rejects_sending(connected):
    _,sent=connected
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    assert grant(c,uid,[EXPORT]).status_code==200
    enable(guest)
    items=guest.get('/api/family-photos/groups').json()['items']
    assert [g['id'] for g in items]==[EXPORT] and items[0]['can_send'] is False
    assert guest.get('/api/family-photos/groups/'+EXPORT+'/messages').status_code==200
    response=guest.get('/api/family-photos/groups/'+EXPORT+'/media/'+FILE)
    assert response.status_code==206 and response.content==b'video-range'
    assert guest.post('/api/family-photos/groups/'+EXPORT+'/messages',json={'text':'Hello','client_id':'offline-1'}).status_code==403
    assert not sent
    assert guest.get('/api/family-photos/groups/'+G1+'/messages').status_code==403
    assert grant(c,uid,[],False).status_code==200
    assert guest.get('/api/family-photos/groups/'+EXPORT+'/media/'+FILE).status_code==403


def test_group_avatar_uses_same_grants_as_gallery(connected):
    bridge,_=connected
    original=bridge.client
    def client_with_avatar(timeout=5):
        def handler(request):
            if request.url.path.endswith('/avatar'):
                assert request.url.params['v']=='photo-1'
                return httpx.Response(200,content=b'jpeg-fixture',headers={'Content-Type':'image/jpeg'})
            with original(timeout) as client:return client.send(request)
        return httpx.Client(base_url=bridge.url+'/',transport=httpx.MockTransport(handler),headers={'Authorization':'Bearer '+bridge.token})
    bridge.client=client_with_avatar
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id'];grant(c,uid,[G1]);enable(guest)
    response=guest.get('/api/family-photos/groups/'+G1+'/avatar?v=photo-1')
    assert response.status_code==200 and response.content==b'jpeg-fixture'
    assert response.headers['cache-control']=='no-store'
    assert guest.get('/api/family-photos/groups/'+G2+'/avatar?v=photo-1').status_code==403
    grant(c,uid,[],False)
    assert guest.get('/api/family-photos/groups/'+G1+'/avatar?v=photo-1').status_code==403


def test_archive_storage_is_owner_only_even_when_module_is_disabled(connected,monkeypatch):
    bridge,_=connected;c,_,_=owner();g,_=member(c)
    payload=dict(objects_bytes=100,database_bytes=20,total_bytes=120,media_bytes=70,
                 thumbnail_bytes=10,message_bytes=10,avatar_bytes=5,other_bytes=5,objects=8,checked_at=123)
    calls=[]
    def fetch(path):
        calls.append(path);return payload|{'bucket':'must-not-leak','prefix':'private','token':'secret'}
    monkeypatch.setattr(bridge,'fetch',fetch)
    assert g.get('/api/family-photos/admin/storage').status_code==403
    assert client().get('/api/family-photos/admin/storage').status_code==401
    assert not calls
    result=c.get('/api/family-photos/admin/storage');assert result.status_code==200
    assert result.json()==dict(configured=True,available=True,**payload)
    assert calls==['v1/storage']
    monkeypatch.setattr(bridge,'fetch',lambda path:{'objects_bytes':True})
    assert c.get('/api/family-photos/admin/storage').status_code==503
    monkeypatch.setattr(bridge,'url','')
    assert c.get('/api/family-photos/admin/storage').json()=={'configured':False,'available':False}


def test_sync_progress_is_group_scoped_opt_in_and_cleanup_is_owner_only(connected):
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    enable(c)
    assert c.get('/api/family-photos/groups/'+G1+'/sync').json()['cleanup']['pending']==12
    assert guest.get('/api/family-photos/groups/'+G1+'/sync').status_code==403
    assert grant(c,uid,[G1]).status_code==200
    own=guest.get('/api/family-photos/groups/'+G1+'/sync')
    assert own.status_code==200 and own.json()['current']['name']=='family.mp4'
    assert 'cleanup' not in own.json() and own.headers['cache-control']=='no-store'
    assert guest.get('/api/family-photos/groups/'+G2+'/sync').status_code==403
    assert guest.put('/api/modules',json={'enabled':{'family_photos':False}}).status_code==200
    assert guest.get('/api/family-photos/groups/'+G1+'/sync').status_code==403
    assert grant(c,uid,[G1]).status_code==200
    assert grant(c,uid,[],False).status_code==200
    assert guest.get('/api/family-photos/groups/'+G1+'/sync').status_code==403


def test_older_archive_reactions_stay_local_but_require_write_grant(connected,monkeypatch):
    bridge,sent=connected
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id'];grant(c,uid,[G1]);enable(guest)
    original=bridge.fetch
    def fetch(path,method='GET',data=None):
        if path==f'v1/groups/{G1}/messages/1':return {'id':1,'media':[],'reactions':[]}
        return original(path,method,data)
    monkeypatch.setattr(bridge,'fetch',fetch)
    path=f'/api/family-photos/groups/{G1}/messages/1/reactions'
    assert guest.post(path,json={'emoji':'🔥'}).status_code==403
    assert c.put('/api/family-photos/admin/users/'+uid,json={'enabled':True,'groups':[G1],'write_groups':[G1]}).status_code==200
    response=guest.post(path,json={'emoji':'🔥'});assert response.status_code==200,response.text
    assert response.json()['reactions'][0]['people'][0]['id']==uid and sent==[]
    message=guest.get(f'/api/family-photos/groups/{G1}/messages/1').json()
    assert message['site_reactions'][0]['source']=='site'
    assert guest.post(path,json={'emoji':'🔥','user_id':'owner'}).status_code==422
    assert guest.post(f'/api/family-photos/groups/{G2}/messages/1/reactions',json={'emoji':'🔥'}).status_code==403
    grant(c,uid,[],False)
    assert guest.post(path,json={'emoji':'🔥'}).status_code==403


def test_sender_avatar_and_inline_emoji_proxy_are_group_gated(connected,monkeypatch):
    bridge,_=connected
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id'];grant(c,uid,[G1]);enable(guest)
    def handler(request):
        if '/people/' in request.url.path:return httpx.Response(200,content=b'avatar',headers={'Content-Type':'image/jpeg'})
        return httpx.Response(200,content=b'emoji',headers={'Content-Type':'image/webp'})
    monkeypatch.setattr(bridge,'client',lambda timeout=5:httpx.Client(base_url=bridge.url+'/',transport=httpx.MockTransport(handler)))
    for suffix in ('people/42/avatar?v=1','emoji/123'):
        response=guest.get(f'/api/family-photos/groups/{G1}/'+suffix);assert response.status_code==200,response.text
        assert response.headers['cache-control']=='no-store'
        assert guest.get(f'/api/family-photos/groups/{G2}/'+suffix).status_code==403
    assert guest.get(f'/api/family-photos/groups/{G1}/people/x/avatar').status_code==404


def test_readonly_archive_reactions_rejected_before_local_storage(connected,monkeypatch):
    bridge,_=connected;c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id'];grant(c,uid,[G1,EXPORT]);enable(guest)
    original=bridge.fetch
    def fetch(path,method='GET',data=None):
        if path=='v1/groups':return {'items':[{'id':G1,'can_send':False},{'id':EXPORT,'kind':'export','can_send':False}]}
        return original(path,method,data)
    monkeypatch.setattr(bridge,'fetch',fetch)
    for gid in (G1,EXPORT):
        response=guest.post(f'/api/family-photos/groups/{gid}/messages/1/reactions',json={'emoji':'🔥'})
        assert response.status_code==403 and 'только для чтения' in response.text


def test_voice_preview_rewrites_scoped_url_and_forwards_audio_range(connected,monkeypatch):
    bridge,_=connected
    admin,_,_=owner();enable(admin)
    original=bridge.client
    forwarded=[]
    def handler(request):
        if request.url.path.endswith('/messages'):
            return httpx.Response(200,json={'items':[{'id':7,'text':'','content_type':'voice','media':[{'id':FILE,'kind':'voice','mime':'audio/ogg','state':'ready','has_audio_preview':True}]}],'more':False})
        if request.url.path.endswith('/media/'+FILE):
            forwarded.append(request)
            return httpx.Response(206,content=b'aac',headers={'Content-Type':'audio/mp4','Content-Range':'bytes 0-2/12','Accept-Ranges':'bytes'})
        with original() as client:return client.send(request)
    monkeypatch.setattr(bridge,'client',lambda timeout=5:httpx.Client(base_url=bridge.url+'/',transport=httpx.MockTransport(handler),headers={'Authorization':'Bearer '+bridge.token}))
    data=admin.get('/api/family-photos/groups/'+G1+'/messages').json()
    voice=data['items'][0]['media'][0]
    assert voice['playback_url']=='/api/family-photos/groups/'+G1+'/media/'+FILE+'?audio=1'
    response=admin.get(voice['playback_url'],headers={'Range':'bytes=0-2'})
    assert response.status_code==206 and response.content==b'aac'
    assert response.headers['Content-Type'].startswith('audio/mp4') and response.headers['Cache-Control']=='no-store'
    assert forwarded[-1].url.params['audio']=='1' and forwarded[-1].headers['Range']=='bytes=0-2'
    assert client().get(voice['playback_url']).status_code==401
    guest,_=member(admin)
    assert guest.get(voice['playback_url']).status_code==403
