"""Bound, short-lived Ed25519 request/response proofs for the local probe.

Server authentication is intentionally separate from user authorization.
No server proof is accepted as a user session or document permission.
"""
from dataclasses import dataclass, field
import hashlib
import re
import threading
from uuid import uuid4

import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .identity import FederationError, PROTOCOL_VERSION, SERVER_ID, ServerDescriptor, SigningIdentity

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_TOKEN_BYTES = 4096
MAX_LIFETIME = 120
CLOCK_SKEW = 30
REQUEST_TYPE = 'drg-federation-request+jwt'
RESPONSE_TYPE = 'drg-federation-response+jwt'
PROBE_PATH = '/_drg/federation/v1/probe'
DISCOVERY_PATH = '/.well-known/drg-federation'
METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}


class RequestClaims(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    iss: str = Field(pattern=SERVER_ID)
    aud: str = Field(pattern=SERVER_ID)
    jti: str = Field(pattern=SERVER_ID)
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    version: int
    source_origin: str = Field(max_length=300)
    destination_origin: str = Field(max_length=300)
    method: str = Field(max_length=6)
    target: str = Field(max_length=512)
    body_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class ResponseClaims(RequestClaims):
    request_id: str = Field(pattern=SERVER_ID)
    status: int = Field(ge=200, le=599)


@dataclass(frozen=True)
class VerifiedServerRequest:
    server_id: str
    request_id: str


@dataclass(frozen=True)
class SignedRequest:
    token: str = field(repr=False)
    request_id: str


class VolatileReplayWindow:
    """Atomic and bounded, for the single-process probe only.

    Production needs a shared durable implementation of consume(), surviving
    restarts and backup restore. Never silently evict an unexpired request.
    """
    def __init__(self, capacity: int = 4096):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('Invalid replay window capacity.')
        self.capacity = capacity
        self._seen = {}
        self._lock = threading.Lock()

    def consume(self, server_id: str, request_id: str, expires: int, now: int) -> None:
        with self._lock:
            self._seen = {key: until for key, until in self._seen.items() if until > now}
            key = (server_id, request_id)
            if key in self._seen:
                raise FederationError('replayed_request')
            if len(self._seen) >= self.capacity:
                raise FederationError('replay_window_full')
            self._seen[key] = expires


def _binding(method: str, target: str, body: bytes, now: int) -> str:
    if (method not in METHODS or not isinstance(target, str) or len(target) > 512
            or not re.fullmatch(r'/_drg/federation/v1/[A-Za-z0-9_/-]+', target)
            or '//' in target or type(now) is not int or now < 0):
        raise FederationError('invalid_request_binding')
    if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
        raise FederationError('body_too_large')
    return hashlib.sha256(body).hexdigest()


def _sign(identity, peer, method, target, body, now, *, request_id=None, status=None):
    digest = _binding(method, target, body, now)
    if identity.descriptor.server_id == peer.server_id:
        raise FederationError('self_connection')
    if PROTOCOL_VERSION not in peer.protocol_versions or PROTOCOL_VERSION not in identity.descriptor.protocol_versions:
        raise FederationError('incompatible_protocol')
    claims = dict(iss=identity.descriptor.server_id, aud=peer.server_id, jti=uuid4().hex,
                  iat=now, exp=now + MAX_LIFETIME, version=PROTOCOL_VERSION,
                  source_origin=identity.descriptor.origin, destination_origin=peer.origin,
                  method=method, target=target, body_sha256=digest)
    model, token_type = RequestClaims, REQUEST_TYPE
    if request_id is not None:
        claims.update(request_id=request_id, status=status)
        model, token_type = ResponseClaims, RESPONSE_TYPE
    try:
        payload = model.model_validate(claims).model_dump()
    except ValidationError:
        raise FederationError('invalid_claims') from None
    token = jwt.encode(payload, identity.private_key, algorithm='EdDSA',
                       headers={'typ': token_type, 'kid': identity.descriptor.key_id})
    return SignedRequest(token, claims['jti'])


def sign_request(identity: SigningIdentity, peer: ServerDescriptor, method: str,
                 target: str, body: bytes, *, now: int) -> SignedRequest:
    return _sign(identity, peer, method, target, body, now)


def sign_response(identity: SigningIdentity, peer: ServerDescriptor, method: str,
                  target: str, body: bytes, *, now: int, request_id: str, status: int) -> str:
    _response_binding(request_id, status)
    return _sign(identity, peer, method, target, body, now, request_id=request_id, status=status).token


def _response_binding(request_id, status):
    if (not isinstance(request_id, str) or not re.fullmatch(SERVER_ID, request_id)
            or type(status) is not int or not 200 <= status <= 599):
        raise FederationError('invalid_response_binding')


def _verify(token, local, peer, method, target, body, now, replay, *, request_id=None, status=None):
    digest = _binding(method, target, body, now)
    if not isinstance(token, str) or not 1 <= len(token) <= MAX_TOKEN_BYTES or not token.isascii():
        raise FederationError('invalid_proof')
    expected_type = REQUEST_TYPE if request_id is None else RESPONSE_TYPE
    model = RequestClaims if request_id is None else ResponseClaims
    try:
        # Header hints never select algorithms, fetch keys, or create trust.
        header = jwt.get_unverified_header(token)
        if header != {'alg': 'EdDSA', 'typ': expected_type, 'kid': peer.key_id}:
            raise FederationError('invalid_proof_header')
        payload = jwt.decode(token, peer.verification_key(), algorithms=['EdDSA'],
                             audience=local.server_id, issuer=peer.server_id,
                             options={'verify_iat': False, 'verify_exp': False,
                                      'strict_aud': True, 'require': ['iss', 'aud', 'iat', 'exp', 'jti']})
        claims = model.model_validate(payload)
    except (jwt.PyJWTError, ValidationError, ValueError):
        raise FederationError('invalid_proof') from None
    if claims.version != PROTOCOL_VERSION or PROTOCOL_VERSION not in peer.protocol_versions or PROTOCOL_VERSION not in local.protocol_versions:
        raise FederationError('incompatible_protocol')
    if peer.server_id == local.server_id:
        raise FederationError('self_connection')
    if claims.source_origin != peer.origin or claims.destination_origin != local.origin:
        raise FederationError('origin_mismatch')
    if not 0 < claims.exp - claims.iat <= MAX_LIFETIME or claims.iat > now + CLOCK_SKEW or claims.exp <= now:
        raise FederationError('expired_or_future_proof')
    if claims.method != method or claims.target != target or claims.body_sha256 != digest:
        raise FederationError('request_binding_mismatch')
    if request_id is not None and (claims.request_id != request_id or claims.status != status):
        raise FederationError('response_binding_mismatch')
    # Consume only after every binding check, before dispatching the operation.
    replay.consume(peer.server_id, claims.jti, claims.exp, now)
    return claims


def verify_request(token: str, local: ServerDescriptor, peer: ServerDescriptor,
                   method: str, target: str, body: bytes, *, now: int,
                   replay: VolatileReplayWindow) -> VerifiedServerRequest:
    claims = _verify(token, local, peer, method, target, body, now, replay)
    return VerifiedServerRequest(peer.server_id, claims.jti)


def verify_response(token: str, local: ServerDescriptor, peer: ServerDescriptor,
                    method: str, target: str, body: bytes, *, now: int,
                    replay: VolatileReplayWindow, request_id: str, status: int) -> None:
    _response_binding(request_id, status)
    _verify(token, local, peer, method, target, body, now, replay, request_id=request_id, status=status)
