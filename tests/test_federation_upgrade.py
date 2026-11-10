"""Additive federation schema regression using an explicit test-only snapshot."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys

import pytest
from app import database as storage
from app.data_crypto import DataCipher, MediaStore, StorageError
from app.federation.schema import FEDERATION_SCHEMA, FEDERATION_TABLES
from tests.test_storage_encryption import seed




@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_documented_temporary_upgrade_preserves_old_rows_files_keys_and_rollback(tmp_path,monkeypatch,engine):
    url = ''
    if engine == 'postgres':
        url = os.getenv('DRG_STORAGE_TEST_POSTGRES_URL') or os.getenv('DRG_TEST_DATABASE_URL')
        if not url: pytest.skip('A disposable PostgreSQL URL is required.')
    old_schema = storage.SCHEMA.replace(FEDERATION_SCHEMA,'',1)
    with sqlite3.connect(':memory:') as c:
        c.executescript(old_schema)
        rows = [tuple(row) for row in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        columns={row[0]:[dict(zip(('cid','name','type','notnull','dflt_value','pk'),v)) for v in c.execute('PRAGMA table_info('+row[0]+')')] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    previous = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    assert previous != storage.SCHEMA_HASH
    schema = 'drg_fed_upgrade_' + secrets.token_hex(8)
    cipher = DataCipher.ephemeral()
    backend = storage.Database(tmp_path/schema,url=url,cipher=cipher,schema=schema)
    try:
        with monkeypatch.context() as old:
            old.setattr(storage,'SCHEMA',old_schema);old.setattr(storage,'SCHEMA_HASH',previous);old.setattr(storage,'SCHEMA_ROWS',rows);old.setattr(storage,'COLUMNS',columns)
            backend.initialize()
        _, image = seed(backend)
        marker = (backend.root/'.storage-format.json').read_bytes()
        def old_rows():
            with backend.connection() as c:
                return {table:[tuple(row) for row in c.execute('SELECT * FROM '+table)]
                        for table in storage.COLUMNS if table not in FEDERATION_TABLES}
        before = old_rows()
        media = (backend.root/'media'/('b'*32)).read_bytes()
        source = (Path(__file__).parent/'fixtures/federation-upgrade.py').read_text().replace('__TEST_PREVIOUS_SCHEMA_HASH__',previous)
        script = tmp_path/'federation-upgrade.py'
        script.write_text(source);script.chmod(0o600)
        env = dict(os.environ,DATA_DIR=str(backend.root),DATABASE_URL=url,DATABASE_SCHEMA=schema,
                   DATA_ENCRYPTION_ENABLED='true',DATA_ENCRYPTION_KEY=base64.urlsafe_b64encode(cipher.key).decode(),
                   PYTHONPATH=str(Path.cwd()))
        def run(*args,success=True):
            result = subprocess.run([sys.executable,str(script),*args],env=env,capture_output=True,text=True,timeout=30)
            assert (result.returncode == 0) is success, result.stderr
        with pytest.raises(StorageError): backend.initialize(create=False)
        run()
        backend.initialize(create=False)
        assert old_rows() == before
        assert (backend.root/'.storage-format.json').read_bytes() == marker
        assert (backend.root/'media'/('b'*32)).read_bytes() == media
        assert MediaStore(backend.root,cipher).read('b'*32) == image
        run(success=False)  # Refuse replay rather than hiding a partial migration.
        run('--rollback')
        assert old_rows() == before
        with monkeypatch.context() as old:
            old.setattr(storage,'SCHEMA',old_schema);old.setattr(storage,'SCHEMA_HASH',previous);old.setattr(storage,'SCHEMA_ROWS',rows);old.setattr(storage,'COLUMNS',columns)
            backend.initialize(create=False)
        run()
        backend.initialize(create=False)
        assert old_rows() == before
        script.unlink()
    finally:
        backend.close_pool()
        if engine == 'postgres':
            import psycopg
            from psycopg import sql
            with psycopg.connect(url) as c:
                c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
