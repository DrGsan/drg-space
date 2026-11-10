"""Holiday preference persistence remains compatible with older clients."""
import json
import pytest
from tests.test_security import m, owner, client, reset


def settings(uid):
    with m.db() as c:
        return json.loads(c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()[0])


def test_legacy_accounts_default_to_auto_without_read_rewriting_settings():
    account, _, _ = owner()
    me = account.get('/api/me').json()
    assert me['settings']['appearance']['seasonal'] == 'auto'
    legacy = settings(me['id'])
    legacy['appearance'].pop('seasonal')
    with m.db() as c:
        c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(legacy), me['id']))
    assert account.get('/api/appearance').json()['preferences']['seasonal'] == 'auto'
    assert settings(me['id']) == legacy


def test_off_survives_partial_native_writes_and_auto_can_be_restored():
    account, _, _ = owner()
    initial = account.get('/api/appearance').json()['preferences']
    saved = account.put('/api/appearance', json={'seasonal': 'off'})
    assert saved.status_code == 200
    assert saved.json() == {**initial, 'seasonal': 'off'}
    native = account.put('/api/appearance', json={'theme': 'sage', 'mode': 'dark'})
    assert native.status_code == 200
    assert native.json() == {**initial, 'seasonal': 'off', 'theme': 'sage', 'mode': 'dark'}
    assert account.get('/api/appearance').json()['preferences'] == native.json()
    restored = account.put('/api/appearance', json={'seasonal': 'auto'})
    assert restored.status_code == 200
    assert restored.json() == {**native.json(), 'seasonal': 'auto'}


@pytest.mark.parametrize('invalid', [{'seasonal': 'halloween'}, {'seasonal': True}, {'seasonal': None}, {'seasonal_preview': 'space'}])
def test_preview_and_invalid_modes_cannot_be_persisted(invalid):
    account, _, _ = owner()
    before = account.get('/api/me').json()['settings']
    assert account.put('/api/appearance', json=invalid).status_code == 422
    assert account.get('/api/me').json()['settings'] == before
    assert client().put('/api/appearance', json={'seasonal': 'off'}).status_code in (401, 403)
