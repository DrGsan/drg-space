"""Inspect stored bytes and exercise real cross-engine, cross-key restores."""
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from cryptography.fernet import Fernet

from app.backups import Backups
from app.database import Database, PROTECTED
from app.data_crypto import DataCipher, MediaStore, StorageError
from app.personal_data import PersonalData


@pytest.fixture
def databases(tmp_path):
    created=[]
    def make(engine='sqlite',encrypted=True):
        url=''
        if engine=='postgres':
            url=os.getenv('DRG_STORAGE_TEST_POSTGRES_URL') or os.getenv('DRG_TEST_DATABASE_URL')
            if not url:pytest.skip('Set DRG_STORAGE_TEST_POSTGRES_URL to test a disposable PostgreSQL database.')
        cipher=DataCipher.ephemeral() if encrypted else DataCipher(enabled=False)
        schema='drg_crypto_'+secrets.token_hex(8)
        backend=Database(tmp_path/schema,url=url,cipher=cipher,schema=schema)
        backend.initialize();backend.start_pool();created.append(backend)
        return backend
    yield make
    for backend in created:
        backend.close_pool()
        if backend.kind=='postgres':
            import psycopg
            from psycopg import sql
            with psycopg.connect(backend.url) as connection:
                connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(backend.schema)))


def seed(backend):
    password='Portable-password-123'
    with backend.connection() as c:
        c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',
                  ('a'*32,'owner',PasswordHasher().hash(password),'owner',json.dumps({'_two_factor_disabled':True,'custom':'PRIVATE_SETTINGS_MARKER'}),1))
        c.execute('INSERT INTO notes(id,user_id,title,body,created,updated) VALUES(?,?,?,?,?,?)',
                  ('n'*16,'a'*32,'PRIVATE_TITLE_MARKER','PRIVATE_BODY_MARKER / личный текст',1,1))
        c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('t'*16,'a'*32,'PRIVATE_TASK_MARKER','2026-10-11',0,1))
        body=Path('tests/fixtures/animated.gif').read_bytes()
        from app.modules.media import inspect_image
        mime,width,height,frames=inspect_image(body)
        MediaStore(backend.root,backend.cipher).write('b'*32,body)
        c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)',('b'*32,'a'*32,mime,len(body),width,height,frames,1))
    return password,body


def backup_service(backend):
    key=Fernet.generate_key()
    return Backups(backend.connection,backend.root,Fernet(key),key.decode(),lambda:123)


def test_aead_uses_fresh_nonces_and_authenticates_columns_and_file_ids():
    cipher=DataCipher.ephemeral();plain=b'private bytes'
    first=cipher.seal(plain,'media:first');second=cipher.seal(plain,'media:first')
    assert first!=second and plain not in first
    assert cipher.open(first,'media:first')==plain
    for broken,context in [(first,'media:second'),(first[:-1]+bytes([first[-1]^1]),'media:first'),(plain,'media:first')]:
        with pytest.raises(StorageError):cipher.open(broken,context)
    text=cipher.encode('private','column:notes.title')
    with pytest.raises(StorageError):cipher.decode(text,'column:notes.body')
    lookup=cipher.encode('owner','lookup:login',True)
    assert lookup==cipher.encode('owner','lookup:login',True)
    assert lookup!=cipher.encode('owner','lookup:ip',True)
    with pytest.raises(StorageError):cipher.decode(lookup,'lookup:ip',True)


@pytest.mark.parametrize('value',['', 'not-a-key',base64.urlsafe_b64encode(b'wrong length').decode()])
def test_default_encryption_refuses_missing_or_bad_keys(monkeypatch,value):
    monkeypatch.delenv('DATA_ENCRYPTION_ENABLED',raising=False)
    monkeypatch.setenv('DATA_ENCRYPTION_KEY',value)
    with pytest.raises(StorageError):DataCipher.from_env()


def test_disabled_encryption_is_explicit_and_invalid_modes_fail(monkeypatch):
    monkeypatch.setenv('DATA_ENCRYPTION_ENABLED','false');monkeypatch.delenv('DATA_ENCRYPTION_KEY',raising=False)
    assert not DataCipher.from_env().enabled
    monkeypatch.setenv('DATA_ENCRYPTION_ENABLED','probably')
    with pytest.raises(StorageError):DataCipher.from_env()


def test_changing_engine_cannot_reuse_and_clean_an_existing_media_directory(databases):
    backend=databases();_,image=seed(backend)
    path=backend.root/'media'/('b'*32);before=path.read_bytes()
    different=Database(backend.root,url='postgresql://unused.example/never',cipher=backend.cipher)
    with pytest.raises(StorageError):different.initialize()
    assert path.read_bytes()==before
    assert MediaStore(backend.root,backend.cipher).read('b'*32)==image


def test_unbound_media_is_not_deleted_by_initialization(tmp_path):
    root=tmp_path/'unbound';(root/'media').mkdir(parents=True)
    asset=root/'media'/('f'*32);asset.write_bytes(b'Unrelated file must remain')
    backend=Database(root,url='',cipher=DataCipher.ephemeral())
    with pytest.raises(StorageError):backend.initialize()
    assert asset.read_bytes()==b'Unrelated file must remain'


def test_data_directory_is_bound_to_database_identity_not_connection_address(databases):
    source=databases();seed(source)
    target=Database(source.root.with_name('identity_target'),url='',cipher=source.cipher);target.initialize()
    # Copying the physical database over another bound directory is rejected
    # even with a correct key. Portable restore preserves the target binding.
    from shutil import copyfile
    with source.connection() as c:c.raw.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    copyfile(source.path,target.path)
    with pytest.raises(StorageError):Database(target.root,url='',cipher=source.cipher).initialize()


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_offline_cli_creates_portable_encrypted_archive(databases,engine,tmp_path):
    backend=databases(engine);seed(backend)
    password='Offline-archive-password-123'
    password_file=tmp_path/'archive-password';password_file.write_text(password);password_file.chmod(0o600)
    output=tmp_path/'offline.drgbackup'
    env=os.environ.copy()
    env.update(DATA_DIR=str(backend.root),DATABASE_URL=backend.url,DATABASE_SCHEMA=backend.schema,
               DATA_ENCRYPTION_ENABLED='true',DATA_ENCRYPTION_KEY=base64.urlsafe_b64encode(backend.cipher.key).decode(),
               APP_SECRET='offline-test-app-secret-at-least-32-characters')
    result=subprocess.run([sys.executable,'scripts/backup.py','--offline',
                           '--output',str(output),'--password-file',str(password_file)],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert output.stat().st_mode&0o777==0o600
    archive=output.read_bytes();assert b'PRIVATE_' not in archive and backend.cipher.key not in archive
    service=backup_service(backend)
    with service.temp() as stage:
        service.prepare(archive,password,stage)
        with service.snapshot_database(stage) as snapshot:
            assert snapshot.execute('SELECT body FROM notes').fetchone()[0].startswith('PRIVATE_BODY_MARKER')


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_encrypted_storage_protects_content_media_and_checks_key_on_restart(databases,engine):
    backend=databases(engine);_,body=seed(backend)
    with backend.connection() as c:
        assert c.execute('SELECT title,body,icon,cover FROM notes').fetchone()['title']=='PRIVATE_TITLE_MARKER'
        assert json.loads(c.execute('SELECT settings FROM users WHERE login=?',('owner',)).fetchone()[0])['custom']=='PRIVATE_SETTINGS_MARKER'
        c.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',('s'*16,'family','PRIVATE_FAMILY_MARKER','a'*32,1))
        c.execute('INSERT INTO space_members VALUES(?,?,?)',('s'*16,'a'*32,1))
        c.execute('INSERT INTO note_databases(note_id,properties,views) VALUES(?,?,?)',('n'*16,'PRIVATE_PROPERTIES_MARKER','PRIVATE_VIEWS_MARKER'))
        c.execute('INSERT INTO note_comments VALUES(?,?,?,?,?,?,?,?,?)',('c'*16,'n'*16,'a'*32,None,None,'PRIVATE_COMMENT_MARKER','[]',1,0))
        c.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',('s'*16,'a'*32,'client-message-123','PRIVATE_CHAT_MARKER',1))
        c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?)',('n'*16,b'PRIVATE_CRDT_MARKER','epoch',1))
        assert c.execute('SELECT state FROM collaborative_docs').fetchone()[0]==b'PRIVATE_CRDT_MARKER'
        if engine=='postgres':
            for table,fields in PROTECTED.items():
                for row in c.raw.execute('SELECT '+','.join(sorted(fields))+' FROM '+table):
                    for value in row:
                        if value is not None:
                            stored=value.encode() if isinstance(value,str) else bytes(value)
                            assert b'PRIVATE_' not in stored
                            assert stored.startswith((DataCipher.TEXT.encode(),DataCipher.MAGIC))
    stored=(backend.root/'media'/('b'*32)).read_bytes()
    assert stored!=body and stored.startswith(DataCipher.MAGIC)
    assert MediaStore(backend.root,backend.cipher).read('b'*32)==body
    if engine=='sqlite':
        assert not backend.path.read_bytes().startswith(b'SQLite format 3')
        for file in backend.root.glob('site.sqlite3*'):
            raw=file.read_bytes();assert b'PRIVATE_' not in raw
        with sqlite3.connect(f'file:{backend.path}?mode=ro',uri=True) as raw:
            with pytest.raises(sqlite3.DatabaseError):raw.execute('SELECT * FROM users').fetchall()
    reopened=Database(backend.root,url=backend.url,cipher=backend.cipher,schema=backend.schema);reopened.initialize()
    with reopened.connection() as c:assert c.execute('SELECT body FROM notes').fetchone()[0].startswith('PRIVATE_BODY_MARKER')
    wrong=Database(backend.root,url=backend.url,cipher=DataCipher.ephemeral(),schema=backend.schema)
    with pytest.raises(StorageError):wrong.initialize()
    disabled=Database(backend.root,url=backend.url,cipher=DataCipher(enabled=False),schema=backend.schema)
    with pytest.raises(StorageError):disabled.initialize()


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_explicit_plaintext_mode_and_no_silent_mode_conversion(databases,engine):
    backend=databases(engine,False);_,body=seed(backend)
    assert (backend.root/'media'/('b'*32)).read_bytes()==body
    with backend.connection() as c:assert c.raw.execute('SELECT body FROM notes').fetchone()[0].startswith('PRIVATE_BODY_MARKER')
    if engine=='sqlite':assert backend.path.read_bytes().startswith(b'SQLite format 3')
    with pytest.raises(StorageError):
        Database(backend.root,url=backend.url,cipher=DataCipher.ephemeral(),schema=backend.schema).initialize()


@pytest.mark.parametrize('source_engine,target_engine',[('sqlite','sqlite'),('sqlite','postgres'),('postgres','sqlite'),('postgres','postgres')])
@pytest.mark.parametrize('target_encrypted',[True,False])
def test_site_archives_restore_across_keys_engines_and_encryption_modes(databases,source_engine,target_engine,target_encrypted):
    source=databases(source_engine);password,image=seed(source)
    service=backup_service(source);archive_password='Independent-archive-password-123'
    with service.temp() as stage:
        manifest=service.snapshot(stage)
        for path in Path(stage).rglob('*'):
            if path.is_file():
                if path.name=='.owner-lock':
                    assert path.read_bytes()==b''  # Kernel lease, no archive content.
                    continue
                assert path.read_bytes().startswith(DataCipher.MAGIC)
                assert b'PRIVATE_BODY_MARKER' not in path.read_bytes()
        archive=service.encrypt(stage,manifest,archive_password).read_bytes()
        assert b'PRIVATE_' not in archive and source.cipher.key not in archive
    target=databases(target_engine,target_encrypted);target_service=backup_service(target)
    with target_service.temp() as stage:
        metadata=target_service.prepare(archive,archive_password,stage)
        assert target_service.restore(stage,metadata)=='owner'
    with target.connection() as c:
        row=c.execute('SELECT * FROM users WHERE login=?',('owner',)).fetchone()
        assert PasswordHasher().verify(row['password'],password)
        assert c.execute('SELECT body FROM notes').fetchone()[0]=='PRIVATE_BODY_MARKER / личный текст'
        assert c.execute('SELECT title,day FROM tasks').fetchone()['day']=='2026-10-11'
    assert MediaStore(target.root,target.cipher).read('b'*32)==image
    raw=(target.root/'media'/('b'*32)).read_bytes()
    assert (raw==image)==(not target_encrypted)


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_personal_archives_reencrypt_files_with_fresh_ids_and_preserve_other_accounts(databases,engine):
    backend=databases(engine);_,image=seed(backend)
    with backend.connection() as c:
        c.execute('INSERT INTO users(id,login,password,role,created) VALUES(?,?,?,?,?)',('d'*32,'friend','hash','member',1))
        c.execute('INSERT INTO notes(id,user_id,title,body,created,updated) VALUES(?,?,?,?,?,?)',('f'*16,'d'*32,'FOREIGN_TITLE','FOREIGN_BODY',1,1))
    service=PersonalData(backend.connection,backend.root,lambda:123,'https://site.example.com')
    with service.temp() as stage:
        content=service.snapshot('a'*32,stage)
        assert len(content.notes)==1
        assert (Path(stage)/('b'*32)).read_bytes().startswith(DataCipher.MAGIC)
        archive=service.encrypt(content,'Personal-archive-password-123',stage).read_bytes()
    with service.temp() as stage:
        content=service.prepare(archive,'Personal-archive-password-123',stage)
        service.restore('d'*32,content,stage,False)
    with backend.connection() as c:
        assert c.execute('SELECT body FROM notes WHERE id=?',('f'*16,)).fetchone()[0]=='FOREIGN_BODY'
        copied=c.execute('SELECT id FROM media WHERE user_id=?',('d'*32,)).fetchone()[0]
        assert copied!='b'*32
    assert MediaStore(backend.root,backend.cipher).read(copied)==image
    copied_bytes=(backend.root/'media'/copied).read_bytes()
    with pytest.raises(StorageError):backend.cipher.open(copied_bytes,'media:'+('b'*32))
