import jwt
import pytest
from tests.test_security import owner, member, client, setup, enroll, reset
from app import main as m
from app.auth_tokens import ACCESS_SECONDS, CHALLENGE_SECONDS, SESSION_SECONDS


def claims(raw):
    return jwt.decode(raw, options={'verify_signature': False})


def copied(access=None, refresh=None, csrf=''):
    c = client()
    for name, value, path in [('drg_session', access, '/'), ('drg_refresh', refresh, '/api/refresh')]:
        if value:
            c.cookies.set(name, value, domain='testserver.local', path=path)
    c.headers['x-csrf-token'] = csrf
    return c


def test_jwt_cookie_lifetime_claims_and_sensitive_data_stays_server_side():
    c, _, _ = owner()
    token = c.cookies.get('drg_session')
    data = claims(token)
    assert data['exp'] - data['iat'] == ACCESS_SECONDS
    assert data['stage'] == 'full' and data['aud'] == 'drg-site:access'
    assert {'password', 'totp', 'login', 'role', 'settings'}.isdisjoint(data)
    assert claims(c.cookies.get('drg_refresh'))['exp'] - data['iat'] == SESSION_SECONDS
    with m.db() as db:
        assert db.execute('SELECT token FROM sessions').fetchone()[0] == m.digest(data['sid'])
        assert token not in repr([tuple(row) for row in db.execute('SELECT * FROM sessions')])
    assert 'drg_session' not in c.get('/api/me').json()


def test_enrollment_and_pending_jwts_cannot_refresh_or_read_private_data():
    c = client(); setup(c)
    enrollment = c.cookies.get('drg_session')
    assert claims(enrollment)['stage'] == 'enrollment'
    assert claims(enrollment)['exp'] - claims(enrollment)['iat'] == CHALLENGE_SECONDS
    assert not c.cookies.get('drg_refresh')
    assert c.get('/api/notes').status_code == 403
    assert c.get('/api/security/bans').status_code == 403
    enroll(c)
    assert copied(enrollment).get('/api/me').status_code == 401
    c.post('/api/logout')
    r = c.post('/api/login', json={'login': 'owner', 'password': 'Owner-password-123'})
    c.headers['x-csrf-token'] = r.json()['csrf']
    assert claims(c.cookies.get('drg_session'))['stage'] == 'pending'
    assert not c.cookies.get('drg_refresh')
    assert c.get('/api/tasks').status_code == 401
    assert c.post('/api/refresh').status_code == 401


@pytest.mark.parametrize('change', [
    {'iss': 'someone-else'}, {'aud': 'drg-site:refresh'}, {'type': 'share'},
    {'sub': 'another-user'}, {'sid': 'x'*43}, {'stage': 'pending'},
    {'exp': 'not-a-timestamp'}, {'iat': None}, {'jti': 42},
])
def test_signed_but_invalid_claims_are_rejected(change):
    c, _, _ = owner()
    data = claims(c.cookies.get('drg_session')) | change
    raw = jwt.encode(data, m.tokens.keys['access'], algorithm='HS256')
    assert copied(raw).get('/api/notes').status_code == 401


def test_wrong_signature_algorithm_missing_claims_and_future_tokens_rejected():
    c, _, _ = owner(); data = claims(c.cookies.get('drg_session'))
    bad = [jwt.encode(data, 'x'*64, algorithm='HS256'),
           jwt.encode(data, m.tokens.keys['access'] + b'extra-test-key-material', algorithm='HS384'),
           jwt.encode(data, '', algorithm='none'),
           jwt.encode({k: v for k, v in data.items() if k != 'exp'}, m.tokens.keys['access'], algorithm='HS256'),
           jwt.encode(data | {'nbf': m.now()+60}, m.tokens.keys['access'], algorithm='HS256'),
           jwt.encode(data | {'iat': m.now()+60}, m.tokens.keys['access'], algorithm='HS256')]
    for raw in bad:
        assert copied(raw).get('/api/me').status_code == 401
    assert copied(c.cookies.get('drg_refresh')).get('/api/me').status_code == 401
    # Authorization header cannot bypass the cookie/CSRF browser contract.
    anonymous = client(); anonymous.headers['authorization'] = 'Bearer ' + c.cookies.get('drg_session')
    assert anonymous.get('/api/me').status_code == 401


def test_expired_access_refreshes_rotates_and_replay_revokes_entire_family(monkeypatch):
    c, _, _ = owner()
    access, refresh, csrf = c.cookies.get('drg_session'), c.cookies.get('drg_refresh'), c.headers['x-csrf-token']
    deadline = claims(refresh)['exp']
    stamp = m.now() + ACCESS_SECONDS + 1
    monkeypatch.setattr(m, 'now', lambda: stamp)
    assert c.get('/api/notes').status_code == 401
    assert c.post('/api/refresh').status_code == 200
    assert c.cookies.get('drg_refresh') != refresh
    assert claims(c.cookies.get('drg_refresh'))['exp'] == stamp+SESSION_SECONDS
    assert claims(c.cookies.get('drg_refresh'))['exp'] > deadline
    assert c.get('/api/notes').status_code == 200
    assert copied(access).get('/api/me').status_code == 401
    replay = copied(refresh=refresh, csrf=csrf)
    assert replay.post('/api/refresh').status_code == 401
    assert c.get('/api/me').status_code == 401
    assert c.post('/api/refresh').status_code == 401
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM refresh_tokens').fetchone()[0] == 0


def test_refresh_requires_csrf_origin_and_rejects_expired_tokens(monkeypatch):
    # Refresh has a sliding seven-day inactivity deadline. Freeze the immediate
    # refresh so crossing a wall-clock second cannot change the expected expiry.
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c, _, _ = owner(); refresh = c.cookies.get('drg_refresh')
    del c.headers['x-csrf-token']
    assert c.post('/api/refresh').status_code == 403
    c.headers['x-csrf-token'] = c.cookies.get('drg_csrf')
    c.headers['origin'] = 'https://evil.example'
    assert c.post('/api/refresh').status_code == 403
    c.headers['origin'] = 'http://testserver'
    assert c.post('/api/refresh').status_code == 200
    assert claims(c.cookies.get('drg_refresh'))['exp'] == claims(refresh)['exp']
    monkeypatch.setattr(m, 'now', lambda: claims(refresh)['exp'])
    assert c.post('/api/refresh').status_code == 401


def test_logout_revokes_copied_access_and_refresh_and_clears_cookies():
    c, _, _ = owner()
    copied_browser = copied(c.cookies.get('drg_session'), c.cookies.get('drg_refresh'), c.headers['x-csrf-token'])
    assert c.post('/api/logout').status_code == 200
    assert not c.cookies.get('drg_session') and not c.cookies.get('drg_refresh') and not c.cookies.get('drg_csrf')
    assert copied_browser.get('/api/notes').status_code == 401
    assert copied_browser.post('/api/refresh').status_code == 401


def test_users_only_revoke_their_own_sessions():
    c, _, codes = owner(); guest, _ = member(c)
    second = client()
    r = second.post('/api/login', json={'login': 'owner', 'password': 'Owner-password-123'})
    second.headers['x-csrf-token'] = r.json()['csrf']
    r = second.post('/api/2fa/verify', json={'code': codes[0]})
    second.headers['x-csrf-token'] = r.json()['csrf']
    rows = c.get('/api/security/sessions').json()
    assert len(rows) == 2
    other = next(row for row in rows if not row['current'])['id']
    assert guest.delete('/api/security/sessions/' + other).status_code == 404
    assert c.delete('/api/security/sessions/' + other).status_code == 200
    assert second.get('/api/me').status_code == 401
    assert second.post('/api/refresh').status_code == 401
    assert c.get('/api/me').status_code == 200


def test_secure_cookie_flags_and_no_jwt_exposure_to_js(monkeypatch):
    monkeypatch.setattr(m.tokens, 'secure', True)
    c = client(); setup(c)
    # Inspect the setup response directly; Secure cookies aren't sent over test HTTP.
    with m.db() as db:
        uid = db.execute('SELECT id FROM users').fetchone()[0]
        db.execute("UPDATE users SET totp='encrypted-test-placeholder' WHERE id=?", (uid,))
    from app.http import Response
    response = Response(); m.issue(response, uid, True)
    cookies = response.headers.getlist('set-cookie')
    assert all('Secure' in value for value in cookies)
    assert all('HttpOnly' in value for value in cookies if value.startswith(('drg_session=', 'drg_refresh=')))
    assert all('HttpOnly' not in value for value in cookies if value.startswith('drg_csrf='))
