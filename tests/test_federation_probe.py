"""Two isolated Flask installations, with no application DB, env, or main import."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.federation.identity import ServerDescriptor, SigningIdentity, check_pinned_descriptor
from app.federation.probe import create_probe_app
from app.federation.protocol import (DISCOVERY_PATH, MAX_BODY_BYTES, PROBE_PATH, VolatileReplayWindow,
                                     sign_request, verify_response)

STAMP = 1793000000
BODY = json.dumps({'challenge': 'b' * 64}).encode()


@pytest.fixture
def pair():
    alice = SigningIdentity.generate('https://alice.example')
    bob = SigningIdentity.generate('https://bob.example')
    apps = [create_probe_app(own, trusted_peers=(other.descriptor,), enabled=True, clock=lambda: STAMP)
            for own, other in ((alice, bob), (bob, alice))]
    return alice, bob, apps


def send(client, sender, recipient, body=BODY, signed=None, **kwargs):
    signed = signed or sign_request(sender, recipient.descriptor, 'POST', PROBE_PATH, body, now=STAMP)
    headers = {'DrG-Server-Id': sender.descriptor.server_id, 'Authorization': 'DrG-Federation ' + signed.token,
               'Content-Type': 'application/json'}
    return client.post(PROBE_PATH, data=body, headers=headers | kwargs), signed


def test_two_servers_confirm_pins_and_exchange_signed_challenges_in_both_directions(pair):
    alice, bob, apps = pair
    for own, other, app in ((alice, bob, apps[1]), (bob, alice, apps[0])):
        with app.test_client() as client:
            discovered = client.get(DISCOVERY_PATH)
            assert discovered.status_code == 200
            observed = ServerDescriptor.model_validate_json(discovered.data)
            assert check_pinned_descriptor(other.descriptor, observed) == 1
            response, signed = send(client, own, other)
            assert response.status_code == 200
            verify_response(response.headers['DrG-Federation-Proof'], own.descriptor, other.descriptor,
                            'POST', PROBE_PATH, response.data, now=STAMP, replay=VolatileReplayWindow(),
                            request_id=signed.request_id, status=response.status_code)
            assert response.json == {'challenge': 'b' * 64, 'protocol_version': 1,
                                      'server_id': other.descriptor.server_id, 'capabilities': ['probe']}
            assert response.headers['Cache-Control'] == 'no-store'
            assert 'Set-Cookie' not in response.headers
            repeated, _ = send(client, own, other, signed=signed)
            assert repeated.status_code == 409


def test_probe_disabled_by_default_even_for_known_peer(pair):
    alice, bob, _ = pair
    app = create_probe_app(bob, trusted_peers=(alice.descriptor,))
    with app.test_client() as client:
        assert client.get(DISCOVERY_PATH).status_code == 404
        assert send(client, alice, bob)[0].status_code == 404
        assert client.options(PROBE_PATH).status_code == 404


def test_discovery_does_not_grant_trust(pair):
    alice, bob, _ = pair
    app = create_probe_app(bob, enabled=True)
    with app.test_client() as client:
        assert client.get(DISCOVERY_PATH).status_code == 200
        response, _ = send(client, alice, bob)
        assert response.status_code == 403
        assert 'DrG-Federation-Proof' not in response.headers


def test_unknown_server_and_stolen_identity_with_replacement_key(pair):
    alice, bob, apps = pair
    imposter = SigningIdentity.generate('https://alice.example')
    with apps[1].test_client() as client:
        assert send(client, imposter, bob)[0].status_code == 403
        response, _ = send(client, imposter, bob, **{'DrG-Server-Id': alice.descriptor.server_id})
        assert response.status_code == 401
        response, _ = send(client, alice, bob, **{'Authorization': ''})
        assert response.status_code == 401


def test_wire_limits_shape_and_request_bindings(pair):
    alice, bob, apps = pair
    with apps[1].test_client() as client:
        for value in ({}, {'challenge': 'bad'}, {'challenge': 'b' * 64, 'user_id': 'owner'}):
            assert send(client, alice, bob, body=json.dumps(value).encode())[0].status_code == 400
        signed = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
        assert send(client, alice, bob, body=BODY + b' ', signed=signed)[0].status_code == 401
        assert send(client, alice, bob, **{'Content-Type': 'text/plain'})[0].status_code == 400
        assert send(client, alice, bob, **{'Content-Encoding': 'gzip'})[0].status_code == 400
        assert client.post(PROBE_PATH, data=b'x' * (MAX_BODY_BYTES + 1), headers={
            'Content-Type': 'application/json', 'DrG-Server-Id': alice.descriptor.server_id,
            'Authorization': 'DrG-Federation ' + signed.token}).status_code == 413
        assert client.post(PROBE_PATH + '?user=owner', data=BODY, headers={
            'Content-Type': 'application/json', 'DrG-Server-Id': alice.descriptor.server_id,
            'Authorization': 'DrG-Federation ' + signed.token}).status_code == 400


def test_no_product_routes_or_storage_exposed_by_probe(pair):
    alice, bob, apps = pair
    expected = {DISCOVERY_PATH, PROBE_PATH}
    for app in apps:
        assert {rule.rule for rule in app.url_map.iter_rules()} == expected
        with app.test_client() as client:
            for path in ('/api/me', '/api/notes', '/api/social', '/api/chat/rooms', '/api/admin/backups'):
                assert client.get(path).status_code == 404


def test_exhausted_replay_capacity_rejects_without_evicting_existing_proof(pair):
    alice, bob, _ = pair
    app = create_probe_app(bob, enabled=True, trusted_peers=(alice.descriptor,),
                           clock=lambda: STAMP, replay=VolatileReplayWindow(capacity=1))
    with app.test_client() as client:
        first, signed = send(client, alice, bob)
        assert first.status_code == 200
        assert send(client, alice, bob)[0].status_code == 503
        assert send(client, alice, bob, signed=signed)[0].status_code == 409


def test_real_loopback_smoke_ignores_application_configuration(tmp_path):
    root = Path(__file__).resolve().parents[1]
    forbidden_data = tmp_path / 'must-not-be-created'
    environment = os.environ | {
        'DATA_DIR': str(forbidden_data), 'APP_SECRET': '', 'DATA_ENCRYPTION_KEY': '',
        'DATABASE_URL': 'postgresql://invalid.invalid/never-open', 'PUBLIC_URL': 'https://ignored.example',
    }
    result = subprocess.run([sys.executable, str(root / 'scripts/federation-smoke.py')],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS: two loopback servers' in result.stdout
    assert 'STOPPED: both servers closed' in result.stdout
    assert not forbidden_data.exists()
    assert list(tmp_path.iterdir()) == []
