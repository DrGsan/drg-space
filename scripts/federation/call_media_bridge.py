"""JSON-lines external-device bridge for real browser RTP through two DrG nodes.

Docker stdin carries only synthetic test SDP/ICE, never device private keys.
The browser tests are external to the server containers; device keys stay in the
dedicated verifier fixture just as in the other federation end-to-end checks.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.federation.actors import DeviceKeys, canonical, verify_device, verify_result
from app.federation.identity import FederationError
from app.federation.protocol import sign_request
from app.federation.prototype import OPERATION_PATH
from app.modules.calls import Candidate, MediaState
from scripts.federation.prototype_client import checked, context, direct, require, stolen_identity, users

CALLER_HANDLE, CALLEE_HANDLE = 'federated-media-caller', 'federated-media-callee'


class Devices:
    def __init__(self):
        self.callee, self.caller, _ = users()
        self.verified = set()
        self.call_id = None

    def local(self, user, operation, payload):
        response = checked(user.http.post('/api/federation/local/prepare', json={
            'device_id': user.card.device_id, 'operation': operation, 'payload': payload}))
        intent = response['intent']
        expected = {'actor_server_id': user.descriptor.server_id, 'actor_id': user.uid,
                    'device_id': user.card.device_id, 'target_actor_id': user.uid,
                    'operation': operation, 'payload': payload}
        require(all(intent.get(key) == value for key, value in expected.items()), 'Local server substituted the call intent.')
        proof = user.keys.sign(user.card, user.descriptor.server_id, intent, int(time.time()))
        result = checked(user.http.post('/api/federation/local/send', json={'intent': intent, 'proof': proof}))
        verify_result(user.descriptor, user.card, intent, result, int(time.time()))
        return user.keys.open(result, intent)

    def verify_peer(self, call, *, outgoing):
        incoming_ring = not outgoing and call.get('status') == 'ringing'
        if call.get('status') not in ('ringing', 'connecting', 'active') or not call.get('owned') and not incoming_ring:
            require(not any(key in call for key in ('sdp', 'candidates', 'peer_actions')),
                    'Unowned/terminal call exposed media parameters.')
            return call
        peer = self.callee if outgoing else self.caller
        meta = call['federation']
        require(meta['home_server_id'] == self.callee.descriptor.server_id and meta['home_actor_id'] == self.callee.uid,
                'Call home changed.')
        require(meta['peer_id'] == peer.descriptor.server_id and meta['actor_id'] == peer.uid, 'Call peer changed.')
        descriptions, candidates, media, invited = set(), set(), None, False
        for action in call.get('peer_actions', []):
            intent, proof = action['intent'], action['proof']
            require(intent.get('actor_server_id') == peer.card.server_id and intent.get('actor_id') == peer.uid
                    and intent.get('device_id') == peer.card.device_id and intent.get('target_actor_id') == self.callee.uid,
                    'Call peer action identity changed.')
            require(intent.get('operation') in ('call_invite', 'call_accept', 'call_signal')
                    and intent.get('payload', {}).get('id') == call['id'], 'Unrelated peer action.')
            fingerprint = hashlib.sha256(canonical(action)).hexdigest()
            if fingerprint not in self.verified:
                verify_device(peer.card, self.callee.descriptor.server_id, intent, proof, int(time.time()))
                self.verified.add(fingerprint)
            payload = intent['payload']
            invited = invited or intent['operation'] == 'call_invite'
            if payload.get('sdp'):
                descriptions.add(payload['sdp'])
            for value in payload.get('candidates', []):
                candidates.add(canonical(Candidate.model_validate(value).model_dump()))
            if payload.get('media') is not None:
                media = MediaState.model_validate(payload['media']).model_dump()
        require(not incoming_ring or invited, 'Unsigned incoming call invitation was rejected.')
        require(not call.get('sdp') or call['sdp'] in descriptions, 'Unsigned peer SDP was rejected.')
        require(all(canonical(Candidate.model_validate(value).model_dump()) in candidates for value in call.get('candidates', [])),
                'Unsigned peer ICE was rejected.')
        if call.get('media'):
            require(media == MediaState.model_validate(call['media']).model_dump(), 'Unsigned peer media state was rejected.')
        return call

    def ack(self, call):
        self.local(self.caller, 'call_outgoing_status', {key: call[key] for key in ('id', 'status', 'answered', 'connected', 'ended')}
                   | {'device': CALLER_HANDLE})

    def theft(self):
        stolen = stolen_identity()
        payload = {'id': self.call_id, 'device': CALLER_HANDLE}
        intent = self.caller.prepare(self.callee, 'call_poll', payload)
        proof = self.caller.keys.sign(self.caller.card, self.callee.descriptor.server_id, intent, int(time.time()))
        captured = sign_request(stolen, self.callee.descriptor, 'POST', OPERATION_PATH,
                                canonical({'intent': intent, 'proof': proof}), now=int(time.time()))
        with httpx.Client(verify=context(), trust_env=False, timeout=10) as outsider:
            direct(outsider, self.callee, stolen, intent, proof, status=403, signed=captured)
        with httpx.Client(verify=context('alice'), trust_env=False, timeout=10) as wrong_tls:
            direct(wrong_tls, self.callee, stolen, intent, proof, status=403, signed=captured)
        attacker = DeviceKeys.generate()
        fake_card = attacker.card(self.caller.card.server_id, self.caller.uid)
        forged = attacker.sign(fake_card, self.callee.descriptor.server_id, intent, int(time.time()))
        with httpx.Client(verify=context('bob'), trust_env=False, timeout=10) as compromised:
            direct(compromised, self.callee, stolen, intent, forged, status=403)
            altered = intent | {'payload': payload | {'device': 'stolen-token-other-handle'}}
            direct(compromised, self.callee, stolen, altered, proof, status=403)
            envelope, _ = direct(compromised, self.callee, stolen, intent, proof, status=200, signed=captured)
            verify_result(self.callee.descriptor, self.caller.card, intent, envelope, int(time.time()))
            require(not any(key in envelope for key in ('sdp', 'candidates', 'peer_actions')),
                    'A stolen server credential exposed raw call signaling.')
            try:
                attacker.open(envelope, intent)
            except FederationError:
                pass
            else:
                raise RuntimeError('A stolen server credential decrypted call signaling without the device key.')
            clear = self.caller.keys.open(envelope, intent)
            require(bool(self.verify_peer(clear, outgoing=True).get('sdp')), 'The legitimate device lost its verified answer.')
            direct(compromised, self.callee, stolen, intent, proof, status=409, signed=captured)
        return {'missing_tls_rejected': True, 'wrong_tls_rejected': True, 'forged_device_rejected': True,
                'substitution_rejected': True, 'device_only_ciphertext': True, 'replay_rejected': True}

    def perform(self, method, payload):
        if method == 'config':
            return {'caller': self.local(self.caller, 'call_config', {}),
                    'callee': self.local(self.callee, 'call_config', {})}
        if method == 'invite':
            self.call_id = payload['id']
            result = self.caller.operation(self.callee, 'call_invite', payload | {'device': CALLER_HANDLE})
            result = self.verify_peer(result, outgoing=True)
            self.ack(result)
            return result
        if method == 'current':
            result = self.local(self.callee, 'call_current', {'device': CALLEE_HANDLE})
            if result.get('call'):
                result['call'] = self.verify_peer(result['call'], outgoing=False)
            return result
        if method == 'accept':
            return self.verify_peer(self.local(self.callee, 'call_accept',
                                    {'id': self.call_id, 'device': CALLEE_HANDLE}), outgoing=False)
        if method in ('caller_signal', 'caller_poll', 'caller_end'):
            operation = {'caller_signal': 'call_signal', 'caller_poll': 'call_poll', 'caller_end': 'call_end'}[method]
            result = self.caller.operation(self.callee, operation,
                                           payload | {'id': self.call_id, 'device': CALLER_HANDLE})
            result = self.verify_peer(result, outgoing=True)
            self.ack(result)
            return result
        if method in ('callee_signal', 'callee_poll', 'callee_end'):
            operation = {'callee_signal': 'call_signal', 'callee_poll': 'call_poll', 'callee_end': 'call_end'}[method]
            return self.verify_peer(self.local(self.callee, operation,
                                    payload | {'id': self.call_id, 'device': CALLEE_HANDLE}), outgoing=False)
        if method == 'tamper':
            result = self.caller.operation(self.callee, 'call_poll', {'id': self.call_id, 'device': CALLER_HANDLE})
            valid = self.verify_peer(result, outgoing=True)
            forged = copy.deepcopy(valid)
            forged['sdp'] = (forged.get('sdp') or '') + '\r\na=fingerprint:sha-256 forged-server-key-fingerprint'
            try:
                self.verify_peer(forged, outgoing=True)
            except (RuntimeError, FederationError):
                return {'rejected': True}
            raise RuntimeError('A server-key attacker substituted peer SDP.')
        if method == 'theft':
            return self.theft()
        raise RuntimeError('Unknown call media test operation.')


def main():
    devices = Devices()
    for line in sys.stdin:
        request = json.loads(line)
        try:
            value = devices.perform(request['method'], request.get('payload') or {})
            response = {'id': request['id'], 'ok': True, 'result': value}
        except Exception as error:
            response = {'id': request.get('id'), 'ok': False, 'error': type(error).__name__ + ': ' + str(error)[:300]}
        print(json.dumps(response, ensure_ascii=False, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
