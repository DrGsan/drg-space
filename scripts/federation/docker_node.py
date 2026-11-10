"""Container-only TLS probe and verifier; never loads DrG application storage."""
import json
from pathlib import Path
import secrets
import socket
import ssl
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.hazmat.primitives import serialization
import httpx
from werkzeug.serving import run_simple

from app.federation.identity import ServerDescriptor, SigningIdentity, check_pinned_descriptor
from app.federation.probe import create_probe_app
from app.federation.protocol import (DISCOVERY_PATH, PROBE_PATH, SignedRequest, VolatileReplayWindow,
                                     sign_request, verify_response)

FIXTURES = Path('/fixtures')
STATE = Path('/state')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def descriptor(path):
    return ServerDescriptor.model_validate_json((path / 'descriptor.json').read_bytes())


def identity(path):
    key = serialization.load_pem_private_key((path / 'signing.pem').read_bytes(), password=None)
    return SigningIdentity(descriptor(path), key)


def client():
    context = ssl.create_default_context(cafile=str(FIXTURES / 'ca.pem'))
    return httpx.Client(verify=context, trust_env=False, follow_redirects=False, timeout=3)


def discover(http, expected):
    response = http.get(expected.origin + DISCOVERY_PATH)
    require(response.status_code == 200, 'Discovery failed.')
    require(len(response.content) <= 4096, 'Oversized discovery response.')
    observed = ServerDescriptor.model_validate_json(response.content)
    check_pinned_descriptor(expected, observed)


def rejected_tls(context, url, *, hostname=False):
    parsed = urlsplit(url)
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=3) as connection:
            with context.wrap_socket(connection, server_hostname=parsed.hostname):
                pass
    except ssl.SSLCertVerificationError as error:
        if hostname:
            require(error.verify_code == 62, 'Hostname mismatch was not checked.')
    else:
        raise RuntimeError('An invalid TLS certificate was accepted.')


def deliver(http, source, destination, *, body=None, signed=None, expected=200):
    body = body if body is not None else json.dumps({'challenge': secrets.token_hex(32)}).encode()
    signed = signed or sign_request(source, destination.descriptor, 'POST', PROBE_PATH, body, now=int(time.time()))
    response = http.post(destination.descriptor.origin + PROBE_PATH, content=body, headers={
        'Authorization': 'DrG-Federation ' + signed.token,
        'DrG-Server-Id': source.descriptor.server_id, 'Content-Type': 'application/json',
    })
    require(response.status_code == expected, 'Unexpected probe result; expected ' + str(expected) + '.')
    if response.status_code == 200:
        require(len(response.content) <= 4096, 'Oversized signed response.')
        verify_response(response.headers.get('DrG-Federation-Proof', ''), source.descriptor,
                        destination.descriptor, 'POST', PROBE_PATH, response.content,
                        now=int(time.time()), replay=VolatileReplayWindow(),
                        request_id=signed.request_id, status=response.status_code)
        require(response.json()['challenge'] == json.loads(body)['challenge'], 'Wrong challenge response.')
    return body, signed


def check():
    alice, bob = (identity(FIXTURES / name) for name in ('alice', 'bob'))
    with client() as http:
        for source, destination in ((alice, bob), (bob, alice)):
            discover(http, destination.descriptor)
            body, signed = deliver(http, source, destination)
            deliver(http, source, destination, body=body, signed=signed, expected=409)
            fresh = sign_request(source, destination.descriptor, 'POST', PROBE_PATH, body, now=int(time.time()))
            deliver(http, source, destination, body=body + b' ', signed=fresh, expected=401)
            deliver(http, source, destination, body=body, signed=fresh)
            expired = sign_request(source, destination.descriptor, 'POST', PROBE_PATH, body, now=int(time.time()) - 121)
            deliver(http, source, destination, body=body, signed=expired, expected=401)
            imposter = SigningIdentity.generate(source.descriptor.origin)
            deliver(http, imposter, destination, expected=403)
            if source is alice:
                path = STATE / 'before-restart.json'
                with path.open('w') as output:
                    json.dump({'body': body.decode(), 'token': signed.token, 'request_id': signed.request_id}, output)
                path.chmod(0o600)
        # A hostname mismatch and an untrusted CA must both fail, even inside Docker.
        rejected_tls(ssl.create_default_context(), bob.descriptor.origin)
        rejected_tls(ssl.create_default_context(cafile=str(FIXTURES / 'ca.pem')),
                     'https://wrong.federation.test:8443', hostname=True)
    print('PASS: two separate containers; pinned HTTPS identities and signed responses in both directions.')
    print('PASS: replay, body tampering, expired proofs, unknown servers, untrusted TLS CA, and wrong TLS hostname rejected.')


def offline():
    alice, bob = (descriptor(FIXTURES / name) for name in ('alice', 'bob'))
    with client() as http:
        discover(http, alice)
        try:
            http.get(bob.origin + DISCOVERY_PATH)
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        else:
            raise RuntimeError('The stopped peer was still reachable.')
    print('PASS: stopped peer is unavailable; the independent peer remains reachable.')


def restarted():
    alice, bob = (identity(FIXTURES / name) for name in ('alice', 'bob'))
    saved = json.loads((STATE / 'before-restart.json').read_bytes())
    with client() as http:
        discover(http, bob.descriptor)
        deliver(http, alice, bob)
        print('PASS: restarted container keeps the same fixture identity and accepts a fresh signed request.')
        # The current probe deliberately has a volatile replay cache. Record the
        # limitation explicitly; never count this as restart-safe authorization.
        deliver(http, alice, bob, body=saved['body'].encode(),
                signed=SignedRequest(saved['token'], saved['request_id']))
        print('KNOWN LIMITATION: restart clears the probe replay cache; a still-valid old proof is accepted.')


def main():
    command = sys.argv[1]
    if command == 'serve':
        own = identity(FIXTURES)
        peer = ServerDescriptor.model_validate_json((FIXTURES / 'peer.json').read_bytes())
        app = create_probe_app(own, trusted_peers=(peer,), enabled=True)
        run_simple('0.0.0.0', 8443, app, threaded=True, use_reloader=False,
                   ssl_context=(str(FIXTURES / 'tls.pem'), str(FIXTURES / 'tls-key.pem')))
    elif command == 'health':
        with client() as http:
            discover(http, descriptor(FIXTURES))
    elif command == 'check':
        check()
    elif command == 'offline':
        offline()
    elif command == 'restarted':
        restarted()
    else:
        raise RuntimeError('Unknown test command.')


if __name__ == '__main__':
    main()
