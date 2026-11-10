"""Device-authorized federation using the existing DrG permissions and stores."""
from app.i18n import message as localized_message
import base64
import hashlib
import json
import re
import secrets
import time
from contextlib import contextmanager
from functools import wraps
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from flask import Response, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.http import Request, Router
from .actors import DeviceCard, canonical, seal_result, verify_device, sign_result
from .identity import FederationError, ServerDescriptor
from .protocol import (DISCOVERY_PATH, MAX_BODY_BYTES, sign_response, verify_request)
from .state import PrototypeState
from .transport import PeerTransport

CHALLENGE_PATH = '/_drg/federation/v1/challenge'
OPERATION_PATH = '/_drg/federation/v1/operation'


class PrototypeRouter(Router):
    def route(self, rule, **options):
        def decorate(function):
            @wraps(function)
            def guarded(**values):
                try:
                    return function(**values)
                except FederationError as error:
                    code = str(error)
                    status = getattr(error, 'status_code', 409 if code == 'replayed_request' else 503 if 'unavailable' in code else 403)
                    return Response(canonical({'error': code}), status=status, content_type='application/json')
                except ValidationError:
                    return Response(canonical({'error': 'invalid_fields'}), status=422, content_type='application/json')
            return super(PrototypeRouter, self).route(rule, **options)(guarded)
        return decorate


class Intent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    actor_server_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    actor_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    device_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    target_actor_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    nonce: str = Field(pattern=r'^[a-f0-9]{64}$')
    event_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    operation: str
    payload: dict


class FederationPrototype:
    def __init__(self, main, *, tls_context=None, private_origins=None, state=None, identity_origin=None):
        self.m = main
        self.state = state if state is not None else PrototypeState(main.DATA, main.data_cipher)
        origin = identity_origin if identity_origin is not None else main.PUBLIC_URL
        if not main.data_cipher.enabled: origin='http://disabled.invalid'
        self.identity = self.state.identity(origin) if origin.startswith('https://') else None
        self.transport = PeerTransport(self.identity, tls_context=tls_context, private_origins=private_origins) if self.identity else None
        self.on_enable = None
        self.transport_public = None
        self.calls = None
        self.router = PrototypeRouter()
        self._routes()
        if getattr(self.state, 'canonical', False):
            from .api import install_routes
            install_routes(self)

    def enabled(self, connection):
        row = connection.execute("SELECT value FROM metadata WHERE key='enabled'").fetchone()
        return bool(row and row[0] == 'true')

    def guard(self, connection):
        if not self.identity or not self.enabled(connection):
            raise FederationError('federation_disabled')

    @contextmanager
    def application(self, connection=None):
        if connection is not None and getattr(self.state, 'canonical', False):
            yield connection.application_connection
        else:
            with self.m.db() as database:
                yield database

    def local_user(self, owner=False):
        wrapped = Request(request)
        session = self.m.admin_access.proof(wrapped) if owner else self.m.ready(wrapped)
        if owner and session['role'] != 'owner':
            self.m.fail(403, localized_message('onlyTheSiteOwnerCanAccessThis'))
        self.m.protection.rate(wrapped, 'federation-local', 1500, subject=session['user_id'])
        return session['user_id']

    @staticmethod
    def body():
        if request.mimetype != 'application/json' or request.query_string or request.headers.get('Content-Encoding'):
            raise FederationError('invalid_request')
        data = request.stream.read(MAX_BODY_BYTES + 1)
        if len(data) > MAX_BODY_BYTES:
            raise FederationError('body_too_large')
        try:
            def invalid_constant(value): raise ValueError()
            value = json.loads(data,parse_constant=invalid_constant)
        except (ValueError, UnicodeError):
            raise FederationError('invalid_json') from None
        if not isinstance(value, dict):
            raise FederationError('invalid_json')
        return value, data

    @staticmethod
    def fields(value, expected):
        if set(value) != set(expected):
            raise FederationError('invalid_fields')

    def peer(self, connection, peer_id):
        self.guard(connection)
        row = connection.execute('SELECT * FROM peers WHERE id=? AND active=1', (peer_id,)).fetchone()
        if not row:
            raise FederationError('untrusted_server')
        return ServerDescriptor.model_validate_json(row['descriptor'])

    def incoming(self, body):
        with self.state.transaction() as connection:
            peer_id = request.headers.get('DrG-Server-Id', '')
            peer = self.peer(connection, peer_id)
            pin = connection.execute('SELECT tls_pin FROM peers WHERE id=?', (peer_id,)).fetchone()[0]
        # This value is populated by the TLS listener, never by HTTP headers.
        pem = request.environ.get('SSL_CLIENT_CERT')
        try:
            fingerprint = x509.load_pem_x509_certificate(pem.encode()).fingerprint(hashes.SHA256()).hex()
        except (ValueError, AttributeError):
            raise FederationError('client_certificate_required') from None
        if not secrets.compare_digest(pin, fingerprint):
            raise FederationError('client_certificate_mismatch')
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('DrG-Federation '):
            raise FederationError('server_proof_required')
        verified = verify_request(authorization[15:], self.identity.descriptor, peer, request.method,
                                  request.path, body, now=int(time.time()), replay=self.state)
        return peer, verified

    def signed_result(self, peer, verified, value):
        body = canonical(value)
        if len(body) > MAX_BODY_BYTES:
            raise FederationError('result_too_large')
        proof = sign_response(self.identity, peer, 'POST', request.path, body, now=int(time.time()),
                              request_id=verified.request_id, status=200)
        return Response(body, content_type='application/json', headers={'DrG-Federation-Proof': proof})

    def contact(self, connection, uid, peer_id, actor_id, accepted=False):
        row = connection.execute('SELECT * FROM contacts WHERE user_id=? AND peer_id=? AND actor_id=?',
                                  (uid, peer_id, actor_id)).fetchone()
        if not row or row['status'] == 'blocked' or accepted and row['status'] != 'accepted':
            raise FederationError('contact_not_confirmed')
        return row

    def confirmed_card(self, connection, uid, peer_id, actor_id, device_id, *, accepted=True):
        contact = self.contact(connection, uid, peer_id, actor_id, accepted=accepted)
        if getattr(self.state, 'canonical', False):
            if connection.execute('SELECT 1 FROM federation_device_revocations WHERE peer_id=? AND actor_id=? AND device_id=?',
                                  (peer_id,actor_id,device_id)).fetchone():
                raise FederationError('device_revoked')
            row = connection.execute('SELECT card FROM federation_contact_devices WHERE user_id=? AND peer_id=? AND actor_id=? AND device_id=? AND active=1',
                                     (uid,peer_id,actor_id,device_id)).fetchone()
            if row: return DeviceCard.model_validate_json(row[0])
            raise FederationError('contact_device_changed')
        card = DeviceCard.model_validate_json(contact['card'])
        if card.device_id != device_id: raise FederationError('contact_device_changed')
        return card

    def _routes(self):
        router = self.router

        @router.get(DISCOVERY_PATH)
        def discovery():
            with self.state.transaction() as connection:
                if not self.identity or not self.enabled(connection):
                    return Response(canonical({'error': 'federation_disabled'}), status=404, content_type='application/json')
            return self.identity.descriptor.model_dump(mode='json')

        @router.get('/api/federation/info')
        def info():
            uid = self.local_user()
            with self.state.transaction() as connection:
                devices = [json.loads(row['card']) | {'active': bool(row['active'])} for row in
                           connection.execute('SELECT card,active FROM devices WHERE user_id=?', (uid,))]
                peers=[]
                with self.application(connection) as database:
                    user=database.execute('SELECT role FROM users WHERE id=?',(uid,)).fetchone()
                if user and user['role']=='owner':
                    for row in connection.execute('SELECT id,descriptor,active FROM peers'):
                        peer=ServerDescriptor.model_validate_json(row['descriptor'])
                        peers.append({'server_id':peer.server_id,'origin':peer.origin,'key_id':peer.key_id,'active':bool(row['active'])})
                return {'enabled': bool(self.identity) and self.enabled(connection), 'configured': bool(self.identity),
                        'descriptor': self.identity.descriptor.model_dump(mode='json') if self.identity else None,
                        'devices': devices, 'transport': self.transport_public, 'peers':peers}

        @router.post('/api/admin/federation/enable')
        def enable():
            self.local_user(owner=True)
            data, _ = self.body()
            self.fields(data, ('enabled',))
            if type(data['enabled']) is not bool:
                raise FederationError('invalid_fields')
            if data['enabled'] and (not self.identity or not self.m.data_cipher.enabled):
                raise FederationError('configure_https_and_encryption_first')
            with self.state.transaction() as connection:
                connection.execute("INSERT INTO metadata VALUES('enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                   ('true' if data['enabled'] else 'false',))
                if data['enabled']:
                    connection.execute("INSERT INTO metadata VALUES('ever_enabled','true') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
                if not data['enabled'] and self.calls:
                    for row in connection.execute('SELECT id FROM peers').fetchall():
                        self.calls.revoke_peer(connection,row[0],int(time.time()))
            if self.on_enable:
                self.on_enable()
            return {'enabled': data['enabled']}

        @router.post('/api/admin/federation/peers')
        def trust():
            self.local_user(owner=True)
            if not self.identity: raise FederationError('federation_disabled')
            data, _ = self.body()
            self.fields(data, ('descriptor', 'tls_pin'))
            peer = ServerDescriptor.model_validate_json(json.dumps(data['descriptor']))
            if peer.server_id == self.identity.descriptor.server_id or not isinstance(data['tls_pin'], str) or not re.fullmatch(r'[a-f0-9]{64}', data['tls_pin']):
                raise FederationError('invalid_peer')
            with self.state.transaction() as connection:
                self.guard(connection)
                existing = connection.execute('SELECT * FROM peers WHERE id=?', (peer.server_id,)).fetchone()
                if existing and (existing['descriptor'] != peer.model_dump_json() or existing['tls_pin'] != data['tls_pin']):
                    raise FederationError('peer_pin_changed')
                connection.execute('INSERT INTO peers VALUES(?,?,?,1) ON CONFLICT(id) DO UPDATE SET active=1',
                                   (peer.server_id, peer.model_dump_json(), data['tls_pin']))
            return {'trusted': peer.server_id}

        @router.post('/api/admin/federation/peers/<peer_id>/revoke')
        def revoke_peer(peer_id):
            self.local_user(owner=True)
            with self.state.transaction() as connection:
                if getattr(self.state,'canonical',False):
                    for contact in connection.execute('SELECT user_id,actor_id FROM contacts WHERE peer_id=?',(peer_id,)).fetchall():
                        connection.execute('INSERT INTO federation_revocations VALUES(?,?,?,?,?) ON CONFLICT(peer_id,actor_id,device_id,target_actor_id) DO NOTHING',
                                           (peer_id,contact['user_id'],'',contact['actor_id'],int(time.time())))
                connection.execute('UPDATE peers SET active=0 WHERE id=?', (peer_id,))
                connection.execute('UPDATE grants SET active=0,revision=revision+1 WHERE peer_id=?', (peer_id,))
                connection.execute("UPDATE contacts SET status='blocked' WHERE peer_id=?", (peer_id,))
                if self.calls: self.calls.revoke_peer(connection, peer_id, int(time.time()))
            return {'revoked': True}

        @router.post('/api/federation/devices/challenge')
        def enrollment_challenge():
            uid = self.local_user()
            with self.state.transaction() as connection:
                self.guard(connection)
                return {'nonce': self.state.challenge(connection, 'enroll:' + uid, int(time.time()))}

        @router.post('/api/federation/devices')
        def enroll():
            uid = self.local_user()
            if not self.identity: raise FederationError('federation_disabled')
            data, _ = self.body()
            self.fields(data, ('card', 'nonce', 'proof'))
            card = DeviceCard.model_validate(data['card'])
            if card.actor_id != uid or card.server_id != self.identity.descriptor.server_id:
                raise FederationError('device_actor_mismatch')
            intent = {'operation': 'enroll', 'card': card.model_dump(), 'nonce': data['nonce']}
            verify_device(card, card.server_id, intent, data['proof'], int(time.time()))
            with self.state.transaction() as connection:
                self.guard(connection)
                self.state.use_challenge(connection, data['nonce'], 'enroll:' + uid, int(time.time()))
                count = connection.execute('SELECT COUNT(*) FROM devices WHERE user_id=? AND active=1', (uid,)).fetchone()[0]
                if connection.execute('SELECT 1 FROM devices WHERE user_id=? AND id=? AND active=0', (uid, card.device_id)).fetchone():
                    raise FederationError('device_revoked')
                existing = connection.execute('SELECT card FROM devices WHERE user_id=? AND id=? AND active=1', (uid, card.device_id)).fetchone()
                if existing:
                    if existing[0] != card.model_dump_json(): raise FederationError('device_key_changed')
                    return {'card': card.model_dump()}
                if count >= 8:
                    raise FederationError('device_limit')
                connection.execute('INSERT INTO devices VALUES(?,?,?,1)', (uid, card.device_id, card.model_dump_json()))
            return {'card': card.model_dump()}

        @router.post('/api/federation/contacts')
        def confirm_contact():
            uid = self.local_user()
            data, _ = self.body()
            self.fields(data, ('card',))
            card = DeviceCard.model_validate(data['card'])
            with self.state.transaction() as connection:
                self.peer(connection, card.server_id)
                old = connection.execute('SELECT * FROM contacts WHERE user_id=? AND peer_id=? AND actor_id=?',
                                          (uid, card.server_id, card.actor_id)).fetchone()
                if old and old['card'] != card.model_dump_json():
                    raise FederationError('contact_device_changed')
                if not old:
                    connection.execute("INSERT INTO contacts VALUES(?,?,?,?,'confirmed','')",
                                       (uid, card.server_id, card.actor_id, card.model_dump_json()))
                if getattr(self.state,'canonical',False):
                    if connection.execute('SELECT 1 FROM federation_device_revocations WHERE peer_id=? AND actor_id=? AND device_id=?',
                                          (card.server_id,card.actor_id,card.device_id)).fetchone():
                        raise FederationError('device_revoked')
                    connection.execute('INSERT INTO federation_contact_devices VALUES(?,?,?,?,?,1) ON CONFLICT(user_id,peer_id,actor_id,device_id) DO NOTHING',
                                       (uid,card.server_id,card.actor_id,card.device_id,card.model_dump_json()))
            return {'confirmed': card.device_id}

        @router.get('/api/federation/contacts')
        def contacts():
            uid = self.local_user()
            with self.state.transaction() as connection:
                result = []
                for row in connection.execute('SELECT * FROM contacts WHERE user_id=?', (uid,)).fetchall():
                    item = dict(row); item.pop('user_id',None)
                    peer = connection.execute('SELECT descriptor FROM peers WHERE id=?', (row['peer_id'],)).fetchone()
                    descriptor = ServerDescriptor.model_validate_json(peer[0])
                    item.update(card=json.loads(row['card']), origin=descriptor.origin, server=descriptor.model_dump(mode='json'))
                    if getattr(self.state,'canonical',False):
                        profile = connection.execute('SELECT profile FROM profiles WHERE user_id=? AND peer_id=? AND actor_id=?',
                                                     (uid,row['peer_id'],row['actor_id'])).fetchone()
                        item.update(json.loads(profile[0]) if profile else {})
                        item['devices'] = [json.loads(device['card']) | {'active':bool(device['active'])} for device in
                            connection.execute('SELECT card,active FROM federation_contact_devices WHERE user_id=? AND peer_id=? AND actor_id=?',
                                               (uid,row['peer_id'],row['actor_id']))]
                    result.append(item)
                return {'contacts': result}

        @router.post('/api/federation/contacts/<peer_id>/<actor_id>/revoke')
        def revoke_contact(peer_id, actor_id):
            uid = self.local_user()
            with self.state.transaction() as connection:
                connection.execute("UPDATE contacts SET status='blocked' WHERE user_id=? AND peer_id=? AND actor_id=?",
                                   (uid, peer_id, actor_id))
                connection.execute('UPDATE grants SET active=0,revision=revision+1 WHERE owner_id=? AND peer_id=? AND actor_id=?',
                                   (uid, peer_id, actor_id))
                if self.calls: self.calls.revoke_contact(connection, uid, peer_id, actor_id, int(time.time()))
                if getattr(self.state,'canonical',False):
                    # The remote server must stop accepting proofs from a lost
                    # home-server credential even when it bypasses this relay.
                    connection.execute('INSERT INTO federation_revocations VALUES(?,?,?,?,?) ON CONFLICT(peer_id,actor_id,device_id,target_actor_id) DO NOTHING',
                                       (peer_id,uid,'',actor_id,int(time.time())))
            return {'revoked': True}

        @router.post(CHALLENGE_PATH)
        def challenge():
            data, raw = self.body()
            self.fields(data, ())
            peer, verified = self.incoming(raw)
            with self.state.transaction() as connection:
                self.peer(connection, peer.server_id)
                nonce = self.state.challenge(connection, peer.server_id, int(time.time()))
            return self.signed_result(peer, verified, {'nonce': nonce})

        @router.post('/api/federation/prepare')
        def prepare():
            uid = self.local_user()
            data, _ = self.body()
            self.fields(data, ('peer_id', 'device_id', 'target_actor_id', 'operation', 'payload'))
            with self.state.transaction() as connection:
                peer = self.peer(connection, data['peer_id'])
                self.local_device(connection, uid, data['device_id'])
                self.contact(connection, uid, peer.server_id, data['target_actor_id'])
            nonce = self.transport.post(peer, CHALLENGE_PATH, {}, int(time.time()))['nonce']
            intent = Intent(actor_server_id=self.identity.descriptor.server_id, actor_id=uid,
                            device_id=data['device_id'], target_actor_id=data['target_actor_id'], nonce=nonce,
                            event_id=uuid4().hex, operation=data['operation'], payload=data['payload'])
            self.payload(intent)
            return {'intent': intent.model_dump()}

        @router.post('/api/federation/send')
        def relay():
            uid = self.local_user()
            data, _ = self.body()
            self.fields(data, ('peer_id', 'intent', 'proof'))
            intent = Intent.model_validate(data['intent'])
            self.payload(intent)
            if intent.actor_id != uid or intent.actor_server_id != self.identity.descriptor.server_id:
                raise FederationError('device_actor_mismatch')
            with self.state.transaction() as connection:
                peer = self.peer(connection, data['peer_id'])
                card = self.local_device(connection, uid, intent.device_id)
                contact = self.contact(connection, uid, peer.server_id, intent.target_actor_id)
                verify_device(card, peer.server_id, intent.model_dump(), data['proof'], int(time.time()))
                if intent.operation == 'invite' and contact['status'] not in ('confirmed', 'outgoing'):
                    raise FederationError('invitation_state')
                if intent.operation == 'accept' and (contact['status'] not in ('incoming', 'accepted') or contact['invitation'] != intent.payload['invitation']):
                    raise FederationError('invitation_state')
                if intent.operation not in ('invite','accept') and contact['status'] != 'accepted':
                    raise FederationError('friendship_required')
                if intent.operation == 'call_invite' and self.calls:
                    self.calls.reserve_outgoing(connection,uid,card,peer,intent.target_actor_id,intent.payload,int(time.time()))
            try:
                result = self.transport.post(peer, OPERATION_PATH, {'intent': intent.model_dump(), 'proof': data['proof']}, int(time.time()))
            except FederationError as error:
                if intent.operation == 'call_invite' and self.calls:
                    with self.state.transaction() as connection:
                        self.calls.outgoing_failed(connection,uid,card,intent.payload['id'],int(time.time()),
                                                   definitive=getattr(error,'status_code',503) in (400,401,403,404,409,410,422))
                raise
            if intent.operation in ('invite', 'accept'):
                with self.state.transaction() as connection:
                    # Never revive a contact revoked while the network was in flight.
                    contact = self.contact(connection, uid, peer.server_id, intent.target_actor_id)
                    status = 'outgoing' if intent.operation == 'invite' else 'accepted'
                    invitation = intent.event_id if intent.operation == 'invite' else intent.payload['invitation']
                    connection.execute('UPDATE contacts SET status=?,invitation=? WHERE user_id=? AND peer_id=? AND actor_id=?',
                                       (status, invitation, uid, peer.server_id, intent.target_actor_id))
            return result

        @router.post(OPERATION_PATH)
        def operation():
            data, raw = self.body()
            self.fields(data, ('intent', 'proof'))
            peer, verified = self.incoming(raw)
            intent = Intent.model_validate(data['intent'])
            self.payload(intent)
            if intent.actor_server_id != peer.server_id:
                raise FederationError('device_actor_mismatch')
            broadcast = None
            with self.state.transaction() as connection:
                self.peer(connection, peer.server_id)
                contact = self.contact(connection, intent.target_actor_id, peer.server_id, intent.actor_id,
                                       accepted=intent.operation not in ('invite','accept'))
                if intent.operation not in ('invite','accept'):
                    card = self.confirmed_card(connection,intent.target_actor_id,peer.server_id,intent.actor_id,intent.device_id)
                else:
                    card = self.confirmed_card(connection,intent.target_actor_id,peer.server_id,intent.actor_id,intent.device_id,accepted=False)
                verify_device(card, self.identity.descriptor.server_id, intent.model_dump(), data['proof'], int(time.time()))
                self.state.use_challenge(connection, intent.nonce, peer.server_id, int(time.time()))
                # Resource authorization is checked again even for a known event.
                if getattr(self.state,'canonical',False) and intent.operation.startswith('tracker_'):
                    from .tracker import authorize
                    authorize(self,connection,peer,intent)
                elif getattr(self.state,'canonical',False) and intent.operation.startswith('database_'):
                    from .databases import authorize
                    authorize(self,connection,peer,intent)
                elif getattr(self.state,'canonical',False) and intent.operation not in ('invite','accept','catalog') and not intent.operation.startswith('call_'):
                    from .notes import effective_grant
                    note_id = intent.payload.get('parent_id') if intent.operation=='create' else intent.payload.get('note_id')
                    grant = effective_grant(self,connection,note_id,peer.server_id,intent.actor_id,intent.device_id,
                                            write=intent.operation not in ('read','asset_read'))
                    if grant['owner_id']!=intent.target_actor_id or intent.operation!='create' and grant['revision']!=intent.payload['revision']:
                        raise FederationError('resource_access_denied')
                    with self.application(connection) as database:self.authorized_note(database,note_id,grant['owner_id'])
                if intent.operation in ('read', 'update') and not getattr(self.state,'canonical',False):
                    grant = self.grant(connection, intent, peer.server_id)
                    with self.application(connection) as database:
                        self.authorized_note(database, grant['note_id'], grant['owner_id'])
                digest = hashlib.sha256(canonical(intent.model_dump(exclude={'nonce'}))).hexdigest()
                from .tracker import READS as TRACKER_READS
                cache = not intent.operation.startswith('call_') and intent.operation not in ('catalog','read','asset_read','database_catalog','database_read','database_formula_preview') and intent.operation not in TRACKER_READS
                if getattr(self.state,'canonical',False):
                    connection.execute('DELETE FROM events WHERE created>0 AND created<?', (int(time.time())-90*86400,))
                previous = connection.execute('SELECT * FROM events WHERE peer_id=? AND id=?', (peer.server_id, intent.event_id)).fetchone() if cache else None
                if previous:
                    if previous['digest'] != digest:
                        raise FederationError('event_conflict')
                    result = json.loads(previous['result'])
                    if getattr(self.state,'canonical',False) and intent.operation == 'update':
                        from .notes import execute
                        read = intent.model_copy(update={'operation':'read','payload':{'note_id':intent.payload['note_id'],'revision':intent.payload['revision']}})
                        result,_ = execute(self,connection,peer,read)
                    if intent.operation.startswith('database_'):
                        from .databases import replay
                        result = replay(self,connection,peer,intent,result)
                else:
                    if cache and connection.execute('SELECT COUNT(*) FROM events').fetchone()[0] >= (100000 if getattr(self.state,'canonical',False) else 1024):
                        raise FederationError('event_capacity')
                    if intent.operation in ('invite', 'accept'):
                        result = self.friendship(connection, contact, intent, peer.server_id)
                    elif intent.operation.startswith('call_'):
                        if not self.calls: raise FederationError('calls_unavailable')
                        result = self.calls.remote_operation(connection,peer,intent,int(time.time()),
                            evidence={'intent':intent.model_dump(),'proof':data['proof']})
                    elif getattr(self.state,'canonical',False):
                        if intent.operation.startswith('tracker_'):
                            from .tracker import execute
                        elif intent.operation.startswith('database_'):
                            from .databases import execute
                        else:
                            from .notes import execute
                        result,broadcast = execute(self,connection,peer,intent)
                    else:
                        result, broadcast = self.note_operation(grant, intent, connection)
                    # Bound before committing an application update or event.
                    if len(canonical(result)) > MAX_BODY_BYTES*3//4-4096:
                        raise FederationError('result_too_large')
                    if cache:
                        stored = {'note_id':result.get('note_id'),'applied':True} if intent.operation == 'update' else result
                        if intent.operation.startswith('database_'):
                            stored = {key:value for key,value in result.items() if key in ('id','ok')}
                        if getattr(self.state,'canonical',False):
                            connection.execute('INSERT INTO events(peer_id,id,digest,result,created) VALUES(?,?,?,?,?)',
                                (peer.server_id,intent.event_id,digest,json.dumps(stored),int(time.time())))
                        else: connection.execute('INSERT INTO events VALUES(?,?,?,?)',
                            (peer.server_id,intent.event_id,digest,json.dumps(stored)))
                encrypted = sign_result(self.identity, card, intent.model_dump(),
                                        seal_result(card, result, intent.model_dump()), int(time.time()))
            if broadcast:
                for event in broadcast if isinstance(broadcast,list) else (broadcast,):
                    self.m.app.runtime.run(self.m.realtime_router.publication_update(*event))
            return self.signed_result(peer, verified, encrypted)

        @router.post('/api/federation/grants')
        def create_grant():
            uid = self.local_user()
            data, _ = self.body()
            self.fields(data, ('note_id', 'peer_id', 'actor_id', 'role'))
            if data['role'] not in ('viewer', 'editor', 'none'):
                raise FederationError('invalid_role')
            with self.state.transaction() as connection:
                self.peer(connection, data['peer_id'])
                contact = self.contact(connection, uid, data['peer_id'], data['actor_id'], accepted=True)
                card = DeviceCard.model_validate_json(contact['card'])
                with self.application(connection) as database:
                    page = self.authorized_note(database, data['note_id'], uid)
                    from .notes import ensure_document
                    if not database.execute('SELECT 1 FROM note_databases WHERE note_id=?',(page['id'],)).fetchone() and not database.execute('SELECT 1 FROM database_records WHERE note_id=?',(page['id'],)).fetchone():
                        ensure_document(database,page,self.m.fail,int(time.time()))
                old = connection.execute('SELECT revision FROM grants WHERE note_id=? AND peer_id=? AND actor_id=?',
                                          (data['note_id'], data['peer_id'], data['actor_id'])).fetchone()
                revision = old[0] + 1 if old else 1
                connection.execute('INSERT INTO grants VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(note_id,peer_id,actor_id) DO UPDATE SET role=excluded.role,revision=excluded.revision,active=excluded.active,device_id=excluded.device_id',
                                   (data['note_id'], uid, data['peer_id'], data['actor_id'], card.device_id,
                                    data['role'], revision, int(data['role'] != 'none')))
            return {'note_id': data['note_id'], 'revision': revision, 'role': data['role'], 'federation_shared':True}

    def local_device(self, connection, uid, device_id):
        row = connection.execute('SELECT card FROM devices WHERE user_id=? AND id=? AND active=1', (uid, device_id)).fetchone()
        if not row:
            raise FederationError('unknown_device')
        return DeviceCard.model_validate_json(row[0])

    def payload(self, intent):
        if intent.operation.startswith('tracker_'):
            if not getattr(self.state,'canonical',False): raise FederationError('tracker_unavailable')
            from .tracker import validate_payload
            validate_payload(intent.operation,intent.payload)
            return
        if intent.operation.startswith('database_'):
            from .databases import validate_payload
            validate_payload(intent.operation,intent.payload)
            return
        if intent.operation.startswith('call_'):
            from .calls import validate_call_payload
            validate_call_payload(intent.operation,intent.payload)
            return
        fields = {'invite': (), 'accept': ('invitation',), 'read': ('note_id', 'revision'),
                  'update': ('note_id', 'revision', 'epoch', 'update'), 'catalog':(),
                  'create':('parent_id','title','blocks'),'delete':('note_id','revision'),
                  'asset_read':('note_id','revision','asset_id','offset','length'),
                  'asset_upload':('note_id','revision','name','mime','body'),
                  'asset_upload_start':('note_id','revision','name','mime','size'),
                  'asset_upload_chunk':('note_id','revision','upload_id','offset','body'),
                  'asset_upload_finish':('note_id','revision','upload_id')}
        if intent.operation not in fields:
            raise FederationError('unsupported_operation')
        expected=set(fields[intent.operation])
        if intent.operation == 'asset_read':
            if not set(intent.payload)<=(expected) or not {'note_id','revision','asset_id'}<=set(intent.payload): raise FederationError('invalid_fields')
        else: self.fields(intent.payload, expected)
        if 'note_id' in intent.payload or intent.operation == 'create':
            note_id = intent.payload.get('parent_id') if intent.operation == 'create' else intent.payload['note_id']
            if not isinstance(note_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16}',note_id):
                raise FederationError('invalid_resource')
        if 'revision' in intent.payload:
            if (not isinstance(intent.payload['note_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{16}', intent.payload['note_id'])
                    or type(intent.payload['revision']) is not int or intent.payload['revision'] < 1):
                raise FederationError('invalid_resource')
        if intent.operation == 'create' and (not isinstance(intent.payload['title'],str) or not 1<=len(intent.payload['title'])<=200 or not isinstance(intent.payload['blocks'],list)):
            raise FederationError('invalid_document')
        if 'asset_id' in intent.payload and (not isinstance(intent.payload['asset_id'],str) or not re.fullmatch(r'(?:[A-Za-z0-9_-]{16}|[a-f0-9]{32})',intent.payload['asset_id'])):
            raise FederationError('invalid_asset')
        if 'upload_id' in intent.payload and (not isinstance(intent.payload['upload_id'],str) or not re.fullmatch(r'[a-f0-9]{32}',intent.payload['upload_id'])):
            raise FederationError('invalid_upload')
        for name in ('name','mime','body','epoch','update'):
            if name in intent.payload and not isinstance(intent.payload[name],str): raise FederationError('invalid_fields')
        if intent.operation == 'accept' and (not isinstance(intent.payload['invitation'], str) or not re.fullmatch(r'[a-f0-9]{32}', intent.payload['invitation'])):
            raise FederationError('invalid_invitation')

    def friendship(self, connection, contact, intent, peer_id):
        with self.application(connection) as database:
            if not database.execute('SELECT 1 FROM users WHERE id=?', (intent.target_actor_id,)).fetchone():
                raise FederationError('unknown_recipient')
        if intent.operation == 'invite':
            if contact['status'] not in ('confirmed', 'incoming') or contact['invitation'] not in ('', intent.event_id):
                raise FederationError('invitation_state')
            status, invitation = 'incoming', intent.event_id
        else:
            if contact['status'] not in ('outgoing', 'accepted') or contact['invitation'] != intent.payload['invitation']:
                raise FederationError('invitation_state')
            status, invitation = 'accepted', contact['invitation']
        connection.execute('UPDATE contacts SET status=?,invitation=? WHERE user_id=? AND peer_id=? AND actor_id=?',
                           (status, invitation, intent.target_actor_id, peer_id, intent.actor_id))
        return {'status': status, 'invitation': invitation}

    def grant(self, connection, intent, peer_id):
        row = connection.execute('SELECT * FROM grants WHERE note_id=? AND peer_id=? AND actor_id=? AND active=1',
                                  (intent.payload['note_id'], peer_id, intent.actor_id)).fetchone()
        if (not row or row['owner_id'] != intent.target_actor_id or row['device_id'] != intent.device_id
                or row['revision'] != intent.payload['revision'] or intent.operation == 'update' and row['role'] != 'editor'):
            raise FederationError('resource_access_denied')
        return row

    def authorized_note(self, database, note_id, owner_id):
        from app.note_access import can_view, access_index
        from app.modules.notion import can_manage
        from app.account_lifecycle import pending
        page = database.execute('SELECT * FROM notes WHERE id=?', (note_id,)).fetchone()
        if (not page or page['deleted'] or page['locked'] or not can_manage(database,page,owner_id)
                or not can_view(database, page, owner_id) or access_index(database).role(page, owner_id) != 'editor'
                or not database.execute('SELECT 1 FROM users WHERE id=?', (owner_id,)).fetchone()):
            raise FederationError('resource_access_denied')
        if pending(database,owner_id) or pending(database,page['user_id']):
            raise FederationError('resource_access_denied')
        return page

    def note_operation(self, grant, intent, connection=None):
        from app.collaboration import MAX_STATE, load_doc, project
        with self.application(connection) as database:
            if not (connection is not None and getattr(self.state, 'canonical', False)):
                database.execute('BEGIN IMMEDIATE')
            page = self.authorized_note(database, grant['note_id'], grant['owner_id'])
            saved = database.execute('SELECT * FROM collaborative_docs WHERE note_id=?', (page['id'],)).fetchone()
            if not saved:
                raise FederationError('document_unavailable')
            doc = load_doc(saved['state'])
            broadcast = None
            if intent.operation == 'update':
                if intent.payload['epoch'] != saved['epoch']:
                    raise FederationError('document_epoch_changed')
                try:
                    update = base64.b64decode(intent.payload['update'], validate=True)
                    if len(update) > 8192:
                        raise ValueError()
                    doc.apply_update(update)
                except (ValueError, TypeError, KeyError, RuntimeError):
                    raise FederationError('invalid_document_update') from None
            title, body, blocks = project(doc, self.m.fail)
            def text_only(items):
                allowed = {'paragraph', 'heading', 'bulletListItem', 'numberedListItem', 'checkListItem', 'quote', 'codeBlock', 'divider'}
                return all(block['type'] in allowed and text_only(block.get('children', [])) for block in items)
            state = doc.get_update()
            if not text_only(blocks):
                raise FederationError('prototype_text_notes_only')
            if len(state) > min(MAX_STATE, 4096):
                raise FederationError('prototype_document_limit')
            result = {'note_id': page['id'], 'title': title, 'body': body, 'epoch': saved['epoch'],
                      'state': base64.b64encode(state).decode(), 'revision': grant['revision']}
            if len(canonical(result)) > 9000:
                raise FederationError('result_too_large')
            if intent.operation == 'update':
                from app.note_history import snapshot
                snapshot(database, page, int(time.time()))
                database.execute('UPDATE collaborative_docs SET state=?,updated=? WHERE note_id=?', (state, int(time.time()), page['id']))
                database.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',
                                 (title, body, json.dumps(blocks, ensure_ascii=False), int(time.time()), page['id']))
                broadcast = (page['id'], state, update, saved['epoch'], page['version'] + 1, False)
            return result, broadcast
