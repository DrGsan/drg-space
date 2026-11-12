import copy
import json
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import pytest

from app.file_store import file_store
from app.version import APP_RELEASE, release_for_language, release_from_changelog, validate_release_notes
from tests.test_security import owner, member, client, share, reset
from app import main as m
from tests.test_notion_sync_service import initialized, imported, ROOT


def test_synced_files_download_without_editing_provider_requests_or_new_imports():
    c, session, provider, service, stamp = initialized()
    names = ['TEST.conf', 'Тестовый файл с пробелами.yaml', 'manual.pdf']
    provider.blocks[ROOT] = [
        {'id': f'{i+1:08x}-aaaa-4aaa-8aaa-000000000001', 'type': 'file', 'has_children': False,
         'file': {'type': 'external', 'name': name, 'external': {'url': 'https://files.example/' + quote(name)}, 'caption': []}}
        for i, name in enumerate(names)
    ]
    root, mapping = imported(service, session)
    nid = mapping[ROOT]
    before = c.get('/api/notes/' + nid).json()
    assert before['notion_sync']
    assert c.put('/api/notes/' + nid, json={'title': 'Blocked edit', 'body': ''}).status_code == 423
    provider.status = 503
    downloads = len(provider.downloads)
    guest, _ = member(c)
    token = share(c, nid, 'users', allowed=['friend'])
    for block, expected in zip(before['blocks'], names):
        path = urlsplit(block['props']['url']).path
        for method in ('head', 'get'):
            assert getattr(client(), method)(path).status_code == 401
            assert getattr(guest, method)(path).status_code == 404
            result = getattr(c, method)(path)
            assert result.status_code == 200, result.text
            assert unquote(result.headers['content-disposition'].split("filename*=UTF-8''")[1]) == expected
            if method == 'get':
                assert result.content == b'private attachment bytes'
        assert c.head(path, headers={'Range': 'bytes=0-3'}).status_code == 206
        published = '/api/shared/' + token + '/files/' + path.rsplit('/', 1)[-1]
        assert guest.head(published).status_code == 200
        assert guest.get(published).content == b'private attachment bytes'
    after = c.get('/api/notes/' + nid).json()
    assert (after['blocks'], after['version']) == (before['blocks'], before['version'])
    assert len(provider.downloads) == downloads
    path = urlsplit(before['blocks'][0]['props']['url']).path
    with m.db() as db:
        row = db.execute('SELECT object_id FROM files WHERE id=?', (path.rsplit('/', 1)[-1],)).fetchone()
        file_store(m.db, m.DATA).delete(row['object_id'])
    result = c.head(path)
    assert result.status_code == 404


def test_current_and_reported_historical_release_have_real_translated_materials():
    historic = release_from_changelog(Path('CHANGELOG.md').read_text(), '5.0.2')
    for release in (APP_RELEASE, historic):
        validate_release_notes(release)
        ru = release_for_language(release, 'ru')
        assert ru['version'] == release['version'] and ru['date'] == release['date']
        assert ru['title'] != release['title'] and len(ru['changes']) == len(release['changes'])
        assert all(any('\u0400' <= char <= '\u04ff' for char in text) for text in [ru['title'], *ru['changes']])
        assert release_for_language(release, 'en') == release
    ru = release_for_language(APP_RELEASE, 'ru')
    ru['changes'].clear()
    assert release_for_language(APP_RELEASE, 'ru')['changes']


def test_release_language_uses_account_preference_and_has_no_cross_request_cache():
    c, _, _ = owner()
    other, _ = member(c)
    for selected, requested, expected in [('ru', 'en', 'ru'), ('en', 'ru', 'en'), ('system', 'en', 'en'), ('system', 'ru', 'ru')]:
        assert c.put('/api/language', json={'language': selected}).status_code == 200
        result = c.get('/api/me', headers={'X-DrG-Language': requested}).json()
        assert result['app_release'] == release_for_language(APP_RELEASE, expected)
        assert other.get('/api/me', headers={'X-DrG-Language': 'ru'}).json()['app_release'] == release_for_language(APP_RELEASE, 'ru')
        assert other.get('/api/me', headers={'X-DrG-Language': 'en'}).json()['app_release'] == release_for_language(APP_RELEASE, 'en')


def test_release_validation_rejects_missing_translation_and_stale_english_source(monkeypatch):
    import app.version as version
    actual = version.catalog
    def missing(language, namespace):
        table = dict(actual(language, namespace))
        if language == 'ru':
            import re
            prefix = 'release' + re.sub('[^A-Za-z0-9]', '_', APP_RELEASE['version'])
            table.pop(prefix + 'Title')
        return table
    monkeypatch.setattr(version, 'catalog', missing)
    with pytest.raises(ValueError, match='Missing release notes'):
        validate_release_notes(APP_RELEASE)
    monkeypatch.setattr(version, 'catalog', actual)
    altered = copy.deepcopy(APP_RELEASE)
    altered['changes'][0] = 'A changed source without updated catalogs'
    with pytest.raises(ValueError, match='differs from CHANGELOG'):
        validate_release_notes(altered)
