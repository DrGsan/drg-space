import json
import pytest
from tests.test_security import owner, member, reset
from tests.test_jwt import copied
from app import main as m
from app.auth_tokens import session_device

SAFARI_MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15'
SAFARI_IOS = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'

@pytest.mark.parametrize('agent,platform,browser,client', [
    (SAFARI_MAC, 'macOS', 'Safari', 'web'),
    (SAFARI_IOS, 'iOS', 'Safari', 'web'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0 Safari/537.36', 'Windows', 'Chrome', 'web'),
    ('Mozilla/5.0 (Windows NT 10.0) Chrome/140 Safari/537.36 Edg/140.0', 'Windows', 'Edge', 'web'),
    ('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) CriOS/140 Mobile/15E148 Safari/604.1', 'iOS', 'Chrome', 'web'),
    ('DrG-iOS/0.19.2', 'iOS', 'DrG', 'app'),
    ('DrG-WebApp; '+SAFARI_IOS, 'iOS', 'Safari', 'web-app'),
    ('DrG-WebApp/iPadOS; '+SAFARI_MAC, 'iPadOS', 'Safari', 'web-app'),
    ('Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0', 'Linux', 'Firefox', 'web'),
])
def test_device_labels(agent, platform, browser, client):
    assert session_device(agent) == dict(platform=platform, browser=browser, client=client, device=platform+' · '+browser+(' App' if client=='app' else ' · Web App' if client=='web-app' else ' · Web'))

def test_names_persist_are_private_and_cannot_rename_another_account():
    c, _, _ = owner(); g, _ = member(c)
    own = c.get('/api/security/sessions').json()[0]; foreign = g.get('/api/security/sessions').json()[0]
    assert c.patch('/api/security/sessions/'+own['id'], json={'name':'Домашний MacBook'}).status_code == 200
    assert c.get('/api/security/sessions').json()[0]['name'] == 'Домашний MacBook'
    assert c.patch('/api/security/sessions/'+foreign['id'], json={'name':'Чужой'}).status_code == 404
    assert g.get('/api/security/sessions').json()[0]['name'] == ''
    assert '_session_names' not in c.get('/api/me').text
    bad = copied(c.cookies.get('drg_session'))
    assert bad.patch('/api/security/sessions/'+own['id'], json={'name':'No CSRF'}).status_code == 403
    for name in ['x'*81, 'line\nbreak', '\x00']:
        assert c.patch('/api/security/sessions/'+own['id'], json={'name':name}).status_code == 422
    assert c.patch('/api/security/sessions/'+own['id'], json={'name':'  '}).status_code == 200
    assert c.get('/api/security/sessions').json()[0]['name'] == ''

def test_activity_client_and_ip_upgrade_existing_sessions_without_relogin(monkeypatch):
    c, _, _ = owner()
    row = c.get('/api/security/sessions').json()[0]; token = row['id']; stamp = m.now()
    with m.db() as db:
        db.execute('DELETE FROM session_info WHERE session_token=?', (token,))
        db.execute('DELETE FROM session_presence WHERE session_token=?', (token,))
    m.tokens._activity.clear()
    c.headers['user-agent'] = SAFARI_IOS; c.headers['x-drg-client'] = 'web-app'
    c.headers['x-forwarded-for'] = '203.0.113.99'
    row = c.get('/api/security/sessions').json()[0]
    assert row['client']=='web-app' and row['platform']=='iOS' and row['online'] and row['last_seen'] >= stamp
    assert row['ip'] != '203.0.113.99', 'Untrusted forwarding headers cannot forge the IP'
    del c.headers['x-drg-client']; m.tokens._activity.clear()
    assert c.get('/api/security/sessions').json()[0]['client']=='web-app', 'Direct downloads preserve the explicit standalone hint'
    c.headers['user-agent']='DrG-iOS/0.19.2'; m.tokens._activity.clear()
    assert c.get('/api/security/sessions').json()[0]['device']=='iOS · DrG App'
    c.headers['user-agent']=SAFARI_MAC; c.headers['x-drg-client']='web'; m.tokens._activity.clear()
    assert c.get('/api/security/sessions').json()[0]['device']=='macOS · Safari · Web'

def test_admin_sessions_reuse_device_name_and_do_not_leak_settings():
    c, _, _ = owner(); row = c.get('/api/security/sessions').json()[0]
    c.patch('/api/security/sessions/'+row['id'], json={'name':'Мой компьютер'})
    rows = c.get('/api/admin/security/sessions').json()['sessions']
    assert rows[0]['name']=='Мой компьютер' and rows[0]['device'] and 'settings' not in rows[0] and 'token' not in rows[0]


def test_metadata_upsert_keeps_postgres_encryption_bindings():
    from app.database import query_plan
    sql, bindings, _, count = query_plan('INSERT INTO session_info VALUES(?,?,?) ON CONFLICT(session_token) DO UPDATE SET agent=excluded.agent,ip=excluded.ip')
    assert bindings == {'p1': ('column:session_info.agent', False), 'p2': ('lookup:ip', True)}
    assert count == 3 and 'ON CONFLICT' in sql


def test_session_activity_is_throttled_and_advances_after_interval(monkeypatch):
    c, _, _ = owner(); first = c.get('/api/security/sessions').json()[0]; stamp = m.now()
    monkeypatch.setattr(m, 'now', lambda: stamp+10)
    assert c.get('/api/security/sessions').json()[0]['last_seen'] == first['last_seen']
    monkeypatch.setattr(m, 'now', lambda: stamp+31)
    assert c.get('/api/security/sessions').json()[0]['last_seen'] == stamp+31


def test_vault_auto_lock_records_session_activity_after_its_transaction(monkeypatch):
    """A fresh presence write must not wait on its own vault transaction lock."""
    from contextlib import contextmanager
    import threading
    from app.database import Database
    from tests.test_secret_vault import enable, unlock
    if not m.secret_vault.encrypted:
        pytest.skip('Vault requires encrypted storage')
    c, _, codes = owner(); enable(c, codes); headers, _ = unlock(c)
    local = threading.local(); observed = []
    transaction = Database._transaction
    activity = m.tokens.record_activity

    @contextmanager
    def tracked(database):
        with transaction(database) as connection:
            local.depth = getattr(local, 'depth', 0) + 1
            try: yield connection
            finally: local.depth -= 1

    def record(request, session):
        assert getattr(local, 'depth', 0) == 0, 'Session metadata cannot open a second writer inside a route transaction'
        observed.append(request.url.path)
        return activity(request, session)

    monkeypatch.setattr(Database, '_transaction', tracked)
    monkeypatch.setattr(m.tokens, 'record_activity', record)
    m.tokens._activity.clear()
    response = c.post('/api/secret-vault/notes/auto-lock', headers=headers)
    assert response.status_code == 200, response.text
    assert observed == ['/api/secret-vault/notes/auto-lock']
    assert c.get('/api/secret-vault/notes/items', headers=headers).status_code == 423
    assert c.get('/api/health').status_code == 200


def test_optional_activity_failure_preserves_successful_mutation_and_csrf(monkeypatch, caplog):
    c, _, _ = owner(); own = c.get('/api/security/sessions').json()[0]; attempts = []
    def unavailable(request, session):
        attempts.append(request.url.path)
        raise RuntimeError('simulated metadata storage failure')
    monkeypatch.setattr(m.tokens, 'record_activity', unavailable)
    assert c.get('/api/me').status_code == 200
    response = c.patch('/api/security/sessions/'+own['id'], json={'name':'Сохранено'})
    assert response.status_code == 200, response.text
    assert c.get('/api/security/sessions').json()[0]['name'] == 'Сохранено'
    before = len(attempts)
    assert c.patch('/api/security/sessions/'+own['id'], json={'name':'Не сохранять'}, headers={'x-csrf-token':'bad'}).status_code == 403
    assert len(attempts) == before, 'Rejected CSRF must not schedule activity'
    assert 'Session activity update failed (RuntimeError)' in caplog.text
    assert 'simulated metadata storage failure' not in caplog.text


def test_deferred_activity_does_not_restore_a_logged_out_session():
    c, _, _ = owner(); own = c.get('/api/security/sessions').json()[0]
    m.tokens._activity.clear()
    assert c.post('/api/logout').status_code == 200
    with m.db() as db:
        assert db.execute('SELECT 1 FROM sessions WHERE token=?', (own['id'],)).fetchone() is None
        assert db.execute('SELECT 1 FROM session_presence WHERE session_token=?', (own['id'],)).fetchone() is None
        assert db.execute('SELECT 1 FROM session_info WHERE session_token=?', (own['id'],)).fetchone() is None
