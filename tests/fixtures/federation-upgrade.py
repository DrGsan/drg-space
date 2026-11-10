import os
import re
import sys
sys.path.insert(0,os.getcwd())
from app.database import Database, SCHEMA_HASH, postgres_schema
from app.data_crypto import DataCipher
from app.federation.schema import FEDERATION_SCHEMA, FEDERATION_TABLES

# Test fixture only; the test substitutes its exact pre-upgrade schema fingerprint.
PREVIOUS = '__TEST_PREVIOUS_SCHEMA_HASH__'
if PREVIOUS.startswith('__TEST_'):
    raise SystemExit('This is a regression fixture, not a deployment migration')
rollback = sys.argv[1:] == ['--rollback']
if sys.argv[1:] and not rollback:
    raise SystemExit('Unsupported argument')
db = Database(os.environ.get('DATA_DIR','/data'), cipher=DataCipher.from_env())
raw = db._connect()
placeholder = '%s' if db.kind == 'postgres' else '?'
try:
    raw.execute('BEGIN IMMEDIATE' if db.kind == 'sqlite' else 'SELECT pg_advisory_xact_lock(712941583)')
    saved = dict(raw.execute('SELECT key,value FROM storage_metadata').fetchall())
    expected = SCHEMA_HASH if rollback else PREVIOUS
    if saved.get('schema') != expected or saved.get('encrypted') != str(db.cipher.enabled):
        raise RuntimeError('Unexpected schema/encryption; no changes applied')
    if db.cipher.enabled and db.cipher.decode(saved.get('check'),'storage-check') != 'drg-storage-v1':
        raise RuntimeError('Storage key mismatch')
    if db._bind_root(raw,saved,db._root_marker()) is not None:
        raise RuntimeError('A matching existing storage root marker is required')
    if rollback:
        # Only before federation has been used. Afterwards use the verified
        # pre-upgrade rollback copy while retaining newer user edits separately.
        for table in FEDERATION_TABLES:
            if table == 'federation_metadata':
                continue
            if raw.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone():
                raise RuntimeError('Federation has data; preserve it before rollback')
        # Protected metadata must be decoded before checking enablement.
        for key,value in raw.execute('SELECT key,value FROM federation_metadata'):
            if key == 'enabled' and db.cipher.decode(value,'column:federation_metadata.value') == 'true':
                raise RuntimeError('Disable unused federation before rollback')
        for table in reversed(FEDERATION_TABLES): raw.execute('DROP TABLE '+table)
    else:
        if db.kind == 'sqlite':
            tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            statements = FEDERATION_SCHEMA.split(';')
        else:
            tables = {row[0] for row in raw.execute('SELECT tablename FROM pg_tables WHERE schemaname=%s',(db.schema,))}
            statements = [s for s in postgres_schema(db.cipher)
                          if re.match(r'CREATE (?:TABLE|INDEX) (?:IF NOT EXISTS )?federation_',s,re.I)]
        if tables.intersection(FEDERATION_TABLES):
            raise RuntimeError('Partial or unexpected federation schema')
        for statement in statements:
            if statement.strip(): raw.execute(statement)
    raw.execute('UPDATE storage_metadata SET value='+placeholder+' WHERE key='+placeholder,
                (PREVIOUS if rollback else SCHEMA_HASH,'schema'))
    raw.commit()
except BaseException:
    raw.rollback()
    raise
finally:
    raw.close()
print('Schema rollback completed' if rollback else 'Schema upgrade completed; existing data preserved')
