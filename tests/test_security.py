import httpx
import os, tempfile, secrets, time, json
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='drg-tests-')
os.environ['DATABASE_URL']=os.getenv('DRG_TEST_DATABASE_URL','')
os.environ['DATABASE_SCHEMA']=os.getenv('DRG_TEST_DATABASE_SCHEMA','drg_tests_'+secrets.token_hex(8))
os.environ['DATA_ENCRYPTION_ENABLED']=os.getenv('DRG_TEST_ENCRYPTION_ENABLED','true')
os.environ['DATA_ENCRYPTION_KEY']='dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU='
os.environ['APP_SECRET']='test-secret-only-do-not-use-in-production-'*2
os.environ['SETUP_TOKEN']='test-setup-token'
os.environ['PUBLIC_URL']='http://testserver'
import pytest, pyotp
from tests.http_client import TestClient
from app import main as m

@pytest.fixture(autouse=True)
def reset():
    with m.db() as c:
        for t in ['security_events','security_incidents','app_settings','share_sessions','shares','notes','tasks','invites','sessions','recovery_codes','users','throttle','auth_failures','security_bans']:
            c.execute('DELETE FROM '+t)
    yield

def client():
    c=TestClient(m.app); c.headers['origin']='http://testserver';return c

def setup(c):
    r=c.post('/api/setup',json={'login':'owner','password':'Owner-password-123','token':'test-setup-token'})
    assert r.status_code==200,r.text
    c.headers['x-csrf-token']=r.json()['csrf']

def enroll(c,password='Owner-password-123'):
    r=c.post('/api/2fa/enroll',json={'password':password}); assert r.status_code==200,r.text
    secret=r.json()['secret'];r=c.post('/api/2fa/confirm',json={'code':pyotp.TOTP(secret).at(m.now())})
    assert r.status_code==200,r.text
    codes=r.json()['recovery_codes']
    r=c.post('/api/2fa/activate',json={'saved_recovery_codes':True});assert r.status_code==200,r.text
    c.headers['x-csrf-token']=r.json()['csrf']
    return secret,codes

def owner():
    c=client();setup(c); secret,codes=enroll(c)
    unlock=c.post('/api/admin/access/unlock',json={'password':'Owner-password-123'})
    assert unlock.status_code==200,unlock.text
    return c,secret,codes

def member(c,name='friend'):
    r=c.post('/api/invites',json={'login':name});assert r.status_code==200,r.text
    token=r.json()['url'].split('/')[-1];guest=client()
    r=guest.post('/api/register',json={'login':name,'password':'Friend-password-123','invite':token});assert r.status_code==200,r.text
    guest.headers['x-csrf-token']=r.json()['csrf'];enroll(guest,'Friend-password-123');return guest,token

def note(c):
    r=c.post('/api/notes',json={'title':'Private title','body':'# Private body'});assert r.status_code==200,r.text
    return r.json()['id']

def share(c,nid,mode,**kw):
    r=c.post('/api/notes/'+nid+'/shares',json={'mode':mode,**kw});assert r.status_code==200,r.text
    return r.json()['token']

def test_setup_once_and_strong_password():
    c=client();assert c.post('/api/setup',json={'login':'owner','password':'short','token':'test-setup-token'}).status_code==422
    assert c.post('/api/setup',json={'login':'owner','password':'Owner-password-123','token':'bad'}).status_code==403
    setup(c)
    assert c.post('/api/setup',json={'login':'other','password':'Owner-password-123','token':'test-setup-token'}).status_code==409

def test_enrollment_required_and_wrong_password():
    c=client();setup(c)
    assert c.get('/api/notes').status_code==403
    assert c.post('/api/2fa/enroll',json={'password':'wrong'}).status_code==401
    enroll(c);assert c.get('/api/notes').status_code==200

def test_2fa_replay_pending_session_and_recovery_once(monkeypatch):
    frozen=(int(time.time())//30)*30+5
    monkeypatch.setattr(m,'now',lambda:frozen)
    c,secret,codes=owner()
    c.post('/api/logout')
    r=c.post('/api/login',json={'login':'owner','password':'Owner-password-123'});assert r.json()['needs_2fa']
    c.headers['x-csrf-token']=r.json()['csrf']
    assert c.get('/api/notes').status_code==401
    assert c.post('/api/2fa/verify',json={'code':pyotp.TOTP(secret).at(m.now())}).status_code==401
    stamp=m.now()+60;monkeypatch.setattr(m,'now',lambda:stamp)
    r=c.post('/api/2fa/verify',json={'code':pyotp.TOTP(secret).at(stamp)});assert r.status_code==200
    c.headers['x-csrf-token']=r.json()['csrf'];assert c.get('/api/notes').status_code==200;c.post('/api/logout')
    r=c.post('/api/login',json={'login':'owner','password':'Owner-password-123'});c.headers['x-csrf-token']=r.json()['csrf']
    r=c.post('/api/2fa/verify',json={'code':codes[0]});assert r.status_code==200;c.headers['x-csrf-token']=r.json()['csrf'];c.post('/api/logout')
    r=c.post('/api/login',json={'login':'owner','password':'Owner-password-123'});c.headers['x-csrf-token']=r.json()['csrf']
    assert c.post('/api/2fa/verify',json={'code':codes[0]}).status_code==401

def test_csrf_origin_and_cookie_flags():
    c,_,_=owner();del c.headers['x-csrf-token']
    assert c.post('/api/notes',json={'title':'x','body':''}).status_code==403
    assert c.post('/api/notes',json={'title':'x','body':''},headers={'x-csrf-token':b'\xff'}).status_code==403
    me=c.get('/api/me').json();c.headers['x-csrf-token']=me['csrf'];c.headers['origin']='https://evil.example'
    assert c.post('/api/notes',json={'title':'x','body':''}).status_code==403
    c.headers['origin']='http://testserver';r=c.post('/api/login',json={'login':'owner','password':'Owner-password-123'})
    assert 'HttpOnly' in r.headers['set-cookie'];assert 'samesite=lax' in r.headers['set-cookie'].lower()

def test_application_version_is_server_metadata_for_both_roles():
    from app.version import APP_VERSION, APP_RELEASE
    c,_,_=owner();guest,_=member(c)
    for account in (c,guest):
        me=account.get('/api/me').json()
        assert me['app_version']==APP_VERSION
        assert 'app_version' not in me['settings']
        assert me['app_release']==APP_RELEASE
        assert me['app_release']['version']==APP_VERSION
        assert me['app_release']['changes']
        assert 'app_release' not in me['settings']

def test_invite_bound_single_use_and_private_notes():
    c,_,_=owner();nid=note(c);g,token=member(c)
    assert g.get('/api/notes').json()==[]
    assert g.put('/api/notes/'+nid,json={'title':'Stolen','body':''}).status_code==404
    assert g.delete('/api/notes/'+nid).status_code==404
    assert g.post('/api/notes/'+nid+'/shares',json={'mode':'public'}).status_code==404
    assert g.post('/api/invites',json={'login':'newuser'}).status_code==403
    assert client().post('/api/register',json={'login':'friend','password':'Friend-password-123','invite':token}).status_code==403

def test_public_share_expiry_revocation_and_note_delete():
    c,_,_=owner();nid=note(c);token=share(c,nid,'public',duration=13,unit='minutes');g=client()
    assert g.get('/api/shared/'+token).json()['body']=='# Private body'
    with m.db() as d:d.execute('UPDATE shares SET expires=? WHERE token=?',(m.now()-1,token))
    assert g.get('/api/shared/'+token).status_code==404
    token=share(c,nid,'public');assert c.delete('/api/shares/'+token).status_code==200
    assert g.get('/api/shared/'+token).status_code==404
    token=share(c,nid,'public');c.delete('/api/notes/'+nid)
    assert g.get('/api/shared/'+token).status_code==404

def test_password_share_does_not_leak_and_expires_even_after_unlock():
    c,_,_=owner();nid=note(c);token=share(c,nid,'password',password='share-secret');g=client()
    r=g.get('/api/shared/'+token);assert r.status_code==423;assert 'Private title' not in r.text
    assert g.post('/api/shared/'+token+'/unlock',json={'password':'wrong'}).status_code==403
    assert g.post('/api/shared/'+token+'/unlock',json={'password':'share-secret'}).status_code==200
    assert g.get('/api/shared/'+token).status_code==200
    with m.db() as d:d.execute('UPDATE shares SET expires=? WHERE token=?',(m.now()-1,token))
    assert g.get('/api/shared/'+token).status_code==404

def test_auth_and_named_login_access():
    c,_,_=owner();nid=note(c);g,_=member(c);other,_=member(c,'other')
    token=share(c,nid,'authenticated')
    assert client().get('/api/shared/'+token).status_code==401
    assert g.get('/api/shared/'+token).status_code==200
    token=share(c,nid,'users',allowed=['friend'])
    assert g.get('/api/shared/'+token).status_code==200
    assert other.get('/api/shared/'+token).status_code==403
    assert c.post('/api/notes/'+nid+'/shares',json={'mode':'users','allowed':['absent']}).status_code==422

def test_tasks_persist_and_are_private():
    c,_,_=owner();g,_=member(c)
    assert c.post('/api/tasks',json={'title':'Task','day':'invalid'}).status_code==422
    r=c.post('/api/tasks',json={'title':'Task','day':'2026-10-11'});tid=r.json()['id']
    assert g.patch('/api/tasks/'+tid,json={'done':True}).status_code==404
    assert c.patch('/api/tasks/'+tid,json={'done':True}).status_code==200
    assert c.get('/api/tasks').json()[0]['done']==1
    assert c.delete('/api/tasks/'+tid).status_code==200;assert c.get('/api/tasks').json()==[]

def test_custom_durations_and_calendar_months(monkeypatch):
    stamp=int(time.time());monkeypatch.setattr(m,'now',lambda:stamp)
    assert m.expiry(13,'minutes')==stamp+780
    assert m.expiry(9,'hours')==stamp+32400
    assert m.expiry(None,'days') is None
    from datetime import datetime,timezone
    dt=datetime.now(timezone.utc);r=datetime.fromtimestamp(m.expiry(7,'months'),timezone.utc)
    assert (r.year*12+r.month)-(dt.year*12+dt.month)==7
    r=datetime.fromtimestamp(m.expiry(2,'years'),timezone.utc);assert r.year==dt.year+2

def test_session_expiration_and_rate_limit():
    c,_,_=owner()
    with m.db() as d:d.execute('UPDATE sessions SET expires=?',(m.now()-1,))
    assert c.get('/api/notes').status_code==401
    g=client()
    statuses=[g.post('/api/login',json={'login':'owner','password':'wrong-password-long'}).status_code for _ in range(9)]
    assert statuses[-1]==429

def test_secure_headers_and_unknown_mode():
    c,_,_=owner();nid=note(c)
    assert c.post('/api/notes/'+nid+'/shares',json={'mode':'invalid'}).status_code==422
    r=c.get('/api/health')
    assert r.headers['x-content-type-options']=='nosniff'
    assert "frame-ancestors 'none'" in r.headers['content-security-policy']
    assert r.headers['cache-control']=='no-store'

def test_non_ascii_setup_and_2fa_codes_fail_without_server_errors():
    c=client()
    assert c.post('/api/setup',json={'login':'owner','password':'Owner-password-123','token':'неверный'}).status_code==403
    c,_,_=owner();c.post('/api/logout')
    r=c.post('/api/login',json={'login':'owner','password':'Owner-password-123'})
    c.headers['x-csrf-token']=r.json()['csrf']
    assert c.post('/api/2fa/verify',json={'code':'неверный'}).status_code==401

def test_saved_edits_visible_through_existing_share_and_blank_titles():
    c,_,_=owner();nid=note(c);token=share(c,nid,'public')
    assert c.put('/api/notes/'+nid,json={'title':'Edited','body':'New text'}).status_code==200
    assert client().get('/api/shared/'+token).json()['body']=='New text'
    assert c.post('/api/notes',json={'title':'   ','body':''}).status_code==422
    assert c.post('/api/tasks',json={'title':'   ','day':'2026-10-11'}).status_code==422

def test_settings_isolation_and_weather_failure(monkeypatch):
    c,_,_=owner();g,_=member(c)
    guest_before=g.get('/api/me').json()['settings']
    layout=c.get('/api/dashboard/layout').json()
    layout['instances'].append({'id':'weather','module_id':'weather','config':{'city':'Казань','latitude':55.79,'longitude':49.12}})
    assert c.put('/api/modules',json={'enabled':{'weather':True}}).status_code==200
    assert c.put('/api/dashboard/layout',json=layout).status_code==200
    assert g.get('/api/me').json()['settings']==guest_before
    assert g.get('/api/widgets/weather/weather').status_code==404
    assert g.put('/api/modules',json={'enabled':{'weather':False}}).status_code==200
    assert g.get('/api/widgets/weather/weather').status_code==404
    assert client().get('/api/widgets/weather/weather').status_code==401
    class BrokenClient:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,*args,**kwargs): raise httpx.ConnectError('unavailable')
    monkeypatch.setattr(httpx,'AsyncClient',BrokenClient)
    assert c.get('/api/widgets/weather/weather').status_code==503
    assert c.get('/api/notes').status_code==200
