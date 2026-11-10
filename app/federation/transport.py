"""Bounded HTTPS + mutual TLS transport with DNS addresses pinned per request."""
import hashlib
import ipaddress
import re
import socket
import ssl
from urllib.parse import urlsplit

import httpx
from .actors import canonical
from .identity import FederationError, normalize_origin
from .protocol import MAX_BODY_BYTES, sign_request, verify_response, VolatileReplayWindow


def _pin_trace(fingerprint):
    verified = False
    def trace(event, info):
        nonlocal verified
        if event in ('connection.connect_tcp.started','connection.start_tls.started'):
            verified = False
        elif event == 'connection.start_tls.complete':
            stream = info.get('return_value')
            try:
                certificate = stream.get_extra_info('ssl_object').getpeercert(True)
                if not certificate or hashlib.sha256(certificate).hexdigest() != fingerprint:
                    raise ValueError()
            except (AttributeError, TypeError, ValueError, OSError):
                if stream is not None:
                    try: stream.close()
                    except Exception: pass
                raise FederationError('peer_tls_pin_mismatch') from None
            verified = True
        elif event in ('http11.send_request_headers.started','http11.send_request_body.started',
                       'http2.send_connection_init.started','http2.send_request_headers.started',
                       'http2.send_request_body.started') and not verified:
            raise FederationError('peer_tls_pin_not_verified')
    return trace


class PeerTransport:
    def __init__(self, identity, *, tls_context=None, private_origins=None):
        self.identity = identity
        self.context = tls_context if tls_context is not None else ssl.create_default_context()
        if self.context.verify_mode != ssl.CERT_REQUIRED or not self.context.check_hostname:
            raise ValueError('Verified TLS is mandatory.')
        # Only the disposable Docker runner supplies exact origin/address pairs.
        self.private_origins = {origin: frozenset(values) for origin, values in (private_origins or {}).items()}
        self.responses = VolatileReplayWindow()
        self.context_provider = None

    def addresses(self, origin):
        parsed = urlsplit(normalize_origin(origin))
        try:
            values = {str(ipaddress.ip_address(item[4][0])) for item in
                      socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
        except (OSError, ValueError):
            raise FederationError('peer_unavailable') from None
        allowed = self.private_origins.get(origin)
        if not values or any(not ipaddress.ip_address(value).is_global and (allowed is None or value not in allowed)
                             for value in values):
            raise FederationError('unsafe_peer_address')
        if allowed is not None and not values <= allowed:
            raise FederationError('peer_address_changed')
        return parsed, sorted(values)

    def post(self, peer, path, value, now):
        body = canonical(value)
        signed = sign_request(self.identity, peer, 'POST', path, body, now=now)
        parsed, addresses = self.addresses(peer.origin)
        # HTTP core connects to this literal IP; TLS still verifies the pinned
        # hostname using SNI. There is no second DNS lookup at connection time.
        url = httpx.URL(peer.origin + path).copy_with(host=addresses[0])
        try:
            context = self.context_provider(peer) if self.context_provider else self.context
            pin = getattr(context, '_drg_federation_pin', None)
            if self.context_provider and (not isinstance(pin,str) or not re.fullmatch(r'[a-f0-9]{64}',pin)):
                raise FederationError('peer_certificate_required')
            extensions = {'sni_hostname': parsed.hostname}
            if pin is not None:
                extensions['trace'] = _pin_trace(pin)
            with httpx.Client(transport=httpx.HTTPTransport(verify=context, retries=0),
                              trust_env=False, follow_redirects=False, timeout=5) as client:
                with client.stream('POST', url, content=body, headers={
                    'Host': parsed.netloc, 'Content-Type': 'application/json',
                    'Authorization': 'DrG-Federation ' + signed.token,
                    'DrG-Server-Id': self.identity.descriptor.server_id,
                }, extensions=extensions) as response:
                    if response.status_code != 200:
                        # Never follow Location or interpret an unsigned error as success.
                        error = FederationError('peer_rejected_' + str(response.status_code))
                        error.status_code = response.status_code if 400 <= response.status_code < 500 else 503
                        raise error
                    result = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        result.extend(chunk)
                        if len(result) > MAX_BODY_BYTES:
                            raise FederationError('peer_response_too_large')
                    proof = response.headers.get('DrG-Federation-Proof', '')
                    verify_response(proof, self.identity.descriptor, peer, 'POST', path, bytes(result),
                                    now=now, replay=self.responses, request_id=signed.request_id, status=200)
                    import json
                    return json.loads(result)
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, FederationError):
                raise
            raise FederationError('peer_unavailable') from None
