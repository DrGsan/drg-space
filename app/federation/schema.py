"""Canonical federation tables, protected by the normal storage and archive gates."""
FEDERATION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS federation_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS federation_peers(id TEXT PRIMARY KEY,descriptor TEXT NOT NULL,tls_pin TEXT NOT NULL,active INTEGER NOT NULL CHECK(active IN (0,1)));
CREATE TABLE IF NOT EXISTS federation_peer_certificates(peer_id TEXT PRIMARY KEY REFERENCES federation_peers(id) ON DELETE CASCADE,certificate TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS federation_devices(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,id TEXT NOT NULL,card TEXT NOT NULL,active INTEGER NOT NULL CHECK(active IN (0,1)),PRIMARY KEY(user_id,id));
CREATE TABLE IF NOT EXISTS federation_contacts(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,peer_id TEXT NOT NULL REFERENCES federation_peers(id) ON DELETE CASCADE,actor_id TEXT NOT NULL,card TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN ('confirmed','outgoing','incoming','accepted','blocked')),invitation TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,peer_id,actor_id));
CREATE TABLE IF NOT EXISTS federation_profiles(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,peer_id TEXT NOT NULL REFERENCES federation_peers(id) ON DELETE CASCADE,actor_id TEXT NOT NULL,profile TEXT NOT NULL,PRIMARY KEY(user_id,peer_id,actor_id));
CREATE TABLE IF NOT EXISTS federation_contact_devices(user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,peer_id TEXT NOT NULL REFERENCES federation_peers(id) ON DELETE CASCADE,actor_id TEXT NOT NULL,device_id TEXT NOT NULL,card TEXT NOT NULL,active INTEGER NOT NULL CHECK(active IN (0,1)),PRIMARY KEY(user_id,peer_id,actor_id,device_id));
CREATE TABLE IF NOT EXISTS federation_uploads(id TEXT PRIMARY KEY,note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,peer_id TEXT NOT NULL,actor_id TEXT NOT NULL,device_id TEXT NOT NULL,revision INTEGER NOT NULL,name TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,received INTEGER NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS federation_upload_chunks(upload_id TEXT NOT NULL REFERENCES federation_uploads(id) ON DELETE CASCADE,position INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(upload_id,position));
CREATE TABLE IF NOT EXISTS federation_grants(note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,peer_id TEXT NOT NULL REFERENCES federation_peers(id) ON DELETE CASCADE,actor_id TEXT NOT NULL,device_id TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('viewer','editor','none')),revision INTEGER NOT NULL,active INTEGER NOT NULL CHECK(active IN (0,1)),PRIMARY KEY(note_id,peer_id,actor_id));
CREATE TABLE IF NOT EXISTS federation_challenges(id TEXT PRIMARY KEY,subject TEXT NOT NULL,expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS federation_replay(peer_id TEXT NOT NULL,id TEXT NOT NULL,expires INTEGER NOT NULL,PRIMARY KEY(peer_id,id));
CREATE INDEX IF NOT EXISTS federation_replay_expiry_idx ON federation_replay(expires);
CREATE TABLE IF NOT EXISTS federation_events(peer_id TEXT NOT NULL,id TEXT NOT NULL,digest TEXT NOT NULL,result TEXT NOT NULL,created INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(peer_id,id));
CREATE TABLE IF NOT EXISTS federation_revocations(peer_id TEXT NOT NULL,actor_id TEXT NOT NULL,device_id TEXT NOT NULL,target_actor_id TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(peer_id,actor_id,device_id,target_actor_id));
CREATE TABLE IF NOT EXISTS federation_device_revocations(peer_id TEXT NOT NULL,actor_id TEXT NOT NULL,device_id TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(peer_id,actor_id,device_id));
'''

FEDERATION_TABLES = ('federation_metadata','federation_peers','federation_peer_certificates',
                     'federation_devices','federation_contacts','federation_profiles','federation_contact_devices',
                     'federation_grants','federation_uploads','federation_upload_chunks',
                     'federation_challenges','federation_replay','federation_events','federation_revocations','federation_device_revocations')

from .calls import FEDERATION_CALLS_SCHEMA
FEDERATION_SCHEMA += FEDERATION_CALLS_SCHEMA
FEDERATION_TABLES += ('federation_calls','federation_call_links')
