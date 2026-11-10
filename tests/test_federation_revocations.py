"""Revocation delivery stays fair and permanent device bans survive restores."""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.http import Application
from app.federation.actors import DeviceKeys
from app.federation.archive import reset_after_restore
from app.federation.identity import FederationError, SigningIdentity
from app.federation.revocations import REVOCATION_PATH, deliver
from app.federation.storage import FederationState
from tests.test_federation_storage import ACTOR, UID, backup_service, configured, databases, seed


def contact_package(service, peer, card):
    """A disposable, independently confirmed public card with a real TLS pin."""
    private = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'other.example')])
    stamp = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(private.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(stamp - timedelta(minutes=1)).not_valid_after(stamp + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('other.example')]), critical=False)
        .sign(private, hashes.SHA256()))
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
    with service.state.transaction() as connection:
        connection.execute('UPDATE peers SET tls_pin=? WHERE id=?', (fingerprint, peer.server_id))
    return {'version': 1, 'server': peer.model_dump(mode='json'), 'actor': card.model_dump(),
            'transport': {'certificate_pem': certificate.public_bytes(serialization.Encoding.PEM).decode(),
                          'fingerprint': fingerprint}, 'profile': {'display_name': 'Remote friend'}}


def local_client(service, peer):
    service.local_user = lambda **kwargs: UID
    service.incoming = lambda body: (peer, None)
    service.signed_result = lambda peer, verified, value: value
    application = Application(__name__)
    application.include_router(service.router)
    return application.test_client()


def enable_after_restore(service):
    with service.state.transaction() as connection:
        connection.execute("UPDATE metadata SET value='true' WHERE key='enabled'")


@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_eight_old_failed_notices_do_not_starve_a_healthy_revocation(databases, engine):
    service, peer, _, _ = configured(databases(engine))
    healthy_peer = peer.server_id
    offline = [SigningIdentity.generate(f'https://offline-{index}.example').descriptor for index in range(8)]
    with service.state.transaction() as connection:
        for index, descriptor in enumerate(offline):
            connection.execute('INSERT INTO peers VALUES(?,?,?,1)',
                               (descriptor.server_id, descriptor.model_dump_json(), 'e' * 64))
            connection.execute('INSERT INTO federation_revocations VALUES(?,?,?,?,?)',
                               (descriptor.server_id, UID, 'f' * 64, '', index + 1))
        connection.execute('INSERT INTO federation_revocations VALUES(?,?,?,?,?)',
                           (healthy_peer, UID, 'f' * 64, '', 9))
    attempted = []

    def transport(descriptor, path, payload, now):
        attempted.append(descriptor.server_id)
        assert path == REVOCATION_PATH
        if descriptor.server_id != healthy_peer:
            raise FederationError('peer_unavailable')
        return {'revoked': True}

    service.transport.post = transport
    deliver(service)
    deliver(service)
    assert healthy_peer in attempted, 'Eight older failures must not indefinitely block a newer healthy notice'
    with service.state.transaction() as connection:
        remaining = connection.execute('SELECT peer_id FROM federation_revocations').fetchall()
        assert len(remaining) == 8
        assert healthy_peer not in {row[0] for row in remaining}


@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_restore_inactive_remote_card_can_be_explicitly_confirmed(databases, engine):
    backend = databases(engine)
    service, peer, _, card = configured(backend)
    package = contact_package(service, peer, card)
    own = DeviceKeys.generate().card(service.identity.descriptor.server_id, UID)
    with service.state.transaction() as connection:
        connection.execute('INSERT INTO devices VALUES(?,?,?,1)', (UID, own.device_id, own.model_dump_json()))
    with backend.connection() as connection:
        reset_after_restore(connection, 100)
    enable_after_restore(service)
    client = local_client(service, peer)
    response = client.post('/api/federation/contacts/import', json={'package': package, 'confirmed': True})
    assert response.status_code == 200, response.text
    with service.state.transaction() as connection:
        assert service.confirmed_card(connection, UID, peer.server_id, ACTOR, card.device_id, accepted=False) == card
        assert connection.execute('SELECT active FROM devices WHERE id=?', (own.device_id,)).fetchone()[0] == 0


@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_true_device_revocation_cannot_be_reconfirmed_after_restore(databases, engine):
    backend = databases(engine)
    service, peer, _, card = configured(backend)
    package = contact_package(service, peer, card)
    client = local_client(service, peer)
    response = client.post(REVOCATION_PATH, json={'actor_id': ACTOR, 'device_id': card.device_id, 'target_actor_id': ''})
    assert response.status_code == 200, response.text
    with backend.connection() as connection:
        reset_after_restore(connection, 100)
    enable_after_restore(service)
    response = client.post('/api/federation/contacts/import', json={'package': package, 'confirmed': True})
    assert response.status_code == 403, response.text
    assert response.get_json()['error'] == 'device_revoked'
    # Even an accidentally active contact-device row must not override the
    # independent permanent revocation record.
    with service.state.transaction() as connection:
        connection.execute('UPDATE peers SET active=1 WHERE id=?', (peer.server_id,))
        connection.execute("UPDATE contacts SET status='accepted' WHERE user_id=? AND peer_id=? AND actor_id=?",
                           (UID, peer.server_id, ACTOR))
        connection.execute('UPDATE federation_contact_devices SET active=1 WHERE user_id=? AND peer_id=? AND actor_id=?',
                           (UID, peer.server_id, ACTOR))
        with pytest.raises(FederationError):
            service.confirmed_card(connection, UID, peer.server_id, ACTOR, card.device_id)


@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_old_archive_preserves_both_source_and_newer_target_device_tombstones(databases, engine):
    source = databases(engine)
    service, peer, _, card = configured(source)
    source_only = DeviceKeys.generate().card(peer.server_id, ACTOR)
    with service.state.transaction() as connection:
        connection.execute('INSERT INTO federation_device_revocations VALUES(?,?,?,?)',
                           (peer.server_id, ACTOR, source_only.device_id, 100))
    password = 'Disposable-revocation-archive-password-123'
    backups = backup_service(source)
    with backups.temp() as stage:
        manifest = backups.snapshot(stage)
        archive = backups.encrypt(stage, manifest, password).read_bytes()
    target = databases(engine)
    seed(target)
    # This revocation happened after the source snapshot, on the installation
    # receiving the older archive. The archive must never erase that knowledge.
    with target.connection() as connection:
        connection.execute('INSERT INTO federation_device_revocations VALUES(?,?,?,?)',
                           (peer.server_id, ACTOR, card.device_id, 200))
    restore = backup_service(target)
    with restore.temp() as stage:
        manifest = restore.prepare(archive, password, stage)
        assert restore.restore(stage, manifest) == 'owner'
    restored = FederationState(target.connection, target.cipher, target.root)
    with restored.transaction() as connection:
        rows = connection.execute('SELECT peer_id,actor_id,device_id FROM federation_device_revocations').fetchall()
        assert {(row['peer_id'], row['actor_id'], row['device_id']) for row in rows} == {
            (peer.server_id, ACTOR, source_only.device_id), (peer.server_id, ACTOR, card.device_id)}
        assert not service.enabled(connection)
        assert connection.execute('SELECT active FROM federation_contact_devices').fetchone()[0] == 0
