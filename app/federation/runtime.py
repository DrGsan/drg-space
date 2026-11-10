"""Optional dedicated mTLS listener; ordinary HTTPS/UI remains on the existing port."""
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import ssl
import tempfile
import threading
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .calls import FederatedCalls
from .identity import FederationError
from .prototype import FederationPrototype
from .storage import FederationState
from app.process_locks import try_file_lock


def listener_class():
    from werkzeug.serving import ThreadedWSGIServer

    class BoundedListener(ThreadedWSGIServer):
        def __init__(self,*args,**kwargs):
            self.slots = threading.BoundedSemaphore(32)
            super().__init__(*args,**kwargs)
            # TLS handshakes run in a bounded worker with a socket timeout.
            self.socket.do_handshake_on_connect = False

        def get_request(self):
            stream,address = super().get_request()
            stream.settimeout(10)
            return stream,address

        def process_request(self,stream,address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(stream)
                return
            try: super().process_request(stream,address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self,stream,address):
            try: super().process_request_thread(stream,address)
            finally: self.slots.release()
    return BoundedListener


class FederationRuntime:
    def __init__(self, main):
        self.main = main
        self.state = FederationState(main.db, main.data_cipher, main.DATA)
        configured = os.getenv('FEDERATION_PUBLIC_URL', '').rstrip('/')
        public = urlsplit(main.PUBLIC_URL)
        host = '[' + public.hostname + ']' if public.hostname and ':' in public.hostname else public.hostname
        self.origin = configured or ('https://' + host + ':8444' if public.scheme == 'https' and host else main.PUBLIC_URL)
        self.external_tls = os.getenv('FEDERATION_EXTERNAL_TLS', 'false').lower() in ('true','1','yes')
        self.server = self.worker = self.lease = None
        self.delivery_lease = None
        self.lock = threading.RLock()
        try:
            self.service = FederationPrototype(main, state=self.state, identity_origin=self.origin)
        except FederationError as error:
            if str(error) != 'server_origin_changed':
                raise
            # An unconfirmed domain change must not take the local site down.
            self.service = FederationPrototype(main, state=self.state, identity_origin='http://disabled.invalid')
        self.service.on_enable = self.refresh
        if self.service.identity:
            self._material()
            self.service.transport.context_provider = self.client_context
            self.service.calls = FederatedCalls(self.service.identity.descriptor.server_id,
                authorize=self.authorize, person=self.person)
        self.stamp = self._state_stamp()
        main.app.include_router(self.service.router)

    def _state_stamp(self):
        with self.state.transaction() as connection:
            values = dict(connection.execute("SELECT key,value FROM metadata WHERE key IN ('descriptor','private_key','result_private_key','tls_certificate','tls_private_key','restore_epoch')"))
        return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).digest()

    def authorize(self, connection, uid, peer_id, actor_id, device_id=None):
        from app.account_lifecycle import pending
        if pending(connection.application_connection if hasattr(connection,'application_connection') else connection,uid):
            raise FederationError('resource_access_denied')
        self.service.peer(connection, peer_id)
        contact = self.service.contact(connection, uid, peer_id, actor_id, accepted=True)
        if device_id is not None:
            self.service.confirmed_card(connection,uid,peer_id,actor_id,device_id)

    def person(self, connection, uid, peer_id, actor_id, now):
        row = connection.execute('SELECT profile FROM profiles WHERE user_id=? AND peer_id=? AND actor_id=?',
                                  (uid, peer_id, actor_id)).fetchone()
        profile = json.loads(row[0]) if row else {}
        return {'id': actor_id, 'server_id': peer_id, 'display_name': profile.get('display_name') or profile.get('login') or actor_id,
                'login': profile.get('login') or actor_id, 'avatar_url': None, 'online': False, 'last_seen': None}

    def _material(self):
        certificate_path = os.getenv('FEDERATION_TLS_CERT', '')
        private_path = os.getenv('FEDERATION_TLS_KEY', '')
        if bool(certificate_path) != bool(private_path):
            raise FederationError('tls_certificate_and_key_required')
        if certificate_path and private_path:
            self.certificate = Path(certificate_path).read_text()
            self.private = Path(private_path).read_bytes()
        else:
            with self.state.transaction() as connection:
                records = dict(connection.execute("SELECT key,value FROM metadata WHERE key IN ('tls_certificate','tls_private_key')"))
                if records:
                    self.certificate = records['tls_certificate']
                    self.private = records['tls_private_key'].encode()
                else:
                    key = ec.generate_private_key(ec.SECP256R1())
                    hostname = urlsplit(self.origin).hostname
                    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
                    try: alternative = x509.IPAddress(ipaddress.ip_address(hostname))
                    except ValueError: alternative = x509.DNSName(hostname)
                    now = datetime.now(timezone.utc)
                    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                        .public_key(key.public_key()).serial_number(x509.random_serial_number())
                        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
                        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                        .add_extension(x509.SubjectAlternativeName([alternative]), critical=False)
                        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
                        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                            encipher_only=False, decipher_only=False), critical=True)
                        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
                        .sign(key, hashes.SHA256()))
                    self.certificate = cert.public_bytes(serialization.Encoding.PEM).decode()
                    self.private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption())
                    connection.execute('INSERT INTO metadata VALUES(?,?)', ('tls_certificate', self.certificate))
                    connection.execute('INSERT INTO metadata VALUES(?,?)', ('tls_private_key', self.private.decode()))
        cert = x509.load_pem_x509_certificate(self.certificate.encode())
        private = serialization.load_pem_private_key(self.private,password=None)
        if cert.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo) != private.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo):
            raise FederationError('tls_certificate_key_mismatch')
        self.service.transport_public = {'certificate_pem': self.certificate, 'fingerprint': cert.fingerprint(hashes.SHA256()).hex()}

    def _load_key(self, context):
        with tempfile.TemporaryDirectory(prefix='.drg-federation-tls-', dir=self.main.DATA) as root:
            cert, key = Path(root) / 'cert.pem', Path(root) / 'key.pem'
            cert.write_text(self.certificate)
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output: output.write(self.private)
            context.load_cert_chain(str(cert), str(key))

    def client_context(self, peer):
        with self.state.transaction() as connection:
            row = connection.execute('SELECT pc.certificate,p.tls_pin FROM peer_certificates pc JOIN peers p ON p.id=pc.peer_id WHERE p.id=? AND p.active=1', (peer.server_id,)).fetchone()
        # Trusting the signing descriptor alone never authorizes a fallback to
        # public CAs. A complete independently confirmed TLS pin is mandatory
        # before creating an HTTP client or transmitting any device intent.
        if not row:
            raise FederationError('peer_certificate_required')
        try:
            fingerprint = x509.load_pem_x509_certificate(row[0].encode()).fingerprint(hashes.SHA256()).hex()
        except ValueError:
            raise FederationError('peer_certificate_mismatch') from None
        if fingerprint != row[1]:
            raise FederationError('peer_certificate_mismatch')
        context = ssl.create_default_context(cadata=row[0])
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        context._drg_federation_pin = fingerprint
        self._load_key(context)
        return context

    def refresh(self):
        with self.lock:
            stamp = self._state_stamp()
            if stamp != self.stamp:
                # Restore can replace the identity while workers remain alive.
                # Never serve with keys cached from the replaced database.
                self.stop()
                try: self.service.identity = self.state.identity(self.origin) if self.origin.startswith('https://') and self.main.data_cipher.enabled else None
                except FederationError: self.service.identity = None
                if self.service.identity:
                    from .transport import PeerTransport
                    self._material()
                    self.service.transport = PeerTransport(self.service.identity)
                    self.service.transport.context_provider = self.client_context
                    self.service.calls = FederatedCalls(self.service.identity.descriptor.server_id,
                        authorize=self.authorize,person=self.person)
                else:
                    self.service.transport = self.service.calls = None
                self.stamp = self._state_stamp()
            with self.state.transaction() as connection:
                enabled = self.service.enabled(connection) and bool(self.service.identity)
                certificates = [row[0] for row in connection.execute(
                    'SELECT pc.certificate FROM peer_certificates pc JOIN peers p ON p.id=pc.peer_id WHERE p.active=1')]
            if not enabled:
                self.stop()
                return
            if self.external_tls:
                return
            if self.server:
                for certificate in certificates:
                    self.server.ssl_context.load_verify_locations(cadata=certificate)
                return
            lease = open(Path(self.main.DATA) / 'federation-listener.lock', 'a+b')
            try: fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lease.close()
                return
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.verify_mode = ssl.CERT_OPTIONAL
            context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
            self._load_key(context)
            for certificate in certificates: context.load_verify_locations(cadata=certificate)
            port = int(os.getenv('FEDERATION_BIND_PORT', '8444'))
            try:
                self.server = listener_class()('0.0.0.0', port, self._rpc_only, ssl_context=context)
                self.server.timeout = 10
            except BaseException:
                lease.close()
                raise
            self.lease = lease
            self.worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.1},
                                           name='drg-federation-mtls', daemon=True)
            self.worker.start()

    def _rpc_only(self, environ, start_response):
        from .protocol import DISCOVERY_PATH
        from .prototype import CHALLENGE_PATH, OPERATION_PATH
        from .revocations import REVOCATION_PATH
        path, method = environ.get('PATH_INFO'), environ.get('REQUEST_METHOD')
        if not ((path == DISCOVERY_PATH and method == 'GET') or
                (path in (CHALLENGE_PATH,OPERATION_PATH,REVOCATION_PATH) and method == 'POST')):
            start_response('404 Not Found',[('Content-Type','application/json'),('Cache-Control','no-store')])
            return [b'{"error":"not_found"}']
        return self.main.app(environ,start_response)

    def maintain(self):
        self.refresh()
        if self.delivery_lease is None:
            self.delivery_lease=try_file_lock(self.main.DATA,'federation-delivery.lock')
        if self.delivery_lease:
            from .revocations import deliver
            if deliver(self.service):return 5
        return 30

    def stop(self):
        with self.lock:
            if self.delivery_lease:
                self.delivery_lease.close()
                self.delivery_lease=None
            if self.server:
                self.server.shutdown()
                self.server.server_close()
                self.worker.join(timeout=5)
                self.server = self.worker = None
            if self.lease:
                self.lease.close()
                self.lease = None
