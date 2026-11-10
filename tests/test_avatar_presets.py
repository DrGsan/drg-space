import io
import json
from PIL import Image
from app.avatar_presets import CATEGORIES, PRESETS, artwork, default_profile
from app.i18n import LANGUAGES, catalog as translation_catalog
from app.modules.profile import valid_profile
from tests.test_security import owner, member, client, reset, m
from tests.test_admin import proof
from tests.test_avatar_upload import image_bytes


def test_every_preset_is_a_real_256px_image_and_only_nature_is_automatic():
    assert len(PRESETS) == 36
    for preset in PRESETS:
        with Image.open(io.BytesIO(artwork(preset))) as image:
            assert image.size == (256, 256) and image.format == 'WEBP'
    assert all(default_profile()['avatar_preset'].startswith('nature-') for _ in range(100))


def test_catalog_translates_every_label_without_changing_selection_or_artwork_ids():
    c, _, _ = owner()
    assert c.put('/api/account/avatar/preset', json={'preset': 'abstract-1'}).status_code == 200
    before = c.get('/api/me').json()['settings']
    expected_titles = {'ru': ['Абстракция', 'Пейзажи', 'Животные', 'Космос'],
                       'en': ['Abstract', 'Landscapes', 'Animals', 'Space']}
    for language in [*LANGUAGES, 'en', 'ru']:
        response = c.get('/api/avatar-presets', headers={
            'X-DrG-Language': language, 'Accept-Language': 'ru'})
        assert response.status_code == 200
        assert response.headers['content-language'] == language
        categories = response.json()['categories']
        translations = translation_catalog(language)
        if language in expected_titles:
            assert [category['name'] for category in categories] == expected_titles[language]
        assert [category['id'] for category in categories] == list(CATEGORIES)
        for category in categories:
            title_key, name_keys = CATEGORIES[category['id']]
            assert category['name'] == translations[title_key]
            assert len(category['items']) == len(name_keys) == 9
            for index, (item, name_key) in enumerate(zip(category['items'], name_keys), 1):
                # Direct indexing fails if a new language omits any dynamic catalog key.
                assert item == {'id': f"{category['id']}-{index}",
                                'name': translations[name_key],
                                'url': f"/api/avatar-presets/{category['id']}-{index}"}
        assert c.get('/api/me').json()['settings'] == before
        assert c.get('/api/account/avatar').content == artwork('abstract-1')
    assert c.get('/api/avatar-presets').json()['categories'][0]['name'] == 'Абстракция'


def test_all_creation_routes_assign_nature_without_media_and_removal_stays_removed():
    c, _, _ = owner(); g, _ = member(c)
    for account in (c, g):
        profile = account.get('/api/me').json()['settings']['profile']
        assert profile['avatar_preset'] in PRESETS and profile['avatar_preset'].startswith('nature-')
        assert account.get('/api/account/avatar').status_code == 200
        assert account.get('/api/media').json() == []
    created = c.post('/api/admin/users', json=proof(login='presetuser', password='Preset-user-password-123')).json()
    assert created['profile']['avatar_preset'].startswith('nature-')
    assert g.delete('/api/account/avatar').status_code == 200
    assert g.get('/api/account/avatar').status_code == 404
    assert 'avatar_preset' not in g.get('/api/me').json()['settings']['profile']
    assert g.put('/api/account/profile', json={'display_name':'Новое имя'}).status_code == 200
    assert g.get('/api/account/avatar').status_code == 404


def test_selection_preserves_settings_retires_upload_and_enforces_visibility():
    c, _, _ = owner(); g, _ = member(c); stranger, _ = member(c, 'stranger')
    uid = g.get('/api/me').json()['id']
    assert stranger.get('/api/users/'+uid+'/avatar').status_code == 404
    assert client().get('/api/avatar-presets').status_code == 401
    assert client().get('/api/avatar-presets/nature-1').status_code == 401
    catalog = g.get('/api/avatar-presets').json()['categories']
    assert [x['name'] for x in catalog] == ['Абстракция','Пейзажи','Животные','Космос']
    for category in catalog:
        assert len(category['items']) == 9
        for item in category['items']:
            assert g.get(item['url']).content == artwork(item['id'])
    uploaded = g.post('/api/account/avatar', content=image_bytes('PNG')).json()['avatar_id']
    assert g.put('/api/appearance', json={'style':'glass','wallpaper':uploaded}).status_code == 200
    for preset in ('abstract-3','nature-9','animals-7'):
        profile = g.put('/api/account/avatar/preset', json={'preset':preset}).json()
        assert profile == {'avatar_preset':preset}
        assert g.get('/api/account/avatar').content == artwork(preset)
        assert c.get('/api/users/'+uid+'/avatar').content == artwork(preset)
    # Uploaded wallpaper remains referenced; it must not be retired as an avatar.
    assert g.get('/api/media/'+uploaded).status_code == 200
    before = g.get('/api/me').json()['settings']
    for preset in ('nature-0', 'animals-10', '../../etc/passwd', '<svg>'):
        assert g.put('/api/account/avatar/preset', json={'preset':preset}).status_code == 422
    assert g.get('/api/me').json()['settings'] == before
    fresh = g.post('/api/account/avatar', content=image_bytes('PNG')).json()
    assert 'avatar_id' in fresh and 'avatar_preset' not in fresh
    g.put('/api/account/avatar/preset', json={'preset':'animals-1'})
    assert g.get('/api/media/'+fresh['avatar_id']).status_code == 404


def test_admin_select_upload_remove_and_legacy_name_changes_keep_preset():
    c, _, _ = owner(); g, _ = member(c); uid = g.get('/api/me').json()['id']
    path = '/api/admin/users/'+uid+'/profile'
    assert g.put(path,json=proof(avatar_preset='animals-1')).status_code == 403
    result = c.put(path,json=proof(avatar_preset='animals-9',display_name='Гость'))
    assert result.status_code == 200 and result.json()['profile']['avatar_preset'] == 'animals-9'
    assert c.put(path,json=proof(display_name='Другое имя',avatar=None,remove_avatar=False)).status_code == 200
    assert g.get('/api/me').json()['settings']['profile']['avatar_preset'] == 'animals-9'
    for data in ({'avatar_preset':'bad'},{'avatar_preset':'nature-1','remove_avatar':True},{'avatar_preset':'nature-1','avatar':''}):
        assert c.put(path,json=proof(**data)).status_code == 422
    assert c.put(path,json=proof(remove_avatar=True)).status_code == 200
    assert g.get('/api/account/avatar').status_code == 404
    assert c.post('/api/admin/users',json=proof(login='noavatar',password='No-avatar-password-123',remove_avatar=True)).json()['profile']['avatar_url'] is None
    assert c.post('/api/admin/users',json=proof(login='chosenavatar',password='Chosen-avatar-password-123',avatar_preset='abstract-2')).json()['profile']['avatar_preset'] == 'abstract-2'


def test_archive_profile_validation_accepts_presets_and_legacy_uploads():
    assert valid_profile({'avatar_preset':'nature-1','display_name':'Имя'},lambda _:False)
    assert valid_profile({'avatar_id':'owned'},lambda aid:aid=='owned')
    assert not valid_profile({'avatar_preset':'bad'},lambda _:True)
    assert not valid_profile({'avatar_preset':'nature-1','avatar_id':'owned'},lambda _:True)
    assert not valid_profile({'avatar_preset':[]},lambda _:True)
    # Existing accounts are never backfilled on read, save-name, or login.
    c, _, _ = owner(); uid = c.get('/api/me').json()['id']
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]); settings.pop('profile',None)
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    assert 'profile' not in c.get('/api/me').json()['settings']
    assert c.get('/api/account/avatar').status_code == 404


def test_delegated_admin_manages_member_presets_and_previews_without_friendship():
    from tests.test_site_roles import administrator
    c, _, _ = owner(); admin, admin_id = administrator(c); peer, _ = member(c, 'other')
    uid=peer.get('/api/me').json()['id']; path='/api/admin/users/'+uid
    result=admin.put(path+'/profile',json={'avatar_preset':'animals-4'})
    assert result.status_code==200,result.text
    url=result.json()['profile']['avatar_url']
    assert url.startswith(path+'/avatar?')
    assert admin.get(url).content==artwork('animals-4')
    assert admin.get('/api/users/'+uid+'/avatar').status_code==404  # Contact privacy is unchanged.
    assert peer.get(url).status_code==403 and client().get(url).status_code==401
    owner_id=c.get('/api/me').json()['id']
    assert admin.put('/api/admin/users/'+owner_id+'/profile',json={'avatar_preset':'nature-1'}).status_code==403
    assert admin.get('/api/admin/users/'+owner_id+'/avatar').status_code==403
    listed=next(u for u in admin.get('/api/admin/users').json()['users'] if u['id']==uid)
    assert admin.get(listed['profile']['avatar_url']).content==artwork('animals-4')
    # Older personal archives may reference an existing PNG from the library.
    png=image_bytes('PNG'); aid=peer.post('/api/media',content=png).json()['id']
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]);settings['profile']={'avatar_id':aid}
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    preview=admin.get(url);assert preview.content==png and preview.headers['content-type'].startswith('image/png')
    assert admin.post('/api/admin/access/lock').status_code==200
    assert admin.get(url).status_code==423
