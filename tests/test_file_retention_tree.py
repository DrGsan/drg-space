"""Drive tree, scoped cancellation, stale confirmations and maintenance."""
import pytest

from app.retention import cleanup
from tests.test_file_sharing_limits import confirm, url
from tests.test_files_trash import ok, folder, upload, row
from tests.test_security import m, owner, member, client, reset
from tests.test_widgets import enable


TIMER = {'mode': 'public', 'duration': 1, 'unit': 'minutes', 'auto_delete': True}


def endpoint(item):
    return '/api/files/' + item['id'] + '/retention'


def preview(c, item):
    return ok(c.post(endpoint(item) + '-cancel-preview', json={}))


def cancel(c, item, plan):
    return c.post(endpoint(item) + '-cancel', json={'confirmation': plan['confirmation']})


def test_cancels_only_scheduled_members_in_selected_branch_and_keeps_links():
    c, _, _ = owner()
    enable(c, 'files')
    root = folder(c, 'Drive')
    branch = folder(c, 'Альбом', root['id'])
    same_name = folder(c, 'Альбом', root['id'])
    file = upload(c, 'Фото.txt', parent=branch['id'])
    other = upload(c, 'Фото.txt', parent=same_name['id'])
    _, publication = confirm(c, root, TIMER)
    later = upload(c, 'Новый.txt', parent=branch['id'])

    view = ok(c.get(endpoint(branch)))
    assert {n['id']: n['parent_id'] for n in view['nodes']} == {
        branch['id']: None, file['id']: branch['id'], later['id']: branch['id'],
    }
    assert {r['id'] for r in view['files']} == {branch['id'], file['id']}
    plan = preview(c, branch)
    assert {(r['id'], r['path']) for r in plan['files']} == {
        (branch['id'], 'Альбом'), (file['id'], 'Альбом/Фото.txt'),
    }
    assert next(r for r in plan['files'] if r['id'] == file['id'])['delete_at'] == publication['expires']
    newest = upload(c, 'Ещё один.txt', parent=branch['id'])
    result = ok(cancel(c, branch, plan))
    assert set(result['cancelled']) == {branch['id'], file['id']}
    assert cancel(c, branch, plan).status_code == 409
    assert {r['id'] for r in ok(c.get(endpoint(root)))['files']} == {root['id'], same_name['id'], other['id']}
    assert client().get(url(publication, file)).content == b'recoverable file'
    assert all(row(f['id'])['deleted'] is None for f in (file, later, newest))
    assert ok(c.get(endpoint(file))) == {'files': [], 'nodes': [
        {'id': file['id'], 'parent_id': None, 'kind': 'file', 'name': 'Фото.txt'}]}


@pytest.mark.parametrize('change', ['new_timer', 'rename_parent', 'move_child', 'new_deadline', 'remove_timer'])
def test_preview_rechecks_entire_branch_before_any_deletion(change):
    c, _, _ = owner()
    enable(c, 'files')
    root = folder(c)
    branch = folder(c, 'Nested', root['id'])
    file = upload(c, parent=branch['id'])
    confirm(c, root, TIMER)
    plan = preview(c, root)
    item = next(r for r in plan['files'] if r['id'] == file['id'])
    if change == 'new_timer':
        new = upload(c, 'new.txt', parent=root['id'])
        confirm(c, new, TIMER)
    elif change == 'rename_parent':
        ok(c.patch('/api/files/' + branch['id'], json={'version': branch['version'], 'name': 'Renamed'}))
    elif change == 'move_child':
        ok(c.post('/api/files/move', json={'items': [{'id': file['id'], 'version': file['version']}], 'parent_id': root['id']}))
    elif change == 'new_deadline':
        body = {'version': item['version'], 'delete_at': item['delete_at'] + 600}
        proposed = ok(c.post(endpoint(file) + '-preview', json=body))
        ok(c.patch(endpoint(file), json={**body, 'confirmation': proposed['confirmation']}))
    else:
        ok(c.request('DELETE', endpoint(file), json={'version': item['version']}))
    current = ok(c.get(endpoint(root)))
    assert cancel(c, root, plan).status_code == 409
    assert ok(c.get(endpoint(root))) == current


def test_recreated_timer_with_identical_deadline_invalidates_preview(monkeypatch):
    c, _, _ = owner()
    enable(c, 'files')
    file = upload(c)
    stamp = m.now()
    monkeypatch.setattr(m, 'now', lambda: stamp)
    _, first = confirm(c, file, TIMER)
    old = preview(c, file)
    ok(c.request('DELETE', endpoint(file), json={'version': old['files'][0]['version']}))
    _, second = confirm(c, file, TIMER)
    new = preview(c, file)
    assert first['expires'] == second['expires']
    assert new['files'][0]['version'] != old['files'][0]['version']
    assert cancel(c, file, old).status_code == 409
    assert ok(cancel(c, file, new))['cancelled'] == [file['id']]
    assert all(client().get(url(s, file)).status_code == 200 for s in (first, second))


def test_visibility_owner_csrf_and_token_binding(monkeypatch):
    c, _, _ = owner()
    enable(c, 'files')
    root = folder(c)
    linked = upload(c, parent=root['id'])
    confirm(c, root, TIMER)
    plan = preview(c, linked)
    guest, _ = member(c)
    enable(guest, 'files')
    for action in (guest.get(endpoint(linked)), guest.post(endpoint(linked) + '-cancel-preview', json={}),
                   cancel(guest, linked, plan)):
        assert action.status_code == 404
    assert cancel(c, root, plan).status_code == 409
    assert c.post(endpoint(linked) + '-cancel', json={'confirmation': 'wrong'}).status_code == 409
    assert c.post(endpoint(linked) + '-cancel', json={'confirmation': plan['confirmation']},
                  headers={'x-csrf-token': 'wrong'}).status_code == 403
    assert client().post(endpoint(linked) + '-cancel-preview', json={}).status_code == 401
    stamp = m.now()
    monkeypatch.setattr(m, 'now', lambda: stamp + 301)
    assert cancel(c, linked, plan).status_code == 409
    assert len(ok(c.get(endpoint(root)))['files']) == 2


@pytest.mark.parametrize('cleanup_first', [True, False])
def test_deadline_order_and_existing_version_one_schedule(cleanup_first):
    c, _, _ = owner()
    enable(c, 'files')
    file = upload(c)
    with m.db() as db:
        db.execute('INSERT INTO file_retention VALUES(?,?,?,1,?)', (file['id'], m.now()-1, m.now()-1, m.now()-2))
    plan = preview(c, file)
    assert plan['files'][0]['version'] == 1
    if cleanup_first:
        cleanup(m.db, m.now())
        assert cancel(c, file, plan).status_code == 409
        assert row(file['id'])['deleted'] is not None
    else:
        ok(cancel(c, file, plan))
        cleanup(m.db, m.now())
        assert row(file['id'])['deleted'] is None


def test_parked_folder_tree_excludes_hidden_deleted_and_foreign_nodes():
    c, _, _ = owner()
    enable(c, 'files')
    root = folder(c)
    _, shared = confirm(c, root, TIMER)
    kept = folder(c, 'Keep', root['id'])
    hidden = folder(c, 'Private', root['id'])
    attachment = upload(c, parent=hidden['id'])
    gone = upload(c, parent=root['id'])
    ok(c.request('DELETE', '/api/files/' + gone['id'], json={'version': gone['version']}))
    guest, _ = member(c)
    enable(guest, 'files')
    foreign = upload(guest)
    with m.db() as db:
        db.execute('UPDATE files SET mime=? WHERE id=?', ('application/x-drg-note;space=test', hidden['id']))
        db.execute('UPDATE files SET parent_id=? WHERE id=?', (root['id'], foreign['id']))
    cleanup(m.db, shared['expires']+1)
    view = ok(c.get(endpoint(root)))
    assert {n['id'] for n in view['nodes']} == {root['id'], kept['id']}
    assert c.get(endpoint(attachment)).status_code == 404
    plan = preview(c, root)
    assert len(plan['files']) == 1 and plan['files'][0]['delete_at'] is None
    assert ok(cancel(c, root, plan))['cancelled'] == [root['id']]


def test_cleanup_and_confirmation_race_has_one_consistent_winner():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    c, _, _ = owner()
    enable(c, 'files')
    root = folder(c)
    items = [root, upload(c, 'a.txt', parent=root['id']), upload(c, 'b.txt', parent=root['id'])]
    _, link = confirm(c, root, TIMER)
    plan = preview(c, root)
    gate = Barrier(2)

    def purge_when_ready():
        gate.wait(timeout=5)
        cleanup(m.db, link['expires'])

    with ThreadPoolExecutor(max_workers=1) as pool:
        purge = pool.submit(purge_when_ready)
        gate.wait(timeout=5)
        response = cancel(c, root, plan)
        purge.result(timeout=15)
    assert response.status_code in (200, 409)
    assert all((row(f['id'])['deleted'] is None) == (response.status_code == 200) for f in items)


def test_large_tree_bounds_queries_and_batches_deletion(monkeypatch):
    from app import file_retention_tree
    from app.database import Connection

    c, _, _ = owner()
    enable(c, 'files')
    root = folder(c, 'Root')
    uid = row(root['id'])['user_id']
    count = 513
    with m.db() as db:
        for index in range(count):
            fid = f'{index:032x}'
            parent = root['id'] if index == 0 or index > 100 else f'{index-1:032x}'
            kind = 'folder' if index < 100 else 'file'
            db.execute('INSERT INTO files(id,user_id,parent_id,kind,name,created,updated) VALUES(?,?,?,?,?,?,?)',
                       (fid, uid, parent, kind, str(index), m.now(), m.now()))
            if kind == 'file':
                db.execute('INSERT INTO file_retention VALUES(?,?,?,1,?)', (fid, m.now()+600, m.now()+600, m.now()))

    statements = []
    original = Connection.execute

    def counted(db, query, *args, **kwargs):
        statements.append(query)
        return original(db, query, *args, **kwargs)

    monkeypatch.setattr(Connection, 'execute', counted)
    with m.db() as db:
        db.execute('BEGIN IMMEDIATE')
        tree = file_retention_tree.read_tree(db, uid, root['id'], preview=True)
        assert len(tree['files']) == count - 100
        assert next(r for r in tree['files'] if r['name'] == '100')['path'] == 'Root/' + '/'.join(map(str, range(101)))
        assert len(statements) <= 8
        if db.backend.kind == 'sqlite':
            tree_sql = next(q for q in statements if q.startswith('WITH RECURSIVE branch'))
            plan = db.raw.execute('EXPLAIN QUERY PLAN ' + tree_sql, (root['id'], uid, uid))
            assert any('file_owner_idx (user_id=? AND parent_id=?)' in step[3] for step in plan)
        statements.clear()
        file_retention_tree.cancel_schedules(db, tree['files'])
        assert len(statements) == 2
