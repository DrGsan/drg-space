"""Security audit privacy, detection, owner gate, controls and retention."""
import json
from contextlib import contextmanager

import pytest
import pyotp
from app.http import HTTPError, Request
from tests.test_security import owner, member, client, setup, reset
from app import main as m
from app.security_monitor import SecurityMonitor, MODE_KEY, purge_security

PASSWORD = 'Owner-password-123'


def fresh_factor(secret, monkeypatch):
    stamp = m.now()+31
    monkeypatch.setattr(m, 'now', lambda: stamp)
    return pyotp.TOTP(secret).at(stamp)


@pytest.fixture(autouse=True)
def clear_security():
    with m.db() as c:
        c.execute('DELETE FROM security_events')
        c.execute('DELETE FROM security_incidents')
    yield


def request(ip='203.0.113.5', path='/api/login'):
    return Request({'client': (ip, 12345), 'path': path, 'headers': [(b'user-agent', b'Browser')], 'method': 'POST'})


def test_audit_aggregates_and_never_records_publication_secrets_or_raw_urls():
    monitor = SecurityMonitor(m.db, lambda: m.now(), m.protection)
    secret = 'private-share-token-do-not-log'
    req = request(path='/api/shares/'+secret+'/unlock')
    for _ in range(3):
        assert monitor.record(req, 'auth_failure', 'share-password', secret, details={'password': secret, 'token': secret, 'status': 403})
    with m.db() as c:
        rows = [dict(row) for row in c.execute('SELECT * FROM security_events')]
    assert len(rows) == 1 and rows[0]['count'] == 3
    assert rows[0]['route'] == '/{unmatched}' and rows[0]['subject'] == ''
    assert secret not in json.dumps(rows)
    assert json.loads(rows[0]['details']) == {'status': 403}


def test_distributed_slow_attack_and_success_are_correlated_without_alert_spam():
    admin, _, _ = owner()
    monitor = SecurityMonitor(m.db, lambda: m.now(), m.protection)
    with m.db() as c:
        c.execute('DELETE FROM security_events'); c.execute('DELETE FROM security_incidents')
        uid = c.execute("SELECT id FROM users WHERE role='owner'").fetchone()[0]
    for i in range(12):
        monitor.record(request('203.0.113.'+str(i+1)), 'auth_failure', 'login', 'owner')
    with m.db() as c:
        distributed = c.execute("SELECT * FROM security_incidents WHERE kind='distributed_attack'").fetchone()
        alerts = c.execute("SELECT COUNT(*) FROM notifications WHERE kind='security'").fetchone()[0]
    assert distributed and distributed['ip_count'] == 12 and distributed['severity'] == 'high'
    for _ in range(5): monitor.record(request('203.0.113.12'), 'auth_failure', 'login', 'owner')
    monitor.record(request('203.0.113.55'), 'login_success', user_id=uid, outcome='success')
    with m.db() as c:
        assert c.execute("SELECT 1 FROM security_incidents WHERE kind='success_after_failures'").fetchone()
        assert c.execute("SELECT COUNT(*) FROM notifications WHERE kind='security'").fetchone()[0] <= alerts+2
        assert not c.execute("SELECT 1 FROM notifications n JOIN users u ON u.id=n.user_id WHERE n.kind='security' AND u.role!='owner'").fetchone()


def test_second_factor_failure_is_distinct_from_invalid_password():
    admin, _, _ = owner()
    monitor = SecurityMonitor(m.db, lambda: m.now(), m.protection)
    with m.db() as c: uid = c.execute("SELECT id FROM users WHERE role='owner'").fetchone()[0]
    for _ in range(3): monitor.record(request(), 'auth_failure', '2fa', uid)
    with m.db() as c:
        incident = c.execute("SELECT * FROM security_incidents WHERE kind='factor_failures'").fetchone()
        assert incident['subject'] == 'owner' and incident['severity'] == 'high'


def test_audit_failure_does_not_abort_caller_transaction_and_health_is_visible(monkeypatch):
    monitor = SecurityMonitor(m.db, lambda: m.now(), m.protection)
    original = monitor._record
    def fail(c, *args):
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', ('audit-partial', '{}'))
        raise RuntimeError('do not leak credential')
    monkeypatch.setattr(monitor, '_record', fail)
    with m.db() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', ('caller-before', '{}'))
        assert monitor.record(request(), 'auth_failure', connection=c) is None
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', ('caller-after', '{}'))
    with m.db() as c:
        assert not c.execute('SELECT 1 FROM app_settings WHERE key=?', ('audit-partial',)).fetchone()
        assert c.execute('SELECT COUNT(*) FROM app_settings WHERE key IN (?,?)', ('caller-before', 'caller-after')).fetchone()[0] == 2
    assert monitor.health()['ok'] is False and monitor.health()['failed_writes'] == 1
    monkeypatch.setattr(monitor, '_record', original)


def test_owner_only_gate_including_journal_export_and_session_listing():
    admin, _, _ = owner(); guest, _ = member(admin)
    paths = ('', '/events', '/events/export', '/incidents', '/bans', '/sessions')
    for path in paths:
        assert client().get('/api/admin/security'+path).status_code == 401
        assert guest.get('/api/admin/security'+path).status_code == 403
        assert admin.get('/api/admin/security'+path).status_code == 200
    assert admin.post('/api/admin/access/lock').status_code == 200
    for path in paths: assert admin.get('/api/admin/security'+path).status_code == 423


def test_controls_require_fresh_factor_and_mode_retains_exact_owner_session(monkeypatch):
    admin, secret, _ = owner()
    payload = dict(mode='logins_closed', password=PASSWORD, reason='Проверка', duration=600)
    assert admin.post('/api/admin/security/mode', json=payload).status_code == 403
    response = admin.post('/api/admin/security/mode', json={**payload, 'code': fresh_factor(secret, monkeypatch)})
    assert response.status_code == 200, response.text
    assert response.json()['mode']['mode'] == 'logins_closed'
    assert 'allowed_session' not in response.text
    with m.db() as c:
        state = json.loads(c.execute('SELECT value FROM app_settings WHERE key=?', (MODE_KEY,)).fetchone()[0])
        assert c.execute("SELECT 1 FROM sessions WHERE token=? AND stage='full'", (state['allowed_session'],)).fetchone()
    assert admin.post('/api/admin/security/mode', json=dict(mode='normal', password=PASSWORD, code=fresh_factor(secret, monkeypatch))).status_code == 200


def test_manual_ban_and_revoke_keep_current_admin_working(monkeypatch):
    admin, secret, _ = owner()
    response = admin.post('/api/admin/security/bans', json=dict(ip='203.0.113.90', duration=600, password=PASSWORD, code=fresh_factor(secret, monkeypatch), reason='Ручная проверка'))
    assert response.status_code == 200, response.text
    bans = admin.get('/api/admin/security/bans').json()['bans']
    assert any(ban['ip'] == '203.0.113.90' for ban in bans)
    sessions = admin.get('/api/admin/security/sessions').json()['sessions']
    current = next(item for item in sessions if item['current'])
    assert len(current['id']) == 64 and 'token' not in current
    response = admin.post('/api/admin/security/sessions/'+current['id']+'/revoke', json=dict(password=PASSWORD, code=fresh_factor(secret, monkeypatch)))
    assert response.status_code == 409, response.text
    assert admin.get('/api/admin/security').status_code == 200
    response = admin.request('DELETE', '/api/admin/security/bans/'+bans[0]['id'], json=dict(password=PASSWORD, code=fresh_factor(secret, monkeypatch)))
    assert response.status_code == 200, response.text


def test_corrupt_control_state_is_not_treated_as_normal():
    monitor = SecurityMonitor(m.db, lambda: m.now(), m.protection)
    with m.db() as c: c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', (MODE_KEY, '{"mode":"unknown"}'))
    with pytest.raises(RuntimeError): monitor.get_mode()


def test_retention_preserves_recent_and_purges_expired_security_records(monkeypatch):
    stamp = [m.now()]
    monitor = SecurityMonitor(m.db, lambda: stamp[0], m.protection)
    monitor.record(request(), 'refresh_replay', severity='critical')
    stamp[0] += 364*86400
    monitor.record(request('203.0.113.6'), 'auth_failure')
    with m.db() as c:
        purge_security(c, stamp[0])
        assert c.execute('SELECT COUNT(*) FROM security_events').fetchone()[0] == 2
        assert c.execute('SELECT COUNT(*) FROM security_incidents').fetchone()[0] == 1
        purge_security(c, stamp[0]+2*86400)
        assert c.execute('SELECT COUNT(*) FROM security_events').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM security_incidents').fetchone()[0] == 0


def test_expired_session_noise_and_blocked_requests_do_not_count_as_brute_force():
    monitor = SecurityMonitor(m.db, lambda: m.now(), m.protection)
    for _ in range(35):
        monitor.record(request(), 'session_rejected', outcome='blocked')
        monitor.record(request(), 'request_blocked', outcome='blocked')
    with m.db() as c:
        assert c.execute('SELECT COUNT(*) FROM security_incidents').fetchone()[0] == 0


def test_alert_flood_is_bounded_while_all_incidents_remain_visible():
    admin, _, _ = owner()
    monitor = SecurityMonitor(m.db, lambda: m.now(), m.protection)
    with m.db() as c: c.execute("DELETE FROM notifications WHERE kind='security'")
    for i in range(20):
        monitor.record(request('203.0.113.'+str(i+1)), 'refresh_replay', severity='critical')
    for _ in range(3): monitor.record(request('203.0.113.1'), 'refresh_replay', severity='critical')
    with m.db() as c:
        assert c.execute("SELECT COUNT(*) FROM notifications WHERE kind='security'").fetchone()[0] == 10
        assert c.execute("SELECT COUNT(*) FROM security_incidents WHERE kind='refresh_replay'").fetchone()[0] == 20
        assert c.execute("SELECT event_count FROM security_incidents WHERE kind='refresh_replay' AND ip=?", ('203.0.113.1',)).fetchone()[0] == 4


def test_invalid_refresh_csrf_is_audited_after_writer_transaction_closes(monkeypatch):
    admin, _, _ = owner()
    original_db, original_record = m.tokens.db, m.security_monitor.record
    depth = [0]
    writes = []
    @contextmanager
    def tracked_db():
        with original_db() as c:
            depth[0] += 1
            try: yield c
            finally: depth[0] -= 1
    def record(*args, **kwargs):
        assert depth[0] == 0, 'Security audit must not open a second writer inside refresh'
        writes.append(args[1])
        return original_record(*args, **kwargs)
    monkeypatch.setattr(m.tokens, 'db', tracked_db)
    monkeypatch.setattr(m.security_monitor, 'record', record)
    admin.headers['x-csrf-token'] = 'invalid'
    response = admin.post('/api/refresh')
    assert response.status_code == 403, response.text
    assert writes == ['csrf_rejected']
    with m.db() as c:
        assert c.execute("SELECT 1 FROM security_events WHERE event='csrf_rejected'").fetchone()


def test_security_mutation_csrf_rejection_is_not_skipped_by_admin_audit_filter():
    admin, _, _ = owner()
    admin.headers['x-csrf-token'] = 'invalid'
    response = admin.post('/api/admin/security/mode', json=dict(mode='normal', password=PASSWORD))
    assert response.status_code == 403, response.text
    with m.db() as c:
        assert c.execute("SELECT 1 FROM security_events WHERE event='csrf_rejected'").fetchone()


@pytest.mark.parametrize('mode', ['logins_closed', 'lockdown'])
def test_full_restore_cannot_reopen_restricted_service_without_mode_confirmation(mode, monkeypatch):
    admin, secret, _ = owner()
    response = admin.post('/api/admin/security/mode', json=dict(mode=mode, password=PASSWORD, code=fresh_factor(secret, monkeypatch)))
    assert response.status_code == 200, response.text
    response = admin.post('/api/admin/backups/restore', content=b'not-an-archive')
    assert response.status_code == 409, response.text
    assert m.security_monitor.get_mode()['mode'] == mode


def test_designated_owner_expired_access_recovers_shell_then_uses_normal_refresh(monkeypatch):
    admin, secret, _ = owner()
    response = admin.post('/api/admin/security/mode', json=dict(mode='lockdown', password=PASSWORD, code=fresh_factor(secret, monkeypatch)))
    assert response.status_code == 200, response.text
    stamp = m.now()+901
    monkeypatch.setattr(m, 'now', lambda: stamp)
    assert admin.get('/').status_code == 200
    assert admin.get('/api/admin/security').status_code == 401
    assert admin.get('/api/shared/nonexistent').status_code == 401
    assert admin.post('/api/refresh').status_code == 200
    assert admin.post('/api/admin/access/unlock', json=dict(password=PASSWORD)).status_code == 200
    assert admin.get('/api/admin/security').status_code == 200
    assert client().get('/').status_code == 503


def test_ip_ban_closes_active_websocket_and_rejects_new_upgrade():
    from app.http import WebSocketDisconnect
    admin, _, _ = owner(); guest, _ = member(admin)
    with guest.websocket_connect('/api/live/inbox') as socket:
        socket.send_json(dict(type='auth', csrf=guest.headers['x-csrf-token']))
        assert socket.receive_json()['type'] == 'ready'
        with m.db() as c:
            ip = '127.0.0.1'
            c.execute('INSERT INTO security_bans VALUES(?,?,?,?,?,?,?)', (m.protection.key(ip), 'ip', ip, 'manual', 1, m.now()+600, m.now()))
        with pytest.raises(WebSocketDisconnect):
            while True: socket.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with guest.websocket_connect('/api/live/inbox'):
            pytest.fail('A blocked IP must not open a WebSocket')


def test_inflight_restore_and_owner_session_rotation_recheck_lockdown(monkeypatch):
    from app.http import Response
    admin, secret, _ = owner()
    req = request(path='/api/admin/backups/restore')
    req.cookies = dict(admin.cookies)
    req.headers['x-csrf-token'] = admin.headers['x-csrf-token']
    session = m.owner(req)  # A long restore upload starts while access is normal.
    response = admin.post('/api/admin/security/mode', json=dict(mode='lockdown', password=PASSWORD, code=fresh_factor(secret, monkeypatch)))
    assert response.status_code == 200, response.text
    with pytest.raises(HTTPError) as caught:
        m.owner(req)  # The restore's second authorization must now reject it.
    assert caught.value.status_code == 409
    with pytest.raises(HTTPError) as caught:
        m.tokens.issue(Response(), session['user_id'], True, created=session['created'])
    assert caught.value.status_code == 503
    assert admin.get('/api/admin/security').status_code == 200


@pytest.mark.parametrize('operation', ['mode', 'ban'])
def test_security_control_rechecks_designated_session_after_concurrent_lockdown(operation, monkeypatch):
    admin, secret, codes = owner()
    other = client()
    response = other.post('/api/login', json=dict(login='owner', password=PASSWORD))
    other.headers['x-csrf-token'] = response.json()['csrf']
    response = other.post('/api/2fa/verify', json=dict(code=codes[0]))
    assert response.status_code == 200, response.text
    other.headers['x-csrf-token'] = response.json()['csrf']
    req = request(path='/api/admin/security/mode')
    req.cookies = dict(other.cookies)
    req.headers['x-csrf-token'] = other.headers['x-csrf-token']
    designated = m.tokens.session(req)
    original = m.tokens.require_trusted
    switched = []

    def concurrent_lockdown(c, session):
        original(c, session)
        # Request A has passed its fresh confirmation. Request B commits
        # lockdown before A starts the control's BEGIN IMMEDIATE transaction.
        m.security_monitor.set_mode('lockdown', designated, reason='Другой сеанс закрыл сервис', connection=c)
        switched.append(True)

    monkeypatch.setattr(m.tokens, 'require_trusted', concurrent_lockdown)
    proof = dict(password=PASSWORD, code=fresh_factor(secret, monkeypatch))
    if operation == 'mode':
        response = admin.post('/api/admin/security/mode', json=dict(mode='normal', **proof))
    else:
        response = admin.post('/api/admin/security/bans', json=dict(ip='203.0.113.77', duration=600, **proof))
    assert switched == [True]
    assert response.status_code == 503, response.text
    assert response.headers['x-service-mode'] == 'lockdown'
    state = m.security_monitor.get_mode()
    assert state['mode'] == 'lockdown' and state['allowed_session'] == designated['token']
    with m.db() as c:
        assert not c.execute('SELECT 1 FROM security_bans WHERE key=?', (m.protection.key('203.0.113.77'),)).fetchone()
