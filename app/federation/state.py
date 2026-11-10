"""Encrypted companion state for the opt-in local prototype, not a site migration.

No production startup imports this module. The prototype's dedicated SQLCipher
file is not included in site backups; activation outside the disposable harness
is unsupported until storage, archive validation, and recovery are integrated.
"""
from contextlib import contextmanager
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from app.database import _SQLCIPHER_CONNECTIONS
from app.data_crypto import StorageError
from .identity import FederationError, ServerDescriptor, SigningIdentity

SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS peers(id TEXT PRIMARY KEY,descriptor TEXT NOT NULL,tls_pin TEXT NOT NULL,active INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS devices(user_id TEXT NOT NULL,id TEXT NOT NULL,card TEXT NOT NULL,active INTEGER NOT NULL,PRIMARY KEY(user_id,id));
CREATE TABLE IF NOT EXISTS contacts(user_id TEXT NOT NULL,peer_id TEXT NOT NULL,actor_id TEXT NOT NULL,card TEXT NOT NULL,status TEXT NOT NULL,invitation TEXT NOT NULL DEFAULT '',PRIMARY KEY(user_id,peer_id,actor_id));
CREATE TABLE IF NOT EXISTS grants(note_id TEXT NOT NULL,owner_id TEXT NOT NULL,peer_id TEXT NOT NULL,actor_id TEXT NOT NULL,device_id TEXT NOT NULL,role TEXT NOT NULL,revision INTEGER NOT NULL,active INTEGER NOT NULL,PRIMARY KEY(note_id,peer_id,actor_id));
CREATE TABLE IF NOT EXISTS challenges(id TEXT PRIMARY KEY,subject TEXT NOT NULL,expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS replay(peer_id TEXT NOT NULL,id TEXT NOT NULL,expires INTEGER NOT NULL,PRIMARY KEY(peer_id,id));
CREATE TABLE IF NOT EXISTS events(peer_id TEXT NOT NULL,id TEXT NOT NULL,digest TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(peer_id,id));
'''


class PrototypeState:
    def __init__(self, root, cipher):
        if not cipher.enabled:
            raise StorageError('The federation prototype requires encrypted storage.')
        self.path = Path(root) / 'federation-prototype.sqlite3'
        if self.path.is_symlink():
            raise StorageError('Invalid federation state path.')
        self.key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                        info=b'drg/federation/prototype/sqlcipher/v1').derive(cipher.key)
        with self.transaction() as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise StorageError('Unsupported federation prototype format.')
            connection.executescript(SCHEMA)
            connection.execute('PRAGMA user_version=1')
        os.chmod(self.path, 0o600)

    @contextmanager
    def transaction(self):
        import sqlcipher3
        with _SQLCIPHER_CONNECTIONS:
            connection = sqlcipher3.connect(self.path, timeout=10)
            connection.row_factory = sqlcipher3.Row
            try:
                connection.execute('PRAGMA key="x\'' + self.key.hex() + '\'"')
                connection.execute('PRAGMA cipher_memory_security=ON')
                connection.execute('PRAGMA temp_store=MEMORY')
                connection.execute('PRAGMA secure_delete=ON')
                connection.execute('BEGIN IMMEDIATE')
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def identity(self, origin):
        with self.transaction() as connection:
            rows = dict(connection.execute('SELECT key,value FROM metadata'))
            if getattr(self,'canonical',False) and 'descriptor' in rows:
                descriptor=ServerDescriptor.model_validate_json(rows['descriptor'])
                if descriptor.origin!=origin:
                    # An ordinary HTTPS installation may configure federation
                    # much later, using a different direct-TLS address. There
                    # are no pins to preserve until the feature has been used.
                    from .schema import FEDERATION_TABLES
                    unused=(rows.get('enabled')!='true' and rows.get('ever_enabled')!='true'
                            and rows.get('needs_confirmation')!='true' and
                            not any(connection.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone()
                                    for table in FEDERATION_TABLES if table!='federation_metadata'))
                    if not unused:raise FederationError('server_origin_changed')
                    names=('descriptor','private_key','result_private_key','tls_certificate','tls_private_key')
                    for name in names:
                        connection.execute('DELETE FROM metadata WHERE key=?',(name,));rows.pop(name,None)
            if 'descriptor' not in rows:
                identity = SigningIdentity.generate(origin)
                descriptor = ServerDescriptor.model_validate(identity.descriptor.model_dump() | {
                    'capabilities': (('device-proofs', 'sealed-results', 'shared-rich-notes', 'mutual-tls', 'calls', 'device-revocation')
                                     if getattr(self,'canonical',False) else
                                     ('device-proofs', 'sealed-results', 'shared-text-notes', 'mutual-tls'))})
                identity = SigningIdentity(descriptor, identity.private_key, identity.result_private_key)
                values = {'descriptor': identity.descriptor.model_dump_json(),
                          'private_key': identity.private_key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
                          'result_private_key': identity.result_private_key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8, serialization.NoEncryption())}
                connection.executemany('INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', values.items())
            else:
                descriptor = ServerDescriptor.model_validate_json(rows['descriptor'])
                if descriptor.origin != origin:
                    raise FederationError('server_origin_changed')
                private = rows['private_key'].encode() if isinstance(rows['private_key'], str) else rows['private_key']
                result_private = rows.get('result_private_key')
                if isinstance(result_private, str): result_private = result_private.encode()
                identity = SigningIdentity(descriptor, serialization.load_pem_private_key(private, password=None),
                    serialization.load_pem_private_key(result_private, password=None) if result_private else None)
            return identity

    def consume(self, peer_id, request_id, expires, now):
        with self.transaction() as connection:
            connection.execute('DELETE FROM replay WHERE expires<=?', (now,))
            if connection.execute('SELECT 1 FROM replay WHERE peer_id=? AND id=?', (peer_id, request_id)).fetchone():
                raise FederationError('replayed_request')
            if connection.execute('SELECT COUNT(*) FROM replay').fetchone()[0] >= 4096:
                raise FederationError('replay_window_full')
            connection.execute('INSERT INTO replay VALUES(?,?,?)', (peer_id, request_id, expires))

    @staticmethod
    def challenge(connection, subject, now):
        import secrets
        connection.execute('DELETE FROM challenges WHERE expires<=?', (now,))
        if connection.execute('SELECT COUNT(*) FROM challenges').fetchone()[0] >= 4096:
            raise FederationError('challenge_capacity')
        nonce = secrets.token_hex(32)
        connection.execute('INSERT INTO challenges VALUES(?,?,?)', (nonce, subject, now + 120))
        return nonce

    @staticmethod
    def use_challenge(connection, nonce, subject, now):
        changed = connection.execute('DELETE FROM challenges WHERE id=? AND subject=? AND expires>?',
                                     (nonce, subject, now)).rowcount
        if changed != 1:
            raise FederationError('challenge_rejected')
