"""Stable server identities and explicitly pinned public descriptors."""
import base64
from dataclasses import dataclass, field
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

PROTOCOL_VERSION = 1
SERVER_ID = r'^[a-f0-9]{32}$'


class FederationError(ValueError):
    """A fixed error code, safe to report without including untrusted input."""


def encode_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def decode_key(value: str) -> bytes:
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', value):
        raise ValueError('Invalid Ed25519 public key.')
    raw = base64.urlsafe_b64decode(value + '=')
    if encode_key(raw) != value:
        raise ValueError('Noncanonical Ed25519 public key.')
    return raw


def key_id(public_key: str) -> str:
    return 'ed25519:' + encode_key(hashlib.sha256(decode_key(public_key)).digest())


def normalize_origin(value: str, *, allow_loopback_http: bool = False) -> str:
    """An origin, never a user-supplied URL to fetch or redirect to.

DNS resolution and connection pinning belong to the future outbound transport.
Only the isolated local probe may opt into HTTP on literal 127.0.0.1.
"""
    if (not isinstance(value, str) or not 1 <= len(value) <= 300
            or not value.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in value)
            or any(c in value for c in '\\%?#')):
        raise ValueError('Invalid federation origin.')
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise ValueError('Invalid federation origin.') from None
    if (not host or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment
            or port is not None and not 1 <= port <= 65535):
        raise ValueError('Invalid federation origin.')
    local = allow_loopback_http and parsed.scheme == 'http' and host == '127.0.0.1' and port is not None
    if parsed.scheme != 'https' and not local:
        raise ValueError('Federation requires HTTPS.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (len(host) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                   for label in host.split('.'))):
            raise ValueError('Invalid federation hostname.') from None
    else:
        if not address.is_global and not local:
            raise ValueError('A public federation origin is required.')
        host = '[' + host + ']' if address.version == 6 else host
    suffix = ':' + str(port) if port is not None and not (parsed.scheme == 'https' and port == 443) else ''
    return parsed.scheme + '://' + host + suffix


class ServerDescriptor(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    server_id: str = Field(pattern=SERVER_ID)
    origin: str
    key_id: str = Field(pattern=r'^ed25519:[A-Za-z0-9_-]{43}$')
    public_key: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    result_key_id: str | None = Field(default=None, pattern=r'^ed25519:[A-Za-z0-9_-]{43}$')
    result_public_key: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{43}$')
    protocol_versions: tuple[int, ...] = Field(min_length=1, max_length=16)
    capabilities: tuple[str, ...] = Field(max_length=32)

    @field_validator('origin')
    @classmethod
    def valid_origin(cls, value: str, info: ValidationInfo) -> str:
        return normalize_origin(value, allow_loopback_http=bool((info.context or {}).get('allow_loopback_http')))

    @field_validator('protocol_versions')
    @classmethod
    def valid_versions(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if any(type(v) is not int or not 1 <= v <= 65535 for v in values) or len(set(values)) != len(values):
            raise ValueError('Invalid protocol versions.')
        return values

    @field_validator('capabilities')
    @classmethod
    def valid_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r'[a-z][a-z0-9_.-]{0,63}', v) for v in values) or len(set(values)) != len(values):
            raise ValueError('Invalid capabilities.')
        return values

    @model_validator(mode='after')
    def matching_key(self):
        if self.key_id != key_id(self.public_key):
            raise ValueError('Public key fingerprint does not match.')
        if (self.result_public_key is None) != (self.result_key_id is None):
            raise ValueError('Incomplete result verification key.')
        if self.result_public_key is not None and self.result_key_id != key_id(self.result_public_key):
            raise ValueError('Result public key fingerprint does not match.')
        return self

    def verification_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(decode_key(self.public_key))


@dataclass(frozen=True)
class SigningIdentity:
    descriptor: ServerDescriptor
    private_key: Ed25519PrivateKey = field(repr=False)
    result_private_key: Ed25519PrivateKey | None = field(default=None, repr=False)

    def __post_init__(self):
        if encode_key(self.private_key.public_key().public_bytes_raw()) != self.descriptor.public_key:
            raise ValueError('Signing key does not match server descriptor.')
        if self.result_private_key is not None and encode_key(self.result_private_key.public_key().public_bytes_raw()) != self.descriptor.result_public_key:
            raise ValueError('Result key does not match server descriptor.')

    @classmethod
    def generate(cls, origin: str, *, allow_loopback_http: bool = False):
        """Ephemeral probe identity, not production key provisioning."""
        private = Ed25519PrivateKey.generate()
        result_private = Ed25519PrivateKey.generate()
        result_public = encode_key(result_private.public_key().public_bytes_raw())
        public = encode_key(private.public_key().public_bytes_raw())
        descriptor = ServerDescriptor.model_validate({
            'server_id': uuid4().hex, 'origin': origin,
            'key_id': key_id(public), 'public_key': public,
            'result_key_id': key_id(result_public), 'result_public_key': result_public,
            'protocol_versions': (PROTOCOL_VERSION,), 'capabilities': ('probe',),
        }, context={'allow_loopback_http': allow_loopback_http})
        return cls(descriptor, private, result_private)


def check_pinned_descriptor(expected: ServerDescriptor, observed: ServerDescriptor) -> int:
    """A discovery response can never install or replace a trust pin."""
    if expected.server_id != observed.server_id:
        raise FederationError('server_identity_changed')
    if expected.origin != observed.origin:
        raise FederationError('server_origin_changed')
    if expected.key_id != observed.key_id or expected.public_key != observed.public_key:
        raise FederationError('server_key_changed')
    if expected.result_key_id != observed.result_key_id or expected.result_public_key != observed.result_public_key:
        raise FederationError('server_result_key_changed')
    common = set(expected.protocol_versions) & set(observed.protocol_versions) & {PROTOCOL_VERSION}
    if not common:
        raise FederationError('incompatible_protocol')
    return max(common)
