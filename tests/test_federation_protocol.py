"""Trust boundaries of the experimental server protocol, independent of storage."""
from concurrent.futures import ThreadPoolExecutor
import json

import jwt
import pytest
from pydantic import ValidationError

from app.federation.identity import (FederationError, ServerDescriptor, SigningIdentity,
                                     check_pinned_descriptor, normalize_origin)
from app.federation.protocol import (CLOCK_SKEW, MAX_BODY_BYTES, MAX_LIFETIME, MAX_TOKEN_BYTES,
                                     PROBE_PATH, REQUEST_TYPE, VolatileReplayWindow,
                                     sign_request, sign_response, verify_request, verify_response)

STAMP = 1793000000
BODY = b'{"challenge":"' + b'a' * 64 + b'"}'


@pytest.fixture
def servers():
    return SigningIdentity.generate('https://alice.example'), SigningIdentity.generate('https://bob.example')


def read_request(token, alice, bob, *, body=BODY, method='POST', target=PROBE_PATH,
                 now=STAMP, replay=None):
    return verify_request(token, bob.descriptor, alice.descriptor, method, target, body,
                          now=now, replay=replay if replay is not None else VolatileReplayWindow())


def altered_proof(alice, bob, changes=None, headers=None):
    signed = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    claims = jwt.decode(signed.token, options={'verify_signature': False})
    claims.update(changes or {})
    return jwt.encode(claims, alice.private_key, algorithm='EdDSA',
                      headers={'typ': REQUEST_TYPE, 'kid': alice.descriptor.key_id} | (headers or {}))


def test_independent_identities_and_public_descriptor_roundtrip(servers):
    alice, bob = servers
    assert alice.descriptor.server_id != bob.descriptor.server_id
    assert alice.descriptor.public_key != bob.descriptor.public_key
    assert 'private' not in alice.descriptor.model_dump_json()
    assert 'private_key=' not in repr(alice)
    observed = ServerDescriptor.model_validate_json(alice.descriptor.model_dump_json())
    assert check_pinned_descriptor(alice.descriptor, observed) == 1
    assert observed.capabilities == ('probe',)


@pytest.mark.parametrize('origin,expected', [
    ('https://EXAMPLE.com:443/', 'https://example.com'),
    ('https://example.com:8443', 'https://example.com:8443'),
    ('https://[2606:4700:4700::1111]', 'https://[2606:4700:4700::1111]'),
])
def test_origin_normalization(origin, expected):
    assert normalize_origin(origin) == expected


@pytest.mark.parametrize('origin', [
    'http://example.com', 'http://127.0.0.1:8000', 'https://127.0.0.1',
    'https://10.0.0.1', 'https://[::1]', 'https://169.254.169.254',
    'https://example.com:0', 'https://example.com:65536', 'https://user@example.com',
    'https://example.com/path', 'https://example.com?', 'https://example.com#',
    'https://example.com\\@evil.example', 'https://example.com/%2e',
    'https://example.com\n', 'https://пример.рф', 'https://.example.com',
    'https://example.com.', 'https://-bad.example', 'https://example..com',
])
def test_origin_rejects_ambiguous_or_nonpublic_addresses(origin):
    with pytest.raises(ValueError):
        normalize_origin(origin)


def test_loopback_http_requires_explicit_development_context():
    origin = 'http://127.0.0.1:8000'
    with pytest.raises(ValueError):
        SigningIdentity.generate(origin)
    local = SigningIdentity.generate(origin, allow_loopback_http=True)
    with pytest.raises(ValidationError):
        ServerDescriptor.model_validate_json(local.descriptor.model_dump_json())
    assert ServerDescriptor.model_validate_json(local.descriptor.model_dump_json(),
               context={'allow_loopback_http': True}) == local.descriptor
    with pytest.raises(ValueError):
        normalize_origin('http://localhost:8000', allow_loopback_http=True)


@pytest.mark.parametrize('change,error', [
    ({'server_id': 'f' * 32}, 'server_identity_changed'),
    ({'origin': 'https://new.example'}, 'server_origin_changed'),
    ({'protocol_versions': (2,)}, 'incompatible_protocol'),
])
def test_discovery_cannot_silently_change_trusted_identity(servers, change, error):
    alice, _ = servers
    observed = ServerDescriptor.model_validate(alice.descriptor.model_dump() | change)
    with pytest.raises(FederationError, match=error):
        check_pinned_descriptor(alice.descriptor, observed)


def test_replacement_key_and_mismatched_private_key_rejected(servers):
    alice, bob = servers
    replacement = ServerDescriptor.model_validate(alice.descriptor.model_dump() | {
        'public_key': bob.descriptor.public_key, 'key_id': bob.descriptor.key_id})
    with pytest.raises(FederationError, match='server_key_changed'):
        check_pinned_descriptor(alice.descriptor, replacement)
    with pytest.raises(ValueError):
        SigningIdentity(alice.descriptor, bob.private_key)
    with pytest.raises(ValidationError):
        ServerDescriptor.model_validate(alice.descriptor.model_dump() | {'public_key': bob.descriptor.public_key})


@pytest.mark.parametrize('changes', [
    {'server_id': 'OWNER'}, {'protocol_versions': (True,)}, {'protocol_versions': (1, 1)},
    {'capabilities': ('probe', 'probe')}, {'capabilities': ('read everything',)},
    {'public_key': '?' * 43}, {'unexpected': True},
])
def test_manifest_is_strict_and_bounded(servers, changes):
    alice, _ = servers
    with pytest.raises(ValidationError):
        ServerDescriptor.model_validate(alice.descriptor.model_dump() | changes)


def test_request_authenticates_server_without_user_privileges(servers):
    alice, bob = servers
    signed = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    verified = read_request(signed.token, alice, bob)
    assert verified.server_id == alice.descriptor.server_id
    assert verified.request_id == signed.request_id
    assert not hasattr(verified, 'user_id')
    assert not hasattr(verified, 'role')
    assert signed.token not in repr(signed)


@pytest.mark.parametrize('changes,error', [
    ({'body': BODY + b' '}, 'request_binding_mismatch'),
    ({'method': 'GET'}, 'request_binding_mismatch'),
    ({'target': '/_drg/federation/v1/other'}, 'request_binding_mismatch'),
    ({'target': PROBE_PATH + '?user=owner'}, 'invalid_request_binding'),
    ({'target': '/_drg/federation/v1/../probe'}, 'invalid_request_binding'),
    ({'target': '/_drg/federation/v1//probe'}, 'invalid_request_binding'),
    ({'target': '/_drg/federation/v1/%70robe'}, 'invalid_request_binding'),
    ({'now': STAMP - CLOCK_SKEW - 1}, 'expired_or_future_proof'),
    ({'now': STAMP + MAX_LIFETIME}, 'expired_or_future_proof'),
])
def test_proof_bound_to_request_and_time(servers, changes, error):
    alice, bob = servers
    signed = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    with pytest.raises(FederationError, match=error):
        read_request(signed.token, alice, bob, **changes)


def test_failed_binding_does_not_consume_valid_proof(servers):
    alice, bob = servers
    signed = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    replay = VolatileReplayWindow()
    with pytest.raises(FederationError):
        read_request(signed.token, alice, bob, body=b'{}', replay=replay)
    read_request(signed.token, alice, bob, replay=replay)
    with pytest.raises(FederationError, match='replayed_request'):
        read_request(signed.token, alice, bob, replay=replay)


@pytest.mark.parametrize('changes', [
    {'aud': 'f' * 32}, {'aud': ['f' * 32]}, {'iss': 'f' * 32},
    {'source_origin': 'https://new.example'}, {'destination_origin': 'https://new.example'},
    {'version': 2}, {'version': True}, {'iat': str(STAMP)}, {'iat': True},
    {'iat': STAMP - 1}, {'exp': STAMP}, {'exp': STAMP + MAX_LIFETIME + 1},
    {'exp': 'tomorrow'}, {'jti': 'not-a-request-id'}, {'user_id': 'owner'}, {'role': 'owner'},
])
def test_signed_but_invalid_claims_are_rejected(servers, changes):
    alice, bob = servers
    with pytest.raises(FederationError):
        read_request(altered_proof(alice, bob, changes), alice, bob)


@pytest.mark.parametrize('headers', [
    {'typ': 'JWT'}, {'kid': 'untrusted'}, {'jku': 'https://evil.example/keys'},
    {'jwk': {'kty': 'OKP'}}, {'crit': ['b64']},
])
def test_headers_cannot_select_remote_keys_or_another_token_context(servers, headers):
    alice, bob = servers
    with pytest.raises(FederationError):
        read_request(altered_proof(alice, bob, headers=headers), alice, bob)


def test_algorithms_signatures_and_other_recipient_rejected(servers):
    alice, bob = servers
    outsider = SigningIdentity.generate('https://outsider.example')
    signed = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    claims = jwt.decode(signed.token, options={'verify_signature': False})
    wrong_tokens = [
        jwt.encode(claims, key=None, algorithm='none', headers={'typ': REQUEST_TYPE, 'kid': alice.descriptor.key_id}),
        jwt.encode(claims, 'x' * 64, algorithm='HS256', headers={'typ': REQUEST_TYPE, 'kid': alice.descriptor.key_id}),
        jwt.encode(claims, outsider.private_key, algorithm='EdDSA', headers={'typ': REQUEST_TYPE, 'kid': alice.descriptor.key_id}),
        'broken', 'x' * (MAX_TOKEN_BYTES + 1), 'токен',
    ]
    for token in wrong_tokens:
        with pytest.raises(FederationError):
            read_request(token, alice, bob)
    with pytest.raises(FederationError):
        read_request(signed.token, alice, outsider)
    with pytest.raises(FederationError):
        sign_request(alice, alice.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)


def test_replay_window_atomic_under_concurrent_delivery(servers):
    alice, bob = servers
    signed = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    replay = VolatileReplayWindow()
    def deliver(_):
        try:
            read_request(signed.token, alice, bob, replay=replay)
            return 'accepted'
        except FederationError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(deliver, range(16)))
    assert results.count('accepted') == 1
    assert results.count('replayed_request') == 15


def test_replay_capacity_fails_closed_until_entries_expire(servers):
    alice, bob = servers
    first = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    second = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    replay = VolatileReplayWindow(capacity=1)
    read_request(first.token, alice, bob, replay=replay)
    with pytest.raises(FederationError, match='replay_window_full'):
        read_request(second.token, alice, bob, replay=replay)
    with pytest.raises(FederationError, match='replayed_request'):
        read_request(first.token, alice, bob, replay=replay)
    later = STAMP + MAX_LIFETIME
    fresh = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=later)
    read_request(fresh.token, alice, bob, replay=replay, now=later)


def test_response_proof_bound_to_request_status_body_and_direction(servers):
    alice, bob = servers
    signed = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    verified = read_request(signed.token, alice, bob)
    result = json.dumps({'challenge': 'a' * 64}).encode()
    proof = sign_response(bob, alice.descriptor, 'POST', PROBE_PATH, result, now=STAMP,
                          request_id=verified.request_id, status=200)
    def verify(**changes):
        arguments = dict(token=proof, local=alice.descriptor, peer=bob.descriptor,
                         method='POST', target=PROBE_PATH, body=result, now=STAMP,
                         replay=VolatileReplayWindow(), request_id=signed.request_id, status=200)
        arguments.update(changes)
        return verify_response(**arguments)
    verify()
    for changes in ({'request_id': 'f' * 32}, {'status': 201}, {'body': b'{}'}, {'token': signed.token}):
        with pytest.raises(FederationError):
            verify(**changes)
    with pytest.raises(FederationError):
        read_request(proof, bob, alice, body=result)


def test_body_limit_and_version_negotiation(servers):
    alice, bob = servers
    with pytest.raises(FederationError, match='body_too_large'):
        sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, b'x' * (MAX_BODY_BYTES + 1), now=STAMP)
    future = ServerDescriptor.model_validate(bob.descriptor.model_dump() | {'protocol_versions': (2,)})
    with pytest.raises(FederationError, match='incompatible_protocol'):
        sign_request(alice, future, 'POST', PROBE_PATH, BODY, now=STAMP)


@pytest.mark.parametrize('request_id,status', [(None, 200), ('', 200), ('a' * 32, True), ('a' * 32, 0)])
def test_response_api_never_falls_back_to_request_proofs(servers, request_id, status):
    alice, bob = servers
    signed = sign_request(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP)
    with pytest.raises(FederationError, match='invalid_response_binding'):
        verify_response(signed.token, bob.descriptor, alice.descriptor, 'POST', PROBE_PATH, BODY,
                        now=STAMP, replay=VolatileReplayWindow(), request_id=request_id, status=status)
    with pytest.raises(FederationError, match='invalid_response_binding'):
        sign_response(alice, bob.descriptor, 'POST', PROBE_PATH, BODY, now=STAMP,
                      request_id=request_id, status=status)
