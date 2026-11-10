"""Bot operations retain the authenticated account and enforce independent grants."""
import json
import httpx
from tests.test_security import owner,member,reset
from app import main as m
from tests.test_family_photos import connected,enable,grant,G1,G2,EXPORT


def test_write_grant_is_separate_subset_and_owner_only(connected):
    admin,_,_=owner();guest,_=member(admin);uid=guest.get('/api/me').json()['id'];enable(admin)
    grant(admin,uid,[G1]);enable(guest)
    assert not guest.get('/api/family-photos/groups').json()['items'][0]['can_send']
    endpoint='/api/family-photos/admin/users/'+uid
    data={'enabled':True,'groups':[G1],'write_groups':[G2]}
    assert admin.put(endpoint,json=data).status_code==422
    assert guest.put(endpoint,json=data).status_code==403
    assert admin.put(endpoint,json={'enabled':True,'groups':[EXPORT],'write_groups':[EXPORT]}).status_code==422
    assert admin.put(endpoint,json={'enabled':True,'groups':[G1],'write_groups':[G1]}).status_code==200
    assert guest.get('/api/family-photos/groups').json()['items'][0]['can_send']
    assert admin.put(endpoint,json={'enabled':True,'groups':[G1],'write_groups':[]}).status_code==200
    assert guest.get('/api/family-photos/groups/'+G1+'/messages').status_code==200
    assert guest.post('/api/family-photos/groups/'+G1+'/messages',json={'text':'Нет','client_id':'revoked'}).status_code==403


def test_public_profile_name_and_session_actor_are_used_not_client_input(connected):
    bridge,sent=connected;admin,_,_=owner();enable(admin)
    assert admin.put('/api/account/profile',json={'display_name':'Александр'}).status_code==200
    response=admin.post('/api/family-photos/groups/'+G1+'/messages?actor_id='+'b'*32,json={'text':'Текст','client_id':'identity','attachments':[]})
    assert response.status_code==200,response.text
    assert sent[0]['author']=='Александр' and sent[0]['actor_id']==admin.get('/api/me').json()['id']
    assert admin.post('/api/family-photos/groups/'+G1+'/messages',json={'text':'Подмена','client_id':'forged','author':'Другой'}).status_code==422


def test_upload_download_and_edit_are_scoped_to_session_and_latest_rights(connected,monkeypatch):
    bridge,_=connected;admin,_,_=owner();guest,_=member(admin);uid=guest.get('/api/me').json()['id']
    assert admin.put('/api/family-photos/admin/users/'+uid,json={'enabled':True,'groups':[G1],'write_groups':[G1]}).status_code==200
    original=bridge.client;calls=[];fid='f'*32
    def handler(request):
        calls.append(request)
        if '/site-uploads/' in request.url.path:
            if request.method=='PUT':
                assert request.read()==b'file bytes'
                return httpx.Response(201,json={'id':fid,'site_upload':True,'name':'notes.txt','kind':'file','url':'/v1/file','size':10})
            if request.method=='DELETE':return httpx.Response(200,json={'ok':True})
            return httpx.Response(206,content=b'ile',headers={'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename=notes.txt','Content-Range':'bytes 1-3/10'})
        if '/site-messages/' in request.url.path:return httpx.Response(403,json={'detail':'not_author'})
        if request.url.path=='/v1/site-permissions':return httpx.Response(200,json={'ok':True})
        with original() as client:return client.send(request)
    monkeypatch.setattr(bridge,'client',lambda timeout=5:httpx.Client(base_url=bridge.url+'/',headers={'Authorization':'Bearer '+bridge.token},transport=httpx.MockTransport(handler)))
    url=f'/api/family-photos/groups/{G1}/site-uploads/{fid}'
    response=guest.put(url+'?name=notes.txt&mode=file&actor_id=owner',content=b'file bytes')
    assert response.status_code==200,response.text
    assert response.json()['url']==url
    uploaded=next(r for r in calls if r.method=='PUT' and '/site-uploads/' in r.url.path)
    assert uploaded.url.params['actor_id']==uid
    response=guest.get(url,headers={'Range':'bytes=1-3'})
    assert response.content==b'ile' and response.status_code==206 and response.headers['cache-control']=='no-store'
    assert calls[-1].headers['Range']=='bytes=1-3'
    response=guest.put(f'/api/family-photos/groups/{G1}/messages/42?actor_id=owner',json={'text':'Новое','revision':0,'client_id':'edit-1'})
    assert response.status_code==403 and 'свои' in response.text
    assert calls[-1].url.params['actor_id']==uid
    assert guest.get(url.replace(G1,G2)).status_code==403
    assert admin.put('/api/family-photos/admin/users/'+uid,json={'enabled':True,'groups':[G1],'write_groups':[]}).status_code==200
    assert guest.put(url+'?name=notes.txt',content=b'file bytes').status_code==403
    assert guest.delete(url).status_code==200
    assert guest.get(url).status_code==206


def test_permission_snapshot_omits_hidden_module_accounts_and_reflects_revocation(connected,monkeypatch):
    bridge,_=connected;admin,_,_=owner();guest,_=member(admin);uid=guest.get('/api/me').json()['id'];enable(admin)
    snapshots=[];original=bridge.bot_fetch
    def capture(path,method='GET',body=None):
        if path=='v1/site-permissions':snapshots.append(body)
        return original(path,method,body)
    monkeypatch.setattr(bridge,'bot_fetch',capture)
    endpoint='/api/family-photos/admin/users/'+uid
    admin.put(endpoint,json={'enabled':True,'groups':[G1],'write_groups':[G1]})
    assert snapshots[-1]['actors'][uid]==[G1]
    assert snapshots[-1]['actors'][admin.get('/api/me').json()['id']]==['*']
    guest.put('/api/modules',json={'enabled':{'chat':False}});bridge.sync_send_permissions()
    assert uid not in snapshots[-1]['actors']
    admin.put(endpoint,json={'enabled':False,'groups':[]})
    assert uid not in snapshots[-1]['actors'] and snapshots[-1]['revision']>snapshots[0]['revision']


def test_recording_metadata_forwarding_is_bounded_and_scoped(connected,monkeypatch):
    bridge,_=connected;admin,_,_=owner();enable(admin);calls=[];fid='e'*32
    original=bridge.client
    def handler(request):
        if '/site-uploads/' in request.url.path:
            calls.append(request)
            assert request.url.params['actor_id']==admin.get('/api/me').json()['id']
            assert json.loads(request.headers['X-DrG-Recording'])=={'kind':'video_note','duration_ms':1200,'waveform':[]}
            return httpx.Response(201,json={'id':fid,'site_upload':True,'name':'circle.mp4','kind':'video_note','duration':1.2,'url':'/v1/file','size':10})
        with original() as client:return client.send(request)
    monkeypatch.setattr(bridge,'client',lambda timeout=5:httpx.Client(base_url=bridge.url+'/',headers={'Authorization':'Bearer '+bridge.token},transport=httpx.MockTransport(handler)))
    url=f'/api/family-photos/groups/{G1}/site-uploads/{fid}?name=circle.mp4&mode=preview'
    info=json.dumps({'kind':'video_note','duration_ms':1200})
    response=admin.put(url,content=b'movie bytes',headers={'X-DrG-Recording':info})
    assert response.status_code==200 and response.json()['duration']==1.2
    assert len(calls)==1
    for header in ['{}',json.dumps({'kind':'video_note','duration_ms':60001}),'x'*2049]:
        assert admin.put(url,content=b'bad',headers={'X-DrG-Recording':header}).status_code==422
    assert len(calls)==1
