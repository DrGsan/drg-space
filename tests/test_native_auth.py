"""Native handoff uses real signed WebAuthn proofs and one-use S256 grants."""
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from tests.test_security import owner, client, reset
from tests.test_passkeys import registered, options
from app import main as m
from app.native_auth import PREFIX
from app.passkeys import encode


def start(native):
    verifier = secrets.token_urlsafe(32)
    state = secrets.token_urlsafe(32)
    response = native.post('/api/native/passkeys/start', json={
        'challenge': encode(hashlib.sha256(verifier.encode()).digest()), 'state': state})
    assert response.status_code == 200, response.text
    flow = parse_qs(urlsplit(response.json()['url']).query)['flow'][0]
    return flow, verifier, state


def approve(browser, device, flow, count=1):
    ceremony = options(browser)
    return browser.post('/api/passkeys/verify', json={
        'credential': device.authenticate(ceremony['options'], count=count),
        'csrf': ceremony['ceremony_csrf'], 'native_flow': flow})


def exchange(native, code, verifier):
    return native.post('/api/native/passkeys/exchange', json={'code': code, 'verifier': verifier})


def test_native_passkey_creates_only_native_session_and_preserves_password_login():
    owner_client, _, codes = owner()
    device, _ = registered(owner_client, codes[0])
    native, browser = client(), client()
    flow, verifier, state = start(native)
    result = approve(browser, device, flow)
    assert result.status_code == 200, result.text
    grant = result.json()['native']
    assert grant['state'] == state and set(grant) == {'code', 'state'}
    assert browser.get('/api/me').status_code == 401
    assert native.get('/api/me').status_code == 401
    with m.db() as c:
        stored = c.execute('SELECT value FROM app_settings WHERE key LIKE ?', (PREFIX+'%',)).fetchall()
        assert len(stored) == 1
        assert verifier not in stored[0]['value'] and grant['code'] not in stored[0]['value']
    response = exchange(native, grant['code'], verifier)
    assert response.status_code == 200, response.text
    native.headers['x-csrf-token'] = response.json()['csrf']
    me = native.get('/api/me').json()
    assert me['verified'] and me['login'] == 'owner'
    sessions = native.get('/api/security/sessions').json()
    assert len(sessions) == 2  # original owner + app; no temporary browser session
    assert exchange(client(), grant['code'], verifier).status_code == 401
    assert native.post('/api/logout').status_code == 200
    assert native.post('/api/login', json={'login': 'owner', 'password': 'Owner-password-123'}).json()['needs_2fa']


def test_native_grant_is_bound_to_verifier_and_flow_is_consumed_once():
    c, _, codes = owner(); device, _ = registered(c, codes[0])
    native, browser = client(), client()
    flow, verifier, _ = start(native)
    result = approve(browser, device, flow).json()['native']
    assert exchange(native, result['code'], secrets.token_urlsafe(32)).status_code == 401
    assert native.get('/api/me').status_code == 401
    assert approve(browser, device, flow, count=2).status_code == 401
    assert exchange(native, result['code'], verifier).status_code == 200


@pytest.mark.parametrize('mutation', ['password', 'totp', 'role', 'passkey', 'expired', 'deleted'])
def test_pending_native_grant_rejects_security_changes_and_expiry(monkeypatch, mutation):
    c, _, codes = owner(); device, _ = registered(c, codes[0])
    native, browser = client(), client()
    flow, verifier, _ = start(native)
    grant = approve(browser, device, flow).json()['native']
    if mutation == 'expired':
        stamp = m.now() + 61; monkeypatch.setattr(m, 'now', lambda: stamp)
    else:
        with m.db() as db:
            if mutation in ('password', 'totp', 'role'):
                db.execute('UPDATE users SET '+mutation+'=? WHERE login=?', ('member' if mutation == 'role' else 'changed', 'owner'))
            elif mutation == 'passkey':
                row = db.execute('SELECT id,settings FROM users WHERE login=?', ('owner',)).fetchone()
                settings = json.loads(row['settings']); settings['_passkeys'] = []
                db.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), row['id']))
            else: db.execute('DELETE FROM users WHERE login=?', ('owner',))
    assert exchange(native, grant['code'], verifier).status_code == 401
    assert native.get('/api/me').status_code == 401


def test_expired_start_bad_origin_and_invalid_webauthn_cannot_create_grant(monkeypatch):
    c, _, codes = owner(); device, _ = registered(c, codes[0])
    native, browser = client(), client()
    flow, _, _ = start(native)
    ceremony = options(browser)
    proof = device.authenticate(ceremony['options'], flags=1)
    response = browser.post('/api/passkeys/verify', json={
        'credential': proof, 'csrf': ceremony['ceremony_csrf'], 'native_flow': flow})
    assert response.status_code == 401
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?', (PREFIX+'grant:%',)).fetchone()
    native.headers['origin'] = 'https://evil.example'
    assert native.post('/api/native/passkeys/start', json={'challenge': 'a'*43, 'state': 'b'*43}).status_code == 403
    stamp = m.now()+181; monkeypatch.setattr(m, 'now', lambda: stamp)
    assert approve(browser, device, flow, count=2).status_code == 401


def test_native_state_uses_existing_cleanup():
    from app.retention import purge_service_records
    native = client(); flow, _, _ = start(native)
    with m.db() as db:
        assert db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?', (PREFIX+'%',)).fetchone()
        purge_service_records(db, m.now()+181)
        assert not db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?', (PREFIX+'%',)).fetchone()


def test_backup_restore_excludes_pending_native_grants_and_preserves_passkeys():
    from tests.test_admin import backup, headers
    from tests.test_passkeys import finish
    c, _, codes = owner(); device, _ = registered(c, codes[0])
    native, browser = client(), client()
    flow, verifier, _ = start(native)
    grant = approve(browser, device, flow).json()['native']
    start(native)  # Both start records and approved grants must stay out of a full backup.
    body = backup(c)
    result = c.post('/api/admin/backups/restore', content=body, headers=headers())
    assert result.status_code == 200, result.text
    assert exchange(native, grant['code'], verifier).status_code == 401
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?', (PREFIX+'%',)).fetchone()
    browser = client(); ceremony = options(browser)
    assert finish(browser, ceremony, device.authenticate(ceremony['options'], count=2)).status_code == 200
