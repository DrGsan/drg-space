"""File moves preserve trees, content and optimistic concurrency boundaries."""
from tests.test_security import owner, member, reset, m
from tests.test_files_trash import folder, upload, ok, used, row


def move(c, items, target=None):
    return c.post('/api/files/move', json={'items': [{'id': f['id'], 'version': f['version']} for f in items], 'parent_id': target})


def test_move_tree_and_selected_descendant_preserves_content_quota_and_zip():
    c, _, _ = owner()
    root = folder(c, 'Root')
    child = folder(c, 'Child', root['id'])
    content = upload(c, parent=child['id'])
    target = folder(c, 'Destination')
    before = used(c)
    assert ok(move(c, [root, content], target['id']))['moved'] == 1
    assert row(root['id'])['parent_id'] == target['id']
    assert row(content['id'])['parent_id'] == child['id']
    assert row(root['id'])['version'] == root['version'] + 1
    assert c.get('/api/files/'+content['id']+'/content').content == b'recoverable file'
    assert used(c) == before
    current = row(root['id'])
    ok(move(c, [current]))
    assert row(root['id'])['parent_id'] is None


def test_move_rejects_cycles_foreign_objects_deleted_targets_and_stale_batches():
    c, _, _ = owner()
    guest, _ = member(c)
    a = folder(c, 'A'); child = folder(c, 'Child', a['id']); b = folder(c, 'B')
    foreign = folder(guest, 'Foreign')
    assert move(c, [a], a['id']).status_code == 422
    assert move(c, [a], child['id']).status_code == 422
    assert move(c, [a], foreign['id']).status_code == 404
    assert move(c, [a, foreign], b['id']).status_code == 404
    assert row(a['id'])['parent_id'] is None
    ok(c.patch('/api/files/'+child['id'], json={'name':'Changed', 'version':child['version']}))
    assert move(c, [a, child], b['id']).status_code == 409
    assert row(a['id'])['parent_id'] is None
    assert move(c, [a, a], b['id']).status_code == 422
    ok(c.delete('/api/files/'+b['id']))
    assert move(c, [a], b['id']).status_code == 404
    assert move(c, [b]).status_code == 404
    file = upload(c)
    assert move(c, [a], file['id']).status_code == 422


def test_moving_into_and_out_of_publication_follows_existing_tree_scope():
    c, _, _ = owner()
    shared = folder(c, 'Shared'); content = upload(c)
    publication = ok(c.post('/api/files/'+shared['id']+'/shares', json={'mode':'public'}))
    prefix = '/api/shared-files/'+publication['token']
    ok(move(c, [content], shared['id']))
    assert c.get(prefix+'/'+content['id']+'/content').status_code == 200
    ok(move(c, [row(content['id'])]))
    assert c.get(prefix+'/'+content['id']+'/content').status_code == 404


def test_recent_and_shared_are_owner_scoped_ignore_expired_revoked_and_trash():
    c, _, _ = owner(); guest, _ = member(c)
    root = folder(c); a = upload(c, parent=root['id']); private = upload(c, 'private.txt')
    foreign = upload(guest, 'foreign.txt')
    for account, file in [(c, a), (guest, foreign)]:
        ok(account.post('/api/files/'+file['id']+'/shares', json={'mode':'public'}))
    shared = ok(c.get('/api/files?view=shared'))['items']
    assert [f['id'] for f in shared] == [a['id']] and shared[0]['shared']
    assert {f['id'] for f in ok(c.get('/api/files?view=recent'))['items']} == {root['id'], a['id'], private['id']}
    with m.db() as db:db.execute('UPDATE file_shares SET expires=? WHERE file_id=?', (m.now()-1, a['id']))
    assert ok(c.get('/api/files?view=shared'))['items'] == []
    assert not next(f for f in ok(c.get('/api/files?view=recent'))['items'] if f['id']==a['id'])['shared']
    ok(c.delete('/api/files/'+root['id']))
    assert {f['id'] for f in ok(c.get('/api/files?view=recent'))['items']} == {private['id']}
    assert c.get('/api/files?view=unknown').status_code == 422
