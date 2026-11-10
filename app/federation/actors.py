"""Device possession proofs and encrypted results, independent of server keys.

Experimental protocol: Ed25519 for authorization, X25519/HKDF/AES-GCM for sealed
results. Device private keys stay on the client; public cards require explicit
confirmation by the resource owner. This is not message E2EE or a recovery system.
"""
import base64
from dataclasses import dataclass, field
import hashlib
import json
import secrets

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .identity import FederationError, SERVER_ID, decode_key, encode_key
from .protocol import CLOCK_SKEW, MAX_LIFETIME, MAX_TOKEN_BYTES


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class DeviceCard(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    server_id: str = Field(pattern=SERVER_ID)
    actor_id: str = Field(pattern=SERVER_ID)
    device_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    signing_key: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    encryption_key: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')

    @model_validator(mode='after')
    def fingerprint(self):
        raw = decode_key(self.signing_key) + decode_key(self.encryption_key)
        if hashlib.sha256(raw).hexdigest() != self.device_id:
            raise ValueError('Device fingerprint mismatch.')
        X25519PrivateKey.generate().exchange(X25519PublicKey.from_public_bytes(decode_key(self.encryption_key)))
        return self


class DeviceClaims(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    iss: str = Field(pattern=r'^[a-f0-9]{64}$')
    aud: str = Field(pattern=SERVER_ID)
    iat: int = Field(ge=0)
    exp: int = Field(ge=0)
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')


@dataclass(frozen=True)
class DeviceKeys:
    signing: Ed25519PrivateKey = field(repr=False)
    encryption: X25519PrivateKey = field(repr=False)

    @classmethod
    def generate(cls):
        return cls(Ed25519PrivateKey.generate(), X25519PrivateKey.generate())

    def card(self, server_id, actor_id):
        signing = self.signing.public_key().public_bytes_raw()
        encryption = self.encryption.public_key().public_bytes_raw()
        return DeviceCard(server_id=server_id, actor_id=actor_id,
                          device_id=hashlib.sha256(signing + encryption).hexdigest(),
                          signing_key=encode_key(signing), encryption_key=encode_key(encryption))

    def sign(self, card, recipient, intent, now):
        if self.card(card.server_id, card.actor_id) != card:
            raise FederationError('device_key_mismatch')
        return jwt.encode(dict(iss=card.device_id, aud=recipient, iat=now, exp=now + MAX_LIFETIME,
                               digest=hashlib.sha256(canonical(intent)).hexdigest()),
                          self.signing, algorithm='EdDSA', headers={'typ': 'drg-device-intent+jwt', 'kid': card.device_id})

    def open(self, envelope, intent):
        try:
            ephemeral = X25519PublicKey.from_public_bytes(decode_key(envelope['ephemeral']))
            context = canonical(intent)
            secret = self.encryption.exchange(ephemeral)
            key = _box_key(secret, context)
            nonce = base64.b64decode(envelope['nonce'], altchars=b'-_', validate=True)
            ciphertext = base64.b64decode(envelope['ciphertext'], altchars=b'-_', validate=True)
            return json.loads(AESGCM(key).decrypt(nonce, ciphertext, context))
        except Exception:
            raise FederationError('sealed_result_rejected') from None


def verify_device(card, recipient, intent, proof, now):
    if not isinstance(proof, str) or len(proof) > MAX_TOKEN_BYTES:
        raise FederationError('device_proof_required')
    try:
        header = jwt.get_unverified_header(proof)
        if header != {'alg': 'EdDSA', 'typ': 'drg-device-intent+jwt', 'kid': card.device_id}:
            raise ValueError()
        value = jwt.decode(proof, Ed25519PublicKey.from_public_bytes(decode_key(card.signing_key)),
                           algorithms=['EdDSA'], audience=recipient, issuer=card.device_id,
                           options={'verify_iat': False, 'verify_exp': False, 'strict_aud': True})
        claims = DeviceClaims.model_validate(value)
    except (ValueError, jwt.PyJWTError, ValidationError):
        raise FederationError('invalid_device_proof') from None
    if not 0 < claims.exp - claims.iat <= MAX_LIFETIME or claims.iat > now + CLOCK_SKEW or claims.exp <= now:
        raise FederationError('expired_device_proof')
    if claims.digest != hashlib.sha256(canonical(intent)).hexdigest():
        raise FederationError('device_intent_mismatch')


def _box_key(secret, context):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(context).digest(),
                info=b'drg/federation/prototype/result/v1').derive(secret)


def seal_result(card, value, intent):
    temporary = X25519PrivateKey.generate()
    try:
        secret = temporary.exchange(X25519PublicKey.from_public_bytes(decode_key(card.encryption_key)))
    except ValueError:
        raise FederationError('invalid_device_encryption_key') from None
    context = canonical(intent)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_box_key(secret, context)).encrypt(nonce, canonical(value), context)
    return {'ephemeral': encode_key(temporary.public_key().public_bytes_raw()),
            'nonce': base64.urlsafe_b64encode(nonce).decode(),
            'ciphertext': base64.urlsafe_b64encode(ciphertext).decode()}


def sign_result(identity, card, intent, envelope, now):
    if identity.result_private_key is None:
        raise FederationError('result_signing_key_unavailable')
    claims = {'iss': identity.descriptor.server_id, 'aud': card.device_id,
              'intent_digest': hashlib.sha256(canonical(intent)).hexdigest(),
              'body_sha256': hashlib.sha256(canonical(envelope)).hexdigest(), 'iat': now, 'exp': now + 120}
    proof = jwt.encode(claims, identity.result_private_key, algorithm='EdDSA',
                       headers={'typ': 'drg-federation-result+jwt', 'kid': identity.descriptor.result_key_id})
    return envelope | {'result_proof': proof}


def verify_result(server, card, intent, envelope, now):
    proof = envelope.get('result_proof', '')
    body = {key: value for key, value in envelope.items() if key != 'result_proof'}
    try:
        if not server.result_public_key or len(proof) > MAX_TOKEN_BYTES or jwt.get_unverified_header(proof) != {
                'alg': 'EdDSA', 'typ': 'drg-federation-result+jwt', 'kid': server.result_key_id}:
            raise ValueError()
        claims = jwt.decode(proof, Ed25519PublicKey.from_public_bytes(decode_key(server.result_public_key)), algorithms=['EdDSA'],
                            audience=card.device_id, issuer=server.server_id,
                            options={'verify_iat': False, 'verify_exp': False, 'strict_aud': True})
        if set(claims) != {'iss','aud','intent_digest','body_sha256','iat','exp'}:
            raise ValueError()
        if (type(claims['iat']) is not int or type(claims['exp']) is not int
                or not 0 < claims['exp'] - claims['iat'] <= 120 or claims['iat'] > now + 30 or claims['exp'] <= now
                or claims['intent_digest'] != hashlib.sha256(canonical(intent)).hexdigest()
                or claims['body_sha256'] != hashlib.sha256(canonical(body)).hexdigest()):
            raise ValueError()
    except (ValueError, jwt.PyJWTError, TypeError, KeyError):
        raise FederationError('result_proof_rejected') from None
