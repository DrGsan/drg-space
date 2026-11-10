import base64
import copy
import io
import json
import os
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from tests.test_security import owner, member, client, reset, note, share
from tests.test_widgets import enable, add, image_bytes
from app import main as m
from app.backups import key
from app.personal_data import MAGIC, MAX_ARCHIVE

PASSWORD = 'Personal-archive-password-123'
PREFIX = '/api/account/data/'


def headers(password='Owner-password-123', settings=True):
    return {'x-account-password-b64': base64.b64encode(password.encode()).decode(),
            'x-archive-password-b64': base64.b64encode(PASSWORD.encode()).decode(),
            'x-import-confirmation': 'IMPORT', 'x-import-settings': str(settings).lower()}


def export(c, password='Owner-password-123'):
    r = c.post(PREFIX+'export', json={'password': password, 'archive_password': PASSWORD})
    assert r.status_code == 200, r.text
    assert r.headers['cache-control'] == 'no-store'
    assert '.drgpersonal' in r.headers['content-disposition']
    return r.content


def unseal(body):
    import tempfile
    from app.archive_stream import open_archive
    with tempfile.TemporaryDirectory() as directory:
        raw=open_archive(body,PASSWORD,MAGIC,directory)
        with ZipFile(raw) as z:
            return json.loads(z.read('personal.json')), {i.filename:z.read(i) for i in z.infolist() if i.filename!='personal.json'}


def seal(manifest, files=None):
    output = io.BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as z:
        z.writestr('personal.json', json.dumps(manifest))
        for name, data in (files or {}).items(): z.writestr(name, data)
    salt = os.urandom(16); nonce = os.urandom(12); header = MAGIC+salt+nonce
    return header+AESGCM(key(PASSWORD, salt)).encrypt(nonce, output.getvalue(), header)


def restore(c, body, password='Owner-password-123', settings=True):
    return c.post(PREFIX+'import', content=body, headers=headers(password, settings))


def rows():
    with m.db() as c:
        return {t: sorted((tuple(r) for r in c.execute('SELECT * FROM '+t)),key=repr) for t in
                ('users', 'notes', 'note_revisions', 'note_databases', 'database_records', 'note_comments',
                 'tasks', 'task_lists', 'task_details', 'task_list_members', 'media', 'spaces', 'space_members', 'friendships', 'messages', 'shares',
                 'sessions', 'refresh_tokens', 'recovery_codes')}


def test_export_includes_accessible_shared_content_but_excludes_foreign_private_data_and_security():
    c, _, codes = owner(); g, _ = member(c)
    nid = note(c)
    foreign = g.post('/api/notes', json={'title': 'FOREIGN_PRIVATE_MARKER', 'body': 'Never export this'}).json()['id']
    sid = c.post('/api/social/families', json={'name': 'FAMILY_PRIVATE_MARKER'}).json()['id']
    shared = c.post('/api/notes', json={'title': 'SHARED_CONTENT_MARKER', 'body': 'Collaborative text', 'space_id': sid}).json()['id']
    own_comment = c.post(f'/api/notes/{nid}/comments', json={'body': 'Only my private comment'}); assert own_comment.status_code == 200
    c.post(f'/api/notes/{shared}/comments', json={'body': 'SHARED_COMMENT_MARKER'})
    c.put('/api/notes/'+nid, json={'title': 'Personal', 'body': 'My updated text'})
    g.post('/api/tasks', json={'title': 'FOREIGN_TASK_MARKER', 'day': '2026-10-11'})
    enable(c, 'media'); enable(g, 'media'); own_asset = c.post('/api/media', content=image_bytes()).json()
    foreign_asset = g.post('/api/media', content=image_bytes(True)).json()
    add(c, 'media', {'items': [{'id': 'mine', 'asset_id': own_asset['id']}], 'selected': ['mine']})
    add(c, 'chat', {'space_id': sid})
    token = share(c, nid, 'users', allowed=['friend'])
    with m.db() as db:
        user = dict(db.execute("SELECT * FROM users WHERE login='owner'").fetchone())
        settings = json.loads(user['settings']); settings['_pending_codes'] = 'SECRET_PENDING_MARKER'
        db.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), user['id']))
        # Defense in depth: even a foreign-authored comment on a private page is excluded.
        friend = db.execute("SELECT id FROM users WHERE login='friend'").fetchone()[0]
        db.execute('INSERT INTO note_comments VALUES(?,?,?,?,?,?,?,?,?)', ('foreignComment12', nid, friend, None, None, 'FOREIGN_COMMENT_MARKER', '[]', m.now(), 0))
    c.get('/api/task-lists')
    before = rows(); body = export(c); manifest, files = unseal(body)
    raw = json.dumps(manifest)
    for forbidden in ('FOREIGN_', 'FAMILY_', 'SECRET_PENDING', user['password'], user['totp'], user['id'], friend, token, foreign, foreign_asset['id'], sid, *codes):
        assert forbidden not in raw
    assert set(manifest['data']) == {'settings', 'notes', 'revisions', 'tasks', 'task_lists', 'media', 'databases', 'records', 'comments','authenticators','expenses','files','note_assets','tracker_projects'}
    assert {n['id'] for n in manifest['data']['notes']} == {nid, shared}
    assert next(n for n in manifest['data']['notes'] if n['id']==shared)['source_shared']
    assert 'SHARED_CONTENT_MARKER' in raw
    assert {item['body'] for item in manifest['data']['comments']} == {'Only my private comment','SHARED_COMMENT_MARKER'}
    assert manifest['data']['settings']['dashboard']['instances'][-1]['config']['space_id'] is None
    assert files == {'media/'+own_asset['id']: image_bytes()}
    assert rows() == before
    # Even an administrator cannot select somebody else's account for this endpoint.
    assert c.post(PREFIX+'export', json={'password': 'Owner-password-123', 'archive_password': PASSWORD, 'user_id': friend}).status_code == 422
    guest_manifest, _ = unseal(export(g, 'Friend-password-123'))
    assert [n['id'] for n in guest_manifest['data']['notes']] == [foreign]
    assert nid not in json.dumps(guest_manifest)


def test_accessible_shared_and_temporary_publications_import_as_private_and_sources_remain_shared():
    from tests.test_social import family, join
    c, _, _ = owner(); g, _ = member(c); outsider, _ = member(c, 'outsider')
    sid = family(c); join(c, g, sid)
    parent = g.post('/api/notes', json={'title': 'Shared parent', 'body': 'Allowed family text', 'space_id': sid}).json()['id']
    child = c.post('/api/notes', json={'title': 'Shared child', 'body': 'Child text', 'space_id': sid, 'parent_id': parent}).json()['id']
    db = g.post('/api/databases', json={'title': 'Family database', 'space_id': sid}).json()['id']
    g.post('/api/databases/'+db+'/records', json={'title': 'Family record', 'properties': {'status': 'Готово'}})
    temporary = share(c, child, 'public', duration=13, unit='minutes')
    # Links expire independently of content; the member's accessible copy is still exportable.
    with m.db() as d: d.execute('UPDATE shares SET expires=? WHERE token=?', (m.now()-1, temporary))
    closed = family(outsider)
    outsider.post('/api/notes', json={'title': 'CLOSED_FAMILY_MARKER', 'body': 'Hidden', 'space_id': closed})
    other = note(outsider)
    body = export(g, 'Friend-password-123'); manifest, _ = unseal(body)
    assert len(manifest['data']['notes']) == 4
    assert 'CLOSED_FAMILY_MARKER' not in json.dumps(manifest) and other not in json.dumps(manifest)
    before = rows(); assert restore(outsider, body, 'Friend-password-123', False).status_code == 200
    copies = outsider.get('/api/notes').json(); new_parent = next(n for n in copies if n['title']=='Shared parent')
    new_child = next(n for n in copies if n['title']=='Shared child')
    assert new_child['space_id'] is None and new_parent['space_id'] is None and new_child['parent_id']==new_parent['id']
    new_db = next(n for n in copies if n['title']=='Family database')
    assert outsider.get('/api/databases/'+new_db['id']).json()['records'][0]['properties']['status']=='Готово'
    assert c.get('/api/notes/'+child).json()['space_id']==sid
    for table in ('spaces','space_members','shares','collaborative_docs'):
        with m.db() as d: assert sorted((tuple(r) for r in d.execute('SELECT * FROM '+table)),key=repr)==before.get(table, [])
    c.delete(f'/api/social/families/{sid}/members/'+g.get('/api/me').json()['id'])
    denied, _ = unseal(export(g, 'Friend-password-123'))
    assert not denied['data']['notes']


def test_import_is_additive_remaps_tree_databases_links_media_and_keeps_security():
    c, _, _ = owner(); g, _ = member(c)
    parent = note(c)
    child = c.post('/api/notes', json={'title': 'Child', 'body': 'Nested', 'parent_id': parent}).json()['id']
    blocks = [{'id': 'linked-page', 'type': 'pageLink', 'props': {'pageId': child}, 'children': []}]
    c.put('/api/notes/'+parent, json={'title': 'Personal root', 'body': 'See /page/'+child, 'blocks': blocks})
    c.post(f'/api/notes/{child}/comments', json={'body': 'My own discussion', 'block_id': None})
    deleted = c.post('/api/notes', json={'title': 'Trash page', 'body': 'Keep trash'}).json()['id']; c.delete('/api/notes/'+deleted)
    database = c.post('/api/databases', json={'title': 'Personal database'}).json()['id']
    record = c.post('/api/databases/'+database+'/records', json={'title': 'Item', 'properties': {'status': 'В работе'}}).json()['id']
    c.post('/api/tasks', json={'title': 'Personal task', 'day': '2026-10-11'})
    enable(c, 'clock'); add(c, 'clock', {'mode': 'digital', 'timezone': 'Asia/Vladivostok', 'label': 'VL'})
    enable(c, 'media'); asset = c.post('/api/media', content=image_bytes(True)).json()
    add(c, 'media', {'items': [{'id': 'animation', 'asset_id': asset['id']}], 'selected': ['animation']})
    c.put('/api/notes/'+child, json={'title': 'Child', 'body': '![My animation](https://old-server.example/api/media/'+asset['id']+')', 'blocks': [{'id': 'my-image', 'type': 'image', 'props': {'url': 'https://old-server.example/api/media/'+asset['id']}, 'children': []}]})
    c.put('/api/appearance', json={'theme': 'sage', 'mode': 'dark'})
    other = note(g); body = export(c); before = rows()
    preview = g.post(PREFIX+'preview', content=body, headers=headers('Friend-password-123'))
    assert preview.status_code == 200, preview.text
    assert preview.json()['summary']['notes'] == 5 and preview.json()['summary']['trash'] == 1
    assert rows() == before
    result = restore(g, body, 'Friend-password-123'); assert result.status_code == 200, result.text
    after = rows()
    assert next(row for row in after['users'] if row[3]=='owner') == next(row for row in before['users'] if row[3]=='owner')
    for table in ('sessions', 'refresh_tokens', 'recovery_codes', 'spaces', 'space_members', 'shares'): assert after[table] == before[table]
    assert g.get('/api/me').json()['role'] == 'member'
    assert g.get('/api/me').json()['two_factor']
    imported = g.get('/api/notes').json(); root = next(n for n in imported if n['title'] == 'Personal root'); new_child = next(n for n in imported if n['title'] == 'Child')
    assert root['id'] != parent and new_child['id'] != child
    assert new_child['parent_id'] == root['id'] and root['blocks'][0]['props']['pageId'] == new_child['id']
    assert root['body'] == 'See /page/'+new_child['id']
    assert any(n['id'] == other for n in imported)
    db = next(n for n in imported if n['title'] == 'Personal database')
    assert db['id'] != database
    rec = g.get('/api/databases/'+db['id']).json()['records'][0]
    assert rec['note_id'] != record and rec['properties']['status'] == 'В работе'
    assert g.get('/api/notes?trash=true').json()[0]['title'] == 'Trash page'
    assert g.get(f"/api/notes/{new_child['id']}/comments").json()[0]['login'] == 'friend'
    layout = g.get('/api/dashboard/layout').json()
    assert next(w for w in layout['instances'] if w['id'] == 'extra_clock')['config']['label'] == 'VL'
    new_asset = next(w for w in layout['instances'] if w['id'] == 'extra_media')['config']['items'][0]['asset_id']
    assert new_asset != asset['id'] and g.get('/api/media/'+new_asset).content == image_bytes(True)
    assert new_child['blocks'][0]['props']['url'] == m.PUBLIC_URL+'/api/media/'+new_asset
    assert 'https://old-server.example' not in new_child['body'] and new_asset in new_child['body']
    assert c.get('/api/media/'+new_asset).status_code == 404
    assert g.get('/api/media/'+asset['id']).status_code == 404
    with m.db() as d:
        assert d.execute('PRAGMA foreign_key_check').fetchone() is None
        assert d.execute('SELECT COUNT(*) FROM note_revisions WHERE note_id=?', (root['id'],)).fetchone()[0] == 1


def test_import_without_settings_keeps_preferences_and_works_with_modules_disabled():
    c, _, _ = owner(); g, _ = member(c); note(c)
    c.put('/api/appearance', json={'theme': 'sage', 'mode': 'dark'})
    c.put('/api/modules', json={'enabled': {'notes': False, 'tasks': False}})
    body = export(c)
    before = g.get('/api/me').json()['settings']
    assert restore(g, body, 'Friend-password-123', False).status_code == 200
    assert g.get('/api/me').json()['settings'] == before
    assert len(g.get('/api/notes').json()) == 1
    assert c.get('/api/notes').status_code == 403


def test_database_options_filters_records_and_own_comments_remap_portable_links():
    c, _, _ = owner(); g, _ = member(c)
    parent = note(c); enable(c, 'media')
    asset = c.post('/api/media', content=image_bytes()).json()['id']
    asset_url = '/api/media/'+asset
    database = c.post('/api/databases', json={'title': 'Linked properties'}).json()['id']
    config = {'properties': [{'id': 'attachment', 'name': 'Attachment', 'type': 'select', 'options': [asset_url]}],
              'views': [{'id': 'table', 'name': 'Table', 'type': 'table', 'filter': {'property': 'title', 'operator': 'equals', 'value': '/page/'+parent}}]}
    with m.db() as db:
        db.execute('UPDATE note_databases SET properties=?,views=? WHERE note_id=?', (json.dumps(config['properties']), json.dumps(config['views']), database))
    c.post('/api/databases/'+database+'/records', json={'title': 'Linked record', 'properties': {'attachment': asset_url}})
    c.post('/api/notes/'+parent+'/comments', json={'body': 'My file '+asset_url+' and page /page/'+parent})
    response = restore(g, export(c), 'Friend-password-123')
    assert response.status_code == 200, response.text
    pages = g.get('/api/notes').json(); new_parent = next(n for n in pages if n['title']=='Private title')
    new_db = next(n for n in pages if n['title']=='Linked properties')
    guest_uid=g.get('/api/me').json()['id']
    with m.db() as db: new_asset = db.execute('SELECT id FROM media WHERE user_id=?', (guest_uid,)).fetchone()[0]
    copied = g.get('/api/databases/'+new_db['id']).json(); new_url = m.PUBLIC_URL+'/api/media/'+new_asset
    assert copied['properties'][0]['options']==[new_url]
    assert copied['views'][0]['filter']['value']=='/page/'+new_parent['id']
    assert copied['records'][0]['properties']['attachment']==new_url
    comment = g.get('/api/notes/'+new_parent['id']+'/comments').json()[0]['body']
    assert new_url in comment and '/page/'+new_parent['id'] in comment and asset not in comment


@pytest.mark.parametrize('attack', ['user_id', 'space_id', 'role', 'totp_setting', 'foreign_parent', 'foreign_database', 'foreign_page_link', 'foreign_asset_ref', 'foreign_database_filter', 'foreign_comment_asset_ref', 'foreign_chat', 'cycle', 'unsafe_blocks', 'duplicate_note', 'bad_date'])
def test_tampered_archives_fail_before_changes_even_if_references_exist_in_live_database(attack):
    c, _, _ = owner(); g, _ = member(c); nid = note(c); foreign = note(g)
    enable(g, 'media'); foreign_asset = g.post('/api/media', content=image_bytes()).json()['id']
    sid = g.post('/api/social/families', json={'name': 'Not your family'}).json()['id']
    manifest, files = unseal(export(c)); data = manifest['data']; n = data['notes'][0]
    if attack in ('user_id', 'space_id'): n[attack] = g.get('/api/me').json()['id'] if attack == 'user_id' else sid
    elif attack == 'role': data['role'] = 'owner'
    elif attack == 'totp_setting': data['settings']['_two_factor_disabled'] = True
    elif attack == 'foreign_parent': n['parent_id'] = foreign
    elif attack == 'foreign_database': data['records'] = [{'note_id': nid, 'database_id': foreign, 'properties': {}}]
    elif attack == 'foreign_page_link': n['blocks'] = [{'type': 'pageLink', 'props': {'pageId': foreign}}]
    elif attack == 'foreign_asset_ref': n['body'] = '/api/media/'+foreign_asset
    elif attack == 'foreign_database_filter': data['databases'] = [{'note_id': nid, 'properties': [], 'views': [{'id': 'table', 'name': 'Table', 'type': 'table', 'filter': {'property': 'title', 'operator': 'equals', 'value': '/api/media/'+foreign_asset}}]}]
    elif attack == 'foreign_comment_asset_ref': data['comments'] = [{'id': 'abcdefghijklmnop', 'note_id': nid, 'parent_id': None, 'block_id': None, 'body': '/api/media/'+foreign_asset, 'created': 1, 'resolved': 0}]
    elif attack == 'foreign_chat': data['settings']['dashboard'] = {'version': 1, 'instances': [{'id': 'bad_chat', 'module_id': 'chat', 'config': {'space_id': sid}}]}
    elif attack == 'cycle': n['parent_id'] = nid
    elif attack == 'unsafe_blocks': n['blocks'] = [{'type': 'paragraph', 'content': [{'type': 'link', 'href': 'javascript:alert(1)'}]}]
    elif attack == 'duplicate_note': data['notes'].append(copy.deepcopy(n))
    elif attack == 'bad_date': data['tasks'] = [{'id': 'abcdefghijklmnop', 'title': 'Bad date', 'day': '2026-02-30', 'done': 0, 'created': 1}]
    before = rows(); r = restore(c, seal(manifest, files))
    assert r.status_code == 422, r.text
    assert rows() == before


def test_colliding_archive_ids_create_new_objects_without_touching_foreign_rows_or_files():
    c, _, _ = owner(); g, _ = member(c); own = note(c); foreign = note(g)
    enable(c, 'media'); enable(g, 'media')
    c.post('/api/media', content=image_bytes()); other = g.post('/api/media', content=image_bytes(True)).json()['id']
    manifest, files = unseal(export(c)); manifest['data']['notes'][0]['id'] = foreign
    old = manifest['data']['media'][0]['id']; manifest['data']['media'][0]['id'] = other
    files = {'media/'+other: files['media/'+old]}; before = rows(); other_bytes = (m.DATA/'media'/other).read_bytes()
    r = restore(c, seal(manifest, files)); assert r.status_code == 200, r.text
    owner_uid=c.get('/api/me').json()['id']
    with m.db() as db:
        foreign_row = tuple(db.execute('SELECT * FROM notes WHERE id=?', (foreign,)).fetchone())
        assert foreign_row == next(row for row in before['notes'] if row[0] == foreign)
        assert db.execute('SELECT COUNT(*) FROM notes WHERE user_id=?', (owner_uid,)).fetchone()[0] == 2
    assert (m.DATA/'media'/other).read_bytes() == other_bytes
    assert g.get('/api/notes/'+own).status_code == 404


@pytest.mark.parametrize('bad', ['wrong_password', 'corrupt', 'full_backup_magic', 'extra_file', 'missing_file', 'wrong_hash', 'duplicate_json'])
def test_damaged_or_wrong_format_archive_does_not_change_account(bad):
    c, _, _ = owner(); note(c); enable(c, 'media'); c.post('/api/media', content=image_bytes())
    body = export(c); manifest, files = unseal(body); h = headers()
    if bad == 'wrong_password': h['x-archive-password-b64'] = base64.b64encode(b'Wrong-archive-password').decode()
    elif bad == 'corrupt': body = body[:-1]+bytes([body[-1] ^ 1])
    elif bad == 'full_backup_magic': body = b'DRG-BACKUP-01\x00'+body
    elif bad == 'extra_file': files['../escape'] = b'escape'; body = seal(manifest, files)
    elif bad == 'missing_file': body = seal(manifest)
    elif bad == 'wrong_hash': manifest['data']['media'][0]['sha256'] = '0'*64; body = seal(manifest, files)
    elif bad == 'duplicate_json':
        out = io.BytesIO()
        with ZipFile(out, 'w') as z:
            text = json.dumps(manifest).replace('"settings":', '"settings": {}, "settings":', 1)
            z.writestr('personal.json', text)
            for name, data in files.items(): z.writestr(name, data)
        salt = os.urandom(16); nonce = os.urandom(12); header = MAGIC+salt+nonce
        body = header+AESGCM(key(PASSWORD, salt)).encrypt(nonce, out.getvalue(), header)
    before = rows(); r = c.post(PREFIX+'import', content=body, headers=h)
    assert r.status_code == 422 and rows() == before
    assert not (m.DATA/'escape').exists()


def test_authentication_csrf_password_and_confirmation_are_required():
    c, _, _ = owner(); body = export(c)
    assert client().post(PREFIX+'export', json={'password': 'Owner-password-123', 'archive_password': PASSWORD}).status_code == 401
    assert c.post(PREFIX+'export', json={'password': 'wrong', 'archive_password': PASSWORD}).status_code == 403
    assert c.post(PREFIX+'export', json={'password': 'Owner-password-123', 'archive_password': 'short'}).status_code == 422
    h = headers(); del h['x-import-confirmation']
    assert c.post(PREFIX+'import', content=body, headers=h).status_code == 422
    h = headers(); h['x-account-password-b64'] = 'invalid!'
    assert c.post(PREFIX+'preview', content=body, headers=h).status_code == 422
    assert c.post(PREFIX+'import', content=body, headers=headers('wrong')).status_code == 403
    del c.headers['x-csrf-token']
    assert c.post(PREFIX+'import', content=body, headers=headers()).status_code == 403


def test_media_quota_failure_is_atomic_and_upload_limit_checked_before_decryption():
    c, _, _ = owner(); note(c); enable(c, 'media'); asset = c.post('/api/media', content=image_bytes()).json()['id']; body = export(c)
    uid=c.get('/api/me').json()['id']
    with m.db() as d:
        used=d.execute('SELECT used_bytes FROM user_limits WHERE user_id=?',(uid,)).fetchone()[0]
        d.execute('UPDATE user_limits SET limit_bytes=? WHERE user_id=?',(used,uid))
    before = rows(); paths = set((m.DATA/'media').iterdir())
    assert restore(c, body).status_code == 413
    assert rows() == before and set((m.DATA/'media').iterdir()) == paths
    h = headers(); h['content-length'] = str(__import__('app.archive_stream',fromlist=['maximum_upload']).maximum_upload(MAX_ARCHIVE)+1)
    assert c.post(PREFIX+'preview', content=b'bad', headers=h).status_code == 413


def test_transaction_failure_after_file_creation_rolls_back_content_and_removes_only_new_files(monkeypatch):
    from app import personal_data as p
    c, _, _ = owner(); note(c); enable(c, 'media'); c.post('/api/media', content=image_bytes()); body = export(c)
    before = rows(); paths = set((m.DATA/'media').iterdir()); original = p.validate_layout; calls = 0
    def reject_during_commit(*args):
        nonlocal calls
        calls += 1
        if calls == 2: p.invalid()
        return original(*args)
    # Include a dashboard to exercise its transactional final validation.
    manifest, files = unseal(body)
    manifest['data']['settings']['dashboard'] = c.get('/api/dashboard/layout').json()
    monkeypatch.setattr(p, 'validate_layout', reject_during_commit)
    assert restore(c, seal(manifest, files)).status_code == 422
    assert rows() == before and set((m.DATA/'media').iterdir()) == paths
    assert not list(m.DATA.glob('.drg-personal-work-*'))


def test_personal_archive_keeps_more_than_fifty_recent_versions():
    c,_,_=owner();nid=note(c)
    with m.db() as db:
        for i in range(57):db.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)',(f'checkpoint{i:06}',nid,'Title '+str(i),'Content',None,m.now()))
        db.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)',('too-old',nid,'Expired','Old',None,m.now()-91*86400))
    body=export(c);manifest,_=unseal(body);assert len(manifest['data']['revisions'])==57
    assert restore(c,body).status_code==200
    copy=next(p for p in c.get('/api/notes').json() if p['id']!=nid)
    assert len(c.get('/api/notes/'+copy['id']+'/history').json())==57
