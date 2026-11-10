"""Bundled backgrounds and account initialization preserve user choices."""
import json
from pathlib import Path

import pytest
from PIL import Image

from app import wallpaper_presets
from app.modules import appearance
from app.i18n import LANGUAGES, catalog as translations
from tests.test_security import m, owner, client, enroll, reset
from tests.test_admin import proof
from tests.test_personal_data import export, unseal, headers
from tests.test_storage_encryption import databases, seed, backup_service


def stored_settings(login):
    with m.db() as c:
        return json.loads(c.execute('SELECT settings FROM users WHERE login=?', (login,)).fetchone()[0])


def test_catalog_has_four_localized_groups_and_all_artwork_is_real():
    assert [category['id'] for category in wallpaper_presets.CATEGORIES] == ['nature', 'space', 'abstract', 'cozy']
    ids = [item['id'] for category in wallpaper_presets.CATEGORIES for item in category['items']]
    assert len(ids) == len(set(ids)) == 24
    assert [len(category['items']) for category in wallpaper_presets.CATEGORIES] == [6, 6, 6, 6]
    assert {'warm', 'forest', 'coast', 'lavender', 'mountains', 'dunes'} <= set(ids)
    for category in wallpaper_presets.CATEGORIES:
        for language in LANGUAGES:
            messages = translations(language)
            assert messages[category['name_key']]
            assert all(messages[item['name_key']] for item in category['items'])
    for identity in wallpaper_presets.PRESETS:
        with Image.open(Path('web/public/assets/wallpapers') / (identity + '.webp')) as image:
            assert image.format == 'WEBP'
            assert image.width >= 1000 and image.width > image.height
            image.verify()


def test_catalog_endpoint_translates_labels_without_mutating_saved_preferences():
    c, _, _ = owner()
    before = stored_settings('owner')
    expected = {'ru': ['Природа', 'Космос', 'Абстракция', 'Уют'], 'en': ['Nature', 'Space', 'Abstract', 'Cozy']}
    for language, names in expected.items():
        response = c.get('/api/appearance', headers={'X-DrG-Language': language}).json()
        categories = response['wallpaper_categories']
        assert [category['name'] for category in categories] == names
        assert {item['id'] for category in categories for item in category['items']} == set(wallpaper_presets.ORDERED_PRESETS)
        for category in categories:
            assert all(item['url'] == '/assets/wallpapers/' + item['id'] + '.webp' for item in category['items'])
        assert response['preferences'] == before['appearance']
    assert stored_settings('owner') == before
    assert client().get('/api/appearance').status_code == 401


def test_random_selection_uses_all_photos_once_without_gradients_or_weighting(monkeypatch):
    pools = []
    def choose(values):
        pools.append(tuple(values))
        return values[-1]
    monkeypatch.setattr(wallpaper_presets.secrets, 'choice', choose)
    result = appearance.new_account_appearance()
    assert result['wallpaper'] == 'cozy-evening'
    assert len(pools) == 1 and len(pools[0]) == len(set(pools[0])) == 24
    assert set(pools[0]) == set(wallpaper_presets.ORDERED_PRESETS)
    assert not set(pools[0]) & {'dusk', 'mist', 'none', 'nature-coast'}


def test_retired_wallpaper_stays_readable_without_returning_to_the_picker():
    c, _, _ = owner()
    response = c.put('/api/appearance', json={'wallpaper': 'nature-coast'})
    assert response.status_code == 200
    response = c.put('/api/appearance', json={'theme': 'rose'})
    assert response.status_code == 200
    assert response.json()['wallpaper'] == 'nature-coast'
    data = c.get('/api/appearance').json()
    assert data['preferences']['wallpaper'] == 'nature-coast'
    assert all(item['id'] != 'nature-coast' for category in data['wallpaper_categories'] for item in category['items'])


@pytest.mark.parametrize(('creation', 'selection'), [
    ('setup', 'nature-waterfall'),
    ('register', 'space-rings'),
    ('invite', 'abstract-glass'),
    ('workspace-invite', 'cozy-rain'),
    ('admin', 'space-stars'),
])
def test_every_account_creation_persists_one_choice_and_other_devices_keep_it(monkeypatch, creation, selection):
    administrator = None if creation == 'setup' else owner()[0]
    choices = []
    def choose():
        choices.append(selection)
        return selection
    monkeypatch.setattr(appearance, 'default_wallpaper', choose)
    login = 'owner' if creation == 'setup' else 'new-wallpaper-user'
    password = 'Owner-password-123' if creation == 'setup' else 'New-wallpaper-password-123'
    codes = None
    if creation == 'setup':
        account, _, codes = owner()
    elif creation == 'admin':
        response = administrator.post('/api/admin/users', json=proof(login=login, password=password, remove_avatar=True))
        assert response.status_code == 200, response.text
        account = client()
        response = account.post('/api/login', json={'login': login, 'password': password})
        assert response.status_code == 200, response.text
        account.headers['x-csrf-token'] = response.json()['csrf']
    else:
        body = {'login': login, 'password': password}
        if creation == 'register':
            assert administrator.put('/api/admin/registration', json={'enabled': True}).status_code == 200
        elif creation == 'invite':
            response = administrator.post('/api/invites', json={'login': login})
            assert response.status_code == 200, response.text
            body['invite'] = response.json()['url'].split('/')[-1]
        else:
            from tests.test_people_groups import workspace
            identity = workspace(administrator, 'organization')
            response = administrator.post('/api/social/spaces/' + identity + '/invite-links')
            assert response.status_code == 200, response.text
            body['invite'] = response.json()['url'].split('/')[-1]
        account = client()
        response = account.post('/api/register', json=body)
        assert response.status_code == 200, response.text
        account.headers['x-csrf-token'] = response.json()['csrf']
        _, codes = enroll(account, password)
    before = stored_settings(login)
    assert before['appearance']['wallpaper'] == selection
    assert choices == [selection]
    monkeypatch.setattr(appearance, 'default_wallpaper', lambda: pytest.fail('An existing account was randomized again'))
    for _ in range(2):
        assert account.get('/api/appearance').json()['preferences'] == before['appearance']
        assert account.get('/api/me').json()['settings']['appearance'] == before['appearance']
    assert stored_settings(login) == before
    another_device = client()
    response = another_device.post('/api/login', json={'login': login, 'password': password})
    assert response.status_code == 200, response.text
    another_device.headers['x-csrf-token'] = response.json()['csrf']
    if codes:
        response = another_device.post('/api/2fa/verify', json={'code': codes[0]})
        assert response.status_code == 200, response.text
        another_device.headers['x-csrf-token'] = response.json()['csrf']
    assert another_device.get('/api/me').json()['settings']['appearance'] == before['appearance']
    saved = another_device.put('/api/appearance', json={'theme': 'sage'})
    assert saved.status_code == 200, saved.text
    assert saved.json()['wallpaper'] == selection
    assert account.get('/api/appearance').json()['preferences']['wallpaper'] == selection


def test_legacy_account_without_appearance_keeps_deterministic_default_and_storage(monkeypatch):
    c, _, _ = owner()
    before = stored_settings('owner')
    before.pop('appearance')
    with m.db() as db:
        db.execute('UPDATE users SET settings=? WHERE login=?', (json.dumps(before), 'owner'))
    monkeypatch.setattr(appearance, 'default_wallpaper', lambda: pytest.fail('Legacy account must not be backfilled'))
    for _ in range(2):
        assert c.get('/api/me').json()['settings']['appearance']['wallpaper'] == 'dunes'
        assert c.get('/api/appearance').json()['preferences']['wallpaper'] == 'dunes'
    assert stored_settings('owner') == before


@pytest.mark.parametrize('selection', ['nature-waterfall', 'space-rings', 'abstract-glass', 'cozy-rain', 'nature-coast'])
def test_personal_export_import_preserves_new_builtin_as_catalog_id_without_media(monkeypatch, selection):
    from tests.test_security import member
    c, _, _ = owner()
    other, _ = member(c)
    assert c.put('/api/appearance', json={'wallpaper': selection}).status_code == 200
    archive = export(c)
    manifest, files = unseal(archive)
    assert manifest['data']['settings']['appearance']['wallpaper'] == selection
    assert manifest['data']['media'] == [] and not files
    monkeypatch.setattr(appearance, 'default_wallpaper', lambda: pytest.fail('Import must not randomize'))
    result = other.post('/api/account/data/import', content=archive, headers=headers(password='Friend-password-123'))
    assert result.status_code == 200, result.text
    assert other.get('/api/appearance').json()['preferences']['wallpaper'] == selection
    assert other.get('/api/media').json() == []


def test_full_encrypted_backup_preserves_saved_builtin_and_legacy_settings(databases, monkeypatch):
    source = databases()
    seed(source)
    expected = appearance.Appearance(wallpaper='space-nebula', theme='rose', mode='dark').model_dump()
    with source.connection() as c:
        row = c.execute("SELECT settings,password FROM users WHERE login='owner'").fetchone()
        legacy = json.loads(row['settings'])
        selected = legacy | {'appearance': expected}
        c.execute('UPDATE users SET settings=? WHERE login=?', (json.dumps(selected), 'owner'))
        c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',
                  ('c'*32, 'legacy', row['password'], 'member', json.dumps(legacy), 2))
    service = backup_service(source)
    with service.temp() as directory:
        metadata = service.snapshot(directory)
        body = service.encrypt(directory, metadata, 'Wallpaper-backup-password-123').read_bytes()
    target = databases()
    restored = backup_service(target)
    monkeypatch.setattr(appearance, 'default_wallpaper', lambda: pytest.fail('Restore must not randomize'))
    with restored.temp() as directory:
        metadata = restored.prepare(body, 'Wallpaper-backup-password-123', directory)
        assert restored.restore(directory, metadata) == 'owner'
    with target.connection() as c:
        assert json.loads(c.execute("SELECT settings FROM users WHERE login='owner'").fetchone()[0]) == selected
        assert json.loads(c.execute("SELECT settings FROM users WHERE login='legacy'").fetchone()[0]) == legacy
