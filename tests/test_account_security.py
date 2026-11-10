import json
import pytest,pyotp
from tests.test_security import owner,member,client,setup,enroll,note,reset
from tests.test_jwt import copied
from tests.test_admin import backup,headers
from app import main as m

PASSWORD='Owner-password-123'
NEW_PASSWORD='New-secure-password-456'
def other(codes):
    c=client();r=c.post('/api/login',json={'login':'owner','password':PASSWORD});c.headers['x-csrf-token']=r.json()['csrf']
    r=c.post('/api/2fa/verify',json={'code':codes[0]});assert r.status_code==200,r.text;c.headers['x-csrf-token']=r.json()['csrf'];return c
def begin(c,password=PASSWORD):
    r=c.post('/api/2fa/enroll',json={'password':password});assert r.status_code==200,r.text;secret=r.json()['secret']
    r=c.post('/api/2fa/confirm',json={'code':pyotp.TOTP(secret).at(m.now())});assert r.status_code==200,r.text
    return secret,r.json()['recovery_codes']

@pytest.mark.parametrize('weak',['abcdefghijk123','ABCDEFGHIJK123','NoDigitsPassword','Abc123'])
def test_password_policy_applies_to_setup_registration_self_and_admin_changes(weak):
    c=client();assert c.post('/api/setup',json={'login':'owner','password':weak,'token':'test-setup-token'}).status_code==422
    c,_,codes=owner();inv=c.post('/api/invites',json={'login':'friend'}).json()['url'].split('/')[-1]
    assert client().post('/api/register',json={'login':'friend','password':weak,'invite':inv}).status_code==422
    assert c.put('/api/security/password',json={'password':PASSWORD,'new_password':weak,'code':codes[0]}).status_code==422
    uid=c.get('/api/admin/users').json()['users'][0]['id']
    assert c.put('/api/admin/users/'+uid+'/password',json={'admin_password':PASSWORD,'password':weak}).status_code==422
    assert c.get('/api/me').status_code==200

def test_2fa_activation_requires_valid_code_and_saved_codes_on_server():
    c=client();setup(c);challenge=copied(c.cookies.get('drg_session'),csrf=c.headers['x-csrf-token'])
    assert c.post('/api/2fa/activate',json={'saved_recovery_codes':True}).status_code==400
    r=c.post('/api/2fa/enroll',json={'password':PASSWORD});secret=r.json()['secret']
    assert r.json()['uri'].startswith('otpauth://totp/')
    assert c.post('/api/2fa/confirm',json={'code':'invalid'}).status_code==401
    r=c.post('/api/2fa/confirm',json={'code':pyotp.TOTP(secret).at(m.now())});codes=r.json()['recovery_codes'];assert len(set(codes))==10
    assert c.get('/api/me').json()['two_factor'] is False and c.get('/api/notes').status_code==403
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM recovery_codes').fetchone()[0]==0
    assert '_pending' not in c.get('/api/me').text and secret not in c.get('/api/me').text
    assert c.post('/api/2fa/activate',json={}).status_code==422
    assert c.post('/api/2fa/activate',json={'saved_recovery_codes':False}).status_code==422
    r=c.post('/api/2fa/activate',json={'saved_recovery_codes':True});assert r.status_code==200,r.text;c.headers['x-csrf-token']=r.json()['csrf']
    assert c.get('/api/me').json()['two_factor'] and c.get('/api/notes').status_code==200
    assert challenge.get('/api/me').status_code==401
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM recovery_codes').fetchone()[0]==10

def test_disable_invalidates_old_sessions_codes_and_next_setup_has_new_secret():
    c,old_secret,codes=owner();g,_=member(c);second=other(codes);nid=note(c)
    stale=copied(c.cookies.get('drg_session'),c.cookies.get('drg_refresh'),c.headers['x-csrf-token'])
    assert c.post('/api/security/2fa/disable',json={'password':'wrong-password','code':codes[1]}).status_code==403
    assert c.post('/api/security/2fa/disable',json={'password':PASSWORD,'code':'bad'}).status_code==403
    r=c.post('/api/security/2fa/disable',json={'password':PASSWORD,'code':codes[1]});assert r.status_code==200,r.text;c.headers['x-csrf-token']=r.json()['csrf']
    assert c.get('/api/me').json()['two_factor'] is False
    assert c.get('/api/notes/'+nid).status_code==200 and g.get('/api/me').status_code==200
    assert second.get('/api/me').status_code==401 and stale.post('/api/refresh').status_code==401
    fresh=client();r=fresh.post('/api/login',json={'login':'owner','password':PASSWORD});assert not r.json()['needs_2fa'] and not r.json()['needs_enrollment'];fresh.headers['x-csrf-token']=r.json()['csrf']
    assert fresh.get('/api/notes').status_code==200
    new_secret,new_codes=begin(c);assert new_secret!=old_secret and set(new_codes).isdisjoint(codes)
    assert not c.get('/api/me').json()['two_factor'] and fresh.get('/api/me').status_code==200
    assert fresh.post('/api/2fa/activate',json={'saved_recovery_codes':True}).status_code==400
    r=c.post('/api/2fa/activate',json={'saved_recovery_codes':True});assert r.status_code==200;c.headers['x-csrf-token']=r.json()['csrf']
    assert fresh.get('/api/me').status_code==401 and c.get('/api/me').json()['two_factor']
    with m.db() as db:
        uid=db.execute("SELECT id FROM users WHERE login='owner'").fetchone()[0]
        hashes={row[0] for row in db.execute('SELECT code FROM recovery_codes WHERE user_id=?',(uid,))};assert m.digest(codes[2]) not in hashes and m.digest(new_codes[0]) in hashes

def test_expired_confirmation_cannot_enable_2fa(monkeypatch):
    c=client();setup(c);secret,_=begin(c);stamp=m.now()+601
    monkeypatch.setattr(m,'now',lambda:stamp)
    r=c.post('/api/login',json={'login':'owner','password':PASSWORD});c.headers['x-csrf-token']=r.json()['csrf']
    assert c.post('/api/2fa/activate',json={'saved_recovery_codes':True}).status_code==400
    assert c.get('/api/me').json()['two_factor'] is False
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM recovery_codes').fetchone()[0]==0

def test_change_password_keeps_only_new_current_session_and_requires_second_factor():
    c,secret,codes=owner();second=other(codes)
    pending=client();r=pending.post('/api/login',json={'login':'owner','password':PASSWORD});pending.headers['x-csrf-token']=r.json()['csrf']
    old=copied(c.cookies.get('drg_session'),c.cookies.get('drg_refresh'),c.headers['x-csrf-token'])
    assert c.put('/api/security/password',json={'password':PASSWORD,'new_password':NEW_PASSWORD}).status_code==403
    assert c.put('/api/security/password',json={'password':'wrong','new_password':NEW_PASSWORD,'code':codes[1]}).status_code==403
    assert c.put('/api/security/password',json={'password':PASSWORD,'new_password':PASSWORD,'code':codes[1]}).status_code==422
    r=c.put('/api/security/password',json={'password':PASSWORD,'new_password':NEW_PASSWORD,'code':codes[1]});assert r.status_code==200,r.text;c.headers['x-csrf-token']=r.json()['csrf']
    assert c.get('/api/me').status_code==200
    assert old.get('/api/me').status_code==401 and old.post('/api/refresh').status_code==401
    assert second.get('/api/me').status_code==401 and pending.get('/api/me').status_code==401
    fresh=client();assert fresh.post('/api/login',json={'login':'owner','password':PASSWORD}).status_code==401
    r=fresh.post('/api/login',json={'login':'owner','password':NEW_PASSWORD});assert r.json()['needs_2fa'];fresh.headers['x-csrf-token']=r.json()['csrf']
    assert fresh.post('/api/2fa/verify',json={'code':codes[1]}).status_code==401
    assert fresh.post('/api/2fa/verify',json={'code':codes[2]}).status_code==200
    with m.db() as db:assert m.cipher.decrypt(db.execute("SELECT totp FROM users WHERE login='owner'").fetchone()[0].encode()).decode()==secret

def test_logout_others_is_scoped_and_removes_pending_sessions_too():
    c,_,codes=owner();g,_=member(c);second=other(codes)
    pending=client();r=pending.post('/api/login',json={'login':'owner','password':PASSWORD});pending.headers['x-csrf-token']=r.json()['csrf']
    missing_csrf=copied(c.cookies.get('drg_session'));assert missing_csrf.post('/api/security/sessions/revoke-others').status_code==403
    r=c.post('/api/security/sessions/revoke-others');assert r.json()['revoked']==2
    assert c.get('/api/me').status_code==200 and g.get('/api/me').status_code==200
    assert second.get('/api/me').status_code==401 and second.post('/api/refresh').status_code==401 and pending.get('/api/me').status_code==401
    assert len(c.get('/api/security/sessions').json())==1

def test_disabled_2fa_account_can_change_password_and_restore_backup():
    c,_,codes=owner();r=c.post('/api/security/2fa/disable',json={'password':PASSWORD,'code':codes[0]});c.headers['x-csrf-token']=r.json()['csrf']
    assert c.post('/api/admin/access/unlock',json={'password':PASSWORD}).status_code==200
    body=backup(c)
    r=c.put('/api/security/password',json={'password':PASSWORD,'new_password':'НовыйНадёжныйПароль123'});assert r.status_code==200;c.headers['x-csrf-token']=r.json()['csrf']
    assert c.get('/api/me').status_code==200 and not c.get('/api/me').json()['two_factor']
    import base64
    h=headers();h['x-admin-password-b64']=base64.b64encode('НовыйНадёжныйПароль123'.encode()).decode()
    assert c.post('/api/admin/access/unlock',json={'password':'НовыйНадёжныйПароль123'}).status_code==200
    assert c.post('/api/admin/backups/restore',content=body,headers=h).status_code==200
    r=c.post('/api/login',json={'login':'owner','password':PASSWORD});assert not r.json()['needs_enrollment'] and not r.json()['needs_2fa'];c.headers['x-csrf-token']=r.json()['csrf']
    assert c.get('/api/admin/users').status_code==423
    assert c.post('/api/admin/access/unlock',json={'password':PASSWORD}).status_code==200
    assert c.get('/api/admin/users').status_code==200
