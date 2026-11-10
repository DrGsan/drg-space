#!/usr/bin/env python3
"""Run two ephemeral loopback probes, verify their proofs, then stop both.

No .env, app.main, application database, persistent keys, or remote servers.
This is a protocol experiment, not a deployable DrG federation service.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import secrets
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
import httpx
from werkzeug.serving import WSGIRequestHandler, make_server

from app.federation.identity import (FederationError, ServerDescriptor, SigningIdentity,
                                     check_pinned_descriptor)
from app.federation.probe import create_probe_app
from app.federation.protocol import (DISCOVERY_PATH, PROBE_PATH, VolatileReplayWindow,
                                     sign_request, verify_response)


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        pass


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run():
    with ExitStack() as stack:
        # Bind both ports before creating identities; no port-selection race.
        servers = []
        for _ in range(2):
            server = make_server('127.0.0.1', 0, Flask('pending-probe'), threaded=True,
                                  request_handler=QuietHandler)
            servers.append(server)
            stack.callback(server.server_close)
        identities = [SigningIdentity.generate('http://127.0.0.1:' + str(server.server_port),
                                               allow_loopback_http=True) for server in servers]
        for index, server in enumerate(servers):
            server.app = create_probe_app(identities[index], enabled=True,
                                           trusted_peers=(identities[1 - index].descriptor,))
            worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            worker.start()
            stack.callback(worker.join, 5)
            stack.callback(server.shutdown)
        client = stack.enter_context(httpx.Client(timeout=5, trust_env=False, follow_redirects=False))
        for source, destination in (identities, identities[::-1]):
            url = destination.descriptor.origin
            discovery = client.get(url + DISCOVERY_PATH)
            require(discovery.status_code == 200, 'Discovery failed.')
            require(len(discovery.content) <= 4096, 'Discovery exceeded its size limit.')
            observed = ServerDescriptor.model_validate_json(discovery.content,
                                                             context={'allow_loopback_http': True})
            check_pinned_descriptor(destination.descriptor, observed)
            challenge = secrets.token_hex(32)
            body = json.dumps({'challenge': challenge}, separators=(',', ':')).encode()
            signed = sign_request(source, destination.descriptor, 'POST', PROBE_PATH, body, now=int(time.time()))
            headers = {'DrG-Server-Id': source.descriptor.server_id,
                       'Authorization': 'DrG-Federation ' + signed.token,
                       'Content-Type': 'application/json'}
            response = client.post(url + PROBE_PATH, content=body, headers=headers)
            require(response.status_code == 200, 'Signed probe was rejected.')
            require(len(response.content) <= 4096, 'Probe exceeded its size limit.')
            verify_response(response.headers.get('DrG-Federation-Proof', ''), source.descriptor,
                            destination.descriptor, 'POST', PROBE_PATH, response.content,
                            now=int(time.time()), replay=VolatileReplayWindow(),
                            request_id=signed.request_id, status=response.status_code)
            require(response.json()['challenge'] == challenge, 'Challenge did not match.')
            repeated = client.post(url + PROBE_PATH, content=body, headers=headers)
            require(repeated.status_code == 409, 'Replay was not rejected.')
            fresh = sign_request(source, destination.descriptor, 'POST', PROBE_PATH, body, now=int(time.time()))
            tampered = client.post(url + PROBE_PATH, content=body + b' ',
                                    headers=headers | {'Authorization': 'DrG-Federation ' + fresh.token})
            require(tampered.status_code == 401, 'Tampered body was not rejected.')
            # A failed tamper must not consume the valid proof.
            accepted = client.post(url + PROBE_PATH, content=body,
                                    headers=headers | {'Authorization': 'DrG-Federation ' + fresh.token})
            require(accepted.status_code == 200, 'Valid proof was consumed by a failed request.')
        print('PASS: two loopback servers; pinned discovery; signed requests and responses in both directions.')
        print('PASS: replay and body tampering rejected; valid retry preserved; no application storage opened.')
    print('STOPPED: both servers closed; ephemeral identities were not written to disk.')


if __name__ == '__main__':
    try:
        run()
    except (FederationError, ValueError, RuntimeError, httpx.HTTPError):
        raise SystemExit('FAIL: local federation probe did not pass; no application storage was opened.') from None
