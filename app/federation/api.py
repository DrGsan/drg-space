"""User-facing enrollment, contact confirmation and local device-authorized RPC."""
import json
import re
import time
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from flask import request
from .actors import DeviceCard, seal_result, sign_result, verify_device
from .identity import FederationError, ServerDescriptor


def install_routes(service):
    from .prototype import Intent
    from .revocations import install as install_revocations
    install_revocations(service)
    router = service.router

    @router.get('/api/federation/contact-card')
    def contact_card():
        uid = service.local_user()
        with service.state.transaction() as connection:
            service.guard(connection)
            card = service.local_device(connection, uid, request.args.get('device_id', ''))
            with service.application(connection) as database:
                row = database.execute('SELECT login,settings FROM users WHERE id=?', (uid,)).fetchone()
            settings = json.loads(row['settings'])
            profile = {'login': row['login'], 'display_name': settings.get('profile',{}).get('display_name') or row['login']}
        if not service.transport_public:
            raise FederationError('transport_not_configured')
        return {'version': 1, 'server': service.identity.descriptor.model_dump(mode='json'),
                'transport': service.transport_public, 'actor': card.model_dump(), 'profile': profile}

    @router.post('/api/federation/contacts/import')
    def import_contact():
        uid = service.local_user()
        with service.state.transaction() as connection:
            service.guard(connection)
        data, _ = service.body()
        if set(data) - {'package', 'confirmed', 'expected_old_device_id'} or data.get('confirmed') is not True:
            raise FederationError('independent_confirmation_required')
        package = data.get('package')
        if (not isinstance(package, dict) or package.get('version') != 1 or
                not {'version','server','transport','actor'} <= set(package) or
                set(package) - {'version','server','transport','actor','profile'}):
            raise FederationError('invalid_contact_package')
        peer = ServerDescriptor.model_validate_json(json.dumps(package['server']))
        card = DeviceCard.model_validate(package['actor'])
        transport = package['transport']
        if card.server_id != peer.server_id or peer.server_id == service.identity.descriptor.server_id or not peer.result_public_key:
            raise FederationError('invalid_contact_package')
        certificate = transport.get('certificate_pem', '') if isinstance(transport, dict) else ''
        try:
            if not isinstance(certificate, str) or len(certificate) > 32768:
                raise ValueError()
            parsed = x509.load_pem_x509_certificate(certificate.encode())
            fingerprint = parsed.fingerprint(hashes.SHA256()).hex()
            if fingerprint != transport.get('fingerprint'):
                raise ValueError()
        except (ValueError, TypeError):
            raise FederationError('invalid_transport_certificate') from None
        profile = package.get('profile', {})
        if (not isinstance(profile, dict) or set(profile)-{'login','display_name'} or
                any(not isinstance(value, str) or len(value) > 200 for value in profile.values())):
            raise FederationError('invalid_contact_profile')
        with service.state.transaction() as connection:
            service.guard(connection)
            existing = connection.execute('SELECT * FROM peers WHERE id=?', (peer.server_id,)).fetchone()
        if not existing or not existing['active']:
            service.local_user(owner=True)
        with service.state.transaction() as connection:
            service.guard(connection)
            existing = connection.execute('SELECT * FROM peers WHERE id=?', (peer.server_id,)).fetchone()
            if existing and (existing['descriptor'] != peer.model_dump_json() or existing['tls_pin'] != fingerprint):
                raise FederationError('peer_pin_changed')
            connection.execute('INSERT INTO peers VALUES(?,?,?,1) ON CONFLICT(id) DO UPDATE SET active=1',
                               (peer.server_id, peer.model_dump_json(), fingerprint))
            connection.execute('INSERT INTO peer_certificates VALUES(?,?) ON CONFLICT(peer_id) DO UPDATE SET certificate=excluded.certificate',
                               (peer.server_id, certificate))
            old = connection.execute('SELECT * FROM contacts WHERE user_id=? AND peer_id=? AND actor_id=?',
                                     (uid, peer.server_id, card.actor_id)).fetchone()
            revoked = connection.execute('SELECT 1 FROM federation_device_revocations WHERE peer_id=? AND actor_id=? AND device_id=?',
                                         (peer.server_id,card.actor_id,card.device_id)).fetchone()
            if revoked: raise FederationError('device_revoked')
            if old and old['card'] != card.model_dump_json():
                previous_card = DeviceCard.model_validate_json(old['card'])
                known = connection.execute('SELECT card FROM federation_contact_devices WHERE user_id=? AND peer_id=? AND actor_id=? AND device_id=? AND active=1',
                    (uid,peer.server_id,card.actor_id,card.device_id)).fetchone()
                if (not known or known[0] != card.model_dump_json()) and data.get('expected_old_device_id') != previous_card.device_id:
                    error = FederationError('contact_device_changed')
                    error.status_code = 409
                    raise error
            status = old['status'] if old and old['status'] != 'blocked' else 'confirmed'
            invitation = old['invitation'] if status != 'confirmed' else ''
            connection.execute('INSERT INTO contacts VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,peer_id,actor_id) DO UPDATE SET card=excluded.card,status=excluded.status,invitation=excluded.invitation',
                               (uid, peer.server_id, card.actor_id, old['card'] if old and old['status']!='blocked' else card.model_dump_json(), status, invitation))
            connection.execute('INSERT INTO federation_contact_devices VALUES(?,?,?,?,?,1) ON CONFLICT(user_id,peer_id,actor_id,device_id) DO UPDATE SET active=1',
                               (uid,peer.server_id,card.actor_id,card.device_id,card.model_dump_json()))
            connection.execute('INSERT INTO profiles VALUES(?,?,?,?) ON CONFLICT(user_id,peer_id,actor_id) DO UPDATE SET profile=excluded.profile',
                               (uid, peer.server_id, card.actor_id, json.dumps(profile)))
        if service.on_enable:
            service.on_enable()
        return {'confirmed': card.device_id, 'peer_id': peer.server_id, 'actor_id': card.actor_id, 'status': status}

    @router.get('/api/federation/grants')
    def grants():
        uid = service.local_user()
        note_id = request.args.get('note_id', '')
        with service.state.transaction() as connection:
            with service.application(connection) as database:
                service.authorized_note(database, note_id, uid)
            return {'grants': [dict(row) for row in connection.execute(
                'SELECT peer_id,actor_id,role,revision,active FROM grants WHERE note_id=? AND owner_id=?', (note_id, uid))]}

    @router.post('/api/federation/local/prepare')
    def local_prepare():
        uid = service.local_user()
        data, _ = service.body()
        service.fields(data, ('device_id','operation','payload'))
        if not service.calls or not isinstance(data['operation'],str) or not data['operation'].startswith('call_'):
            raise FederationError('unsupported_local_operation')
        from .calls import validate_call_payload
        validate_call_payload(data['operation'], data['payload'])
        with service.state.transaction() as connection:
            service.guard(connection)
            card = service.local_device(connection, uid, data['device_id'])
            nonce = service.state.challenge(connection, 'local:' + uid, int(time.time()))
        intent = Intent(actor_server_id=service.identity.descriptor.server_id, actor_id=uid,
                        target_actor_id=uid, device_id=card.device_id, nonce=nonce, event_id=uuid4().hex,
                        operation=data['operation'], payload=data['payload'])
        return {'intent': intent.model_dump()}

    @router.post('/api/federation/local/send')
    def local_send():
        uid = service.local_user()
        data, _ = service.body()
        service.fields(data, ('intent','proof'))
        intent = Intent.model_validate(data['intent'])
        if not service.identity: raise FederationError('federation_disabled')
        if not service.calls or not intent.operation.startswith('call_'):
            raise FederationError('unsupported_local_operation')
        if intent.actor_server_id != service.identity.descriptor.server_id or intent.actor_id != uid or intent.target_actor_id != uid:
            raise FederationError('device_actor_mismatch')
        with service.state.transaction() as connection:
            service.guard(connection)
            card = service.local_device(connection, uid, intent.device_id)
            verify_device(card, service.identity.descriptor.server_id, intent.model_dump(), data['proof'], int(time.time()))
            service.state.use_challenge(connection, intent.nonce, 'local:' + uid, int(time.time()))
            result = service.calls.local_operation(connection, uid, card, intent.operation, intent.payload,
                int(time.time()), evidence={'intent': intent.model_dump(), 'proof': data['proof']})
            return sign_result(service.identity, card, intent.model_dump(),
                               seal_result(card, result, intent.model_dump()), int(time.time()))

    @router.post('/api/federation/devices/<device_id>/revoke')
    def revoke_device(device_id):
        uid = service.local_user()
        if not re.fullmatch(r'[a-f0-9]{64}', device_id):
            raise FederationError('invalid_device')
        with service.state.transaction() as connection:
            connection.execute('UPDATE devices SET active=0 WHERE user_id=? AND id=?', (uid, device_id))
            if service.calls: service.calls.revoke_device(connection, uid, device_id, int(time.time()))
            from .revocations import enqueue
            enqueue(connection,uid,device_id=device_id)
        return {'revoked': True}
