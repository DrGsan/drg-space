"""Real generated certificates and the production dedicated mTLS listeners."""
import json
import socket
import time
from types import SimpleNamespace

import httpx
import pytest
from app.http import Application
from app.federation.identity import FederationError, SigningIdentity
from app.federation.runtime import FederationRuntime
from app.federation.prototype import CHALLENGE_PATH
from app.federation.protocol import DISCOVERY_PATH
from tests.test_storage_encryption import databases


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        return listener.getsockname()[1]


def test_dedicated_listener_generated_certificates_mutual_tls_and_rpc_boundary(databases,monkeypatch):
    monkeypatch.delenv('FEDERATION_TLS_CERT',raising=False)
    monkeypatch.delenv('FEDERATION_TLS_KEY',raising=False)
    monkeypatch.delenv('FEDERATION_EXTERNAL_TLS',raising=False)
    nodes = []
    try:
        for name in ('first','second'):
            port = free_port();origin = f'https://{name}.example:{port}'
            monkeypatch.setenv('FEDERATION_PUBLIC_URL',origin)
            monkeypatch.setenv('FEDERATION_BIND_PORT',str(port))
            backend = databases();app = Application(name)
            @app.get('/api/health')
            def health(): return {'ok':True}
            main = SimpleNamespace(db=backend.connection,data_cipher=backend.cipher,DATA=backend.root,
                                   PUBLIC_URL='https://'+name+'.example',app=app)
            runtime = FederationRuntime(main)
            nodes.append((runtime,port))
        first,second = [item[0] for item in nodes]
        for runtime,other in ((first,second),(second,first)):
            with runtime.state.transaction() as c:
                c.execute("INSERT INTO metadata VALUES('enabled','true')")
                c.execute('INSERT INTO peers VALUES(?,?,?,1)',
                    (other.service.identity.descriptor.server_id,other.service.identity.descriptor.model_dump_json(),
                     other.service.transport_public['fingerprint']))
                c.execute('INSERT INTO peer_certificates VALUES(?,?)',
                    (other.service.identity.descriptor.server_id,other.certificate))
            runtime.service.transport.private_origins = {other.origin:frozenset({'127.0.0.1'})}
        original = socket.getaddrinfo
        def resolve(host,port,*args,**kwargs):
            return original('127.0.0.1' if host in ('first.example','second.example') else host,port,*args,**kwargs)
        monkeypatch.setattr(socket,'getaddrinfo',resolve)
        for runtime,port in nodes:
            monkeypatch.setenv('FEDERATION_BIND_PORT',str(port));runtime.refresh()
        result = first.service.transport.post(second.service.identity.descriptor,CHALLENGE_PATH,{},int(time.time()))
        assert len(result['nonce']) == 64
        context = first.client_context(second.service.identity.descriptor)
        with httpx.Client(verify=context,trust_env=False) as browser:
            url = f'https://127.0.0.1:{nodes[1][1]}'
            options = {'headers':{'Host':'second.example'},'extensions':{'sni_hostname':'second.example'}}
            discovery = browser.get(url+DISCOVERY_PATH,**options)
            assert discovery.status_code == 200
            assert discovery.json() == json.loads(second.service.identity.descriptor.model_dump_json())
            assert browser.get(url+'/api/health',**options).status_code == 404
            assert browser.post(url+'/api/admin/federation/enable',json={'enabled':False},**options).status_code == 404
        assert second.main.app.test_client().get('/api/health').json == {'ok':True}
        for runtime,port in nodes:
            assert not list(runtime.main.DATA.glob('.drg-federation-tls-*'))
    finally:
        for runtime,port in nodes: runtime.stop()


@pytest.mark.parametrize('state,error', [('missing','peer_certificate_required'),
                                        ('mismatched','peer_certificate_mismatch'),
                                        ('revoked','peer_certificate_required')])
def test_outbound_requires_complete_active_matching_tls_pin_before_http_client(databases,monkeypatch,state,error):
    monkeypatch.setenv('FEDERATION_PUBLIC_URL','https://own.example:8444')
    monkeypatch.delenv('FEDERATION_TLS_CERT',raising=False)
    monkeypatch.delenv('FEDERATION_TLS_KEY',raising=False)
    backend=databases()
    runtime=FederationRuntime(SimpleNamespace(db=backend.connection,data_cipher=backend.cipher,DATA=backend.root,
        PUBLIC_URL='https://own.example',app=Application('pin-boundary')))
    peer=SigningIdentity.generate('https://other.example:8444').descriptor
    try:
        with runtime.state.transaction() as connection:
            connection.execute('INSERT INTO peers VALUES(?,?,?,?)',(peer.server_id,peer.model_dump_json(),
                '0'*64 if state=='mismatched' else runtime.service.transport_public['fingerprint'],0 if state=='revoked' else 1))
            if state!='missing':
                connection.execute('INSERT INTO peer_certificates VALUES(?,?)',(peer.server_id,runtime.certificate))
        runtime.service.transport.private_origins={peer.origin:frozenset({'127.0.0.1'})}
        monkeypatch.setattr(socket,'getaddrinfo',lambda *args,**kwargs:[
            (socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',8444))])
        def forbidden_http(*args,**kwargs):
            pytest.fail('An incomplete or revoked TLS pin must fail before HTTP or a private request body is sent.')
        monkeypatch.setattr(httpx,'HTTPTransport',forbidden_http)
        with pytest.raises(FederationError,match='^'+error+'$'):
            runtime.service.transport.post(peer,CHALLENGE_PATH,{'private_device_intent':'DO_NOT_TRANSMIT'},int(time.time()))
    finally:
        runtime.stop()


def test_ca_signed_different_live_leaf_is_rejected_before_any_http_bytes(databases,monkeypatch,tmp_path):
    from datetime import datetime,timedelta,timezone
    import queue
    import ssl
    import threading
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID,NameOID
    runtimes=[];listener=None;thread=None;stop=threading.Event();received=queue.Queue()
    try:
        for name in ('own','other'):
            origin=f'https://{name}.example:{free_port()}'
            monkeypatch.setenv('FEDERATION_PUBLIC_URL',origin)
            monkeypatch.delenv('FEDERATION_TLS_CERT',raising=False)
            monkeypatch.delenv('FEDERATION_TLS_KEY',raising=False)
            backend=databases()
            runtimes.append(FederationRuntime(SimpleNamespace(db=backend.connection,data_cipher=backend.cipher,
                DATA=backend.root,PUBLIC_URL='https://'+name+'.example',app=Application(name))))
        own,other=runtimes;peer=other.service.identity.descriptor
        with own.state.transaction() as connection:
            connection.execute('INSERT INTO peers VALUES(?,?,?,1)',(peer.server_id,peer.model_dump_json(),other.service.transport_public['fingerprint']))
            connection.execute('INSERT INTO peer_certificates VALUES(?,?)',(peer.server_id,other.certificate))
        key=ec.generate_private_key(ec.SECP256R1())
        issuer=x509.load_pem_x509_certificate(other.certificate.encode())
        issuer_key=serialization.load_pem_private_key(other.private,password=None)
        now=datetime.now(timezone.utc)
        leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'other.example')]))
            .issuer_name(issuer.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('other.example')]),critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
            .sign(issuer_key,hashes.SHA256()))
        cert_path,key_path=tmp_path/'leaf-chain.pem',tmp_path/'leaf-key.pem'
        cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM)+other.certificate.encode())
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        key_path.chmod(0o600)
        server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.load_cert_chain(cert_path,key_path)
        listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        listener.bind(('127.0.0.1',int(peer.origin.rsplit(':',1)[1])));listener.listen();listener.settimeout(0.2)
        def serve():
            while not stop.is_set():
                try:connection,_=listener.accept()
                except socket.timeout:continue
                except OSError:break
                wire=b''
                try:
                    with server_context.wrap_socket(connection,server_side=True) as stream:
                        stream.settimeout(2)
                        while b'\r\n\r\n' not in wire:
                            chunk=stream.recv(65536)
                            if not chunk:break
                            wire+=chunk
                        if b'\r\n\r\n' in wire:
                            headers,body=wire.split(b'\r\n\r\n',1)
                            size=next(int(row.split(b':',1)[1]) for row in headers.split(b'\r\n') if row.lower().startswith(b'content-length:'))
                            while len(body)<size:
                                chunk=stream.recv(65536)
                                if not chunk:break
                                wire+=chunk;body+=chunk
                            stream.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
                except OSError:connection.close()
                finally:received.put(wire)
        thread=threading.Thread(target=serve,daemon=True);thread.start()
        resolve=socket.getaddrinfo
        monkeypatch.setattr(socket,'getaddrinfo',lambda host,port,*args,**kw:resolve('127.0.0.1' if host=='other.example' else host,port,*args,**kw))
        context=own.client_context(peer)
        # This is a valid same-hostname chain under the pinned CA certificate,
        # so ordinary CA validation alone accepts the different live leaf.
        with httpx.Client(verify=context,trust_env=False) as client:
            assert client.post(peer.origin+CHALLENGE_PATH,content=b'CA_ONLY_WOULD_SEND_PRIVATE_BODY').status_code==200
        assert b'CA_ONLY_WOULD_SEND_PRIVATE_BODY' in received.get(timeout=3)
        own.service.transport.private_origins={peer.origin:frozenset({'127.0.0.1'})}
        with pytest.raises(FederationError,match='^peer_tls_pin_mismatch$'):
            own.service.transport.post(peer,CHALLENGE_PATH,{'private_intent':'MUST_NOT_SEND'},int(time.time()))
        # Even Authorization and other HTTP headers must remain undisclosed.
        assert received.get(timeout=3)==b''
    finally:
        stop.set()
        if listener:listener.close()
        if thread:thread.join(timeout=3)
        for runtime in runtimes:runtime.stop()
        (tmp_path/'leaf-chain.pem').unlink(missing_ok=True)
        (tmp_path/'leaf-key.pem').unlink(missing_ok=True)


def test_outbound_header_guard_fails_closed_without_tls_completion(monkeypatch):
    import ssl
    from app.federation.transport import PeerTransport
    own=SigningIdentity.generate('https://own.example')
    peer=SigningIdentity.generate('https://other.example').descriptor
    context=ssl.create_default_context();context._drg_federation_pin='a'*64
    transport=PeerTransport(own,tls_context=context,private_origins={peer.origin:{'127.0.0.1'}})
    monkeypatch.setattr(socket,'getaddrinfo',lambda *args,**kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    class MissingTLS(httpx.BaseTransport):
        def handle_request(self,request):
            request.extensions['trace']('http11.send_request_headers.started',{'request':request})
            pytest.fail('HTTP must never begin if TLS pin verification evidence is missing.')
    monkeypatch.setattr(httpx,'HTTPTransport',lambda **kwargs:MissingTLS())
    with pytest.raises(FederationError,match='^peer_tls_pin_not_verified$'):
        transport.post(peer,CHALLENGE_PATH,{'private_intent':'MUST_NOT_SEND'},int(time.time()))
