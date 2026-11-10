"""Opt-in isolated probe application. Never import app.main or open its storage."""
import json
import time
from types import MappingProxyType

from flask import Flask, Response, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .identity import FederationError, SigningIdentity
from .protocol import (DISCOVERY_PATH, MAX_BODY_BYTES, PROBE_PATH, VolatileReplayWindow,
                       sign_response, verify_request)


class Challenge(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    challenge: str = Field(pattern=r'^[a-f0-9]{64}$')


def create_probe_app(identity: SigningIdentity, *, trusted_peers=(), enabled=False,
                     clock=None, replay=None) -> Flask:
    """Peers must be independently confirmed before passing them here.

    No discovery result automatically becomes a trusted peer. Only the local
    harness enables this factory; there is no product route or .env switch.
    """
    peers = {peer.server_id: peer for peer in trusted_peers}
    if len(peers) != len(trusted_peers) or identity.descriptor.server_id in peers:
        raise ValueError('Duplicate or local trust pin.')
    peers = MappingProxyType(peers)
    replay = replay if replay is not None else VolatileReplayWindow()
    clock = clock if clock is not None else lambda: int(time.time())
    app = Flask(__name__, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = MAX_BODY_BYTES

    def response(value, status=200, headers=None):
        body = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
        return Response(body, status=status, content_type='application/json', headers=headers)

    @app.before_request
    def disabled():
        if not enabled:
            return response({'error': 'federation_disabled'}, 404)

    @app.after_request
    def secure_headers(result):
        result.headers['Cache-Control'] = 'no-store'
        result.headers['X-Content-Type-Options'] = 'nosniff'
        return result

    @app.errorhandler(413)
    def oversized(_):
        return response({'error': 'body_too_large'}, 413)

    @app.get(DISCOVERY_PATH)
    def discovery():
        return response(identity.descriptor.model_dump(mode='json'))

    @app.post(PROBE_PATH)
    def probe():
        peer = peers.get(request.headers.get('DrG-Server-Id', ''))
        if peer is None:
            return response({'error': 'untrusted_server'}, 403)
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('DrG-Federation '):
            return response({'error': 'proof_required'}, 401)
        if request.query_string or request.mimetype != 'application/json' or request.headers.get('Content-Encoding'):
            return response({'error': 'invalid_request'}, 400)
        body, stamp = request.get_data(), clock()
        try:
            # Validate shape before consuming the request proof.
            data = Challenge.model_validate_json(body)
            verified = verify_request(authorization[len('DrG-Federation '):], identity.descriptor, peer,
                                      request.method, PROBE_PATH, body, now=stamp, replay=replay)
        except ValidationError:
            return response({'error': 'invalid_challenge'}, 400)
        except FederationError as error:
            code = str(error)
            status = 409 if code == 'replayed_request' else 503 if code == 'replay_window_full' else 401
            return response({'error': code}, status)
        result = response({'challenge': data.challenge, 'protocol_version': 1,
                           'server_id': identity.descriptor.server_id, 'capabilities': ['probe']})
        proof = sign_response(identity, peer, request.method, PROBE_PATH, result.get_data(),
                              now=stamp, request_id=verified.request_id, status=result.status_code)
        result.headers['DrG-Federation-Proof'] = proof
        return result

    return app
