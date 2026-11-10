"""File trash preserves recoverable content, access boundaries and quota."""
import io
from zipfile import ZipFile

from app.retention import cleanup
from tests.test_security import m, owner, member, client, reset
from tests.test_widgets import enable


WEEK = 7 * 86400


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def folder(c, name='Folder', parent=None):
    return ok(c.post('/api/files/folders', json={'name': name, 'parent_id': parent}))


def upload(c, name='file.txt', body=b'recoverable file', parent=None):
    return ok(c.post('/api/files/upload', params={'name': name, 'parent_id': parent} if parent else {'name': name}, content=body))


def row(fid):
    with m.db() as db:
        found = db.execute('SELECT * FROM files WHERE id=?', (fid,)).fetchone()
        return dict(found) if found else None


def trash(c):
    return ok(c.get('/api/files/trash'))


def used(c):
    return ok(c.get('/api/account/storage'))['used_bytes']


def test_deleted_tree_keeps_objects_and_quota_until_restored(monkeypatch):
    c, _, _ = owner()
    enable(c, 'files')
    root = folder(c, 'Documents')
    nested = folder(c, 'Nested', root['id'])
    first = upload(c, 'first.txt', b'first original', root['id'])
    second = upload(c, 'second.txt', b'second original', nested['id'])
    ids = [root['id'], nested['id'], first['id'], second['id']]
    objects = [row(fid)['object_id'] for fid in (first['id'], second['id'])]
    quota = used(c)
    stamp = m.now()
    monkeypatch.setattr(m, 'now', lambda: stamp)

    ok(c.delete('/api/files/' + root['id']))
    listing = ok(c.get('/api/files'))
    assert listing['items'] == [] and listing['trash_count'] == 1
    result = trash(c)
    assert result['retention_days'] == 7
    assert result['quota']['used_bytes'] == quota == used(c)
    assert len(result['items']) == 1
    item = result['items'][0]
    assert item['id'] == root['id'] and item['name'] == 'Documents'
    assert item['deleted'] == stamp and item['purge_at'] == stamp + WEEK
    for fid in ids:
        assert row(fid)['deleted'] == stamp and row(fid)['trash_batch'] == root['id']
    for identity in objects:
        assert identity in m.files_router.store.ids()
    with m.db() as db:
        for identity in objects:
            assert not db.execute('SELECT 1 FROM blob_gc WHERE id=? AND kind=?', (identity, 'file')).fetchone()

    ok(c.post('/api/files/' + root['id'] + '/restore'))
    assert trash(c)['items'] == [] and ok(c.get('/api/files'))['trash_count'] == 0
    assert used(c) == quota
    for fid in ids:
        assert row(fid)['deleted'] is None and row(fid)['trash_batch'] is None
    assert c.get('/api/files/' + first['id'] + '/content').content == b'first original'
    assert c.get('/api/files/' + second['id'] + '/content').content == b'second original'


def test_nested_separate_deletions_keep_their_own_retention_and_restore_batches(monkeypatch):
    c, _, _ = owner()
    root = folder(c)
    child = folder(c, 'Child', root['id'])
    inside = upload(c, parent=child['id'])
    sibling = upload(c, 'sibling.txt', parent=root['id'])
    stamp = m.now()
    monkeypatch.setattr(m, 'now', lambda: stamp)
    ok(c.delete('/api/files/' + child['id']))
    monkeypatch.setattr(m, 'now', lambda: stamp + 20)
    ok(c.delete('/api/files/' + root['id']))
    assert {x['id'] for x in trash(c)['items']} == {root['id'], child['id']}
    for fid in (child['id'], inside['id']):
        assert row(fid)['deleted'] == stamp and row(fid)['trash_batch'] == child['id']
    assert row(sibling['id'])['deleted'] == stamp + 20

    ok(c.post('/api/files/' + root['id'] + '/restore'))
    assert {x['id'] for x in ok(c.get('/api/files', params={'parent_id': root['id']}))['items']} == {sibling['id']}
    assert row(child['id'])['deleted'] == stamp
    ok(c.post('/api/files/' + child['id'] + '/restore'))
    assert row(child['id'])['parent_id'] == root['id']
    assert c.get('/api/files/' + inside['id'] + '/content').content == b'recoverable file'


def test_restore_detaches_a_root_whose_original_parent_is_still_trashed():
    c, _, _ = owner()
    root = folder(c)
    child = folder(c, 'Child', root['id'])
    inside = upload(c, parent=child['id'])
    ok(c.delete('/api/files/' + child['id']))
    ok(c.delete('/api/files/' + root['id']))
    ok(c.post('/api/files/' + child['id'] + '/restore'))
    assert row(child['id'])['parent_id'] is None
    assert row(inside['id'])['parent_id'] == child['id']
    assert {x['id'] for x in ok(c.get('/api/files'))['items']} == {child['id']}
    assert [x['id'] for x in trash(c)['items']] == [root['id']]
    ok(c.post('/api/files/' + root['id'] + '/restore'))
    assert row(child['id'])['parent_id'] is None


def test_permanent_removal_does_not_cascade_into_another_live_trash_batch():
    c, _, _ = owner()
    root = folder(c)
    child = folder(c, 'Child', root['id'])
    keep = upload(c, 'keep.txt', parent=child['id'])
    remove = upload(c, 'remove.txt', parent=root['id'])
    keep_object = row(keep['id'])['object_id']
    remove_object = row(remove['id'])['object_id']
    ok(c.delete('/api/files/' + child['id']))
    ok(c.delete('/api/files/' + root['id']))
    before = used(c)
    ok(c.delete('/api/files/' + root['id'] + '/permanent'))
    assert row(root['id']) is None and row(remove['id']) is None
    assert row(child['id'])['parent_id'] is None
    assert row(keep['id'])['trash_batch'] == child['id']
    assert keep_object in m.files_router.store.ids()
    m.files_router.cleanup()
    assert remove_object not in m.files_router.store.ids()
    assert used(c) < before
    ok(c.post('/api/files/' + child['id'] + '/restore'))
    assert c.get('/api/files/' + keep['id'] + '/content').content == b'recoverable file'


def test_only_a_trashed_batch_root_can_be_restored_or_permanently_removed():
    c, _, _ = owner()
    root = folder(c)
    inside = upload(c, parent=root['id'])
    assert c.post('/api/files/' + root['id'] + '/restore').status_code == 404
    assert c.delete('/api/files/' + root['id'] + '/permanent').status_code == 404
    ok(c.delete('/api/files/' + root['id']))
    assert c.post('/api/files/' + inside['id'] + '/restore').status_code == 404
    assert c.delete('/api/files/' + inside['id'] + '/permanent').status_code == 404
    assert row(inside['id']) is not None


def test_seven_day_boundary_enqueues_blobs_and_preserves_one_second_younger_batch():
    c, _, _ = owner()
    root = folder(c)
    older = upload(c, 'older.txt', parent=root['id'])
    child = folder(c, 'Keep child', root['id'])
    younger = upload(c, 'younger.txt', parent=child['id'])
    old_object = row(older['id'])['object_id']
    young_object = row(younger['id'])['object_id']
    ok(c.delete('/api/files/' + child['id']))
    ok(c.delete('/api/files/' + root['id']))
    stamp = m.now()
    with m.db() as db:
        db.execute('UPDATE files SET deleted=? WHERE trash_batch=?', (stamp - WEEK, root['id']))
        db.execute('UPDATE files SET deleted=? WHERE trash_batch=?', (stamp - WEEK + 1, child['id']))
    before = used(c)
    cleanup(m.db, stamp)
    assert row(root['id']) is None and row(older['id']) is None
    assert row(child['id'])['parent_id'] is None and row(younger['id']) is not None
    assert used(c) < before
    with m.db() as db:
        assert db.execute('SELECT 1 FROM blob_gc WHERE id=? AND kind=?', (old_object, 'file')).fetchone()
        assert not db.execute('SELECT 1 FROM blob_gc WHERE id=? AND kind=?', (young_object, 'file')).fetchone()
    assert old_object in m.files_router.store.ids()
    m.files_router.cleanup()
    assert old_object not in m.files_router.store.ids() and young_object in m.files_router.store.ids()
    cleanup(m.db, stamp + 1)
    m.files_router.cleanup()
    assert row(child['id']) is None and row(younger['id']) is None
    assert young_object not in m.files_router.store.ids()


def test_blob_cleanup_retries_failed_storage_deletion(monkeypatch):
    c, _, _ = owner()
    file = upload(c)
    identity = row(file['id'])['object_id']
    ok(c.delete('/api/files/' + file['id']))
    stamp = m.now()
    with m.db() as db:
        db.execute('UPDATE files SET deleted=? WHERE id=?', (stamp - WEEK, file['id']))
    cleanup(m.db, stamp)
    original = m.files_router.store.delete
    calls = []
    def unavailable(value):
        if value == identity:
            calls.append(value)
            raise OSError('Temporary storage failure')
        return original(value)
    monkeypatch.setattr(m.files_router.store, 'delete', unavailable)
    m.files_router.cleanup()
    assert calls == [identity] and identity in m.files_router.store.ids()
    with m.db() as db:
        assert db.execute('SELECT 1 FROM blob_gc WHERE id=?', (identity,)).fetchone()
    monkeypatch.setattr(m.files_router.store, 'delete', original)
    m.files_router.cleanup()
    assert identity not in m.files_router.store.ids()
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM blob_gc WHERE id=?', (identity,)).fetchone()


def test_listing_expires_trash_and_returns_the_committed_quota():
    c, _, _ = owner()
    file = upload(c)
    identity = row(file['id'])['object_id']
    ok(c.delete('/api/files/' + file['id']))
    before = used(c)
    with m.db() as db:
        db.execute('UPDATE files SET deleted=? WHERE id=?', (m.now() - WEEK, file['id']))
    result = trash(c)
    assert result['items'] == [] and row(file['id']) is None
    assert result['quota']['used_bytes'] == used(c) < before
    assert ok(c.get('/api/files'))['trash_count'] == 0
    assert c.post('/api/files/' + file['id'] + '/restore').status_code == 404
    m.files_router.cleanup()
    assert identity not in m.files_router.store.ids()


def test_trash_does_not_free_upload_quota_and_restore_works_when_quota_is_full():
    c, _, _ = owner()
    file = upload(c, body=b'x' * 200)
    uid = ok(c.get('/api/me'))['id']
    before = used(c)
    ok(c.put('/api/admin/users/' + uid + '/quota', json={'admin_password': 'Owner-password-123', 'limit_bytes': before}))
    ok(c.delete('/api/files/' + file['id']))
    assert used(c) == before
    assert c.post('/api/files/upload?name=new.txt', content=b'y').status_code == 413
    ok(c.post('/api/files/' + file['id'] + '/restore'))
    assert used(c) == before and c.get('/api/files/' + file['id'] + '/content').content == b'x' * 200
    ok(c.delete('/api/files/' + file['id']))
    ok(c.delete('/api/files/' + file['id'] + '/permanent'))
    assert used(c) < before
    assert c.post('/api/files/upload?name=new.txt', content=b'y').status_code == 200


def test_ordinary_file_paths_and_upload_grants_refuse_trashed_content():
    c, _, _ = owner()
    imported = ok(c.post('/api/files/folder-import', json={'name': 'Imported', 'folders': ['nested']}))
    root = imported['root']
    file = upload(c, parent=imported['folders']['nested'])
    keep = upload(c, 'visible.txt', b'visible')
    ok(c.delete('/api/files/' + root['id']))
    deleted_version = row(file['id'])['version']
    base = '/api/files/' + file['id']
    denied = [
        c.get(base + '/content'),
        c.get(base + '/text'),
        c.put(base + '/text', json={'text': 'overwrite', 'version': 1}),
        c.put(base + '/content?version=1', content=b'overwrite'),
        c.patch(base, json={'name': 'renamed.txt', 'version': 1}),
        c.get(base + '/shares'),
        c.post(base + '/shares', json={'mode': 'public'}),
        c.get('/api/files', params={'parent_id': root['id']}),
        c.post('/api/files/folders', json={'name': 'New', 'parent_id': root['id']}),
        c.post('/api/files/folder-import', json={'name': 'New', 'parent_id': root['id']}),
        c.post('/api/files/folder-import/' + root['id'] + '/renew'),
        c.post('/api/files/upload', params={'name': 'new.txt', 'parent_id': root['id']}, content=b'new'),
        c.post('/api/files/upload', params={'name': 'grant.txt', 'parent_id': root['id']}, content=b'new', headers={'X-Folder-Upload-Token': imported['upload_token']}),
        c.get('/api/files/download.zip?ids=' + file['id']),
        c.post('/api/files/download', json={'ids': [root['id']]}),
    ]
    assert all(response.status_code == 404 for response in denied), [(r.status_code, r.text) for r in denied]
    assert row(file['id'])['version'] == deleted_version
    archive = c.get('/api/files/download.zip')
    assert archive.status_code == 200
    with ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.namelist() == ['visible.txt'] and zipped.read('visible.txt') == b'visible'
    assert [x['id'] for x in ok(c.get('/api/files'))['items']] == [keep['id']]


def test_active_folder_publication_hides_deleted_child_and_restore_does_not_revive_its_link():
    c, _, _ = owner()
    root = folder(c, 'Shared')
    file = upload(c, 'secret.txt', b'secret', root['id'])
    keep = upload(c, 'keep.txt', b'keep', root['id'])
    root_token = ok(c.post('/api/files/' + root['id'] + '/shares', json={'mode': 'public'}))['token']
    file_token = ok(c.post('/api/files/' + file['id'] + '/shares', json={'mode': 'public'}))['token']
    guest = client()
    root_url = '/api/shared-files/' + root_token
    file_url = '/api/shared-files/' + file_token
    assert guest.get(file_url + '/' + file['id'] + '/content').content == b'secret'
    ok(c.delete('/api/files/' + file['id']))
    assert guest.get(file_url).status_code == 404
    assert guest.get(root_url + '/' + file['id'] + '/content').status_code == 404
    assert guest.get(root_url + '/' + file['id'] + '/text').status_code == 404
    assert guest.get(root_url + '/download.zip?ids=' + file['id']).status_code == 404
    assert [x['id'] for x in ok(guest.get(root_url))['items']] == [keep['id']]
    archive = guest.get(root_url + '/download.zip')
    with ZipFile(io.BytesIO(archive.content)) as zipped:
        assert 'Shared/secret.txt' not in zipped.namelist() and zipped.read('Shared/keep.txt') == b'keep'
    ok(c.post('/api/files/' + file['id'] + '/restore'))
    assert guest.get(file_url).status_code == 404
    assert ok(c.get('/api/files/' + file['id'] + '/shares')) == []
    assert guest.get(root_url + '/' + file['id'] + '/content').content == b'secret'


def test_deleting_shared_tree_revokes_password_and_editor_grants_permanently():
    c, _, _ = owner()
    root = folder(c)
    file = upload(c, parent=root['id'])
    token = ok(c.post('/api/files/' + root['id'] + '/shares', json={'mode': 'password', 'password': 'shared-password', 'permission': 'edit'}))['token']
    url = '/api/shared-files/' + token
    guest = client()
    ok(guest.post(url + '/unlock', json={'password': 'shared-password'}))
    grant = ok(guest.post(url + '/edit-session'))
    guest.headers['X-CSRF-Token'] = grant['csrf']
    assert guest.get(url + '/' + file['id'] + '/content').status_code == 200
    with m.db() as db:
        assert db.execute('SELECT 1 FROM file_share_sessions WHERE share_token=?', (token,)).fetchone()
    ok(c.delete('/api/files/' + root['id']))
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM file_share_sessions WHERE share_token=?', (token,)).fetchone()
    assert guest.get(url).status_code == 404
    assert guest.post(url + '/unlock', json={'password': 'shared-password'}).status_code == 404
    assert guest.post(url + '/edit-session').status_code == 404
    assert guest.put(url + '/' + file['id'] + '/text', json={'text': 'stale grant', 'version': 1}).status_code == 404
    assert guest.post(url + '/upload', params={'name': 'bad.txt', 'parent_id': root['id']}, content=b'bad').status_code == 404
    ok(c.post('/api/files/' + root['id'] + '/restore'))
    assert guest.get(url).status_code == 404
    assert guest.put(url + '/' + file['id'] + '/text', json={'text': 'old cookie', 'version': 1}).status_code == 404
    assert c.get('/api/files/' + file['id'] + '/content').content == b'recoverable file'


def test_trash_is_private_and_mutations_require_csrf_and_enabled_module():
    c, _, _ = owner()
    other, _ = member(c)
    file = upload(c)
    ok(c.delete('/api/files/' + file['id']))
    assert trash(other)['items'] == [] and ok(other.get('/api/files'))['trash_count'] == 0
    assert client().get('/api/files/trash').status_code == 401
    assert other.post('/api/files/' + file['id'] + '/restore').status_code == 404
    assert other.delete('/api/files/' + file['id'] + '/permanent').status_code == 404
    csrf = c.headers.pop('x-csrf-token')
    assert c.post('/api/files/' + file['id'] + '/restore').status_code == 403
    assert c.delete('/api/files/' + file['id'] + '/permanent').status_code == 403
    c.headers['x-csrf-token'] = csrf
    ok(c.put('/api/modules', json={'enabled': {'files': False}}))
    assert c.get('/api/files/trash').status_code == 403
    assert c.post('/api/files/' + file['id'] + '/restore').status_code == 403
    assert c.delete('/api/files/' + file['id'] + '/permanent').status_code == 403
    enable(c, 'files')
    ok(c.post('/api/files/' + file['id'] + '/restore'))


def test_chat_attachments_cannot_enter_file_trash():
    from tests.test_social import family
    c, _, _ = owner()
    enable(c, 'chat', 'files')
    sid = family(c)
    attachment = ok(c.post('/api/chat/' + sid + '/attachments', params={'name': 'chat.txt'}, content=b'chat attachment'))
    fid = attachment['id']
    assert c.delete('/api/files/' + fid).status_code == 404
    assert c.post('/api/files/' + fid + '/restore').status_code == 404
    assert c.delete('/api/files/' + fid + '/permanent').status_code == 404
    assert trash(c)['items'] == [] and ok(c.get('/api/files'))['trash_count'] == 0
    assert c.get(attachment['url']).content == b'chat attachment'
    assert row(fid)['deleted'] is None
