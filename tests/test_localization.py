import concurrent.futures
import json
import pytest
from app.i18n import negotiate_language, system_text, current_language, identify_message
from tests.test_security import owner, member, client, reset, note, share
from app import main as m


def test_account_language_is_validated_persisted_and_isolated_without_changing_other_settings():
    c, _, _ = owner(); other, _ = member(c)
    before = c.get('/api/me').json()['settings']
    assert client().put('/api/language', json={'language': 'en'}).status_code == 401
    for payload in [{'language': 'eng'}, {'language': 'en-US'}, {'language': '../en'}, {'language': 'en', 'unexpected': True}]:
        assert c.put('/api/language', json=payload).status_code == 422
    for language in ('en', 'ru', 'system'):
        response = c.put('/api/language', json={'language': language})
        assert response.status_code == 200, response.text
        settings = c.get('/api/me').json()['settings']
        assert settings == {**before, 'language': language}
        assert 'language' not in other.get('/api/me').json()['settings']
    csrf = c.headers.pop('x-csrf-token')
    assert c.put('/api/language', json={'language': 'en'}).status_code == 403
    c.headers['x-csrf-token'] = csrf


def test_language_negotiation_uses_weights_region_fallback_and_rejects_bad_tags():
    assert negotiate_language('fr;q=1,en-US;q=0.9,ru;q=0.5') == 'en'
    assert negotiate_language('en;q=0,ru-RU;q=0.8') == 'ru'
    assert negotiate_language('en;q=oops,ru;q=1') == 'ru'
    assert negotiate_language('de,*;q=0.1') == 'ru'
    assert negotiate_language('../../en') == 'ru'


def test_errors_have_stable_codes_and_request_local_languages_in_parallel():
    def request(language):
        response = client().get('/api/notes', headers={'Accept-Language': language})
        assert response.status_code == 401
        assert response.headers['content-language'] == language
        return response.json()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(request, ['en', 'ru'] * 8))
    for index, result in enumerate(results):
        assert result['code'] == results[0]['code']
        assert result['params'] == {}
        assert result['detail'] == ('Sign in to your account.' if index % 2 == 0 else 'Войдите в аккаунт.')
    assert current_language.get() == 'ru'


def test_dynamic_errors_translate_parameters_but_successful_user_content_is_unchanged():
    c, _, _ = owner(); nid = note(c)
    title = 'Моя страница <img src=x onerror=alert(1)> & English'
    assert c.put('/api/notes/'+nid, json={'title': title, 'body': '# Русский текст\nEnglish text'}).status_code == 200
    assert c.put('/api/modules', json={'enabled': {'notes': False}}).status_code == 200
    response = c.get('/api/notes', headers={'Accept-Language': 'en'})
    assert response.status_code == 403
    assert response.json()['params']
    assert '{{' not in response.json()['detail']
    assert 'Заметки' not in response.json()['detail']
    assert response.json()['params']['module'] == 'notes'
    assert c.put('/api/modules', json={'enabled': {'notes': True}}).status_code == 200
    response = c.get('/api/notes/'+nid, headers={'Accept-Language': 'en'})
    assert response.json()['title'] == title
    assert response.json()['body'] == '# Русский текст\nEnglish text'
    token = share(c, nid, 'public')
    assert client().get('/api/shared/'+token, headers={'Accept-Language': 'en'}).json()['title'] == title


def test_language_preserves_personal_archive_compatibility_and_import_keeps_current_language():
    from tests.test_personal_data import export, unseal, restore
    c, _, _ = owner()
    assert c.put('/api/language', json={'language': 'en'}).status_code == 200
    body = export(c)
    manifest, _ = unseal(body)
    assert 'language' not in manifest['data']['settings']
    assert c.put('/api/language', json={'language': 'ru'}).status_code == 200
    response = restore(c, body)
    assert response.status_code == 200, response.text
    assert c.get('/api/me').json()['settings']['language'] == 'ru'


def test_explicit_tab_language_overrides_the_shared_browser_cookie():
    c = client()
    c.cookies.set('drg_language', 'ru')
    english = c.get('/api/notes', headers={'Accept-Language': 'ru-RU', 'X-DrG-Language': 'en-GB'})
    assert english.headers['content-language'] == 'en'
    assert english.json()['detail'] == 'Sign in to your account.'
    russian = c.get('/api/notes', headers={'Accept-Language': 'en-US'})
    assert russian.headers['content-language'] == 'ru'
    invalid = c.get('/api/notes', headers={'X-DrG-Language': '../en'})
    assert invalid.headers['content-language'] == 'ru'
def stored_default_name(login):
    with m.db() as connection:
        rows = connection.execute('SELECT l.payload FROM task_lists l JOIN users u ON u.id=l.owner_id WHERE u.login=? AND l.is_default=1',(login,)).fetchall()
    assert len(rows) == 1
    return json.loads(rows[0]['payload'])['name']


@pytest.mark.parametrize('header,expected', [
    ('en-US', 'Reminders'), ('ru-RU', 'Напоминания'),
    ('fr-FR,en-GB;q=0.8', 'Reminders'), ('de-DE', 'Напоминания')])
def test_initial_setup_seeds_default_list_in_browser_language(header, expected):
    c = client()
    response = c.post('/api/setup', headers={'Accept-Language': header}, json={
        'login': 'first-owner', 'password': 'Owner-password-123', 'token': 'test-setup-token'})
    assert response.status_code == 200, response.text
    # Check persistence before enrollment, dashboard or reminder requests can seed it.
    assert stored_default_name('first-owner') == expected


@pytest.mark.parametrize('invited', [False, True])
@pytest.mark.parametrize('language,expected', [('en-GB','Reminders'), ('ru-RU','Напоминания')])
def test_registration_seeds_default_list_and_later_requests_do_not_rename_it(invited, language, expected):
    manager, _, _ = owner()
    payload = {'login': 'new-member', 'password': 'Friend-password-123'}
    if invited:
        payload['invite'] = manager.post('/api/invites',json={}).json()['url'].split('/')[-1]
    else:
        assert manager.put('/api/admin/registration',json={'enabled':True}).status_code == 200
    newcomer = client()
    # The current tab's language takes precedence over another tab's cookie.
    newcomer.cookies.set('drg_language', 'ru' if language.startswith('en') else 'en')
    response = newcomer.post('/api/register',headers={'X-DrG-Language':language},json=payload)
    assert response.status_code == 200, response.text
    assert stored_default_name('new-member') == expected
    newcomer.headers['x-csrf-token'] = response.json()['csrf']
    response = newcomer.post('/api/2fa/skip')
    assert response.status_code == 200, response.text
    newcomer.headers['x-csrf-token'] = response.json()['csrf']
    for selected in ('ru','en'):
        assert newcomer.put('/api/language',json={'language':selected}).status_code == 200
        lists = newcomer.get('/api/task-lists',headers={'X-DrG-Language':selected}).json()
        assert len(lists) == 1 and lists[0]['name'] == expected
    response = newcomer.patch('/api/task-lists/'+lists[0]['id'],json={'name':'Мои дела & English'})
    assert response.status_code == 200, response.text
    assert newcomer.get('/api/task-lists',headers={'X-DrG-Language':'en'}).json()[0]['name'] == 'Мои дела & English'


@pytest.mark.parametrize('account_language,request_language,expected', [
    ('system','en-US','Reminders'), ('en','ru-RU','Reminders'), ('ru','en-GB','Напоминания')])
def test_managed_account_first_visit_uses_its_own_language(account_language, request_language, expected):
    manager, _, _ = owner()
    response = manager.post('/api/admin/users',json={'login':'managed-newcomer','password':'Member-password-123'})
    assert response.status_code == 200, response.text
    newcomer = client()
    response = newcomer.post('/api/login',json={'login':'managed-newcomer','password':'Member-password-123'})
    assert response.status_code == 200, response.text
    newcomer.headers['x-csrf-token'] = response.json()['csrf']
    assert newcomer.put('/api/language',json={'language':account_language}).status_code == 200
    lists = newcomer.get('/api/task-lists',headers={'X-DrG-Language':request_language}).json()
    assert len(lists) == 1 and lists[0]['name'] == expected
    assert stored_default_name('managed-newcomer') == expected
