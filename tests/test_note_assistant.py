import json

import httpx
import pytest

from tests.test_security import member, note, owner, reset, share
from app import main
from app.modules import note_assistant as ai

ROOT = '/api/notes-assistant'
ADMIN = '/api/admin/notes-assistant'


def account_id(client):
    return client.get('/api/me').json()['id']


def ref(page):
    return {key: page[key] for key in ('id', 'version')}


def changes(**kwargs):
    return ai.Changes(**dict.fromkeys(ai.Changes.model_fields) | kwargs)


def suggest(client, **kwargs):
    response = client.post(ROOT + '/propose', json={'prompt': 'Synthetic instruction', **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def configure(client, uid):
    response = client.put(ADMIN, json={'user_id': uid})
    assert response.status_code == 200, response.text


@pytest.fixture
def active(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-key-for-local-tests')
    client, _, _ = owner()
    configure(client, account_id(client))
    return client


def test_create_preview_cancel_select_apply_and_prevent_replay(active, monkeypatch):
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: changes(title='Draft', icon='📝', cover='forest', append='First.\nSecond.'))
    preview = suggest(active)
    assert active.get('/api/notes').json() == []
    assert active.delete(ROOT + '/draft/' + preview['draft']['id']).status_code == 200
    payload = {'draft': preview['draft'], 'fields': ['title', 'append', 'icon']}
    assert active.post(ROOT + '/apply', json=payload).status_code == 409
    payload['draft'] = suggest(active)['draft']
    response = active.post(ROOT + '/apply', json=payload)
    assert response.status_code == 200, response.text
    saved = active.get('/api/notes/' + response.json()['id']).json()
    assert saved['title'] == 'Draft' and saved['icon'] == '📝' and saved['cover'] == ''
    assert [b['content'][0]['text'] for b in saved['blocks']] == ['First.', 'Second.']
    assert active.post(ROOT + '/apply', json=payload).status_code == 409
    assert len(active.get('/api/notes').json()) == 1


def test_edit_preserves_rich_blocks_and_rejects_changed_version(active, monkeypatch):
    paragraph = ai.rich_paragraph('Original sentence')
    block = {'type': 'codeBlock', 'id': 'code', 'props': {'language': 'python'}, 'content': 'print(1)', 'children': []}
    page = active.post('/api/notes', json={'title': 'Original', 'body': 'Original sentence\n\n```python\nprint(1)\n```', 'blocks': [paragraph, block]}).json()
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: changes(title='Renamed', cover='mountains', replace_before='Original sentence', replace_after='Updated sentence'))
    preview = suggest(active, target=ref(page), include_target_text=True)
    assert preview['before'] == {'title': 'Original', 'cover': ''}
    assert active.get('/api/notes/' + page['id']).json()['title'] == 'Original'
    data = {'draft': preview['draft'], 'fields': preview['fields']}
    data['draft']['changes']['title'] = 'Tampered'
    assert active.post(ROOT + '/apply', json=data).status_code == 409
    data['draft']['changes']['title'] = 'Renamed'
    response = active.post(ROOT + '/apply', json=data)
    assert response.status_code == 200, response.text
    saved = active.get('/api/notes/' + page['id']).json()
    assert saved['version'] == page['version'] + 1
    assert saved['title'] == 'Renamed' and saved['cover_url'].endswith('/mountains.webp')
    assert saved['blocks'][0]['id'] == paragraph['id'] and saved['blocks'][0]['content'][0]['text'] == 'Updated sentence'
    assert saved['blocks'][1] == block
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: changes(title='Assistant title'))
    stale = suggest(active, target=ref(saved))
    result = active.put('/api/notes/' + page['id'], json={'title': 'Manual edit', 'body': saved['body'], 'blocks': saved['blocks'], 'version': saved['version']})
    assert result.status_code == 200, result.text
    assert active.post(ROOT + '/apply', json={'draft': stale['draft'], 'fields': stale['fields']}).status_code == 409
    assert active.get('/api/notes/' + page['id']).json()['title'] == 'Manual edit'


def test_scopes_and_disabling(active, monkeypatch):
    guest, _ = member(active)
    foreign = guest.get('/api/notes/' + note(guest)).json()
    public = active.get('/api/notes/' + note(active)).json()
    share(active, public['id'], 'public')
    assert active.get(ROOT + '/pages').json() == {'items': []}
    assert guest.get(ROOT).json() == {'enabled': False}
    assert guest.get(ADMIN).status_code == 403
    assert guest.post(ROOT + '/propose', json={'prompt': 'test'}).status_code == 403
    for unavailable in (foreign, public):
        for kind in ('target', 'source'):
            assert active.post(ROOT + '/context', json={kind: ref(unavailable)}).status_code == 404
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: changes(title='Draft', append='Text'))
    preview = suggest(active)
    configure(active, account_id(guest))
    assert active.post(ROOT + '/apply', json={'draft': preview['draft'], 'fields': preview['fields']}).status_code == 403
    assert guest.get(ROOT).json() == {'enabled': True}
    configure(active, None)
    assert guest.get(ROOT).json() == {'enabled': False}
    assert active.get('/api/notes/' + public['id']).status_code == 200


def test_notion_read_only_and_provider_failure_do_not_save(active, monkeypatch):
    imported = active.post('/api/notes', json={'title': 'Imported', 'body': 'Source text ![asset](https://private.invalid/image)'}).json()
    uid = account_id(active)
    with main.db() as c:
        stamp = main.now()
        c.execute('INSERT INTO notion_connections(id,user_id,kind,workspace_id,bot_id,payload,created,updated) VALUES(?,?,?,?,?,?,?,?)', ('test-conn', uid, 'personal', 'ws', 'bot', '{}', stamp, stamp))
        c.execute('INSERT INTO notion_roots(id,user_id,connection_id,notion_id,note_id,mode,payload,created,updated) VALUES(?,?,?,?,?,?,?,?,?)', ('test-root', uid, 'test-conn', 'root', imported['id'], 'duplex', '{}', stamp, stamp))
        c.execute('INSERT INTO notion_pages(id,user_id,root_id,notion_id,note_id,payload,updated,writable) VALUES(?,?,?,?,?,?,?,?)', ('test-page', uid, 'test-root', 'root', imported['id'], '{}', stamp, 1))
    assert active.post(ROOT + '/context', json={'target': ref(imported)}).status_code == 404
    seen = []

    def timeout(key, prompt, *, target, source):
        seen.append((target, source))
        raise httpx.ReadTimeout('Provider secret error')

    monkeypatch.setattr(ai, 'proposal', timeout)
    response = active.post(ROOT + '/propose', json={'prompt': 'Use source', 'source': ref(imported)})
    assert response.status_code == 502 and 'secret' not in response.text
    assert seen == [(None, {'title': 'Imported', 'excerpt': 'Source text '})]
    assert len(active.get('/api/notes').json()) == 1
    monkeypatch.delenv('OPENAI_API_KEY')
    assert active.get(ROOT).json() == {'enabled': False}
    assert active.get('/api/notes').status_code == 200


def test_provider_request_opts_out_of_storage_and_uses_no_tools(monkeypatch):
    def handler(request):
        args = json.loads(request.content)
        assert request.url == 'https://api.openai.com/v1/responses'
        assert request.headers['Authorization'] == 'Bearer synthetic-key'
        assert args['store'] is False and 'tools' not in args
        assert args['text']['format']['strict'] is True
        assert json.loads(args['input']) == {'prompt': 'Synthetic prompt', 'target': None, 'source': None}
        text = changes(title='Synthetic', append='Sample').model_dump_json()
        return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]})

    client = httpx.Client
    monkeypatch.setattr(ai.httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    assert ai.proposal('synthetic-key', 'Synthetic prompt', target=None, source=None).title == 'Synthetic'


def test_simple_legacy_paragraphs_can_change_but_attachment_references_are_rejected(active, monkeypatch):
    old = 'Original sentence.'
    page = active.post('/api/notes', json={'title': 'Legacy', 'body': old}).json()
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: changes(append='/api/notes/another_note_id/files/another_file_id'))
    assert active.post(ROOT + '/propose', json={'prompt': 'Test', 'target': ref(page)}).status_code == 502
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: changes(replace_before=old, replace_after='Updated sentence'))
    preview = suggest(active, target=ref(page), include_target_text=True)
    assert active.post(ROOT + '/apply', json={'draft': preview['draft'], 'fields': preview['fields']}).status_code == 200
    assert active.get('/api/notes/' + page['id']).json()['body'] == 'Updated sentence'
