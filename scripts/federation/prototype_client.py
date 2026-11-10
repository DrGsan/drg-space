"""External device driver for two real DrG API installations and theft attacks."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import ssl
import sys
import subprocess
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import httpx
import pyotp
from pycrdt import Doc, Map, XmlElement, XmlFragment, XmlText
from websockets.sync.client import connect

from app.federation.actors import DeviceCard, DeviceKeys, canonical, verify_result
from app.federation.identity import FederationError, ServerDescriptor, SigningIdentity
from app.federation.protocol import SignedRequest, sign_request
from app.federation.prototype import OPERATION_PATH, Intent

FIXTURES, STATE = Path('/fixtures'), Path('/state')
PASSWORD = 'Disposable-prototype-password-123'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def checked(response, status=200):
    require(response.status_code == status, 'HTTP ' + str(response.status_code) + ': ' + response.text[:300])
    return response.json()


def context(name=None):
    result = ssl.create_default_context(cafile=str(FIXTURES / 'ca.pem'))
    if name:
        result.load_cert_chain(str(FIXTURES / name / 'tls.pem'), str(FIXTURES / name / 'tls-key.pem'))
    return result


def cert_pin(name):
    return x509.load_pem_x509_certificate((FIXTURES / name / 'tls.pem').read_bytes()).fingerprint(hashes.SHA256()).hex()


class User:
    def __init__(self, name, saved=None, registered_by=None):
        self.name = name
        self.origin = 'https://' + name + '.federation.test:8443'
        self.http = httpx.Client(base_url=self.origin, headers={'Origin': self.origin},
                                 verify=context(), trust_env=False, timeout=15)
        self.config = json.loads((FIXTURES / name / 'app.json').read_bytes())
        if saved:
            for cookie in saved['cookies']:
                self.http.cookies.set(**cookie)
            self.http.headers['x-csrf-token'] = saved['csrf']
            self.descriptor = ServerDescriptor.model_validate_json(json.dumps(saved['descriptor']))
            self.card = DeviceCard.model_validate(saved['card'])
            self.keys = DeviceKeys(Ed25519PrivateKey.from_private_bytes(bytes.fromhex(saved['signing'])),
                                   X25519PrivateKey.from_private_bytes(bytes.fromhex(saved['encryption'])))
            self.uid = self.card.actor_id
        else:
            if registered_by:
                checked(registered_by.http.post('/api/admin/access/unlock', json={'password': PASSWORD}))
                invitation = checked(registered_by.http.post('/api/invites', json={'login': 'attacker'}))
                response = checked(self.http.post('/api/register', json={'login': 'attacker', 'password': PASSWORD,
                                                                          'invite': invitation['url'].split('/')[-1]}))
            else:
                response = checked(self.http.post('/api/setup', json={
                    'login': 'owner', 'password': PASSWORD, 'token': self.config['SETUP_TOKEN']}))
            self.http.headers['x-csrf-token'] = response['csrf']
            enrolled = checked(self.http.post('/api/2fa/enroll', json={'password': PASSWORD}))
            checked(self.http.post('/api/2fa/confirm', json={'code': pyotp.TOTP(enrolled['secret']).now()}))
            response = checked(self.http.post('/api/2fa/activate', json={'saved_recovery_codes': True}))
            self.http.headers['x-csrf-token'] = response['csrf']
            if not registered_by:
                checked(self.http.post('/api/admin/access/unlock', json={'password': PASSWORD}))
                checked(self.http.post('/api/admin/federation/enable', json={'enabled': True}))
            info = checked(self.http.get('/api/federation/info'))
            self.descriptor = ServerDescriptor.model_validate_json(json.dumps(info['descriptor']))
            self.uid = checked(self.http.get('/api/me'))['id']
            self.keys = DeviceKeys.generate()
            self.card = self.keys.card(self.descriptor.server_id, self.uid)
            nonce = checked(self.http.post('/api/federation/devices/challenge', json={}))['nonce']
            intent = {'operation': 'enroll', 'card': self.card.model_dump(), 'nonce': nonce}
            proof = self.keys.sign(self.card, self.descriptor.server_id, intent, int(time.time()))
            checked(self.http.post('/api/federation/devices', json={'card': self.card.model_dump(), 'nonce': nonce, 'proof': proof}))

    def save(self):
        cookies = [{'name': item.name, 'value': item.value, 'domain': item.domain, 'path': item.path}
                   for item in self.http.cookies.jar]
        return {'cookies': cookies, 'csrf': self.http.headers['x-csrf-token'],
                'descriptor': self.descriptor.model_dump(mode='json'), 'card': self.card.model_dump(),
                'signing': self.keys.signing.private_bytes_raw().hex(),
                'encryption': self.keys.encryption.private_bytes_raw().hex()}

    def trust(self, other):
        # The fixture independently knows this exact public card and TLS pin;
        # use the same complete, explicit confirmation as browser/native users.
        package = {'version': 1, 'server': other.descriptor.model_dump(mode='json'),
                   'actor': other.card.model_dump(), 'transport': {
                       'certificate_pem': (FIXTURES / other.name / 'tls.pem').read_text(),
                       'fingerprint': cert_pin(other.name)}}
        checked(self.http.post('/api/federation/contacts/import', json={'package': package, 'confirmed': True}))

    def prepare(self, other, operation, payload):
        result = checked(self.http.post('/api/federation/prepare', json={
            'peer_id': other.descriptor.server_id, 'device_id': self.card.device_id,
            'target_actor_id': other.uid, 'operation': operation, 'payload': payload}))
        intent = Intent.model_validate(result['intent']).model_dump()
        expected = {'actor_server_id': self.descriptor.server_id, 'actor_id': self.uid,
                    'device_id': self.card.device_id, 'target_actor_id': other.uid,
                    'operation': operation, 'payload': payload}
        require(all(intent[key] == value for key, value in expected.items()), 'Server substituted the device intent.')
        return intent

    def send(self, other, intent, *, status=200, proof=None):
        proof = proof or self.keys.sign(self.card, other.descriptor.server_id, intent, int(time.time()))
        response = self.http.post('/api/federation/send', json={
            'peer_id': other.descriptor.server_id, 'intent': intent, 'proof': proof})
        result = checked(response, status)
        if status==200:
            verify_result(other.descriptor,self.card,intent,result,int(time.time()))
            return self.keys.open(result,intent)
        return result

    def operation(self, other, operation, payload, *, status=200):
        return self.send(other, self.prepare(other, operation, payload), status=status)

    def grant(self, other, note_id, role):
        return checked(self.http.post('/api/federation/grants', json={
            'note_id': note_id, 'peer_id': other.descriptor.server_id, 'actor_id': other.uid, 'role': role}))

    def socket(self, note_id):
        cookies = '; '.join(cookie.name + '=' + cookie.value for cookie in self.http.cookies.jar)
        socket = connect(self.origin.replace('https://', 'wss://') + '/api/live/notes/' + note_id,
                         origin=self.origin, additional_headers={'Cookie': cookies}, ssl=context(),
                         legacy=True, open_timeout=10, close_timeout=2)
        socket.send(json.dumps({'type': 'auth', 'csrf': self.http.headers['x-csrf-token'], 'client_id': 12345}))
        return socket


def event(socket, kind):
    for _ in range(30):
        item = json.loads(socket.recv(timeout=10))
        require(item['type'] != 'error', 'CRDT error: ' + str(item))
        if item['type'] == kind:
            return item
    raise RuntimeError('Missing socket event: ' + kind)


def document(text):
    paragraph = XmlElement('paragraph', {'textAlignment': 'left'}, [XmlText(text)])
    group = XmlElement('blockGroup', {}, [XmlElement('blockContainer', {'id': 'prototype-paragraph'}, [paragraph])])
    return Doc({'prosemirror': XmlFragment([group]), 'meta': Map({'title': 'Federated prototype'})})


def text_node(doc):
    group = list(doc.get('prosemirror', type=XmlFragment).children)[0]
    return list(list(list(group.children)[0].children)[0].children)[0]


def load(state):
    doc = Doc({'prosemirror': XmlFragment(), 'meta': Map()})
    doc.apply_update(base64.b64decode(state))
    return doc


def create_note(owner, text):
    space = checked(owner.http.post('/api/social/families', json={'name': 'Federation prototype'}))['id']
    note = checked(owner.http.post('/api/notes', json={'title': 'Federated prototype', 'body': text, 'space_id': space}))
    socket = owner.socket(note['id'])
    initial = event(socket, 'init')
    doc = document(text)
    socket.send(json.dumps({'type': 'init', 'version': initial['page']['version'],
                            'update': base64.b64encode(doc.get_update()).decode()}))
    sync = event(socket, 'sync')
    return note['id'], socket, sync


def shared_note(owner, guest):
    note_id, socket, sync = create_note(owner, 'PRIVATE_SHARED_BODY')
    try:
        grant = owner.grant(guest, note_id, 'editor')
        payload = {'note_id': note_id, 'revision': grant['revision']}
        read = guest.operation(owner, 'read', payload)
        require(read['body'] == 'PRIVATE_SHARED_BODY', 'Shared document was not read.')
        a, b = load(sync['state']), load(read['state'])
        before_a, before_b = a.get_state(), b.get_state()
        text_node(a).insert(0, 'LOCAL_EDIT ')
        text_node(b).insert(0, 'REMOTE_EDIT ')
        update = payload | {'epoch': read['epoch'], 'update': base64.b64encode(b.get_update(before_b)).decode()}
        intent = guest.prepare(owner, 'update', update)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(guest.send, owner, intent)
            socket.send(json.dumps({'type': 'update', 'epoch': sync['epoch'], 'seq': 1,
                                    'update': base64.b64encode(a.get_update(before_a)).decode()}))
            kinds = set()
            for _ in range(30):
                item = json.loads(socket.recv(timeout=10))
                require(item['type'] != 'error', 'Local CRDT error.')
                kinds.add(item['type'])
                if {'ack', 'update'} <= kinds:
                    break
            require({'ack', 'update'} <= kinds, 'Remote edit was not broadcast to the open local editor.')
            future.result(timeout=15)
        saved = checked(owner.http.get('/api/notes/' + note_id))
        require(all(value in saved['body'] for value in ('LOCAL_EDIT', 'REMOTE_EDIT', 'PRIVATE_SHARED_BODY')),
                'Concurrent edits did not merge in the real notes table.')
        reread = guest.operation(owner, 'read', payload)
        require(reread['body'] == saved['body'], 'Remote and local document content diverged.')
        retry = guest.prepare(owner, 'update', update)
        retry['event_id'] = intent['event_id']
        guest.send(owner, retry)
        require(checked(owner.http.get('/api/notes/' + note_id))['version'] == saved['version'],
                'A retried application event changed the document twice.')
        print('PASS: ' + owner.name + ' owns a real shared note; remote read, concurrent CRDT edits, live local broadcast, and event deduplication.')
        return {'note_id': note_id, 'revision': grant['revision'], 'intent': intent, 'payload': update}
    finally:
        socket.close()


def save(value):
    path = STATE / 'client.json'
    with path.open('w') as output:
        path.chmod(0o600)
        json.dump(value, output)


def users():
    saved = json.loads((STATE / 'client.json').read_bytes())
    return User('alice', saved['alice']), User('bob', saved['bob']), saved


def scenario():
    alice, bob = User('alice'), User('bob')
    require(alice.uid != bob.uid, 'The test installations did not create independent users.')
    alice.trust(bob)
    bob.trust(alice)
    invite = bob.prepare(alice, 'invite', {})
    bob.send(alice, invite)
    require(checked(alice.http.get('/api/federation/contacts'))['contacts'][0]['status'] == 'incoming', 'Invitation missing.')
    alice.operation(bob, 'accept', {'invitation': invite['event_id']})
    for user in (alice, bob):
        require(checked(user.http.get('/api/federation/contacts'))['contacts'][0]['status'] == 'accepted', 'Friendship not mutual.')
    first, second = shared_note(alice, bob), shared_note(bob, alice)
    from scripts.federation.database_scenario import run as database_scenario
    database_scenario(alice,bob,first)
    database_scenario(bob,alice,second)
    save({'alice': alice.save(), 'bob': bob.save(), 'first': first, 'second': second, 'invitation': invite['event_id']})
    print('PASS: two independent full DrG installations; explicit owner pins, device confirmation, mutual friendship, SQLite/SQLCipher and PostgreSQL.')


def stolen_identity():
    data = json.loads((STATE / 'attack/server.json').read_bytes())
    descriptor = ServerDescriptor.model_validate_json(json.dumps(data['descriptor']))
    private = serialization.load_pem_private_key(base64.b64decode(data['private_key']), password=None)
    return SigningIdentity(descriptor, private)


def direct(http, recipient, identity, intent, proof, *, status, signed=None):
    body = canonical({'intent': intent, 'proof': proof})
    signed = signed or sign_request(identity, recipient.descriptor, 'POST', OPERATION_PATH, body, now=int(time.time()))
    response = http.post(recipient.origin + OPERATION_PATH, content=body, headers={
        'Authorization': 'DrG-Federation ' + signed.token, 'DrG-Server-Id': identity.descriptor.server_id,
        'Content-Type': 'application/json', 'SSL-Client-Cert': (FIXTURES / 'bob/tls.pem').read_text().replace('\n', '\\n'),
    })
    return checked(response, status), signed


def attacks():
    alice, bob, saved = users()
    stolen = stolen_identity()
    payload = {'note_id': saved['first']['note_id'], 'revision': saved['first']['revision']}
    intent = bob.prepare(alice, 'read', payload)
    proof = bob.keys.sign(bob.card, alice.descriptor.server_id, intent, int(time.time()))
    body = canonical({'intent': intent, 'proof': proof})
    captured = sign_request(stolen, alice.descriptor, 'POST', OPERATION_PATH, body, now=int(time.time()))
    outsider = User('bob', registered_by=bob)
    checked(outsider.http.post('/api/federation/contacts', json={'card': alice.card.model_dump()}))
    outsider.send(alice, outsider.prepare(alice, 'accept', {'invitation': saved['invitation']}), status=403)
    outsider.operation(alice, 'read', payload, status=403)
    checked(outsider.http.post('/api/federation/send', json={'peer_id': alice.descriptor.server_id,
                                                         'intent': intent, 'proof': proof}), 403)
    # A stolen token and even a complete valid signed operation cannot cross a
    # new TLS channel without the separately pinned client private key.
    with httpx.Client(verify=context(), trust_env=False, timeout=10) as no_cert:
        direct(no_cert, alice, stolen, intent, proof, status=403, signed=captured)
    with httpx.Client(verify=context('alice'), trust_env=False, timeout=10) as wrong_cert:
        direct(wrong_cert, alice, stolen, intent, proof, status=403, signed=captured)
    with httpx.Client(verify=context('bob'), trust_env=False, timeout=10) as compromised:
        unconfirmed = outsider.prepare(alice, 'read', payload)
        direct(compromised, alice, stolen, unconfirmed,
               outsider.keys.sign(outsider.card, alice.descriptor.server_id, unconfirmed, int(time.time())), status=403)
        fake_device = DeviceKeys.generate()
        fake_card = fake_device.card(bob.card.server_id, bob.uid)
        forged = fake_device.sign(fake_card, alice.descriptor.server_id, intent, int(time.time()))
        direct(compromised, alice, stolen, intent, forged, status=403)
        altered = intent | {'payload': payload | {'note_id': saved['second']['note_id']}}
        direct(compromised, alice, stolen, altered, proof, status=403)
        # Worst case: both server credentials plus an unused exact device proof
        # are stolen. The attacker can race the same approved read, but receives
        # ciphertext for the user's independently pinned X25519 key.
        envelope, _ = direct(compromised, alice, stolen, intent, proof, status=200, signed=captured)
        require('PRIVATE_SHARED_BODY' not in json.dumps(envelope), 'Stolen server credentials exposed plaintext.')
        try:
            fake_device.open(envelope, intent)
        except FederationError:
            pass
        else:
            raise RuntimeError('An attacker decrypted a result without the user key.')
        require('PRIVATE_SHARED_BODY' in bob.keys.open(envelope, intent)['body'], 'The legitimate device could not decrypt.')
        direct(compromised, alice, stolen, intent, proof, status=409, signed=captured)
    saved['capture'] = {'intent': intent, 'proof': proof, 'token': captured.token, 'request_id': captured.request_id}
    recovered = bob.prepare(alice, 'read', payload)
    recovered['event_id'] = intent['event_id']
    require('PRIVATE_SHARED_BODY' in bob.send(alice, recovered)['body'], 'The legitimate device could not recover the raced result.')
    save(saved)
    print('PASS: stolen token before first use, spoofed certificate header, wrong TLS key, stolen server signing + TLS keys, forged user proof, and intent substitution tested.')
    print('PASS: an ordinary unconfirmed account cannot use a stolen invitation, grant identifiers, or another user device proof.')
    print('PASS: even an exact read raced with both server keys returns device-only ciphertext; replay is rejected.')


def offline():
    _, bob, _ = users()
    checked(bob.http.get('/api/health'))
    with httpx.Client(verify=context(), trust_env=False, timeout=3) as http:
        try:
            http.get('https://alice.federation.test:8443/api/health')
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        else:
            raise RuntimeError('Stopped peer is reachable.')
    print('PASS: stopping the SQLite node leaves the independent PostgreSQL node available.')


def restarted():
    alice, bob, saved = users()
    require(checked(alice.http.get('/api/federation/info'))['descriptor']['server_id'] == alice.descriptor.server_id,
            'Server identity did not survive restart.')
    capture = saved['capture']
    with httpx.Client(verify=context('bob'), trust_env=False, timeout=10) as attacker:
        direct(attacker, alice, stolen_identity(), capture['intent'], capture['proof'], status=409,
               signed=SignedRequest(capture['token'], capture['request_id']))
        # A freshly signed server token cannot resurrect the used device nonce.
        direct(attacker, alice, stolen_identity(), capture['intent'], capture['proof'], status=403)
    payload = {'note_id': saved['first']['note_id'], 'revision': saved['first']['revision']}
    require('REMOTE_EDIT' in bob.operation(alice, 'read', payload)['body'], 'Data or authorization lost at restart.')
    # A downgrade invalidates all previous grant revisions and prohibits writes.
    viewer = alice.grant(bob, payload['note_id'], 'viewer')
    bob.operation(alice, 'read', payload, status=403)
    fresh = payload | {'revision': viewer['revision']}
    bob.operation(alice, 'read', fresh)
    bob.operation(alice, 'update', saved['first']['payload'] | {'revision': viewer['revision']}, status=403)
    # Revocation denies both a new request and a retry of a cached successful event.
    alice.grant(bob, payload['note_id'], 'none')
    bob.operation(alice, 'read', fresh, status=403)
    retry = bob.prepare(alice, 'read', capture['intent']['payload'])
    retry['event_id'] = capture['intent']['event_id']
    bob.send(alice, retry, status=403)
    checked(alice.http.post('/api/admin/access/unlock', json={'password': PASSWORD}))
    checked(alice.http.post('/api/admin/federation/peers/' + bob.descriptor.server_id + '/revoke', json={}))
    result = bob.http.post('/api/federation/prepare', json={'peer_id': alice.descriptor.server_id,
        'device_id': bob.card.device_id, 'target_actor_id': alice.uid, 'operation': 'read', 'payload': fresh})
    checked(result, 403)
    require('PRIVATE_SHARED_BODY' in checked(alice.http.get('/api/notes/' + payload['note_id']))['body'],
            'Revocation deleted or damaged the original document.')
    print('PASS: persistent replay and nonce rejection after restart; fresh authorized read still works.')
    print('PASS: viewer downgrade, stale grant, write denial, full grant revocation, cached-event revocation, and peer revocation; original notes preserved.')


if __name__ == '__main__':
    if sys.argv[1] == 'regression':
        config = json.loads((FIXTURES / 'bob/app.json').read_bytes())
        environment = os.environ | {'DRG_STORAGE_TEST_POSTGRES_URL': config['DATABASE_URL'],
                                     'DRG_TEST_DATABASE_URL': '', 'DATABASE_URL': '', 'FILE_STORAGE': 'local'}
        subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp=/tmp/pytest',
            'tests/test_federation_protocol.py', 'tests/test_federation_probe.py', 'tests/test_federation_security.py',
            'tests/test_federation_calls.py', 'tests/test_federation_upgrade.py', 'tests/test_federation_storage.py',
            'tests/test_federation_runtime.py',
            'tests/test_federation_revocations.py',
            'tests/test_federation_databases.py', 'tests/test_notion.py', 'tests/test_database_features.py',
            'tests/test_federation_optional.py',
            'tests/test_federation_isolation.py', 'tests/test_social.py', 'tests/test_task_lists.py',
            'tests/test_security.py::test_application_version_is_server_metadata_for_both_roles',
            'tests/test_storage_encryption.py::test_encrypted_storage_protects_content_media_and_checks_key_on_restart',
            'tests/test_task_storage.py::test_reminder_payload_encryption_and_full_restore_preserve_tree'],
            check=True, env=environment)
    else:
        {'scenario': scenario, 'attacks': attacks, 'offline': offline, 'restarted': restarted}[sys.argv[1]]()
