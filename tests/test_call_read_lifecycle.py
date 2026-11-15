"""Read budgets, expiry privacy and account-scoped call subscriptions."""
import json
import pytest
from app.database import Connection
from app.modules.calls import cleanup_active_calls
from tests.test_security import reset,member
from app import main as m
from tests.test_voice_calls import peers,begin,call_transport
from tests.test_social import next_event


def test_idle_and_live_poll_do_not_acquire_writer_or_scan_foreign_calls(monkeypatch):
    a,b,sid=peers();idle,_=member(a,'idle-reader');call,data=begin(a,sid)
    # Session activity has its independent occasional write; warm it first.
    idle.get('/api/calls/current?device=idle-device-123456')
    a.get('/api/calls/current?device='+data['device'])
    queries=[];original=Connection.execute
    def execute(c,query,params=()):
        queries.append((query,params));return original(c,query,params)
    monkeypatch.setattr(Connection,'execute',execute)
    assert idle.get('/api/calls/current?device=idle-device-123456').json()=={'call':None}
    assert a.get('/api/calls/current?device='+data['device']).json()['call']['id']==call['id']
    assert not any(q.strip().upper().startswith(('BEGIN','UPDATE CALLS','DELETE FROM CALLS')) for q,_ in queries), [(q,queries[i+1][0] if i+1<len(queries) else '') for i,(q,_) in enumerate(queries) if q.strip().upper().startswith(('BEGIN','UPDATE CALLS','DELETE FROM CALLS'))]
    reads=[(q,p) for q,p in queries if 'FROM calls' in q]
    assert len(reads)==2 and all('caller_id=? OR recipient_id=?' in q and p[0]==p[1] for q,p in reads)


def test_scoped_expiry_leaves_other_users_to_maintenance(monkeypatch):
    a,b,sid=peers();idle,_=member(a,'idle-reader');call,data=begin(a,sid);stamp=m.now()+60
    monkeypatch.setattr(m,'now',lambda:stamp)
    # An unrelated idle read neither loads nor modifies another call.
    assert idle.get('/api/calls/current?device=idle-device-123456').json()=={'call':None}
    with m.db() as c:assert c.execute('SELECT status FROM calls WHERE id=?',(call['id'],)).fetchone()[0]=='ringing'
    cleanup_active_calls(m.db,stamp)
    with m.db() as c:
        row=c.execute('SELECT status,payload FROM calls WHERE id=?',(call['id'],)).fetchone()
        assert row['status']=='missed' and row['payload']=='{}'
    assert a.get('/api/calls/current?device='+data['device']+'&call_id='+call['id']).json()['call']['status']=='missed'


def test_maintenance_preserves_healthy_heartbeats_and_exact_expiry_boundaries(monkeypatch):
    a,b,sid=peers();stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    call,data=begin(a,sid)
    b.post('/api/calls/'+call['id']+'/accept',json={'device':'recipient-device-123456'})
    cleanup_active_calls(m.db,stamp+39)
    with m.db() as c:assert c.execute('SELECT status FROM calls WHERE id=?',(call['id'],)).fetchone()[0]=='connecting'
    cleanup_active_calls(m.db,stamp+40)
    with m.db() as c:
        row=c.execute('SELECT status,payload FROM calls WHERE id=?',(call['id'],)).fetchone();assert row['status']=='failed' and row['payload']=='{}'


def test_inbox_announces_call_changes_and_proves_liveness_without_private_payloads():
    a,b,sid=peers()
    with b.websocket_connect('/api/live/inbox') as ws:
        ws.send_json({'type':'auth','csrf':b.headers['x-csrf-token']})
        assert next_event(ws,'ready')['call_events'] is True
        ws.send_json({'type':'ping'});assert next_event(ws,'pong')=={'type':'pong'}
        call,_=begin(a,sid)
        assert next_event(ws,'calls_changed')=={'type':'calls_changed'}
        b.post('/api/calls/'+call['id']+'/end',json={'reason':'decline'})
        assert next_event(ws,'calls_changed')=={'type':'calls_changed'}


def test_inbox_includes_federated_incoming_state_without_returning_peer_payload():
    a,b,sid=peers();uid=b.get('/api/me').json()['id'];stamp=m.now()
    with b.websocket_connect('/api/live/inbox') as ws:
        ws.send_json({'type':'auth','csrf':b.headers['x-csrf-token']});assert next_event(ws,'ready')['call_events']
        with m.db() as c:
            c.execute('INSERT INTO federation_calls(id,caller_server_id,caller_id,recipient_id,caller_device,status,created,payload) VALUES(?,?,?,?,?,?,?,?)',('synthetic-remote-call','a'*32,'b'*32,uid,'c'*64,'ringing',stamp,json.dumps({'private_synthetic_sdp':'never-in-inbox'})))
        assert next_event(ws,'calls_changed')=={'type':'calls_changed'}
        with m.db() as c:c.execute('DELETE FROM federation_calls WHERE id=?',('synthetic-remote-call',))
        assert next_event(ws,'calls_changed')=={'type':'calls_changed'}
