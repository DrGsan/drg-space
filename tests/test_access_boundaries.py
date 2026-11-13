"""Cross-account reads and session-bound writes through ordinary HTTP APIs."""
import io
from zipfile import ZipFile

import pytest

from tests.test_community_files import upload
from tests.test_security import owner, member, reset
from tests.test_widgets import enable


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(params=['owner', 'member'])
def pair(request):
    admin, _, _ = owner()
    guest, _ = member(admin)
    for account in (admin, guest):
        enable(account, 'files')
    return (admin, guest) if request.param == 'owner' else (guest, admin)


@pytest.mark.parametrize('surface', ['page', 'history', 'revision', 'comments', 'shares'])
def test_private_note_reads_have_no_owner_role_bypass(pair, surface):
    actor, victim = pair
    marker = 'PRIVATE_NOTE_BOUNDARY_MARKER'
    page = ok(victim.post('/api/notes', json={'title': marker, 'body': marker}))
    path = '/api/notes/' + page['id']
    ok(victim.put(path, json={'title': marker, 'body': marker + ' edited', 'version': page['version']}))
    history = ok(victim.get(path + '/history'))
    assert history
    suffix = {'page': '', 'history': '/history', 'revision': '/history/' + history[0]['id'],
              'comments': '/comments', 'shares': '/shares'}[surface]
    assert victim.get(path + suffix).status_code == 200
    denied = actor.get(path + suffix)
    assert denied.status_code == 404
    assert marker not in denied.text
    assert victim.get(path).json()['body'] == marker + ' edited'


@pytest.mark.parametrize('method', ['get', 'head'])
@pytest.mark.parametrize('byte_range', [None, 'bytes=0-3', 'bytes=9-1'])
def test_private_file_reads_reject_cross_account_ids_with_range_headers(pair, method, byte_range):
    actor, victim = pair
    private_bytes = b'PRIVATE_FILE_BOUNDARY_MARKER'
    private = upload(victim, 'private-boundary.txt', private_bytes)
    own = upload(actor, 'own-boundary.txt', b'0123456789')
    headers = {'Range': byte_range} if byte_range else {}
    # Keep the valid ownership control independent of optional range support.
    allowed = getattr(actor, method)('/api/files/' + own['id'] + '/content')
    assert allowed.status_code == 200
    if method == 'get':
        assert allowed.content == b'0123456789'
    denied = getattr(actor, method)('/api/files/' + private['id'] + '/content', headers=headers)
    assert denied.status_code == 404
    assert 'content-disposition' not in denied.headers
    assert 'content-range' not in denied.headers
    assert private_bytes not in denied.content
    assert 'private-boundary.txt' not in str(denied.headers)
    assert victim.get('/api/files/' + private['id'] + '/content').content == private_bytes


@pytest.mark.parametrize('method', ['get', 'head'])
def test_zip_selection_rejects_mixed_ownership_atomically(pair, method):
    actor, victim = pair
    own = upload(actor, 'own.txt', b'OWN_ZIP_CONTENT')
    private = upload(victim, 'private.txt', b'PRIVATE_ZIP_CONTENT')
    path = '/api/files/download.zip?ids=' + own['id'] + ',' + private['id']
    denied = getattr(actor, method)(path)
    assert denied.status_code == 404
    assert 'content-disposition' not in denied.headers
    assert not denied.content.startswith(b'PK')
    assert b'PRIVATE_ZIP_CONTENT' not in denied.content
    allowed = actor.get('/api/files/download.zip?ids=' + own['id'])
    assert allowed.status_code == 200
    with ZipFile(io.BytesIO(allowed.content)) as archive:
        assert archive.namelist() == ['own.txt']
        assert archive.read('own.txt') == b'OWN_ZIP_CONTENT'
    assert victim.get('/api/files/' + private['id'] + '/content').content == b'PRIVATE_ZIP_CONTENT'


@pytest.mark.parametrize('surface', ['notes', 'files', 'sessions', 'refresh'])
def test_another_accounts_valid_csrf_cannot_mutate_or_rotate_a_session(pair, surface):
    actor, other = pair
    headers = {'x-csrf-token': other.headers['x-csrf-token']}
    assert headers['x-csrf-token'] != actor.headers['x-csrf-token']
    if surface == 'notes':
        page = ok(actor.post('/api/notes', json={'title': 'Keep title', 'body': 'Keep body'}))
        path = '/api/notes/' + page['id']
        denied = actor.put(path, json={'title': 'Forged edit', 'body': '', 'version': page['version']}, headers=headers)
        current = ok(actor.get(path))
        assert (current['title'], current['body'], current['version']) == ('Keep title', 'Keep body', page['version'])
        assert denied.status_code == 403
        allowed = ok(actor.put(path, json={'title': 'Allowed edit', 'body': 'Saved', 'version': page['version']}))
        assert (allowed['title'], allowed['body']) == ('Allowed edit', 'Saved')
    elif surface == 'files':
        item = upload(actor, 'keep.txt', b'KEEP_FILE_BYTES')
        denied = actor.patch('/api/files/' + item['id'], json={'name': 'forged.txt', 'version': item['version']}, headers=headers)
        current = ok(actor.get('/api/files'))['items']
        assert next(row for row in current if row['id'] == item['id'])['name'] == 'keep.txt'
        assert actor.get('/api/files/' + item['id'] + '/content').content == b'KEEP_FILE_BYTES'
        assert denied.status_code == 403
        assert ok(actor.patch('/api/files/' + item['id'], json={'name': 'allowed.txt', 'version': item['version']}))['name'] == 'allowed.txt'
    elif surface == 'sessions':
        session = ok(actor.get('/api/security/sessions'))[0]
        denied = actor.patch('/api/security/sessions/' + session['id'], json={'name': 'Forged name'}, headers=headers)
        current = ok(actor.get('/api/security/sessions'))
        assert next(row for row in current if row['id'] == session['id'])['name'] == session['name']
        assert denied.status_code == 403
        assert ok(actor.patch('/api/security/sessions/' + session['id'], json={'name': 'Allowed name'}))['name'] == 'Allowed name'
    else:
        refresh = actor.cookies.get('drg_refresh')
        denied = actor.post('/api/refresh', headers=headers)
        assert actor.cookies.get('drg_refresh') == refresh
        assert denied.status_code == 403
        ok(actor.post('/api/refresh'))
        assert actor.cookies.get('drg_refresh') != refresh
    assert denied.status_code == 403
    assert actor.get('/api/me').status_code == 200
    assert other.get('/api/me').status_code == 200
