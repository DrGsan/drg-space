"""Archives preserve file trash lifetimes and reject inconsistent file trees."""
import sqlite3

import pytest
from pydantic import ValidationError

from app.backups import BackupError, Backups
from app.personal_data import Content, PortableFile, validate_content
from tests.test_security import owner, reset
from tests.test_widgets import enable
from tests.test_community_files import upload
from tests.test_personal_data import export, restore, seal, unseal
from tests.test_admin import backup, headers as admin_headers
from app import main as m


def tree(client):
    enable(client, 'files')
    parent = client.post('/api/files/folders', json={'name': 'Deleted folder'}).json()
    child = upload(client, 'separate.txt', b'SEPARATE_TRASH_CONTENT', parent['id'])
    sibling = upload(client, 'together.txt', b'FOLDER_TRASH_CONTENT', parent['id'])
    return parent, child, sibling


def mark_deleted(file_id, deleted, batch):
    with m.db() as connection:
        connection.execute('UPDATE files SET deleted=?,trash_batch=? WHERE id=?', (deleted, batch, file_id))


def test_personal_import_accepts_old_file_fields_and_exports_active_state():
    client, _, _ = owner()
    enable(client, 'files')
    original = upload(client, 'legacy.txt', b'LEGACY_FILE_CONTENT')
    manifest, objects = unseal(export(client))
    row = manifest['data']['files'][0]
    assert row['deleted'] is None and row['trash_batch'] is None
    row.pop('deleted'); row.pop('trash_batch')
    result = restore(client, seal(manifest, objects))
    assert result.status_code == 200, result.text
    with m.db() as connection:
        copied = connection.execute('SELECT * FROM files WHERE id<>?', (original['id'],)).fetchone()
        assert copied['deleted'] is None and copied['trash_batch'] is None
    assert client.get('/api/files/'+copied['id']+'/content').content == b'LEGACY_FILE_CONTENT'


def test_personal_roundtrip_remaps_nested_batches_without_resetting_deletion_time():
    client, _, _ = owner()
    parent, child, sibling = tree(client)
    stamp = m.now()
    mark_deleted(child['id'], stamp-86400, child['id'])
    mark_deleted(parent['id'], stamp, parent['id'])
    mark_deleted(sibling['id'], stamp, parent['id'])
    manifest, objects = unseal(export(client))
    assert {row['id'] for row in manifest['data']['files']} == {parent['id'], child['id'], sibling['id']}
    assert b'SEPARATE_TRASH_CONTENT' in objects.values()
    result = restore(client, seal(manifest, objects))
    assert result.status_code == 200, result.text
    old_ids = {parent['id'], child['id'], sibling['id']}
    with m.db() as connection:
        copied = {row['name']: dict(row) for row in connection.execute('SELECT * FROM files') if row['id'] not in old_ids}
    root = copied['Deleted folder']
    assert root['trash_batch'] == root['id'] and root['deleted'] == stamp
    assert copied['together.txt']['parent_id'] == root['id']
    assert copied['together.txt']['trash_batch'] == root['id'] and copied['together.txt']['deleted'] == stamp
    assert copied['separate.txt']['parent_id'] == root['id']
    assert copied['separate.txt']['trash_batch'] == copied['separate.txt']['id']
    assert copied['separate.txt']['deleted'] == stamp-86400
    assert client.get('/api/files/'+copied['separate.txt']['id']+'/content').status_code == 404


def test_import_purges_expired_batch_but_keeps_newer_independent_child():
    client, _, _ = owner()
    parent, child, sibling = tree(client)
    stamp = m.now()
    mark_deleted(child['id'], stamp-86400, child['id'])
    mark_deleted(parent['id'], stamp-6*86400, parent['id'])
    mark_deleted(sibling['id'], stamp-6*86400, parent['id'])
    manifest, objects = unseal(export(client))
    for row in manifest['data']['files']:
        if row['trash_batch'] == parent['id']:
            row['deleted'] = stamp-7*86400
    result = restore(client, seal(manifest, objects))
    assert result.status_code == 200, result.text
    assert result.json()['imported']['files'] == 1 and result.json()['imported']['folders'] == 0
    old_ids = {parent['id'], child['id'], sibling['id']}
    with m.db() as connection:
        copies = [dict(row) for row in connection.execute('SELECT * FROM files') if row['id'] not in old_ids]
        assert len(copies) == 1
        survivor = copies[0]
        assert survivor['name'] == 'separate.txt' and survivor['parent_id'] is None
        assert survivor['trash_batch'] == survivor['id'] and survivor['deleted'] == stamp-86400
    assert m.files_router.store.read(survivor['object_id']) == b'SEPARATE_TRASH_CONTENT'


def test_full_backup_roundtrip_keeps_trashed_objects_and_original_lifetime():
    client, _, _ = owner()
    enable(client, 'files')
    original = upload(client, 'recoverable.txt', b'FULL_TRASH_CONTENT')
    deleted = m.now()-3*86400
    mark_deleted(original['id'], deleted, original['id'])
    archive = backup(client)
    with m.db() as connection:
        connection.execute('UPDATE files SET deleted=NULL,trash_batch=NULL WHERE id=?', (original['id'],))
    result = client.post('/api/admin/backups/restore', content=archive, headers=admin_headers())
    assert result.status_code == 200, result.text
    with m.db() as connection:
        restored = connection.execute('SELECT * FROM files WHERE id=?', (original['id'],)).fetchone()
        assert restored['deleted'] == deleted and restored['trash_batch'] == original['id']
    assert m.files_router.store.read(restored['object_id']) == b'FULL_TRASH_CONTENT'


def portable_folder(identifier='a'*16, parent=None, deleted=None, batch=None):
    return {'id': identifier, 'parent_id': parent, 'kind': 'folder', 'name': 'Folder',
            'mime': 'application/octet-stream', 'size': 0, 'object_id': None,
            'created': 1, 'updated': 1, 'deleted': deleted, 'trash_batch': batch}


@pytest.mark.parametrize('stamp', [True, False, -1, 1.5, '1'])
def test_personal_file_deletion_stamp_is_strict(stamp):
    with pytest.raises(ValidationError):
        PortableFile.model_validate(portable_folder(deleted=stamp, batch='a'*16))


def validate_portable(rows):
    validate_content(Content(files=rows, settings={}, notes=[], revisions=[], tasks=[],
                             media=[], databases=[], records=[], comments=[]))


@pytest.mark.parametrize('rows', [
    [portable_folder(deleted=1)],
    [portable_folder(batch='a'*16)],
    [portable_folder(deleted=1, batch='b'*16)],
    [portable_folder(deleted=1, batch='a'*16), portable_folder('b'*16, 'a'*16)],
    [portable_folder(deleted=1, batch='a'*16), portable_folder('b'*16, 'a'*16, 2, 'a'*16)],
    [portable_folder(deleted=1, batch='a'*16), portable_folder('b'*16, deleted=1, batch='a'*16)],
])
def test_personal_archive_rejects_invalid_file_trash_tree(rows):
    with pytest.raises(BackupError):
        validate_portable(rows)


def test_full_validator_checks_trash_batches_and_chat_attachment_state():
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript('''
            CREATE TABLE files(id TEXT,parent_id TEXT,kind TEXT,name TEXT,mime TEXT,size INTEGER,
                               object_id TEXT,created INTEGER,updated INTEGER,deleted INTEGER,
                               trash_batch TEXT,user_id TEXT);
            CREATE TABLE user_limits(limit_bytes INTEGER);
            CREATE TABLE note_assets(payload TEXT);
        ''')
        def add(row, owner='owner'):
            data = row | {'user_id': owner}
            connection.execute('INSERT INTO files('+','.join(data)+') VALUES('+','.join('?' for _ in data)+')', tuple(data.values()))
        add(portable_folder(deleted=1, batch='a'*16))
        add(portable_folder('b'*16, 'a'*16, 2, 'b'*16))
        Backups.validate_files(None, connection, {'objects': []})
        for statement, parameters in [
            ('UPDATE files SET deleted=NULL,trash_batch=NULL WHERE id=?', ('b'*16,)),
            ('UPDATE files SET deleted=1,trash_batch=? WHERE id=?', ('a'*16, 'b'*16)),
            ('UPDATE files SET user_id=? WHERE id=?', ('foreign', 'b'*16)),
            ('UPDATE files SET deleted=? WHERE id=?', (-1, 'b'*16)),
            ('UPDATE files SET trash_batch=? WHERE id=?', ('missingBatch1234', 'b'*16)),
        ]:
            connection.execute('SAVEPOINT invalid_tree')
            connection.execute(statement, parameters)
            if parameters == ('a'*16, 'b'*16):
                # Matching time alone cannot make a separate root part of a foreign subtree.
                connection.execute('UPDATE files SET parent_id=NULL WHERE id=?', ('b'*16,))
            with pytest.raises(BackupError):
                Backups.validate_files(None, connection, {'objects': []})
            connection.execute('ROLLBACK TO invalid_tree'); connection.execute('RELEASE invalid_tree')
        attachment = portable_folder('chat-'+'s'*16+'-'+'f'*16, deleted=1, batch='a'*16)
        add(attachment)
        with pytest.raises(BackupError):
            Backups.validate_files(None, connection, {'objects': []})
