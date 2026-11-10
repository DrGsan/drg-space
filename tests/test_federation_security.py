"""Possession, durable replay and address restrictions of the API prototype."""
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import ssl
from types import SimpleNamespace

import httpx
import pytest

from app.data_crypto import DataCipher, StorageError
from app.federation.actors import DeviceKeys, canonical, seal_result, verify_device
from app.federation.identity import FederationError, SigningIdentity
from app.federation.state import PrototypeState
from app.federation.transport import PeerTransport

NOW = 1793000000


def test_stolen_device_proof_is_bound_to_intent_recipient_and_time():
    device = DeviceKeys.generate()
    card = device.card('a' * 32, 'b' * 32)
    intent = {'operation': 'read', 'nonce': 'c' * 64, 'note_id': 'secret-note'}
    proof = device.sign(card, 'd' * 32, intent, NOW)
    verify_device(card, 'd' * 32, intent, proof, NOW)
    for audience, body, now in [('e' * 32, intent, NOW), ('d' * 32, intent | {'operation': 'update'}, NOW),
                                ('d' * 32, intent, NOW + 120)]:
        with pytest.raises(FederationError):
            verify_device(card, audience, body, proof, now)
    attacker = DeviceKeys.generate()
    stolen_card = attacker.card(card.server_id, card.actor_id)
    forged = attacker.sign(stolen_card, 'd' * 32, intent, NOW)
    with pytest.raises(FederationError):
        verify_device(card, 'd' * 32, intent, forged, NOW)


def test_stolen_response_requires_recipient_private_key_and_exact_intent():
    device, attacker = DeviceKeys.generate(), DeviceKeys.generate()
    card = device.card('a' * 32, 'b' * 32)
    intent = {'nonce': 'c' * 64, 'event_id': 'd' * 32}
    result = {'body': 'PRIVATE_DOCUMENT_MARKER'}
    sealed = seal_result(card, result, intent)
    assert b'PRIVATE_DOCUMENT_MARKER' not in canonical(sealed)
    assert device.open(sealed, intent) == result
    with pytest.raises(FederationError):
        attacker.open(sealed, intent)
    with pytest.raises(FederationError):
        device.open(sealed, intent | {'event_id': 'e' * 32})
    with pytest.raises(FederationError):
        device.open(sealed | {'ciphertext': 'aGVsbG8='}, intent)


def test_server_identity_and_replay_survive_reopening_encrypted_state(tmp_path):
    cipher = DataCipher.ephemeral()
    state = PrototypeState(tmp_path, cipher)
    identity = state.identity('https://first.example')
    state.consume('peer', 'request', NOW + 120, NOW)
    reopened = PrototypeState(tmp_path, cipher)
    assert reopened.identity('https://first.example').descriptor == identity.descriptor
    with pytest.raises(FederationError, match='replayed_request'):
        reopened.consume('peer', 'request', NOW + 120, NOW)
    with pytest.raises(FederationError, match='server_origin_changed'):
        reopened.identity('https://moved.example')
    body = state.path.read_bytes()
    assert not body.startswith(b'SQLite format')
    assert identity.descriptor.server_id.encode() not in body
    with pytest.raises(Exception):
        PrototypeState(tmp_path, DataCipher.ephemeral())


def test_durable_replay_serializes_concurrent_connections(tmp_path):
    state = PrototypeState(tmp_path, DataCipher.ephemeral())
    def deliver(_):
        try:
            state.consume('peer', 'same-request', NOW + 120, NOW)
            return 'accepted'
        except FederationError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(deliver, range(12)))
    assert results.count('accepted') == 1
    assert results.count('replayed_request') == 11


def test_nonce_is_one_use_bound_to_peer_and_durable(tmp_path):
    cipher = DataCipher.ephemeral()
    state = PrototypeState(tmp_path, cipher)
    with state.transaction() as connection:
        nonce = state.challenge(connection, 'alice', NOW)
    other = PrototypeState(tmp_path, cipher)
    with pytest.raises(FederationError):
        with other.transaction() as connection:
            other.use_challenge(connection, nonce, 'imposter', NOW)
    with other.transaction() as connection:
        other.use_challenge(connection, nonce, 'alice', NOW)
    with pytest.raises(FederationError):
        with state.transaction() as connection:
            state.use_challenge(connection, nonce, 'alice', NOW)
    with pytest.raises(StorageError):
        PrototypeState(tmp_path, DataCipher(enabled=False))


@pytest.mark.parametrize('addresses', [('127.0.0.1',), ('169.254.169.254',), ('10.0.0.1',),
                                        ('8.8.8.8', '127.0.0.1'), ('::1',)])
def test_outbound_transport_rejects_private_and_mixed_dns_answers(monkeypatch, addresses):
    transport = PeerTransport(SigningIdentity.generate('https://own.example'))
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in addresses])
    with pytest.raises(FederationError, match='unsafe_peer_address'):
        transport.addresses('https://other.example')


def test_private_exception_is_exact_and_detects_changed_dns(monkeypatch):
    origin = 'https://bob.federation.test:8443'
    transport = PeerTransport(SigningIdentity.generate('https://own.example'), private_origins={origin: {'10.2.0.3'}})
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.2.0.3', 8443))])
    assert transport.addresses(origin)[1] == ['10.2.0.3']
    with pytest.raises(FederationError):
        transport.addresses('https://different.federation.test:8443')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.2.0.4', 8443))])
    with pytest.raises(FederationError):
        transport.addresses(origin)


def test_transport_cannot_disable_certificate_verification():
    with pytest.raises(ValueError):
        PeerTransport(SigningIdentity.generate('https://own.example'), tls_context=ssl._create_unverified_context())


@pytest.mark.parametrize('changed', [
    {'operation': 'update'}, {'target_actor_id': 'c' * 32}, {'actor_server_id': 'c' * 32},
    {'device_id': 'c' * 64}, {'payload': {'note_id': 'another-document', 'revision': 1}},
])
def test_device_driver_rejects_intent_substitution_by_own_server(changed):
    from scripts.federation.prototype_client import User
    own = SigningIdentity.generate('https://own.example')
    other = SimpleNamespace(descriptor=SigningIdentity.generate('https://other.example').descriptor, uid='b' * 32)
    user = object.__new__(User)
    user.uid, user.descriptor = 'a' * 32, own.descriptor
    user.keys = DeviceKeys.generate()
    user.card = user.keys.card(own.descriptor.server_id, user.uid)
    payload = {'note_id': 'requested-document', 'revision': 1}
    intent = {'actor_server_id': own.descriptor.server_id, 'actor_id': user.uid,
              'device_id': user.card.device_id, 'target_actor_id': other.uid,
              'operation': 'read', 'payload': payload, 'nonce': 'd' * 64, 'event_id': 'e' * 32}
    user.http = SimpleNamespace(post=lambda *args, **kwargs: httpx.Response(200, json={'intent': intent | changed}))
    with pytest.raises(RuntimeError, match='substituted'):
        user.prepare(other, 'read', payload)
