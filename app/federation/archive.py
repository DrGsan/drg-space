"""Validate complete federation archives; restored authority always starts disabled."""
import json
import re
import secrets
from cryptography import x509
from cryptography.hazmat.primitives import serialization, hashes
from .actors import DeviceCard
from .identity import ServerDescriptor, SigningIdentity


def validate_archive(c, fail):
    try:
        metadata=dict(c.execute('SELECT key,value FROM federation_metadata'))
        if 'descriptor' in metadata:
            descriptor=ServerDescriptor.model_validate_json(metadata['descriptor'])
            private=serialization.load_pem_private_key(metadata['private_key'].encode(),password=None)
            result=serialization.load_pem_private_key(metadata['result_private_key'].encode(),password=None)
            SigningIdentity(descriptor,private,result)
        if metadata.get('enabled','false') not in ('true','false'): raise ValueError()
        if 'tls_certificate' in metadata or 'tls_private_key' in metadata:
            certificate=x509.load_pem_x509_certificate(metadata['tls_certificate'].encode())
            private=serialization.load_pem_private_key(metadata['tls_private_key'].encode(),password=None)
            if certificate.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)!=private.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo): raise ValueError()
        peers={}
        for row in c.execute('SELECT * FROM federation_peers'):
            peer=ServerDescriptor.model_validate_json(row['descriptor'])
            if row['id']!=peer.server_id or row['active'] not in (0,1) or not re.fullmatch(r'[a-f0-9]{64}',row['tls_pin']): raise ValueError()
            peers[row['id']]=row
        for row in c.execute('SELECT * FROM federation_peer_certificates'):
            certificate=x509.load_pem_x509_certificate(row['certificate'].encode())
            if certificate.fingerprint(hashes.SHA256()).hex()!=peers[row['peer_id']]['tls_pin']: raise ValueError()
        for row in c.execute('SELECT * FROM federation_devices'):
            card=DeviceCard.model_validate_json(row['card'])
            if card.actor_id!=row['user_id'] or card.device_id!=row['id'] or row['active'] not in (0,1): raise ValueError()
            if 'descriptor' not in metadata or card.server_id!=descriptor.server_id: raise ValueError()
        contacts={}
        for row in c.execute('SELECT * FROM federation_contacts'):
            card=DeviceCard.model_validate_json(row['card'])
            if card.server_id!=row['peer_id'] or card.actor_id!=row['actor_id'] or row['status'] not in ('confirmed','outgoing','incoming','accepted','blocked'): raise ValueError()
            contacts[(row['user_id'],row['peer_id'],row['actor_id'])]=card
        for row in c.execute('SELECT * FROM federation_contact_devices'):
            card=DeviceCard.model_validate_json(row['card'])
            if (row['user_id'],row['peer_id'],row['actor_id']) not in contacts or card.server_id!=row['peer_id'] or card.actor_id!=row['actor_id'] or card.device_id!=row['device_id'] or row['active'] not in (0,1): raise ValueError()
        for row in c.execute('SELECT * FROM federation_profiles'):
            profile=json.loads(row['profile'])
            if ((row['user_id'],row['peer_id'],row['actor_id']) not in contacts or not isinstance(profile,dict) or
                    set(profile)-{'login','display_name'} or
                    any(not isinstance(value,str) or len(value)>200 for value in profile.values())): raise ValueError()
        for row in c.execute('SELECT * FROM federation_device_revocations'):
            if (not re.fullmatch(r'[a-f0-9]{32}',row['peer_id']) or not re.fullmatch(r'[a-f0-9]{32}',row['actor_id']) or
                    not re.fullmatch(r'[a-f0-9]{64}',row['device_id']) or type(row['created']) is not int or row['created']<0): raise ValueError()
        for row in c.execute('SELECT * FROM federation_grants'):
            if (row['owner_id'],row['peer_id'],row['actor_id']) not in contacts or row['role'] not in ('viewer','editor','none') or type(row['revision']) is not int or row['revision']<1 or row['active'] not in (0,1): raise ValueError()
        from .calls import validate_federated_call_archive
        validate_federated_call_archive(c, fail)
    except Exception:
        fail()


def reset_after_restore(c, now):
    # Never resurrect an old nonce, revoked device, stale grant, or active call.
    c.execute("INSERT INTO federation_metadata(key,value) VALUES('enabled','false') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    c.execute("INSERT INTO federation_metadata(key,value) VALUES('needs_confirmation','true') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    c.execute("INSERT INTO federation_metadata(key,value) VALUES('restore_epoch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(secrets.token_hex(16),))
    c.execute('UPDATE federation_peers SET active=0')
    c.execute('UPDATE federation_devices SET active=0')
    c.execute('UPDATE federation_contact_devices SET active=0')
    c.execute("UPDATE federation_contacts SET status='blocked',invitation=''")
    c.execute('UPDATE federation_grants SET active=0,revision=revision+1')
    for table in ('federation_replay','federation_challenges','federation_events','federation_upload_chunks','federation_uploads','federation_revocations'):
        c.execute('DELETE FROM '+table)
    from .calls import reset_federated_calls_after_restore
    reset_federated_calls_after_restore(c,now)
