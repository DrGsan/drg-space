"""Exercise emergency isolation across HTTP, live sockets, refresh and CLI."""
import json
import os
import subprocess

import pyotp
import pytest

from app.http import Request, WebSocketDisconnect
from app.security_monitor import MODE_KEY
from tests.test_security import owner, member, client, note, share, reset, m


def mode(admin, secret, monkeypatch, value, duration=0):
    stamp=m.now()+31
    monkeypatch.setattr(m,'now',lambda:stamp)
    result=admin.post('/api/admin/security/mode',json={
        'mode':value,'duration':duration,'reason':'Проверка изоляции',
        'password':'Owner-password-123','code':pyotp.TOTP(secret).at(stamp)})
    assert result.status_code==200,result.text
    return result


def test_denied_security_center_access_is_audited_without_exposing_history():
    admin,_,_=owner();guest,_=member(admin)
    for visitor,expected,event in ((client(),401,'session_rejected'),(guest,403,'access_denied')):
        response=visitor.get('/api/admin/security/events/export')
        assert response.status_code==expected
        assert 'events' not in response.json()
        with m.db() as c:
            row=c.execute('SELECT * FROM security_events WHERE event=? ORDER BY last_seen DESC',(event,)).fetchone()
        assert row and row['route']=='/api/admin/security/events/export'


def test_closed_logins_keep_existing_accounts_but_cover_all_entry_paths(monkeypatch):
    admin,secret,_=owner();guest,_=member(admin)
    mode(admin,secret,monkeypatch,'logins_closed',600)
    assert guest.get('/api/me').status_code==200
    visitor=client()
    for path in ('/api/login','/api/register','/api/setup','/api/password-reset',
                 '/api/passkeys/options','/api/passkeys/verify','/api/2fa/verify','/api/2fa/skip',
                 '/api/native/passkeys/start','/api/native/passkeys/exchange'):
        result=visitor.post(path,json={})
        assert result.status_code==503,path
        assert result.headers['x-service-mode']=='logins_closed'
    assert visitor.get('/api/password-hint').status_code==503
    assert admin.get('/api/admin/security').status_code==200
    stamp=m.now()+601;monkeypatch.setattr(m,'now',lambda:stamp)
    assert visitor.post('/api/login',json={}).status_code==422


def test_lockdown_closes_public_data_and_other_sessions_including_same_owner(monkeypatch):
    admin,secret,codes=owner();guest,_=member(admin)
    token=share(admin,note(admin),'public')
    other=client()
    answer=other.post('/api/login',json={'login':'owner','password':'Owner-password-123'})
    other.headers['x-csrf-token']=answer.json()['csrf']
    answer=other.post('/api/2fa/verify',json={'code':codes[0]})
    other.headers['x-csrf-token']=answer.json()['csrf']
    mode(admin,secret,monkeypatch,'lockdown')
    for visitor in (guest,client(),other):
        for path in ('/api/me','/api/notes','/api/shared/'+token,'/share/'+token,
                     '/api/admin/security','/api/admin/security/events/export'):
            assert visitor.get(path).status_code==503,path
        assert visitor.get('/api/health').status_code==200
    assert admin.get('/api/me').status_code==200
    assert admin.get('/api/admin/security').status_code==200
    # Admin access JWT and unlock grant expire independently. The designated
    # session can refresh then re-unlock; no other owner session bypasses isolation.
    stamp=m.now()+901;monkeypatch.setattr(m,'now',lambda:stamp)
    assert admin.post('/api/refresh').status_code==200
    assert admin.post('/api/admin/access/unlock',json={'password':'Owner-password-123'}).status_code==200
    assert admin.get('/api/admin/security').status_code==200
    mode(admin,secret,monkeypatch,'normal')
    assert guest.post('/api/refresh').status_code==200
    assert guest.get('/api/me').status_code==200


def test_existing_websocket_closes_and_new_upgrade_is_rejected(monkeypatch):
    admin,secret,_=owner();guest,_=member(admin)
    with guest.websocket_connect('/api/live/inbox') as socket:
        socket.send_json({'type':'auth','csrf':guest.headers['x-csrf-token']})
        assert socket.receive_json()['type']=='ready'
        mode(admin,secret,monkeypatch,'lockdown')
        with pytest.raises(WebSocketDisconnect):
            while True:socket.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with guest.websocket_connect('/api/live/inbox'):
            pytest.fail('Upgrade must be rejected during lockdown')


def test_stream_stops_after_isolation_and_closes_source(monkeypatch):
    admin,secret,_=owner();closed=[]
    def body():
        try:
            yield b'first'
            yield b'private-rest'
        finally:closed.append(True)
    visitor=Request({'method':'GET','path':'/api/shared/private','client':('203.0.113.5',123)})
    stream=m.security_policy.guard_stream(visitor,body())
    assert next(stream)==b'first'
    mode(admin,secret,monkeypatch,'lockdown')
    assert list(stream)==[] and closed==[True]


def test_corrupt_mode_fails_closed_except_health_and_local_command_recovers():
    admin,_,_=owner()
    with m.db() as c:
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',(MODE_KEY,'broken-json'))
    assert client().get('/api/status').status_code==503
    assert admin.get('/api/admin/security').status_code==503
    assert client().get('/api/health').status_code==200
    result=subprocess.run([os.sys.executable,'scripts/security.py','open-service','--revoke-sessions'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert client().get('/api/status').status_code==200
    assert admin.get('/api/me').status_code==401
    with m.db() as c:
        assert c.execute("SELECT COUNT(*) FROM users WHERE role='owner'").fetchone()[0]==1
        assert c.execute("SELECT 1 FROM security_events WHERE action='cli-open-service'").fetchone()
