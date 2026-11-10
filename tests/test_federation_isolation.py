"""Federation is present but never enabled by an unrelated probe or import."""
from tests.test_security import client, m, reset
from app.federation.identity import SigningIdentity
from app.federation.probe import create_probe_app
from app.federation.protocol import DISCOVERY_PATH, PROBE_PATH
from app.federation.prototype import CHALLENGE_PATH, OPERATION_PATH


def test_creating_enabled_probe_cannot_enable_main_or_create_application_users():
    identity = SigningIdentity.generate('https://probe.example')
    create_probe_app(identity, enabled=True)
    with m.db() as connection:
        assert connection.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
    with client() as browser:
        assert browser.get(DISCOVERY_PATH).status_code == 404
        assert browser.post(PROBE_PATH, json={'challenge': 'a' * 64}).status_code == 404
        assert browser.post(OPERATION_PATH, json={}).status_code == 403
        assert browser.post(CHALLENGE_PATH, json={}).status_code == 403
        assert browser.get('/api/federation/info').status_code == 401
        assert browser.post('/api/admin/federation/enable', json={'enabled': True}).status_code == 401
        assert browser.get('/api/health').json() == {'ok': True}
