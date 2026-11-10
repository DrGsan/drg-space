"""Real signaling state, privacy boundaries and multi-device arbitration."""
import json
import secrets
import pytest
from tests.test_security import owner,member,client,reset
from app import main as m
from app.modules.calls import purge_calls


@pytest.fixture(autouse=True)
def call_transport(monkeypatch):
    monkeypatch.setattr('app.modules.calls.turn_available',lambda:True)

def peers():
    a,_,_=owner();b,_=member(a);q=a.post('/api/social/friends',json={'login':'friend'}).json();sid=b.post('/api/social/requests/'+q['id']+'/accept').json()['space_id'];return a,b,sid

def begin(a,sid,**kw):
    data={'id':secrets.token_urlsafe(18),'device':'caller-device-123456','space_id':sid,'sdp':'v=0\r\no=voice-offer'}|kw
    r=a.post('/api/calls',json=data);assert r.status_code==200,r.text;return r.json(),data

def test_private_voice_call_signals_and_history():
    a,b,sid=peers();call,data=begin(a,sid);cid=call['id']
    assert call['owned'] and call['outgoing'] and call['status']=='ringing'
    assert a.post('/api/calls',json=data).json()['id']==cid
    incoming=b.get('/api/calls/current?device=recipient-device-123456').json()['call']
    assert not incoming['outgoing'] and not incoming['owned'] and 'sdp' not in incoming
    assert a.post('/api/calls/'+cid+'/accept',json={'device':'recipient-device-123456'}).status_code==403
    accepted=b.post('/api/calls/'+cid+'/accept',json={'device':'recipient-device-123456'}).json()
    assert accepted['status']=='connecting' and accepted['sdp']==data['sdp']
    assert b.post('/api/calls/'+cid+'/accept',json={'device':'another-device-123456'}).status_code==409
    assert b.post('/api/calls/'+cid+'/signal',json={'device':'another-device-123456'}).status_code==409
    candidate={'candidate':'candidate:1 1 udp 1 192.0.2.1 10000 typ host','sdpMid':'0','sdpMLineIndex':0}
    answer={'device':'recipient-device-123456','sdp':'v=0\r\no=voice-answer','candidates':[candidate]}
    assert b.post('/api/calls/'+cid+'/signal',json=answer).status_code==200
    assert b.post('/api/calls/'+cid+'/signal',json=answer).status_code==200
    remote=a.get('/api/calls/current?device='+data['device']+'&call_id='+cid).json()['call']
    assert remote['sdp']==answer['sdp'] and len(remote['candidates'])==1
    hidden=a.get('/api/calls/current?device=another-device-123456').json()['call']
    assert not hidden['owned'] and 'sdp' not in hidden and 'candidates' not in hidden
    assert a.post('/api/calls/'+cid+'/signal',json={'device':data['device'],'connected':True}).json()['status']=='active'
    ended=b.post('/api/calls/'+cid+'/end',json={}).json();assert ended['status']=='ended' and ended['connected']
    assert b.post('/api/calls/'+cid+'/end',json={}).json()['status']=='ended'
    with m.db() as c:assert json.loads(c.execute('SELECT payload FROM calls WHERE id=?',(cid,)).fetchone()[0])=={}
    history=a.get('/api/calls/history').json()['items'];assert history[0]['id']==cid and history[0]['outgoing'] and 'sdp' not in history[0]
    assert a.post('/api/calls/'+cid+'/signal',json={'device':data['device']}).status_code==409


def test_call_busy_foreign_access_and_module_permissions():
    a,b,sid=peers();c,_=member(a,'stranger');call,data=begin(a,sid);cid=call['id']
    assert c.get('/api/calls/current?device=stranger-device-123456&call_id='+cid).status_code==404
    assert c.post('/api/calls/'+cid+'/end',json={}).status_code==404
    assert c.get('/api/calls/history').json()['items']==[]
    busy=b.post('/api/calls',json=data|{'id':secrets.token_urlsafe(18),'device':'recipient-device-123456'});assert busy.status_code==409
    a.post('/api/calls/'+cid+'/end',json={})
    uid=b.get('/api/me').json()['id']
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]);settings.setdefault('modules',{})['chat']=False;db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    assert b.get('/api/calls/history').status_code==403
    assert a.post('/api/calls',json=data|{'id':secrets.token_urlsafe(18)}).status_code==409


def test_unanswered_and_abandoned_calls_expire(monkeypatch):
    a,b,sid=peers();call,data=begin(a,sid);stamp=m.now();cid=call['id']
    monkeypatch.setattr(m,'now',lambda:stamp+61)
    assert a.get('/api/calls/current?device='+data['device']+'&call_id='+cid).json()['call']['status']=='missed'
    assert b.post('/api/calls/'+cid+'/accept',json={'device':'recipient-device-123456'}).status_code==409
    call,data=begin(a,sid);cid=call['id'];b.post('/api/calls/'+cid+'/accept',json={'device':'recipient-device-123456'})
    monkeypatch.setattr(m,'now',lambda:stamp+110)
    assert a.get('/api/calls/current?device='+data['device']+'&call_id='+cid).json()['call']['status']=='failed'
    with m.db() as db:assert db.execute('SELECT payload FROM calls WHERE id=?',(cid,)).fetchone()[0]=='{}'


def test_decline_cancel_disconnect_and_security_policy():
    a,b,sid=peers();call,_=begin(a,sid)
    assert b.post('/api/calls/'+call['id']+'/end',json={'reason':'decline'}).json()['status']=='declined'
    call,_=begin(a,sid);assert a.post('/api/calls/'+call['id']+'/end',json={}).json()['status']=='cancelled'
    assert 'microphone=(self)' in a.get('/').headers['permissions-policy']
    family=a.post('/api/social/families',json={'name':'Family'}).json()['id']
    assert a.post('/api/calls',json={'id':secrets.token_urlsafe(18),'space_id':family,'device':'caller-device-123456','sdp':'offer'}).status_code==422
    del a.headers['x-csrf-token']
    assert a.post('/api/calls/'+call['id']+'/end',json={}).status_code==403


def test_turn_credentials_are_temporary_and_secret_stays_private(monkeypatch):
    import base64,hmac,hashlib
    a,b,sid=peers();secret='private-test-turn-secret-at-least-32-chars'
    monkeypatch.setenv('CALL_TURN_URLS','turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=tcp');monkeypatch.setenv('CALL_TURN_SECRET',secret)
    r=a.get('/api/calls/config');assert r.status_code==200,r.text
    config=r.json();assert config['relay_configured'] and secret not in r.text
    turn=config['iceServers'][-1];assert turn['credential']==base64.b64encode(hmac.new(secret.encode(),turn['username'].encode(),hashlib.sha1).digest()).decode()
    assert int(turn['username'].split(':')[0])>m.now()
    monkeypatch.setenv('CALL_TURN_SECRET','');assert a.get('/api/calls/config').status_code==503


def test_call_history_survives_encrypted_backup_and_restore():
    from tests.test_admin import backup,headers
    a,b,sid=peers();call,_=begin(a,sid);b.post('/api/calls/'+call['id']+'/end',json={'reason':'decline'})
    body=backup(a)
    with m.db() as db:db.execute('DELETE FROM calls')
    response=a.post('/api/admin/backups/restore',content=body,headers=headers())
    assert response.status_code==200,response.text
    with m.db() as db:
        saved=db.execute('SELECT * FROM calls WHERE id=?',(call['id'],)).fetchone();assert saved['status']=='declined' and saved['payload']=='{}'


def test_call_push_is_short_lived_and_never_delivers_after_hangup(monkeypatch):
    from tests.test_web_push import register
    a,b,sid=peers();monkeypatch.setattr(m.web_push,'enabled',True);register(b)
    sent=[];monkeypatch.setattr(m.web_push,'send',lambda subscription,payload,ttl:(sent.append((payload,ttl)) or (201,None)))
    call,_=begin(a,sid);assert m.web_push.deliver_one()
    payload,ttl=sent[0];assert payload['type']=='call' and payload['url']=='/#chat/'+sid and 1<=ttl<=60
    assert 'voice' not in payload['body'] and 'actor' not in payload
    a.post('/api/calls/'+call['id']+'/end',json={});call,_=begin(a,sid);a.post('/api/calls/'+call['id']+'/end',json={})
    assert m.web_push.deliver_one() and len(sent)==1


def test_calls_are_unavailable_when_coturn_cannot_be_reached(monkeypatch):
    a,b,sid=peers();monkeypatch.setattr('app.modules.calls.turn_available',lambda:False)
    assert a.get('/api/calls/config').json()['available'] is False
    r=a.post('/api/calls',json={'id':secrets.token_urlsafe(18),'space_id':sid,'device':'caller-device-123456','sdp':'offer'})
    assert r.status_code==503 and a.get('/api/calls/history').json()['items']==[]


def test_media_state_is_bounded_owned_private_and_erased_on_finish():
    from app.modules.calls import validate_call_archive
    a,b,sid=peers();call,data=begin(a,sid);cid=call['id'];device='recipient-device-123456'
    b.post('/api/calls/'+cid+'/accept',json={'device':device})
    state={'muted':True,'camera':True,'screen':True}
    result=a.post('/api/calls/'+cid+'/signal',json={'device':data['device'],'media':state});assert result.status_code==200,result.text
    assert b.get('/api/calls/current?device='+device).json()['call']['media']==state
    assert 'media' not in b.get('/api/calls/current?device=other-device-123456').json()['call']
    assert b.post('/api/calls/'+cid+'/signal',json={'device':'other-device-123456','media':state}).status_code==409
    for bad in ({'muted':'yes'},{'screen':True,'window':'secret'},{'camera':1}):
        assert a.post('/api/calls/'+cid+'/signal',json={'device':data['device'],'media':bad}).status_code==422
    with m.db() as db:validate_call_archive(db,lambda:pytest.fail('Invalid live media archive'))
    a.post('/api/calls/'+cid+'/end',json={})
    with m.db() as db:assert db.execute('SELECT payload FROM calls WHERE id=?',(cid,)).fetchone()[0]=='{}'
    policy=a.get('/').headers['permissions-policy'];assert 'camera=(self)' in policy and 'display-capture=(self)' in policy


def test_call_history_expires_at_three_calendar_months_in_api_and_maintenance(monkeypatch):
    from datetime import datetime,timezone
    from app.chat_storage import cutoff
    from app.retention import cleanup
    stamp=int(datetime(2026,5,31,12,tzinfo=timezone.utc).timestamp());monkeypatch.setattr(m,'now',lambda:stamp)
    a,b,sid=peers();ids=[]
    for age in (-1,0,1):
        call,_=begin(a,sid);a.post('/api/calls/'+call['id']+'/end',json={});ids.append(call['id'])
        with m.db() as db:db.execute('UPDATE calls SET created=?,ended=? WHERE id=?',(cutoff(stamp)+age,cutoff(stamp)+age+10,call['id']))
    assert [row['id'] for row in a.get('/api/calls/history').json()['items']]==[ids[-1]]
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
    monkeypatch.setattr(m,'now',lambda:stamp+2);cleanup(m.db,stamp+2)
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==0
