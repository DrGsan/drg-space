"""Request-scoped Media profiles/reactions preserve the individual-read contract."""
import json
import time

import pytest

from app.modules.family_photo_page import FamilyPhotoPage
from app.notifications import actor
from app.reactions import key, payload
from tests.test_family_photos import connected, enable
from tests.test_security import owner, reset
from tests.test_storage_encryption import databases


class Counted:
    def __init__(self, raw):
        self.raw, self.queries = raw, []

    def execute(self, query, args=()):
        self.queries.append(query)
        return self.raw.execute(query, args)


@pytest.fixture(params=['sqlite', 'postgres'])
def backend(databases, request):
    return databases(request.param)


def seed(c, stamp, total=3):
    users = [f'{n + 1:032x}' for n in range(total)]
    for n, uid in enumerate(users):
        settings = {'profile': {'display_name': 'Person ' + str(n), 'avatar_preset': 'blue'},
                    'modules': {'family_photos': True, 'chat': True},
                    'family_photo_access': {'enabled': True, 'groups': ['-1001'], 'write_groups': ['-1001']}}
        c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',
                  (uid, 'person.' + str(n), 'test', 'owner' if n == 0 else 'user', json.dumps(settings), stamp))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', ('session-' + uid, uid, 'csrf', 'full', stamp + 1000, stamp))
        c.execute('INSERT INTO session_presence VALUES(?,?)', ('session-' + uid, stamp - 10))
        c.execute('INSERT INTO user_presence VALUES(?,?)', (uid, stamp - 10))
    return users


def reaction(c, gid, mid, users):
    value = {'version': 1, 'space': 'telegram:' + gid, 'message': mid, 'users': users}
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', (key(value['space'], mid), json.dumps(value)))


def test_hundred_rich_messages_match_individual_reads_and_keep_presence_current(backend):
    stamp = int(time.time())
    with backend.connection() as c:
        users = seed(c, stamp); missing = 'f' * 32
        items = [{'id': mid, 'media': [], 'site_actor_id': [*users, missing][mid % 4]} for mid in range(1, 101)]
        for item in items:
            reaction(c, '-1001', item['id'], {users[0]: ['👍', '❤️'], users[1]: ['👍'], missing: ['👍']})

        def compare():
            expected = [{'site_reactions': payload(c, 'telegram:-1001', row['id'], stamp),
                         'site_profile': actor(c, row['site_actor_id'], stamp)} for row in items]
            counted = Counted(c); page = FamilyPhotoPage(counted, '-1001', items, stamp)
            assert len(counted.queries) <= 5, 'Selected reactions and unique profiles must be batched.'
            for item, wanted in zip(items, expected):
                actual = dict(item); page.decorate(actual)
                assert {name: actual[name] for name in wanted} == wanted
            return page

        page = compare(); assert page.profiles[users[0]]['online']
        c.execute('DELETE FROM sessions WHERE user_id=?', (users[0],))
        settings = json.loads(c.execute('SELECT settings FROM users WHERE id=?', (users[0],)).fetchone()[0])
        settings['profile']['display_name'] = 'Changed in this request'
        c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), users[0]))
        page = compare(); assert not page.profiles[users[0]]['online']
        assert page.profiles[users[0]]['display_name'] == 'Changed in this request'
        assert page.profiles[missing] == actor(c, missing, stamp)


@pytest.mark.parametrize('revoke', ['grant', 'module', 'chat', 'write', 'delete'])
def test_next_page_observes_current_reactor_permissions_and_deleted_users(backend, revoke):
    stamp = int(time.time())
    with backend.connection() as c:
        users = seed(c, stamp); uid = users[1]; items = [{'id': 1, 'media': [], 'site_actor_id': uid}]
        reaction(c, '-1001', 1, {users[0]: ['👍'], uid: ['👍']})
        before = dict(items[0]); FamilyPhotoPage(c, '-1001', items, stamp).decorate(before)
        assert before['site_reactions'][0]['count'] == 2
        if revoke == 'delete':
            c.execute('DELETE FROM users WHERE id=?', (uid,))
        else:
            settings = json.loads(c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()[0])
            if revoke == 'grant': settings['family_photo_access']['enabled'] = False
            if revoke == 'module': settings['modules']['family_photos'] = False
            if revoke == 'chat': settings['modules']['chat'] = False
            if revoke == 'write': settings['family_photo_access']['write_groups'] = []
            c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), uid))
        actual = dict(items[0]); FamilyPhotoPage(c, '-1001', items, stamp).decorate(actual)
        assert actual['site_reactions'] == payload(c, 'telegram:-1001', 1, stamp)
        assert actual['site_reactions'][0]['count'] == 1
        assert actual['site_profile'] == actor(c, uid, stamp)


def test_page_selects_only_its_group_and_ids_and_handles_empty_missing_and_invalid_authors(backend):
    stamp = int(time.time())
    with backend.connection() as c:
        users = seed(c, stamp)
        reaction(c, '-1001', 1, {users[0]: ['👍']})
        reaction(c, '-1001', 999, {users[0]: ['🔥']})
        reaction(c, '-1002', 1, {users[0]: ['❤️']})
        items = [{'id': 1, 'media': [], 'site_actor_id': 'not-a-user'}, {'id': 2, 'media': [], 'site_actor_id': {'invalid': 1}}]
        for gid, expected in [('-1001', '👍'), ('-1002', '❤️')]:
            page = FamilyPhotoPage(c, gid, items, stamp)
            first, missing = map(dict, items); page.decorate(first); page.decorate(missing)
            assert first['site_reactions'][0]['emoji'] == expected
            assert missing['site_reactions'] == []
            assert 'site_profile' not in first and 'site_profile' not in missing
        counted = Counted(c)
        FamilyPhotoPage(counted, '-1001', [], stamp)
        FamilyPhotoPage(counted, '-1001', [{'id': 'attachment', 'kind': 'photo'}], stamp)
        assert counted.queries == []


def test_chunks_cover_all_selected_reactions_and_unique_profiles(backend):
    stamp = int(time.time())
    with backend.connection() as c:
        users = seed(c, stamp, total=401)
        items = [{'id': n + 1, 'media': [], 'site_actor_id': uid} for n, uid in enumerate(users)]
        for item, uid in zip(items, users): reaction(c, '-1001', item['id'], {uid: ['👍']})
        counted = Counted(c); page = FamilyPhotoPage(counted, '-1001', items, stamp)
        assert len(counted.queries) <= 12
        for item, uid in zip(items, users):
            actual = dict(item); page.decorate(actual)
            assert actual['site_profile']['id'] == uid
            assert actual['site_reactions'][0]['people'][0]['id'] == uid


def test_http_history_keeps_album_tail_and_single_message_contract(connected, monkeypatch):
    from app import main as m
    from app.database import Connection

    client, _, _ = owner(); enable(client); uid = client.get('/api/me').json()['id']
    rows = [{'id': mid, 'text': 'Synthetic', 'media': [], 'site_actor_id': uid, 'album_id': 'album'} for mid in range(1, 110)]
    source = m.family_photos.fetch
    def fetch(path, method='GET', data=None):
        if path.startswith('v1/groups/-1001/messages'):
            return {'items': [dict(row) for row in rows], 'more': True} if '?' in path else dict(rows[0])
        return source(path, method, data)
    monkeypatch.setattr(m.family_photos, 'fetch', fetch)
    with m.db() as c:
        for row in rows: reaction(c, '-1001', row['id'], {uid: ['👍']})
    original = Connection._execute; queries = []
    def execute(self, query, parameters=()):
        queries.append(query); return original(self, query, parameters)
    monkeypatch.setattr(Connection, '_execute', execute)
    response = client.get('/api/family-photos/groups/-1001/messages?limit=100')
    assert response.status_code == 200 and len(queries) <= 30
    result = response.json(); assert result['more'] and [item['id'] for item in result['items']] == list(range(1, 110))
    assert all(item['site_profile']['id'] == uid and item['site_reactions'][0]['count'] == 1 for item in result['items'])
    single = client.get('/api/family-photos/groups/-1001/messages/1')
    assert single.status_code == 200 and single.json() == result['items'][0]
