import subprocess
import os
import pytest
from concurrent.futures import ThreadPoolExecutor

from app.http import Request, HTTPError
from tests.http_client import TestClient
from tests.test_security import owner, member, client, note, share, reset
from app import main as m
from app.protection import Protection, client_ip, trusted_networks


def visitor(ip='203.0.113.10'):
    c = TestClient(m.app, client=(ip, 12345))
    c.headers['origin'] = 'http://testserver'
    return c


def request(ip='203.0.113.10', forwarded=None):
    return Request({'type': 'http', 'method': 'POST', 'path': '/api/login', 'client': (ip, 12345),
                    'headers': [(b'x-forwarded-for', forwarded.encode())] if forwarded else []})


def wrong(c, login='owner'):
    return c.post('/api/login', json={'login': login, 'password': 'wrong-password-long'})


def test_failure_ban_persists_escalates_and_blocked_requests_do_not_extend_it(monkeypatch):
    stamp = m.now(); monkeypatch.setattr(m, 'now', lambda: stamp)
    owner(); attacker = visitor()
    assert [wrong(attacker).status_code for _ in range(7)] == [401]*7
    r = wrong(attacker); assert r.status_code == 429 and int(r.headers['retry-after']) == 900
    with m.db() as db:
        ban = dict(db.execute('SELECT * FROM security_bans').fetchone())
    restarted = Protection(m.db, lambda: m.now())
    try:
        restarted.check(request(), 'login', 'owner')
        assert False, 'Persisted ban must survive a new service object'
    except HTTPError as error:
        assert error.status_code == 429
    assert attacker.post('/api/login', json={'login': 'owner', 'password': 'Owner-password-123'}).status_code == 429
    with m.db() as db:
        assert db.execute('SELECT until FROM security_bans').fetchone()[0] == ban['until']
    stamp = ban['until']+1; monkeypatch.setattr(m, 'now', lambda: stamp)
    assert [wrong(attacker).status_code for _ in range(7)] == [401]*7
    r = wrong(attacker); assert int(r.headers['retry-after']) == 1800
    with m.db() as db:
        assert db.execute('SELECT strikes FROM security_bans').fetchone()[0] == 2


def test_ip_ban_aggregates_rotating_targets_and_has_retry_after_and_headers():
    owner(); attacker = visitor()
    for i in range(m.protection.ip_limit):
        r = wrong(attacker, 'absent' + str(i))
    assert r.status_code == 429
    assert attacker.get('/api/notes').status_code == 429
    assert attacker.get('/api/status').status_code == 429
    r = attacker.get('/api/me')
    assert int(r.headers['retry-after']) > 0
    assert r.headers['cache-control'] == 'no-store'
    assert r.headers['x-content-type-options'] == 'nosniff'
    assert attacker.get('/api/health').status_code == 200
    assert visitor('203.0.113.11').get('/api/status').status_code == 200


def test_success_clears_target_failures_but_not_ip_aggregate():
    owner(); attacker = visitor()
    for _ in range(5): assert wrong(attacker).status_code == 401
    assert attacker.post('/api/login', json={'login': 'owner', 'password': 'Owner-password-123'}).status_code == 200
    for _ in range(5): assert wrong(attacker).status_code == 401
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM auth_failures WHERE key=?', (m.protection.key('203.0.113.10'),)).fetchone()[0] == 10


def test_failed_2fa_survives_new_password_login_and_bans_correct_codes():
    _, _, codes = owner(); attacker = visitor()
    r = attacker.post('/api/login', json={'login': 'owner', 'password': 'Owner-password-123'})
    attacker.headers['x-csrf-token'] = r.json()['csrf']
    for _ in range(4): assert attacker.post('/api/2fa/verify', json={'code': 'invalid'}).status_code == 401
    r = attacker.post('/api/login', json={'login': 'owner', 'password': 'Owner-password-123'})
    attacker.headers['x-csrf-token'] = r.json()['csrf']
    for _ in range(3): assert attacker.post('/api/2fa/verify', json={'code': 'invalid'}).status_code == 401
    assert attacker.post('/api/2fa/verify', json={'code': 'invalid'}).status_code == 429
    assert attacker.post('/api/2fa/verify', json={'code': codes[0]}).status_code == 429


def test_password_publication_has_independent_failure_bans_and_jwt_grants():
    c, _, _ = owner(); nid = note(c)
    token = share(c, nid, 'password', password='publication-password')
    attacker = visitor()
    for _ in range(7): assert attacker.post('/api/shared/'+token+'/unlock', json={'password': 'wrong'}).status_code == 403
    assert attacker.post('/api/shared/'+token+'/unlock', json={'password': 'wrong'}).status_code == 429
    assert attacker.post('/api/shared/'+token+'/unlock', json={'password': 'publication-password'}).status_code == 429
    friend = visitor('203.0.113.11')
    assert friend.post('/api/shared/'+token+'/unlock', json={'password': 'publication-password'}).status_code == 200
    grant = friend.cookies.get('drg_share_'+token)
    assert grant.count('.') == 2
    assert friend.get('/api/shared/'+token).status_code == 200
    # A grant for one publication cannot open another or authenticate an account.
    second = share(c, nid, 'password', password='publication-password')
    friend.cookies.set('drg_share_'+second, grant, domain='testserver.local', path='/api/shared/'+second)
    assert friend.get('/api/shared/'+second).status_code == 423
    friend.cookies.set('drg_session', grant, domain='testserver.local', path='/')
    assert friend.get('/api/me').status_code == 401


def test_only_owner_can_view_or_release_bans_and_release_clears_counters(monkeypatch):
    import pyotp
    c, secret, _ = owner(); guest, _ = member(c); attacker = visitor()
    for _ in range(8): wrong(attacker)
    listing = c.get('/api/security/bans').json()
    assert listing['total'] == 1 and listing['bans'][0]['ip'] == '203.0.113.10'
    assert guest.get('/api/security/bans').status_code == 403
    ban_id = listing['bans'][0]['id']
    assert guest.delete('/api/security/bans/'+ban_id).status_code == 403
    csrf = c.headers.pop('x-csrf-token')
    assert c.delete('/api/security/bans/'+ban_id).status_code == 403
    c.headers['x-csrf-token'] = csrf
    assert c.delete('/api/security/bans/'+ban_id).status_code == 410
    stamp=m.now()+31;monkeypatch.setattr(m,'now',lambda:stamp)
    assert c.request('DELETE','/api/admin/security/bans/'+ban_id,json={
        'password':'Owner-password-123','code':pyotp.TOTP(secret).at(stamp)}).status_code == 200
    assert c.get('/api/security/bans').json()['total'] == 0
    assert wrong(attacker).status_code == 401
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM auth_failures WHERE key=?', (m.protection.key('203.0.113.10'),)).fetchone()[0] == 1


def test_untrusted_forwarded_headers_cannot_avoid_a_ban():
    owner(); attacker = visitor()
    for i in range(8):
        attacker.headers['x-forwarded-for'] = '198.51.100.' + str(i+1)
        r = wrong(attacker)
    assert r.status_code == 429
    attacker.headers['x-forwarded-for'] = '198.51.100.99'
    assert wrong(attacker).status_code == 429


def test_trusted_proxy_chains_and_invalid_headers():
    networks = trusted_networks('172.30.73.2/32,2001:db8:1::/64')
    assert client_ip(request('203.0.113.10', '1.2.3.4'), networks) == '203.0.113.10'
    assert client_ip(request('172.30.73.2', '203.0.113.10'), networks) == '203.0.113.10'
    assert client_ip(request('172.30.73.2', '1.2.3.4,203.0.113.10'), networks) == '203.0.113.10'
    assert client_ip(request('172.30.73.2', '1.2.3.4,2001:db8:1::2'), networks) == '1.2.3.4'
    for value in ['not-an-ip', '1.2.3.4,not-an-ip', ',1.2.3.4', 'x'*2050]:
        assert client_ip(request('172.30.73.2', value), networks) == '172.30.73.2'
    with pytest.raises(RuntimeError):
        trusted_networks('trust-everything')


def test_concurrent_failures_commit_one_ban_without_lost_updates(monkeypatch):
    monkeypatch.setattr(m.protection, 'limit', 4)
    def fail_once(_):
        try: m.protection.failure(request(), 'login', 'owner')
        except HTTPError as error: return error.status_code
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(fail_once, range(8)))
    assert 429 in results
    with m.db() as db:
        row = db.execute("SELECT * FROM security_bans WHERE kind='target'").fetchone()
        assert row['strikes'] == 1
        assert db.execute('SELECT COUNT(*) FROM auth_failures WHERE key=?', (row['key'],)).fetchone()[0] == 0


def test_emergency_cli_can_release_an_ip_without_app_secrets():
    owner(); attacker = visitor()
    for _ in range(8): wrong(attacker)
    env = os.environ.copy(); env.pop('APP_SECRET', None)
    env['DATA_DIR'] = str(m.DATA)
    result = subprocess.run([os.sys.executable, 'scripts/security.py', 'unban', '--ip', '203.0.113.10'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert wrong(attacker).status_code == 401


def test_all_attempt_rate_returns_the_actual_remaining_window(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    protection=Protection(m.db,lambda:m.now())
    # Staggered historical requests can exceed a newly lowered limit.
    key=protection.key('203.0.113.10','upload','account')
    with m.db() as d:
        for offset in (0,10,20,30):d.execute('INSERT INTO throttle VALUES(?,?)',(key,stamp+offset))
    stamp+=40
    with pytest.raises(HTTPError) as caught:protection.rate(request(),'upload',2,period=60,subject='account')
    assert caught.value.headers['Retry-After']=='40'
    stamp+=40
    protection.rate(request(),'upload',2,period=60,subject='account')
