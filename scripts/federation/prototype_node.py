"""Disposable full DrG node with explicit prototype mounting and optional mTLS."""
import base64
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

FIXTURES = Path('/fixtures')


def configuration():
    value = json.loads((FIXTURES / 'app.json').read_bytes())
    os.environ.update(value)
    os.environ.update(FEDERATION_PUBLIC_URL=value['PUBLIC_URL'],FEDERATION_EXTERNAL_TLS='true',
                      FEDERATION_TLS_CERT=str(FIXTURES/'tls.pem'),FEDERATION_TLS_KEY=str(FIXTURES/'tls-key.pem'))
    os.environ.update(DATA_DIR='/data', WEB_PUSH_ENABLED='false', FILE_STORAGE='local',
                      FAMILY_PHOTO_URL='', FAMILY_PHOTO_TOKEN='', CALL_STUN_URLS='')
    return value


def context(client=False):
    if client:
        value = ssl.create_default_context(cafile=str(FIXTURES / 'ca.pem'))
        value.load_cert_chain(str(FIXTURES / 'tls.pem'), str(FIXTURES / 'tls-key.pem'))
    else:
        value = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        value.load_cert_chain(str(FIXTURES / 'tls.pem'), str(FIXTURES / 'tls-key.pem'))
        value.load_verify_locations(cafile=str(FIXTURES / 'ca.pem'))
        value.verify_mode = ssl.CERT_OPTIONAL
    return value


def main():
    config = configuration()
    command = sys.argv[1]
    if command == 'health':
        import httpx
        with httpx.Client(verify=ssl.create_default_context(cafile=str(FIXTURES / 'ca.pem')),
                          trust_env=False, timeout=3) as client:
            result = client.get(config['PUBLIC_URL'] + '/api/health')
            result.raise_for_status()
        return
    if command == 'compromise':
        # Fault injection ONLY via docker exec in the disposable test project.
        # No network route exports a key; nothing secret is printed to stdout.
        from app.data_crypto import DataCipher
        from app.federation.storage import FederationState
        from app.database import Database
        from cryptography.hazmat.primitives import serialization
        backend=Database(Path('/data'),cipher=DataCipher.from_env());backend.initialize(create=False)
        state = FederationState(backend.connection,backend.cipher,backend.root)
        identity = state.identity(config['PUBLIC_URL'])
        key = identity.private_key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        path = Path('/attack/server.json')
        with path.open('w') as output:
            path.chmod(0o600)
            json.dump({'descriptor': identity.descriptor.model_dump(mode='json'),
                       'private_key': base64.b64encode(key).decode()}, output)
        return
    from app import main as main_app
    from app.federation.transport import PeerTransport
    allowed = {}
    for name in ('alice', 'bob'):
        origin = 'https://' + name + '.federation.test:8443'
        for attempt in range(60):
            try:
                allowed[origin] = {item[4][0] for item in socket.getaddrinfo(name + '.federation.test', 8443,
                                                                           type=socket.SOCK_STREAM)}
                break
            except socket.gaierror:
                if attempt == 59:
                    raise
                time.sleep(0.5)
    main_app.federation_service.transport = PeerTransport(main_app.federation_service.identity,
        tls_context=context(client=True),private_origins=allowed)
    main_app.federation_service.transport.context_provider=main_app.federation_runtime.client_context
    from werkzeug.serving import make_server
    server = make_server('0.0.0.0', 8443, main_app.app, threaded=True, ssl_context=context())
    def stop(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    main_app.app.start_services()
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        main_app.app.stop_services()


if __name__ == '__main__':
    main()
