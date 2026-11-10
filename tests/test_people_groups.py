"""Managed workspaces keep consent, ownership and isolation for every template."""
import json
import pytest
from tests.test_security import owner, member, note, reset
from app import main as m
from app.backups import schema
from app.space_profiles import PREFIX, validate_archive
from tests.test_social import family, shared, enabled
from tests.test_admin import backup, headers, proof
from tests.test_family_avatars import image


def workspace(c, template='group', name='Шашлыки 2026'):
    r = c.post('/api/social/spaces', json={'name': name, 'template': template})
    assert r.status_code == 200, r.text
    return r.json()['id']


def invite(c, guest, sid, login='outsider'):
    r = c.post(f'/api/social/spaces/{sid}/invites', json={'login': login})
    assert r.status_code == 200, r.text
    r = guest.post('/api/social/requests/' + r.json()['id'] + '/accept')
    assert r.status_code == 200, r.text


@pytest.mark.parametrize('template', ['family', 'group', 'project', 'organization'])
def test_regular_user_creates_each_template_with_consent_and_isolated_access(template):
    admin, _, _ = owner(); creator, _ = member(admin); guest, _ = member(admin, 'outsider')
    private = note(creator)
    sid = workspace(creator, template)
    root = shared(creator, sid)
    assert creator.get('/api/me').json()['role'] == 'member'
    assert guest.get('/api/notes/' + root['id']).status_code == 404
    assert admin.get('/api/notes/' + root['id']).status_code == 404
    assert admin.post(f'/api/social/spaces/{sid}/invites', json={'login': 'outsider'}).status_code == 404
    invite(creator, guest, sid)
    assert guest.get('/api/notes/' + root['id']).status_code == 200
    assert guest.get('/api/notes/' + private).status_code == 404
    assert guest.post(f'/api/social/spaces/{sid}/invites', json={'login': 'owner'}).status_code == 403
    assert guest.patch('/api/social/spaces/' + sid, json={'name': 'Hijack'}).status_code == 403
    assert guest.delete('/api/social/spaces/' + sid).status_code == 403
    assert guest.post('/api/notes/' + root['id'] + '/shares', json={'mode': 'public'}).status_code == 403
    child = shared(guest, sid, root['id'])
    for account in (creator, guest):
        saved = account.get('/api/social').json()['spaces'][0]
        assert saved['template'] == template and len(saved['members']) == 2
        enabled(account)
        room = account.get('/api/chat/rooms').json()['items'][0]
        assert room['id'] == sid and room['template'] == template
    assert guest.post(f'/api/chat/{sid}/messages', json={'body': 'Продукты куплены', 'client_id': 'group-message-123'}).status_code == 200
    assert guest.put(f'/api/chat/{sid}/timer', json={'seconds': 86400}).status_code == 403
    assert creator.get(f'/api/chat/{sid}/messages').json()['items'][0]['body'] == 'Продукты куплены'
    assert creator.patch('/api/social/spaces/' + sid, json={'name': 'Наши планы'}).status_code == 200
    creator_id = creator.get('/api/me').json()['id']
    assert creator.delete(f'/api/social/spaces/{sid}/members/{creator_id}').status_code == 409
    guest_id = guest.get('/api/me').json()['id']
    assert creator.delete(f'/api/social/spaces/{sid}/members/{guest_id}').status_code == 200
    assert guest.get('/api/notes/' + child['id']).status_code == 404
    assert guest.get(f'/api/chat/{sid}/messages').status_code == 404
    assert creator.get('/api/notes/' + child['id']).status_code == 200
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM friendships').fetchone()[0] == 0


@pytest.mark.parametrize('payload', [
    {'name': '  ', 'template': 'group'},
    {'name': 'A', 'template': 'business'},
    {'name': 'A', 'template': None},
    {'name': 'A', 'template': ['organization']},
    {'name': 'A', 'template': 'organization', 'owner_id': 'someone'},
    {'name': 'A', 'template': 'organization', 'advanced': True},
])
def test_workspace_creation_rejects_unknown_templates_and_extra_settings(payload):
    c, _, _ = owner()
    assert c.post('/api/social/spaces', json=payload).status_code == 422
    assert c.get('/api/social').json()['spaces'] == []
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?', (PREFIX + '%',)).fetchone()


def test_default_template_legacy_families_and_bilateral_contacts_stay_distinct():
    c, _, _ = owner(); guest, _ = member(c)
    legacy = family(c)
    default = c.post('/api/social/spaces', json={'name': 'Без шаблона'}).json()['id']
    invitation = c.post('/api/social/friends', json={'login': 'friend'}).json()['id']
    contact = guest.post('/api/social/requests/' + invitation + '/accept').json()['space_id']
    spaces = {s['id']: s for s in c.get('/api/social').json()['spaces']}
    assert spaces[legacy]['template'] == 'family'
    assert spaces[default]['template'] == 'group'
    assert spaces[contact]['template'] is None and spaces[contact]['kind'] == 'friends'
    assert c.patch('/api/social/spaces/' + contact, json={'name': 'Group'}).status_code == 403
    assert c.post(f'/api/social/spaces/{contact}/invites', json={'login': 'friend'}).status_code == 403
    assert c.patch('/api/social/spaces/' + default, json={'name': 'Company', 'template': 'organization'}).status_code == 422


def test_workspace_avatar_leave_and_delete_use_generic_routes():
    c, _, _ = owner(); guest, _ = member(c)
    sid = workspace(c, 'project')
    invite(c, guest, sid, 'friend')
    url = f'/api/social/spaces/{sid}/avatar'
    assert guest.post(url, content=image()).status_code == 403
    saved = c.post(url, content=image()); assert saved.status_code == 200, saved.text
    assert c.get(url).status_code == 200
    assert guest.get(saved.json()['avatar_url']).status_code == 200
    guest_id = guest.get('/api/me').json()['id']
    assert guest.delete(f'/api/social/spaces/{sid}/members/{guest_id}').status_code == 200
    assert guest.get(saved.json()['avatar_url']).status_code == 404
    response=c.delete('/api/social/spaces/' + sid);assert response.status_code == 200
    with m.db() as db:
        from app.workspace_lifecycle import purge
        purge(db,response.json()['deletion']['delete_at'])
        assert not db.execute('SELECT 1 FROM app_settings WHERE key=?', (PREFIX + sid,)).fetchone()


def test_profiles_and_legacy_data_survive_encrypted_full_backup_without_schema_change():
    c, _, _ = owner(); guest, _ = member(c)
    with m.db() as db: before = schema(db)
    legacy = family(c); legacy_note = shared(c, legacy)
    sid = workspace(c, 'organization', 'Компания'); invite(c, guest, sid, 'friend')
    page = shared(guest, sid)
    enabled(c)
    assert c.post(f'/api/chat/{sid}/messages', json={'body': 'Рабочая переписка', 'client_id': 'backup-group-123'}).status_code == 200
    with m.db() as db: assert schema(db) == before
    saved = backup(c)
    assert c.delete('/api/social/spaces/' + sid).status_code == 200
    restored = c.post('/api/admin/backups/restore', content=saved, headers=headers())
    assert restored.status_code == 200, restored.text
    with m.db() as db:
        assert schema(db) == before
        assert json.loads(db.execute('SELECT value FROM app_settings WHERE key=?', (PREFIX + sid,)).fetchone()[0])['template'] == 'organization'
        assert db.execute('SELECT title FROM notes WHERE id=?', (page['id'],)).fetchone()[0] == page['title']
        assert db.execute('SELECT id FROM notes WHERE id=?', (legacy_note['id'],)).fetchone()[0] == legacy_note['id']
        assert db.execute('SELECT COUNT(*) FROM space_members WHERE space_id=?', (sid,)).fetchone()[0] == 2
        assert db.execute('SELECT body FROM messages WHERE space_id=?', (sid,)).fetchone()[0] == 'Рабочая переписка'


def test_full_backup_without_profiles_restores_existing_families():
    c, _, _ = owner(); sid = family(c); page = shared(c, sid)
    saved = backup(c)
    assert c.delete('/api/social/spaces/' + sid).status_code == 200
    restored = c.post('/api/admin/backups/restore', content=saved, headers=headers())
    assert restored.status_code == 200, restored.text
    from app.space_profiles import templates
    with m.db() as db:
        assert templates(db).get(sid, 'family') == 'family'
        assert db.execute('SELECT id FROM notes WHERE id=?', (page['id'],)).fetchone()[0] == page['id']


def test_corrupted_profile_in_full_backup_cannot_replace_live_data():
    c, _, _ = owner(); sid = workspace(c, 'organization'); page = shared(c, sid)
    from app.backups import Backups
    import base64, hashlib
    from tests.test_admin import BACKUP_PASSWORD
    service = Backups(m.db, m.DATA, m.cipher, base64.urlsafe_b64encode(hashlib.sha256(m.APP_SECRET.encode()).digest()).decode(), m.now)
    with service.temp() as stage:
        manifest = service.snapshot(stage)
        with service.snapshot_database(stage, write=True) as database:
            with database:
                database.execute('UPDATE app_settings SET value=? WHERE key=?', (json.dumps({'version': 1, 'template': 'unknown'}), PREFIX + sid))
        encrypted = service.encrypt(stage, manifest, BACKUP_PASSWORD).read_bytes()
    restored = c.post('/api/admin/backups/restore', content=encrypted, headers=headers())
    assert restored.status_code == 422
    assert c.get('/api/notes/' + page['id']).status_code == 200
    assert c.get('/api/social').json()['spaces'][0]['template'] == 'organization'


@pytest.mark.parametrize('value', [
    {'version': True, 'template': 'group'},
    {'version': 2, 'template': 'group'},
    {'version': 1, 'template': 'unknown'},
    {'version': 1, 'template': 'group', 'admin': True},
    {'version': 1, 'template': ['group']},
])
def test_invalid_profiles_are_rejected_by_archive_validation(value):
    c, _, _ = owner(); sid = workspace(c)
    def invalid(): raise ValueError('invalid archive')
    with m.db() as db:
        db.execute('UPDATE app_settings SET value=? WHERE key=?', (json.dumps(value), PREFIX + sid))
        with pytest.raises(ValueError, match='invalid archive'): validate_archive(db, invalid)


def test_profiles_are_cleaned_up_after_owner_account_deletion():
    c, _, _ = owner(); guest, _ = member(c)
    sid = workspace(guest, 'organization')
    guest_id = guest.get('/api/me').json()['id']
    assert c.post('/api/admin/users/delete', json=proof(user_id=guest_id, confirmation='friend')).status_code == 409
    response=guest.delete('/api/social/spaces/'+sid);assert response.status_code==200
    with m.db() as db:
        from app.workspace_lifecycle import purge
        purge(db,response.json()['deletion']['delete_at'])
    assert c.post('/api/admin/users/delete', json=proof(user_id=guest_id, confirmation='friend')).status_code == 200
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM app_settings WHERE key=?', (PREFIX + sid,)).fetchone()
