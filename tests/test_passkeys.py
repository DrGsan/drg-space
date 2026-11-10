"""Real ES256 WebAuthn ceremonies: no mocked verifier or trusted browser flags."""
import hashlib,json,secrets
import cbor2,pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from tests.test_security import owner,member,client,reset
from tests.test_admin import backup,headers
from app import main as m
from app.passkeys import encode,index_key,PREFIX,digest,validate_passkeys

PASSWORD='Owner-password-123'
ORIGIN='http://testserver'

class Authenticator:
    def __init__(self):
        self.key=ec.generate_private_key(ec.SECP256R1());self.id=secrets.token_bytes(32)
    def client(self,options,kind,**changes):
        return json.dumps({'type':'webauthn.'+kind,'challenge':options['challenge'],'origin':ORIGIN,'crossOrigin':False,**changes},separators=(',',':')).encode()
    def credential(self,response):
        return {'id':encode(self.id),'rawId':encode(self.id),'type':'public-key','response':{key:encode(value) for key,value in response.items()},'clientExtensionResults':{}}
    def register(self,options,flags=0x45,rp='testserver',**changes):
        self.uid=options['user']['id'];numbers=self.key.public_key().public_numbers()
        cose=cbor2.dumps({1:2,3:-7,-1:1,-2:numbers.x.to_bytes(32,'big'),-3:numbers.y.to_bytes(32,'big')})
        auth=hashlib.sha256(rp.encode()).digest()+bytes([flags])+bytes(4)+bytes(16)+len(self.id).to_bytes(2,'big')+self.id+cose
        return self.credential({'clientDataJSON':self.client(options,'create',**changes),'attestationObject':cbor2.dumps({'fmt':'none','attStmt':{},'authData':auth})})
    def authenticate(self,options,count=1,flags=5,rp='testserver',uid=None,**changes):
        data=self.client(options,'get',**changes);auth=hashlib.sha256(rp.encode()).digest()+bytes([flags])+count.to_bytes(4,'big')
        signature=self.key.sign(auth+hashlib.sha256(data).digest(),ec.ECDSA(hashes.SHA256()))
        result=self.credential({'clientDataJSON':data,'authenticatorData':auth,'signature':signature})
        result['response']['userHandle']=uid if uid is not None else self.uid
        return result

def options(c,code=None,**changes):
    path='/api/security/passkeys/options' if code is not None else '/api/passkeys/options'
    body={'password':PASSWORD,'code':code,'name':'iPhone'} if code is not None else {}
    r=c.post(path,json={**body,**changes});assert r.status_code==200,r.text;return r.json()
def finish(c,start,credential,register=False):
    return c.post('/api/'+('security/' if register else '')+'passkeys/verify',json={'credential':credential,'csrf':start['ceremony_csrf']})
def registered(c,code):
    device=Authenticator();start=options(c,code);r=finish(c,start,device.register(start['options']),True);assert r.status_code==200,r.text;return device,r.json()

def test_resident_key_registration_usernameless_login_and_password_fallback():
    c,_,codes=owner();device,key=registered(c,codes[0]);me=c.get('/api/me').json()
    assert '_passkeys' not in me['settings'] and 'public_key' not in c.get('/api/security/passkeys').text
    listed=c.get('/api/security/passkeys').json();assert listed['items']==[key] and listed['rp_id']=='testserver'
    assert c.patch('/api/security/passkeys/'+key['id'],json={'name':'MacBook'}).json()['name']=='MacBook'
    anonymous=client();start=options(anonymous);assert start['options']['userVerification']=='required' and not start['options'].get('allowCredentials')
    response=finish(anonymous,start,device.authenticate(start['options']));assert response.status_code==200,response.text
    assert 'csrf' in response.json();anonymous.headers['x-csrf-token']=response.json()['csrf']
    assert anonymous.get('/api/me').json()['id']==me['id'] and anonymous.get('/api/notes').status_code==200
    assert anonymous.post('/api/notes',json={'title':'Passkey session'}).status_code==200
    assert anonymous.get('/api/security/passkeys').json()['items'][0]['last_used'] is not None
    assert client().post('/api/login',json={'login':'owner','password':PASSWORD}).json()['needs_2fa']

@pytest.mark.parametrize('changes',[{'flags':0x41},{'flags':0x44},{'rp':'evil.example'},{'origin':'https://evil.example'},{'crossOrigin':True},{'challenge':encode(b'wrong')}])
def test_registration_rejects_missing_uv_up_wrong_rp_origin_and_challenge(changes):
    c,_,codes=owner();start=options(c,codes[0]);device=Authenticator();credential=device.register(start['options'],**changes)
    assert finish(c,start,credential,True).status_code==401
    assert finish(c,start,credential,True).status_code==401
    assert not c.get('/api/security/passkeys').json()['items']

@pytest.mark.parametrize('changes',[{'flags':1},{'flags':4},{'rp':'evil.example'},{'origin':'https://evil.example'},{'crossOrigin':True},{'challenge':encode(b'wrong')},{'uid':encode(b'other')}])
def test_authentication_rejects_invalid_proof(changes):
    c,_,codes=owner();device,_=registered(c,codes[0]);anonymous=client();start=options(anonymous)
    assert finish(anonymous,start,device.authenticate(start['options'],**changes)).status_code==401
    assert anonymous.get('/api/me').status_code==401

def test_modified_signature_counter_clone_and_replay_are_rejected():
    c,_,codes=owner();device,_=registered(c,codes[0]);anonymous=client();start=options(anonymous);credential=device.authenticate(start['options'])
    credential['response']['signature']=encode(bytes(70));assert finish(anonymous,start,credential).status_code==401
    start=options(anonymous);credential=device.authenticate(start['options'],count=5);assert finish(anonymous,start,credential).status_code==200
    assert finish(anonymous,start,credential).status_code==401
    start=options(anonymous);assert finish(anonymous,start,device.authenticate(start['options'],count=4)).status_code==401
    # Synced passkeys without counters are valid; counted authenticators may never go backwards.
    start=options(anonymous);assert finish(anonymous,start,device.authenticate(start['options'],count=0)).status_code==401

def test_ceremony_is_browser_bound_one_use_expiring_and_origin_csrf_protected(monkeypatch):
    c,_,codes=owner();start=options(c,codes[0]);device=Authenticator();credential=device.register(start['options'])
    stranger=client();assert finish(stranger,start,credential,True).status_code==401
    without_csrf=dict(c.headers);without_csrf.pop('x-csrf-token');saved=c.headers;c.headers=without_csrf
    assert finish(c,start,credential,True).status_code==403;c.headers=saved
    wrong={**start,'ceremony_csrf':'x'*43};assert finish(c,wrong,credential,True).status_code==403
    c.headers['origin']='https://evil.example';assert finish(c,start,credential,True).status_code==403;c.headers['origin']=ORIGIN
    stamp=m.now()+181;monkeypatch.setattr(m,'now',lambda:stamp)
    assert finish(c,start,credential,True).status_code==401
    fresh=options(client())
    with m.db() as db:assert db.execute("SELECT COUNT(*) FROM app_settings WHERE key LIKE ?",('passkey:challenge:%',)).fetchone()[0]==1

def test_register_requires_password_second_factor_full_session_and_unique_id():
    c,_,codes=owner();assert c.post('/api/security/passkeys/options',json={'password':PASSWORD}).status_code==403
    assert c.post('/api/security/passkeys/options',json={'password':'wrong','code':codes[0]}).status_code==403
    device,key=registered(c,codes[0]);start=options(c,codes[1]);assert start['options']['authenticatorSelection']['residentKey']=='required'
    assert start['options']['excludeCredentials'][0]['id']==encode(device.id)
    assert finish(c,start,device.register(start['options']),True).status_code==409
    pending=client();r=pending.post('/api/login',json={'login':'owner','password':PASSWORD});pending.headers['x-csrf-token']=r.json()['csrf']
    assert pending.get('/api/security/passkeys').status_code==401

def test_revoking_session_or_changing_password_during_registration_rejects_save():
    c,_,codes=owner();start=options(c,codes[0]);device=Authenticator();credential=device.register(start['options'])
    assert c.put('/api/security/password',json={'password':PASSWORD,'new_password':'Changed-password-123','code':codes[1]}).status_code==200
    assert finish(c,start,credential,True).status_code in (401,403)
    assert not c.get('/api/security/passkeys').json()['items']

def test_revocation_scoped_to_owner_removes_index_and_prevents_prepared_login():
    c,_,codes=owner();device,key=registered(c,codes[0]);other,_=member(c)
    assert other.patch('/api/security/passkeys/'+key['id'],json={'name':'hijack'}).status_code==404
    anonymous=client();start=options(anonymous);credential=device.authenticate(start['options'])
    assert c.post('/api/security/passkeys/'+key['id']+'/delete',json={'password':PASSWORD}).status_code==403
    assert c.post('/api/security/passkeys/'+key['id']+'/delete',json={'password':PASSWORD,'code':codes[1]}).status_code==200
    assert finish(anonymous,start,credential).status_code==401
    with m.db() as db:assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',(index_key(encode(device.id)),)).fetchone()

@pytest.mark.parametrize('action',['password','reset-2fa','reset-link'])
def test_admin_account_recovery_revokes_passkeys(action):
    c,_,_=owner();g,_=member(c)
    # Member has its own TOTP. Disable it through admin reset, then skip enrollment.
    uid=g.get('/api/me').json()['id'];assert c.post('/api/admin/users/'+uid+'/reset-2fa',json={'admin_password':PASSWORD}).status_code==200
    g=client();r=g.post('/api/login',json={'login':'friend','password':'Friend-password-123'});g.headers['x-csrf-token']=r.json()['csrf']
    r=g.post('/api/2fa/skip',json={});assert r.status_code==200,r.text;g.headers['x-csrf-token']=r.json()['csrf']
    device=Authenticator();start=options(g,'',password='Friend-password-123');assert finish(g,start,device.register(start['options']),True).status_code==200
    if action=='password':assert c.put('/api/admin/users/'+uid+'/password',json={'admin_password':PASSWORD,'password':'Changed-password-123'}).status_code==200
    elif action=='reset-2fa':assert c.post('/api/admin/users/'+uid+'/reset-2fa',json={'admin_password':PASSWORD}).status_code==200
    else:
        r=c.post('/api/admin/users/'+uid+'/password-reset',json={'admin_password':PASSWORD});assert r.status_code==200,r.text
        token=r.json()['url'].split('/')[-1];r=client().post('/api/password-reset',json={'token':token,'password':'Changed-password-123'});assert r.status_code==200,r.text
    anonymous=client();start=options(anonymous);assert finish(anonymous,start,device.authenticate(start['options'])).status_code==401

def test_full_backup_preserves_credentials_and_excludes_active_ceremonies():
    c,_,codes=owner();device,_=registered(c,codes[0]);old=options(client());body=backup(c)
    assert c.post('/api/admin/backups/restore',content=body,headers=headers()).status_code==200
    with m.db() as db:assert db.execute("SELECT COUNT(*) FROM app_settings WHERE key LIKE ?",('passkey:challenge:%',)).fetchone()[0]==0
    anonymous=client();start=options(anonymous);r=finish(anonymous,start,device.authenticate(start['options']));assert r.status_code==200,r.text

def test_passkeys_zero_counter_sync_and_login_without_2fa_still_need_uv():
    c,_,codes=owner();device,_=registered(c,codes[0]);r=c.post('/api/security/2fa/disable',json={'password':PASSWORD,'code':codes[1]});c.headers['x-csrf-token']=r.json()['csrf']
    for _ in range(2):
        anonymous=client();start=options(anonymous);r=finish(anonymous,start,device.authenticate(start['options'],count=0));assert r.status_code==200,r.text
        assert not anonymous.get('/api/me').json()['needs_enrollment']

def test_login_options_cookie_and_rate_limit():
    c=client();r=c.post('/api/passkeys/options',json={});assert r.status_code==200
    cookie=r.headers['set-cookie'].lower();assert 'httponly' in cookie and 'samesite=strict' in cookie and 'path=/api/' in cookie
    for _ in range(29):assert c.post('/api/passkeys/options',json={}).status_code==200
    assert c.post('/api/passkeys/options',json={}).status_code==429

def test_malformed_client_data_and_non_ascii_csrf_do_not_crash():
    c=client();start=options(c)
    credential={'id':encode(b'unknown'),'rawId':encode(b'unknown'),'type':'public-key','response':{'clientDataJSON':encode(b'[]')}}
    assert c.post('/api/passkeys/verify',json={'credential':credential,'csrf':'ю'*43}).status_code==422
    assert finish(c,start,credential).status_code==401
