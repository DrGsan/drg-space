"""Canonical callee call signaling, device ownership and peer action evidence."""
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.federation.actors import DeviceKeys, verify_device
from app.federation.calls import (CallError, FEDERATION_CALLS_SCHEMA, FederatedCalls,
                                  federated_busy, reset_federated_calls_after_restore,
                                  validate_call_payload, validate_federated_call_archive)
from app.federation.identity import FederationError
from app.schema import SCHEMA

NOW = 1793000000
CALLER_SERVER, CALLEE_SERVER = 'a' * 32, 'b' * 32
CALLER, CALLEE, OTHER = 'c' * 32, 'd' * 32, 'e' * 32
CALLER_HANDLE, CALLEE_HANDLE = 'caller-runtime-handle', 'callee-runtime-handle'


@pytest.fixture
def pair():
    database = sqlite3.connect(':memory:')
    database.row_factory = sqlite3.Row
    database.execute('PRAGMA foreign_keys=ON')
    database.executescript(SCHEMA + FEDERATION_CALLS_SCHEMA)
    for uid in (CALLER, CALLEE, OTHER):
        database.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',
                         (uid, uid, 'test-only-password', 'user', '{}', NOW))
    caller_keys, callee_keys = DeviceKeys.generate(), DeviceKeys.generate()
    caller_card, callee_card = caller_keys.card(CALLER_SERVER, CALLER), callee_keys.card(CALLEE_SERVER, CALLEE)
    trust = {'accepted': True, 'available': True}
    def authorize(c, local_uid, peer_id, actor_id, device_id):
        if not trust['accepted'] or (local_uid, peer_id, actor_id) != (CALLEE, CALLER_SERVER, CALLER):
            raise FederationError('friendship_required')
        if device_id is not None and device_id != caller_card.device_id:
            raise FederationError('contact_device_changed')
    service = FederatedCalls(CALLEE_SERVER, authorize=authorize,
                            available=lambda c, uid: trust['available'],
                            config=lambda c, uid, now: {'available': True, 'relay_configured': True,
                                'iceServers': [{'urls': ['turn:private.example'], 'username': 'own-user', 'credential': 'own-secret'}]})
    yield SimpleNamespace(c=database, service=service, caller_keys=caller_keys, callee_keys=callee_keys,
                          caller_card=caller_card, callee_card=callee_card, trust=trust, cid=uuid4().hex)
    database.close()


def evidence(keys, card, operation, payload, now=NOW):
    intent = {'actor_server_id': card.server_id, 'actor_id': card.actor_id, 'device_id': card.device_id,
              'target_actor_id': CALLEE, 'operation': operation, 'payload': payload,
              'nonce': uuid4().hex * 2, 'event_id': uuid4().hex}
    proof = keys.sign(card, CALLEE_SERVER, intent, now)
    verify_device(card, CALLEE_SERVER, intent, proof, now)
    return SimpleNamespace(**intent), {'intent': intent, 'proof': proof}


def remote(p, operation, payload, now=NOW, *, card=None, keys=None):
    intent, action = evidence(keys or p.caller_keys, card or p.caller_card, operation, payload, now)
    return p.service.remote_operation(p.c, SimpleNamespace(server_id=CALLER_SERVER), intent, now, evidence=action)


def local(p, operation, payload, now=NOW, *, card=None, keys=None, uid=CALLEE):
    card, keys = card or p.callee_card, keys or p.callee_keys
    _, action = evidence(keys, card, operation, payload, now)
    return p.service.local_operation(p.c, uid, card, operation, payload, now, evidence=action)


def invite(p, now=NOW):
    return remote(p, 'call_invite', {'id': p.cid, 'device': CALLER_HANDLE, 'sdp': 'v=0\r\no=private-offer'}, now)


def test_full_call_uses_existing_shape_and_independently_verifiable_peer_actions(pair):
    p = pair
    call = invite(p)
    assert call['outgoing'] and call['owned'] and call['status'] == 'ringing'
    assert call['federation'] == {'home_server_id': CALLEE_SERVER, 'home_actor_id': CALLEE,
                                  'peer_id': CALLEE_SERVER, 'actor_id': CALLEE}
    incoming = local(p, 'call_current', {'device': CALLEE_HANDLE})['call']
    assert not incoming['outgoing'] and not incoming['owned']
    assert 'sdp' not in incoming and incoming['peer_actions'][0]['intent']['operation'] == 'call_invite'
    accepted = local(p, 'call_accept', {'id': p.cid, 'device': CALLEE_HANDLE})
    assert accepted['owned'] and accepted['sdp'] == 'v=0\r\no=private-offer'
    action = accepted['peer_actions'][0]
    verify_device(p.caller_card, CALLEE_SERVER, action['intent'], action['proof'], NOW)
    assert action['intent']['payload']['sdp'] == accepted['sdp']
    answer = {'id': p.cid, 'device': CALLEE_HANDLE, 'sdp': 'v=0\r\no=private-answer',
              'candidates': [{'candidate': 'candidate:1 1 udp 1 127.0.0.1 5000 typ host', 'sdpMid': '0'}],
              'media': {'muted': False, 'camera': True, 'screen': False}}
    local(p, 'call_signal', answer)
    polled = remote(p, 'call_poll', {'id': p.cid, 'device': CALLER_HANDLE})
    assert polled['sdp'] == answer['sdp'] and len(polled['candidates']) == 1
    assert polled['media']['camera']
    for action in polled['peer_actions']:
        verify_device(p.callee_card, CALLEE_SERVER, action['intent'], action['proof'], NOW)
    active = remote(p, 'call_signal', {'id': p.cid, 'device': CALLER_HANDLE, 'connected': True})
    assert active['status'] == 'active' and active['connected'] == NOW
    ended = local(p, 'call_end', {'id': p.cid, 'device': CALLEE_HANDLE, 'reason': 'hangup'})
    assert ended['status'] == 'ended'
    assert json.loads(p.c.execute('SELECT payload FROM federation_calls WHERE id=?', (p.cid,)).fetchone()[0]) == {}
    again = remote(p, 'call_poll', {'id': p.cid, 'device': CALLER_HANDLE})
    assert 'sdp' not in again and 'candidates' not in again and 'peer_actions' not in again


def test_another_runtime_or_device_cannot_read_or_change_media(pair):
    p = pair
    invite(p)
    local(p, 'call_accept', {'id': p.cid, 'device': CALLEE_HANDLE})
    other_runtime = remote(p, 'call_poll', {'id': p.cid, 'device': 'another-runtime-handle'})
    assert not other_runtime['owned'] and 'sdp' not in other_runtime
    with pytest.raises(CallError, match='device_mismatch'):
        remote(p, 'call_signal', {'id': p.cid, 'device': 'another-runtime-handle', 'connected': True})
    other = DeviceKeys.generate()
    card = other.card(CALLEE_SERVER, CALLEE)
    view = local(p, 'call_poll', {'id': p.cid, 'device': CALLEE_HANDLE}, keys=other, card=card)
    assert not view['owned'] and 'sdp' not in view
    with pytest.raises(CallError, match='already_claimed'):
        local(p, 'call_accept', {'id': p.cid, 'device': CALLEE_HANDLE}, keys=other, card=card)
    with pytest.raises(CallError, match='device_mismatch'):
        local(p, 'call_end', {'id': p.cid, 'device': CALLEE_HANDLE}, keys=other, card=card)


def test_server_or_id_theft_cannot_substitute_actor_device_or_evidence(pair):
    p = pair
    request = {'id': p.cid, 'device': CALLER_HANDLE, 'sdp': 'offer'}
    intent, action = evidence(p.caller_keys, p.caller_card, 'call_invite', request)
    with pytest.raises(CallError, match='evidence_required'):
        p.service.remote_operation(p.c, SimpleNamespace(server_id=CALLER_SERVER), intent, NOW)
    fake = action | {'intent': action['intent'] | {'payload': request | {'sdp': 'substitute'}}}
    with pytest.raises(CallError, match='evidence_mismatch'):
        p.service.remote_operation(p.c, SimpleNamespace(server_id=CALLER_SERVER), intent, NOW, evidence=fake)
    invite(p)
    other_keys = DeviceKeys.generate()
    other_card = other_keys.card(CALLER_SERVER, OTHER)
    with pytest.raises(FederationError):
        remote(p, 'call_poll', {'id': p.cid, 'device': CALLER_HANDLE}, keys=other_keys, card=other_card)
    with pytest.raises(CallError, match='not_found'):
        local(p, 'call_poll', {'id': p.cid, 'device': CALLEE_HANDLE}, uid=OTHER)


def test_immutable_sdp_candidate_deduplication_and_evidence_bound(pair):
    p = pair
    invite(p)
    local(p, 'call_accept', {'id': p.cid, 'device': CALLEE_HANDLE})
    value = {'id': p.cid, 'device': CALLEE_HANDLE, 'sdp': 'answer',
             'candidates': [{'candidate': 'candidate:one', 'sdpMid': '0'}]}
    local(p, 'call_signal', value)
    local(p, 'call_signal', value)
    remote_call = remote(p, 'call_poll', {'id': p.cid, 'device': CALLER_HANDLE})
    assert len(remote_call['candidates']) == 1
    assert len(remote_call['peer_actions']) == 2  # accept and one material signal
    with pytest.raises(CallError, match='description_changed'):
        local(p, 'call_signal', value | {'sdp': 'new-DTLS-fingerprint'})


def test_expiry_and_revocation_remove_sdp_and_free_busy_state(pair):
    p = pair
    invite(p)
    assert federated_busy(p.c, CALLEE, NOW)
    missed = remote(p, 'call_poll', {'id': p.cid, 'device': CALLER_HANDLE}, NOW + 61)
    assert missed['status'] == 'missed' and 'sdp' not in missed
    assert not federated_busy(p.c, CALLEE, NOW + 61)
    p.cid = uuid4().hex
    invite(p, NOW + 62)
    p.trust['accepted'] = False
    assert local(p, 'call_current', {'device': CALLEE_HANDLE}, NOW + 63)['call'] is None
    row = p.c.execute('SELECT * FROM federation_calls WHERE id=?', (p.cid,)).fetchone()
    assert row['status'] == 'ended' and json.loads(row['payload']) == {}


def test_long_call_media_changes_keep_latest_signed_state_and_all_rtc_proofs(pair):
    p = pair
    invite(p)
    local(p, 'call_accept', {'id': p.cid, 'device': CALLEE_HANDLE})
    local(p, 'call_signal', {'id': p.cid, 'device': CALLEE_HANDLE, 'sdp': 'immutable-answer',
                            'candidates': [{'candidate': 'candidate:retained', 'sdpMid': '0'}],
                            'media': {'muted': False}})
    for index in range(160):
        local(p, 'call_signal', {'id': p.cid, 'device': CALLEE_HANDLE, 'media': {'muted': index % 2 == 0}})
    call = remote(p, 'call_poll', {'id': p.cid, 'device': CALLER_HANDLE})
    assert call['media'] == {'muted': False, 'camera': False, 'screen': False}
    assert call['sdp'] == 'immutable-answer' and call['candidates'][0]['candidate'] == 'candidate:retained'
    assert len(call['peer_actions']) == 3
    assert call['peer_actions'][-1]['intent']['payload']['media'] == {'muted': False}
    for action in call['peer_actions']:
        verify_device(p.callee_card, CALLEE_SERVER, action['intent'], action['proof'], NOW)


def test_callee_capabilities_never_disclose_other_server_turn_credentials(pair):
    p = pair
    capability = remote(p, 'call_capabilities', {})
    assert capability == {'available': True, 'audio': True, 'video': True, 'screen': True, 'shared_turn': False}
    own_config = local(p, 'call_config', {})
    assert own_config['iceServers'][0]['credential'] == 'own-secret'
    with pytest.raises(CallError):
        remote(p, 'call_config', {})
    p.trust['available'] = False
    with pytest.raises(CallError, match='unavailable'):
        invite(p)
    assert p.c.execute('SELECT COUNT(*) FROM federation_calls').fetchone()[0] == 0


def test_local_and_guest_calls_block_federated_admission_until_finished(pair):
    p = pair
    sid, cid = uuid4().hex, uuid4().hex
    p.c.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',
                (sid, 'friends', '', CALLEE, NOW))
    p.c.execute('INSERT INTO calls(id,space_id,caller_id,recipient_id,status,created,payload) VALUES(?,?,?,?,?,?,?)',
                (cid, sid, OTHER, CALLEE, 'ringing', NOW, '{}'))
    with pytest.raises(CallError, match='call_busy'):
        invite(p)
    p.c.execute('UPDATE calls SET status=? WHERE id=?', ('ended', cid))
    room_id, participant_id = uuid4().hex, uuid4().hex
    p.c.execute('INSERT INTO guest_call_rooms VALUES(?,?,?,?,?,?)',
                (room_id, CALLEE, 'room-token-hash', NOW, NOW + 43200, '{}'))
    p.c.execute('INSERT INTO guest_call_participants VALUES(?,?,?,?,?,?,?)',
                (participant_id, room_id, CALLEE, 'participant-secret-hash', NOW, NOW, '{}'))
    with pytest.raises(CallError, match='call_busy'):
        invite(p)
    p.c.execute('DELETE FROM guest_call_participants WHERE id=?', (participant_id,))
    assert invite(p)['status'] == 'ringing'


def test_outgoing_reservation_cannot_change_route_owner_or_reopen_after_end(pair):
    p = pair
    # This instance also represents a caller's home store for reservations.
    service = FederatedCalls(CALLER_SERVER, authorize=lambda *args: None, available=lambda *args: True)
    peer = SimpleNamespace(server_id=CALLEE_SERVER)
    payload = {'id': p.cid, 'device': CALLER_HANDLE, 'sdp': 'offer'}
    service.reserve_outgoing(p.c, CALLER, p.caller_card, peer, CALLEE, payload, NOW)
    assert federated_busy(p.c, CALLER, NOW)
    value = {'id': p.cid, 'device': CALLER_HANDLE, 'status': 'active', 'answered': NOW, 'connected': NOW}
    saved = service.local_operation(p.c, CALLER, p.caller_card, 'call_outgoing_status', value, NOW)
    assert saved['federation']['home_server_id'] == CALLEE_SERVER
    stale = service.local_operation(p.c, CALLER, p.caller_card, 'call_outgoing_status',
                                     {'id': p.cid, 'device': CALLER_HANDLE, 'status': 'ringing'}, NOW)
    assert stale['status'] == 'active' and stale['connected'] == NOW
    with pytest.raises(CallError, match='device_mismatch'):
        service.local_operation(p.c, CALLER, p.caller_card, 'call_outgoing_status', value | {'device': 'another-runtime-handle'}, NOW)
    service.local_operation(p.c, CALLER, p.caller_card, 'call_outgoing_status', value | {'status': 'ended', 'ended': NOW + 1}, NOW + 1)
    terminal = service.local_operation(p.c, CALLER, p.caller_card, 'call_outgoing_status', value, NOW + 2)
    assert terminal['status'] == 'ended'
    assert not federated_busy(p.c, CALLER, NOW + 2)


def test_restore_ends_live_calls_and_drops_all_private_signaling(pair):
    p = pair
    invite(p)
    local(p, 'call_accept', {'id': p.cid, 'device': CALLEE_HANDLE})
    local(p, 'call_signal', {'id': p.cid, 'device': CALLEE_HANDLE, 'sdp': 'private-answer'})
    def rejected():
        raise AssertionError('Invalid archive')
    validate_federated_call_archive(p.c, rejected)
    p.c.row_factory = None  # Raw portable backup staging uses tuple rows.
    reset_federated_calls_after_restore(p.c, NOW + 1)
    p.c.row_factory = sqlite3.Row
    row = p.c.execute('SELECT * FROM federation_calls WHERE id=?', (p.cid,)).fetchone()
    assert row['status'] == 'failed' and row['ended'] == NOW + 1 and row['payload'] == '{}'
    validate_federated_call_archive(p.c, rejected)


def test_revoking_a_local_device_ends_its_claim_and_outgoing_reservation(pair):
    p = pair
    invite(p)
    local(p, 'call_accept', {'id': p.cid, 'device': CALLEE_HANDLE})
    p.service.revoke_device(p.c, CALLEE, 'f' * 64, NOW + 1)
    assert p.c.execute('SELECT status FROM federation_calls WHERE id=?', (p.cid,)).fetchone()[0] == 'connecting'
    p.service.revoke_device(p.c, CALLEE, p.callee_card.device_id, NOW + 2)
    row = p.c.execute('SELECT status,payload FROM federation_calls WHERE id=?', (p.cid,)).fetchone()
    assert row['status'] == 'ended' and row['payload'] == '{}'
    service = FederatedCalls(CALLER_SERVER, authorize=lambda *args: None, available=lambda *args: True)
    link_id = uuid4().hex
    service.reserve_outgoing(p.c, CALLER, p.caller_card, SimpleNamespace(server_id=CALLEE_SERVER), CALLEE,
                             {'id': link_id, 'device': CALLER_HANDLE, 'sdp': 'offer'}, NOW + 2)
    service.revoke_device(p.c, CALLER, p.caller_card.device_id, NOW + 3)
    assert not federated_busy(p.c, CALLER, NOW + 3)
    assert p.c.execute('SELECT status FROM federation_call_links WHERE id=?', (link_id,)).fetchone()[0] == 'ended'


def test_history_is_metadata_only_and_paginates_beyond_first_hundred(pair):
    p = pair
    invite(p)
    ringing = local(p, 'call_history', {})['items'][0]
    assert 'peer_actions' not in ringing and 'sdp' not in ringing and 'candidates' not in ringing
    p.c.execute('DELETE FROM federation_calls')
    for index in range(115):
        p.c.execute('INSERT INTO federation_calls(id,caller_server_id,caller_id,recipient_id,caller_device,status,created,ended,payload) VALUES(?,?,?,?,?,?,?,?,?)',
                    (uuid4().hex,CALLER_SERVER,CALLER,CALLEE,p.caller_card.device_id,'ended',NOW+index,NOW+index+1,'{}'))
    first = local(p,'call_history',{},NOW+120)
    assert len(first['items']) == 100 and first['more']
    second = local(p,'call_history',{'before':first['items'][-1]['id']},NOW+120)
    assert len(second['items']) == 15 and not second['more']
    assert not {item['id'] for item in first['items']} & {item['id'] for item in second['items']}


def test_archive_rejects_private_fields_in_outgoing_summary(pair):
    p = pair
    service = FederatedCalls(CALLER_SERVER, authorize=lambda *args: None, available=lambda *args: True)
    service.reserve_outgoing(p.c, CALLER, p.caller_card, SimpleNamespace(server_id=CALLEE_SERVER), CALLEE,
                             {'id': p.cid, 'device': CALLER_HANDLE, 'sdp': 'offer'}, NOW)
    p.c.execute('UPDATE federation_call_links SET payload=? WHERE id=?', (json.dumps({'sdp': 'must-never-be-here'}), p.cid))
    def rejected():
        raise AssertionError('Invalid archive')
    with pytest.raises(AssertionError):
        validate_federated_call_archive(p.c, rejected)


def test_external_device_rejects_server_only_sdp_and_candidate_substitution(pair, monkeypatch):
    from scripts.federation.call_media_bridge import Devices
    p = pair
    invite(p)
    local(p, 'call_accept', {'id': p.cid, 'device': CALLEE_HANDLE})
    local(p, 'call_signal', {'id': p.cid, 'device': CALLEE_HANDLE, 'sdp': 'verified-answer',
                            'candidates': [{'candidate': 'candidate:signed', 'sdpMid': '0'}]})
    result = remote(p, 'call_poll', {'id': p.cid, 'device': CALLER_HANDLE})
    driver = object.__new__(Devices)
    driver.caller = SimpleNamespace(card=p.caller_card, uid=CALLER, descriptor=SimpleNamespace(server_id=CALLER_SERVER))
    driver.callee = SimpleNamespace(card=p.callee_card, uid=CALLEE, descriptor=SimpleNamespace(server_id=CALLEE_SERVER))
    driver.verified = set()
    monkeypatch.setattr('scripts.federation.call_media_bridge.time.time', lambda: NOW)
    driver.verify_peer(result, outgoing=True)
    for changed in ({'sdp': 'forged-answer'}, {'candidates': [{'candidate': 'candidate:forged'}]},
                    {'peer_actions': []}, {'id': uuid4().hex}):
        with pytest.raises(RuntimeError):
            driver.verify_peer(result | changed, outgoing=True)
    p.cid = uuid4().hex
    local(p, 'call_end', {'id': result['id'], 'device': CALLEE_HANDLE, 'reason': 'hangup'})
    invite(p)
    incoming = local(p, 'call_current', {'device': CALLEE_HANDLE})['call']
    driver.verify_peer(incoming, outgoing=False)
    with pytest.raises(RuntimeError, match='Unsigned incoming'):
        driver.verify_peer(incoming | {'peer_actions': []}, outgoing=False)


@pytest.mark.parametrize('operation,payload', [
    ('call_invite', {'id': 'short', 'device': CALLER_HANDLE, 'sdp': 'offer'}),
    ('call_signal', {'id': 'a' * 32, 'device': CALLER_HANDLE, 'connected': 'yes'}),
    ('call_end', {'id': 'a' * 32, 'device': CALLER_HANDLE, 'reason': 'forged'}),
    ('call_poll', {'id': 'a' * 32, 'device': CALLER_HANDLE, 'user_id': OTHER}),
    ('unknown', {}),
])
def test_call_operations_are_bounded_and_strict(operation, payload):
    with pytest.raises(CallError):
        validate_call_payload(operation, payload)
