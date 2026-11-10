import copy
import pytest
from tests.test_security import m, owner, member, client, reset
from tests.test_widgets import image_bytes
from tests.test_personal_data import export, unseal, headers
from app.modules.appearance import Appearance, WALLPAPER_PRESETS


def test_accounts_keep_their_initial_glass_wallpaper_without_rewriting_stored_settings():
    c, _, _ = owner()
    before = c.get('/api/me').json()['settings']
    uid=c.get('/api/me').json()['id']
    with m.db() as db:stored=db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]
    preferences = c.get('/api/appearance').json()['preferences']
    assert Appearance.model_validate(preferences).style == 'glass'
    assert Appearance.model_validate(preferences).wallpaper in WALLPAPER_PRESETS
    assert c.get('/api/me').json()['settings'] == before
    assert before['appearance']==preferences
    with m.db() as db:assert db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]==stored
    assert client().get('/api/appearance').status_code == 401


def test_built_in_wallpapers_save_without_personal_media_and_survive_legacy_writes():
    c, _, _ = owner()
    assert {'warm','forest','coast','lavender','mountains','dunes'} <= WALLPAPER_PRESETS
    assert len(WALLPAPER_PRESETS) == 25
    for wallpaper in WALLPAPER_PRESETS:
        result = c.put('/api/appearance', json={'style':'glass','wallpaper':wallpaper})
        assert result.status_code == 200, result.text
        assert result.json()['wallpaper'] == wallpaper
        assert c.put('/api/appearance', json={'theme':'sage'}).json()['wallpaper'] == wallpaper
    assert c.get('/api/media').json() == []


def test_legacy_native_writes_preserve_web_style_wallpaper_and_unrelated_data():
    c, _, _ = owner()
    layout = c.get('/api/dashboard/layout').json()
    assert c.put('/api/appearance', json={'theme':'amber','mode':'light','style':'glass','wallpaper':'mist'}).status_code == 200
    saved = c.put('/api/appearance', json={'theme':'sage','mode':'dark','day_start':'08:00','night_start':'21:00'})
    assert saved.status_code == 200
    assert saved.json() == {'theme':'sage','mode':'dark','day_start':'08:00','night_start':'21:00','style':'glass','wallpaper':'mist','seasonal':'auto'}
    assert c.get('/api/dashboard/layout').json() == layout
    assert c.put('/api/appearance', json={'style':'classic'}).json()['wallpaper'] == 'mist'
    assert c.get('/api/me').json()['settings']['appearance']['theme'] == 'sage'


def test_wallpaper_is_owned_private_media_and_invalid_inputs_do_not_change_preferences():
    c, _, _ = owner(); other, _ = member(c)
    asset = c.post('/api/media', content=image_bytes()).json()['id']
    foreign = other.post('/api/media', content=image_bytes()).json()['id']
    saved = c.put('/api/appearance', json={'style':'glass','wallpaper':asset}).json()
    assert saved['wallpaper'] == asset
    assert other.get('/api/media/'+asset).status_code == 404
    assert client().get('/api/media/'+asset).status_code == 401
    for data in [{'wallpaper':foreign},{'wallpaper':'0'*32},{'wallpaper':'https://example.com/photo.jpg'},
                 {'wallpaper':'url(javascript:alert(1))'},{'style':'unknown'},{'wallpaper':None},{'style':False}]:
        assert c.put('/api/appearance', json=data).status_code == 422
        assert c.get('/api/appearance').json()['preferences'] == saved
    assert c.delete('/api/media/'+asset).status_code == 409
    assert c.put('/api/appearance', json={'wallpaper':'warm'}).status_code == 200
    assert c.delete('/api/media/'+asset).status_code == 200


def test_avatar_replacement_cannot_delete_an_image_used_as_wallpaper():
    c, _, _ = owner()
    avatar = c.post('/api/account/avatar', content=image_bytes()).json()['avatar_id']
    assert c.put('/api/appearance', json={'style':'glass','wallpaper':avatar}).status_code == 200
    assert c.delete('/api/account/avatar').status_code == 200
    assert c.get('/api/media/'+avatar).status_code == 200
    assert c.get('/api/appearance').json()['preferences']['wallpaper'] == avatar


def test_removing_an_event_photo_preserves_its_selected_wallpaper():
    from tests.test_next_features import event
    c, _, _ = owner()
    e = event(c)
    photo = c.post('/api/expenses/'+e['id']+'/photos', content=image_bytes()).json()['photos'][0]
    assert c.put('/api/appearance', json={'style':'glass','wallpaper':photo}).status_code == 200
    assert c.delete('/api/expenses/'+e['id']+'/photos/'+photo).status_code == 200
    assert c.get('/api/media/'+photo).status_code == 200
    assert c.get('/api/appearance').json()['preferences']['wallpaper'] == photo


def test_personal_archive_remaps_custom_wallpaper_and_keeps_other_account_private():
    c, _, _ = owner(); other, _ = member(c)
    body = image_bytes()
    asset = c.post('/api/media', content=body).json()['id']
    assert c.put('/api/appearance', json={'theme':'amber','style':'glass','wallpaper':asset}).status_code == 200
    archive = export(c)
    manifest, _ = unseal(archive)
    assert manifest['data']['settings']['appearance']['wallpaper'] == asset
    imported = other.post('/api/account/data/import', content=archive, headers=headers(password='Friend-password-123'))
    assert imported.status_code == 200, imported.text
    settings = other.get('/api/me').json()['settings']['appearance']
    assert settings['style'] == 'glass' and settings['theme'] == 'amber'
    assert settings['wallpaper'] != asset
    assert other.get('/api/media/'+settings['wallpaper']).content == body
    assert c.get('/api/media/'+settings['wallpaper']).status_code == 404


def test_personal_archive_validation_rejects_missing_wallpaper_and_accepts_legacy_preferences():
    from app.personal_data import validate_settings
    # Validation is also exercised through the complete encrypted import above.
    # Use the same validator to cover an old archive and a forged asset reference.
    legacy = {'appearance':{'theme':'ocean','mode':'light','day_start':'07:00','night_start':'22:00'}}
    assert validate_settings(copy.deepcopy(legacy), {}, {})['appearance']['style'] == 'glass'
    forged = copy.deepcopy(legacy)
    forged['appearance']['wallpaper'] = '0'*32
    from app.backups import BackupError
    with pytest.raises(BackupError):validate_settings(forged, {}, {})


def test_native_and_web_receive_same_defaults_for_new_and_legacy_accounts():
    import json
    c,_,_=owner(); other,_=member(c)
    for account in [c,other]:
        appearance=account.get('/api/me').json()['settings']['appearance']
        assert appearance==account.get('/api/appearance').json()['preferences']
        assert appearance['style']=='glass' and appearance['wallpaper'] in WALLPAPER_PRESETS
    uid=other.get('/api/me').json()['id']
    legacy={'theme':'sage','mode':'schedule','day_start':'08:00','night_start':'20:00'}
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]);settings['appearance']=legacy
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    resolved=other.get('/api/me').json()['settings']['appearance']
    assert resolved=={**legacy,'style':'glass','wallpaper':'dunes','seasonal':'auto'}
    with m.db() as db:assert json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])['appearance']==legacy
    assert other.put('/api/appearance',json={'theme':'rose'}).json()=={**resolved,'theme':'rose'}


def test_explicit_classic_and_selected_wallpapers_still_override_default():
    c,_,_=owner()
    for style,wallpaper in [('classic','none'),('glass','coast'),('glass','mountains')]:
        saved=c.put('/api/appearance',json={'style':style,'wallpaper':wallpaper,'theme':'amber','mode':'dark'}).json()
        assert saved['style']==style and saved['wallpaper']==wallpaper
        assert c.get('/api/me').json()['settings']['appearance']==saved
        assert c.get('/api/appearance').json()['preferences']==saved
        assert c.put('/api/appearance',json={'day_start':'08:00'}).json()=={**saved,'day_start':'08:00'}
