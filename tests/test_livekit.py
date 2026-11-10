"""Scoped media grants, backward compatibility, revocation and bounded health I/O."""
import json
import secrets
import uuid

import httpx
import jwt
import pytest

from tests.test_security import owner, client, reset
from app import livekit, main as m

SECRET='test-only-livekit-secret-'*3


@pytest.fixture
def sfu(monkeypatch):
    monkeypatch.setenv('CALL_LIVEKIT_ENABLED','true')
    monkeypatch.setenv('CALL_LIVEKIT_URL','wss://media.example')
    monkeypatch.setenv('CALL_LIVEKIT_API_URL','https://media.example')
    monkeypatch.setenv('CALL_LIVEKIT_API_KEY','test-key')
    monkeypatch.setenv('CALL_LIVEKIT_API_SECRET',SECRET)
    monkeypatch.setattr('app.modules.calls.turn_available',lambda:False)
    livekit._health.update(key=None,until=0,ok=False)
    livekit._membership.clear()
    events=[];remote={};failing=set()
    def handle(request):
        method=request.url.path.rsplit('/',1)[-1];data=json.loads(request.content)
        grant=jwt.decode(request.headers['authorization'][7:],SECRET,algorithms=['HS256'])['video']
        events.append((method,data,grant))
        # A network operation must not hold the application write transaction.
        with m.db() as c:c.execute('BEGIN IMMEDIATE')
        if method in failing:return httpx.Response(503,json={'code':'unavailable'})
        if method=='CreateRoom':remote.setdefault(data['name'],[]);result={}
        elif method=='DeleteRoom':remote.pop(data['room'],None);result={}
        elif method=='RemoveParticipant':remote[data['room']]=[p for p in remote.get(data['room'],[]) if p['identity']!=data['identity']];result={}
        elif method=='ListParticipants':result={'participants':remote.get(data['room'],[])}
        else:result={'rooms':[{'name':name,'num_participants':len(people)} for name,people in remote.items() if not data.get('names') or name in data['names']]}
        return httpx.Response(200,json=result)
    monkeypatch.setattr(livekit,'_http',httpx.Client(transport=httpx.MockTransport(handle)))
    return events,remote,failing


def create(a,mode='auto'):
    value=a.post('/api/calls/guest-rooms',json={'id':uuid.uuid4().hex,'transport':mode})
    assert value.status_code==200,value.text
    return value.json()


def enter(room,*,supports=True):
    c=client();data={'id':uuid.uuid4().hex,'nonce':secrets.token_urlsafe(32),'name':'Test guest'}
    if supports:data['transport']='livekit'
    result=c.post('/api/guest-calls/'+room['url'].split('/')[-1]+'/join',json=data)
    if result.status_code==200:c.headers['authorization']='Bearer '+result.json()['credential']
    return c,result,data


def test_group_works_without_turn_with_scoped_grant_and_no_secret_in_state(sfu):
    a,_,_=owner();room=create(a);assert room['transport']=='livekit'
    c,result,data=enter(room);assert result.status_code==200,result.text
    access=result.json()['livekit'];grant=jwt.decode(access['token'],SECRET,algorithms=['HS256'])
    assert grant['sub']==data['id'] and grant['video']=={
        'roomJoin':True,'room':livekit.room_name(room['id']),'canPublish':True,'canSubscribe':True,
        'canPublishData':False,'canPublishSources':['microphone']}
    assert grant['exp']-grant['nbf']<=95
    config=a.get('/api/calls/config').json()
    assert not config['available'] and config['guest_available'] and config['livekit_available']
    csp=a.get('/').headers['content-security-policy']
    assert "connect-src 'self' wss://media.example https://media.example;" in csp
    assert '*' not in csp and SECRET not in csp
    state=c.post('/api/guest-calls/rooms/'+room['id']+'/sync',json={'muted':False}).json()
    assert state['peers']==[] and 'livekit' not in state and SECRET not in json.dumps(state)
    with m.db() as db:
        assert 'eyJ' not in db.execute('SELECT payload FROM guest_call_participants WHERE id=?',(data['id'],)).fetchone()[0]
        assert db.execute('SELECT COUNT(*) FROM guest_call_links').fetchone()[0]==0
    assert c.post('/api/guest-calls/rooms/'+room['id']+'/sync',json={'muted':False,'signals':[{'peer_id':uuid.uuid4().hex}]}).status_code==422


def test_old_clients_cannot_create_or_join_and_existing_legacy_sessions_can_finish(sfu,monkeypatch):
    a,_,_=owner();room=create(a);c,_,_=enter(room)
    _,result,_=enter(room,supports=False);assert result.status_code==422 and 'браузере' in result.text
    for transport in (None,'webrtc'):
        body={'id':uuid.uuid4().hex}
        if transport:body['transport']=transport
        assert a.post('/api/calls/guest-rooms',json=body).status_code==422
    with m.db() as db:
        row=db.execute('SELECT payload FROM guest_call_rooms WHERE id=?',(room['id'],)).fetchone();payload=json.loads(row['payload']);payload['transport']='webrtc'
        db.execute('UPDATE guest_call_rooms SET payload=? WHERE id=?',(json.dumps(payload),room['id']))
        db.execute('DELETE FROM app_settings WHERE key=?',(livekit.INDEX+room['id'],))
    monkeypatch.setenv('CALL_LIVEKIT_ENABLED','false')
    assert c.post('/api/guest-calls/rooms/'+room['id']+'/sync',json={'muted':False}).status_code==200
    # Both old and refreshed clients are rejected by the server, not just the UI.
    for supports in (True,False):
        guest=client();body={'id':uuid.uuid4().hex,'nonce':secrets.token_urlsafe(32),'name':'New'}
        if supports:body['transport']='livekit'
        response=guest.post('/api/guest-calls/'+room['url'].split('/')[-1]+'/join',json=body)
        assert response.status_code in (409,503)
    info=c.get('/api/guest-calls/'+room['url'].split('/')[-1]).json()
    assert info['join_disabled_reason']
    english=c.get('/api/guest-calls/'+room['url'].split('/')[-1],headers={'accept-language':'en'}).json()
    assert 'previous format' in english['join_disabled_reason']
    assert c.post('/api/guest-calls/rooms/'+room['id']+'/leave').status_code==200


@pytest.mark.parametrize('turn,sfu_enabled',[(False,False),(True,False),(False,True),(True,True)])
def test_capabilities_and_group_creation_require_sfu_without_mesh_fallback(sfu,monkeypatch,turn,sfu_enabled):
    monkeypatch.setattr('app.modules.calls.turn_available',lambda:turn)
    monkeypatch.setenv('CALL_LIVEKIT_ENABLED','true' if sfu_enabled else 'false')
    # A deprecated preference cannot bypass the server requirement.
    monkeypatch.setenv('CALL_GUEST_TRANSPORT','webrtc')
    a,_,_=owner();config=a.get('/api/calls/config').json()
    assert config['available'] is turn and config['guest_available'] is sfu_enabled
    assert config['guest_transports']==['livekit']
    response=a.post('/api/calls/guest-rooms',json={'id':uuid.uuid4().hex,'transport':'auto'})
    assert response.status_code==(200 if sfu_enabled else 503)
    if sfu_enabled:assert response.json()['transport']=='livekit'
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM guest_call_rooms').fetchone()[0]==int(sfu_enabled)


def test_sfu_outage_blocks_new_creation_and_join_without_changing_existing_room(sfu):
    _,_,failing=sfu;a,_,_=owner();room=create(a);c,_,_=enter(room)
    failing.add('ListRooms');livekit._health.update(key=None,until=0,ok=False)
    assert a.post('/api/calls/guest-rooms',json={'id':uuid.uuid4().hex,'transport':'livekit'}).status_code==503
    assert enter(room)[1].status_code==503
    assert c.post('/api/guest-calls/rooms/'+room['id']+'/sync',json={'muted':False}).status_code==200
    with m.db() as db:
        rows=list(db.execute('SELECT payload FROM guest_call_rooms'));assert len(rows)==1
        assert json.loads(rows[0]['payload'])['transport']=='livekit'


def test_leave_revokes_media_and_failed_revocation_is_reconciled(sfu):
    events,remote,failing=sfu;a,_,_=owner();room=create(a);c,result,data=enter(room)
    name=livekit.room_name(room['id']);remote[name]=[{'identity':data['id']}]
    failing.add('RemoveParticipant')
    assert c.post('/api/guest-calls/rooms/'+room['id']+'/leave').status_code==200
    assert c.post('/api/guest-calls/rooms/'+room['id']+'/sync',json={'muted':False}).status_code==401
    assert remote[name]
    failing.clear();livekit.reconcile(m.db,m.now());assert remote[name]==[]


def test_end_stops_joins_and_retries_cleanup_without_touching_other_app_rooms(sfu):
    events,remote,failing=sfu;a,_,_=owner();room=create(a);c,_,_=enter(room)
    remote['matrix-room']=[{'identity':'another-app'}]
    failing.add('DeleteRoom')
    assert a.post('/api/calls/guest-rooms/'+room['id']+'/end').status_code==503
    assert enter(room)[1].status_code==410
    assert c.post('/api/guest-calls/rooms/'+room['id']+'/sync',json={'muted':False}).status_code==410
    failing.clear();livekit.reconcile(m.db,m.now())
    assert livekit.room_name(room['id']) not in remote and remote['matrix-room']
    with m.db() as db:assert db.execute('SELECT 1 FROM guest_call_rooms WHERE id=?',(room['id'],)).fetchone() is None


def test_expiry_revokes_orphan_media_even_if_local_row_is_already_gone(sfu):
    _,remote,_=sfu;a,_,_=owner();room=create(a);enter(room)
    with m.db() as db:db.execute('DELETE FROM guest_call_rooms WHERE id=?',(room['id'],))
    livekit.reconcile(m.db,m.now());assert livekit.room_name(room['id']) not in remote


def test_health_is_cached_and_disabled_feature_performs_no_network(sfu,monkeypatch):
    events,_,_=sfu
    for _ in range(4):assert livekit.available()
    assert len(events)==1
    monkeypatch.setenv('CALL_LIVEKIT_ENABLED','false')
    assert not livekit.available()
    assert len(events)==1


def test_unchanged_rooms_do_not_query_each_participant_list(sfu):
    events,remote,_=sfu;a,_,_=owner();room=create(a);_,_,data=enter(room)
    remote[livekit.room_name(room['id'])]=[{'identity':data['id']}]
    livekit.reconcile(m.db,m.now());events.clear()
    livekit.reconcile(m.db,m.now())
    assert [event[0] for event in events]==['ListRooms']


def test_appearance_is_saved_even_if_metadata_notification_must_wait_for_heartbeat(sfu):
    events,_,failing=sfu;a,_,_=owner();room=create(a);enter(room)
    failing.add('UpdateRoomMetadata')
    response=a.put('/api/calls/guest-rooms/'+room['id'],json={'title':'New name'})
    assert response.status_code==200 and response.json()['title']=='New name'
    assert any(method=='UpdateRoomMetadata' for method,_,_ in events)


def test_incomplete_or_unsafe_settings_disable_feature(sfu,monkeypatch):
    for url in ('ws://media.example','wss://u:p@media.example','wss://media.example/path','wss://media.example?token=a','wss://media.example; script-src *','wss://[broken','wss://media.example:99999','wss://media.\nexample'):
        monkeypatch.setenv('CALL_LIVEKIT_URL',url);assert livekit.settings() is None
    monkeypatch.setenv('CALL_LIVEKIT_URL','wss://media.example')
    monkeypatch.setenv('CALL_LIVEKIT_API_SECRET','short');assert livekit.settings() is None
