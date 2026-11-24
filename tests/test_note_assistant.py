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


def reply(intent, **kwargs):
    return ai.Reply(intent=intent, message='Assistant reply', changes=changes(**kwargs))


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


def test_create_preview_cancel_apply_and_prevent_replay(active, monkeypatch):
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('create', title='Draft', icon='📝', cover='forest', append='First.\nSecond.'))
    preview = suggest(active)
    assert active.get('/api/notes').json() == []
    assert active.delete(ROOT + '/draft/' + preview['draft']['id']).status_code == 200
    payload = {'draft': preview['draft']}
    assert active.post(ROOT + '/apply', json=payload).status_code == 409
    payload['draft'] = suggest(active)['draft']
    response = active.post(ROOT + '/apply', json=payload)
    assert response.status_code == 200, response.text
    saved = active.get('/api/notes/' + response.json()['id']).json()
    assert saved['title'] == 'Draft' and saved['icon'] == '📝' and saved['cover'] == 'forest'
    assert [b['content'][0]['text'] for b in saved['blocks']] == ['First.', 'Second.']
    assert active.post(ROOT + '/apply', json=payload).status_code == 409
    assert len(active.get('/api/notes').json()) == 1


def test_edit_preserves_rich_blocks_and_rejects_changed_version(active, monkeypatch):
    paragraph = ai.rich_paragraph('Original sentence')
    block = {'type': 'codeBlock', 'id': 'code', 'props': {'language': 'python'}, 'content': 'print(1)', 'children': []}
    page = active.post('/api/notes', json={'title': 'Original', 'body': 'Original sentence\n\n```python\nprint(1)\n```', 'blocks': [paragraph, block]}).json()
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('edit', title='Renamed', cover='mountains', replace_before='Original sentence', replace_after='Updated sentence'))
    preview = suggest(active, target=ref(page), include_target_text=True)
    assert preview['before'] == {'title': 'Original', 'cover': ''}
    assert active.get('/api/notes/' + page['id']).json()['title'] == 'Original'
    data = {'draft': preview['draft']}
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
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('edit', title='Assistant title'))
    stale = suggest(active, target=ref(saved))
    result = active.put('/api/notes/' + page['id'], json={'title': 'Manual edit', 'body': saved['body'], 'blocks': saved['blocks'], 'version': saved['version']})
    assert result.status_code == 200, result.text
    assert active.post(ROOT + '/apply', json={'draft': stale['draft']}).status_code == 409
    assert active.get('/api/notes/' + page['id']).json()['title'] == 'Manual edit'


def test_addition_recovers_from_invalid_replacement_and_can_be_restored(active, monkeypatch):
    old = 'Install the authenticator application.'
    styled = ai.rich_paragraph(old)
    styled['content'][0]['styles'] = {'bold': True}
    page = active.post('/api/notes', json={'title': 'Key setup', 'body': '**' + old + '**', 'blocks': [styled]}).json()
    appearance = active.patch('/api/notes/' + page['id'], json={'icon': '📚', 'cover': 'forest', 'version': page['version']})
    assert appearance.status_code == 200, appearance.text
    page = active.get('/api/notes/' + page['id']).json()
    calls = []
    command = 'brew install --cask fixture-app'

    def generate(key, prompt, *, correction=False, timeout=ai.PROPOSAL_TIMEOUT, **kwargs):
        calls.append(correction)
        assert prompt == 'Add the Homebrew command' and 0 < timeout <= ai.PROPOSAL_TIMEOUT
        if not correction:
            return reply('edit', replace_before=old, replace_after=command)
        return reply('edit', append=command, title='Updated key setup', icon='💻', cover='mountains')

    monkeypatch.setattr(ai, 'proposal', generate)
    response = active.post(ROOT + '/propose', json={'prompt': 'Add the Homebrew command', 'target': ref(page), 'include_target_text': True})
    assert response.status_code == 200, response.text
    assert calls == [False, True] and 'append' in response.json()['fields']
    result = active.post(ROOT + '/apply', json={'draft': response.json()['draft']})
    assert result.status_code == 200, result.text
    path = '/api/notes/' + page['id']
    saved = active.get(path).json()
    assert saved['blocks'][0] == styled and saved['blocks'][1]['content'][0]['text'] == command
    assert (saved['title'], saved['icon'], saved['cover']) == ('Updated key setup', '💻', 'mountains')
    previous = active.get(path + '/history').json()[0]['id']
    details = active.get(path + '/history/' + previous).json()
    assert (details['title'], details['icon'], details['cover']) == ('Key setup', '📚', 'forest')
    restored = active.post(path + '/history/' + previous + '/restore', params={'version': saved['version']})
    assert restored.status_code == 200, restored.text
    note = restored.json()
    assert (note['title'], note['icon'], note['cover'], note['blocks']) == (page['title'], page['icon'], page['cover'], page['blocks'])

    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('edit', icon='💻'))
    changed_icon = suggest(active, target=ref(note))
    assert active.post(ROOT + '/apply', json={'draft': changed_icon['draft']}).status_code == 200
    with_icon = active.get(path).json()
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('edit', append=command))
    added = suggest(active, target=ref(with_icon))
    assert active.post(ROOT + '/apply', json={'draft': added['draft']}).status_code == 200
    latest = active.get(path + '/history').json()[0]['id']
    assert active.get(path + '/history/' + latest).json()['icon'] == '💻'


def test_scopes_and_disabling(active, monkeypatch):
    guest, _ = member(active)
    foreign = guest.get('/api/notes/' + note(guest)).json()
    public = active.get('/api/notes/' + note(active)).json()
    share(active, public['id'], 'public')
    assert active.get(ROOT + '/pages').json() == {'items': []}
    assert guest.get(ROOT).json() == {'enabled': False}
    assert active.get('/api/me').json()['notes_assistant_enabled'] is True
    assert guest.get('/api/me').json()['notes_assistant_enabled'] is False
    assert guest.get(ROOT + '/pages/' + public['id']).status_code == 403
    assert guest.get(ADMIN).status_code == 403
    assert guest.post(ROOT + '/propose', json={'prompt': 'test'}).status_code == 403
    for unavailable in (foreign, public):
        assert active.get(ROOT + '/pages/' + unavailable['id']).status_code == 404
        for kind in ('target', 'source'):
            assert active.post(ROOT + '/context', json={kind: ref(unavailable)}).status_code == 404
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('create', title='Draft', append='Text'))
    preview = suggest(active)
    configure(active, account_id(guest))
    assert active.post(ROOT + '/apply', json={'draft': preview['draft']}).status_code == 403
    assert guest.get(ROOT).json() == {'enabled': True}
    assert active.get('/api/me').json()['notes_assistant_enabled'] is False
    assert guest.get('/api/me').json()['notes_assistant_enabled'] is True
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
    option = active.get(ROOT + '/pages/' + imported['id'])
    assert option.status_code == 200
    assert option.json() == ref(imported) | {'title': 'Imported', 'read_only': True}
    ordinary = active.post('/api/notes', json={'title': 'Local'}).json()
    assert active.get(ROOT + '/pages/' + ordinary['id']).json() == ref(ordinary) | {'title': 'Local', 'read_only': False}
    seen = []

    def timeout(key, prompt, *, target, source, previous, history):
        seen.append((target, source))
        raise httpx.ReadTimeout('Provider secret error')

    monkeypatch.setattr(ai, 'proposal', timeout)
    response = active.post(ROOT + '/propose', json={'prompt': 'Use source', 'source': ref(imported)})
    assert response.status_code == 502 and 'secret' not in response.text
    assert seen == [(None, {'title': 'Imported', 'excerpt': 'Source text '})]
    assert len(active.get('/api/notes').json()) == 2
    monkeypatch.delenv('OPENAI_API_KEY')
    assert active.get(ROOT).json() == {'enabled': False}
    assert active.get('/api/me').json()['notes_assistant_enabled'] is False
    assert active.get('/api/notes').status_code == 200


def test_provider_request_opts_out_of_storage_and_uses_no_tools(monkeypatch):
    def handler(request):
        args = json.loads(request.content)
        assert request.url == 'https://api.openai.com/v1/responses'
        assert request.headers['Authorization'] == 'Bearer synthetic-key'
        assert args['store'] is False and 'tools' not in args
        assert args['text']['format']['strict'] is True
        context, instruction = args['input']
        assert json.loads(context['content']) == {'target': None, 'source': None, 'previous': None, 'history': []}
        assert instruction == {'role': 'user', 'content': 'Synthetic prompt'}
        text = reply('create', title='Synthetic', append='Sample').model_dump_json()
        return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]})

    client = httpx.Client
    monkeypatch.setattr(ai.httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    assert ai.proposal('synthetic-key', 'Synthetic prompt', target=None, source=None).changes.title == 'Synthetic'


def test_simple_legacy_paragraphs_can_change_but_attachment_references_are_rejected(active, monkeypatch):
    old = 'Original sentence.'
    page = active.post('/api/notes', json={'title': 'Legacy', 'body': old}).json()
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('edit', append='/api/notes/another_note_id/files/another_file_id'))
    assert active.post(ROOT + '/propose', json={'prompt': 'Test', 'target': ref(page)}).status_code == 502
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('edit', replace_before=old, replace_after='Updated sentence'))
    preview = suggest(active, target=ref(page), include_target_text=True)
    assert active.post(ROOT + '/apply', json={'draft': preview['draft']}).status_code == 200
    assert active.get('/api/notes/' + page['id']).json()['body'] == 'Updated sentence'


def test_read_and_revise_draft_then_rewrite_existing_note(active, monkeypatch):
    image = {'id': 'image', 'type': 'image', 'props': {'url': 'https://example.invalid/fixture.png'}}
    child_id = active.post('/api/notes', json={'title': 'Child'}).json()['id']
    shortcut = {'id': 'child', 'type': 'pageLink', 'props': {'pageId': child_id, 'title': 'Child'}}
    old = 'Original paragraph about a fictional park.'
    page = active.post('/api/notes', json={'title': 'Local', 'body': old,
        'blocks': [ai.rich_paragraph(old), image, shortcut]}).json()
    with main.db() as c:
        c.execute('UPDATE notes SET parent_id=? WHERE id=?', (page['id'], child_id))
    payload = {'prompt': 'Read this', 'target': ref(page), 'include_target_text': True}
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('answer', rewrite='This must never be saved'))
    read = active.post(ROOT + '/propose', json=payload)
    assert read.json() == {'message': 'Assistant reply', 'draft': None}
    assert active.get('/api/notes/' + page['id']).json()['version'] == page['version']

    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('edit', rewrite='New paragraph.'))
    first = suggest(active, target=ref(page), include_target_text=True)
    assert first['fields'] == ['rewrite'] and first['retains_attachments'] is True

    def refine(key, prompt, *, target, source, previous, history):
        assert previous['intent'] == 'edit' and previous['changes']['rewrite'] == 'New paragraph.'
        assert history == [{'role': 'user', 'text': 'Rewrite the whole note'}]
        return reply('edit', rewrite='Revised first paragraph.\nRevised second paragraph.')

    monkeypatch.setattr(ai, 'proposal', refine)
    final = suggest(active, target=ref(page), include_target_text=True, previous=first['draft'],
                    history=[{'role': 'user', 'text': 'Rewrite the whole note'}])
    assert active.post(ROOT + '/apply', json={'draft': first['draft']}).status_code == 409
    assert active.get('/api/notes/' + page['id']).json()['body'] == old
    saved = active.post(ROOT + '/apply', json={'draft': final['draft']})
    assert saved.json()['id'] == page['id']
    rewritten = active.get('/api/notes/' + page['id']).json()
    assert 'fictional park' not in rewritten['body'] and 'Revised second paragraph' in rewritten['body']
    assert [block['id'] for block in rewritten['blocks'][-2:]] == ['image', 'child']
    assert active.get('/api/notes/' + child_id).json()['parent_id'] == page['id']
    with main.db() as c:
        assert c.execute('SELECT body FROM note_revisions WHERE note_id=?', (page['id'],)).fetchone()[0] == old


def test_create_from_chat_on_existing_note_keeps_the_original(active, monkeypatch):
    original = active.get('/api/notes/' + note(active)).json()
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('create', title='Separate note', append='Separate text'))
    preview = suggest(active, target=ref(original), include_target_text=True)
    assert preview['draft']['target'] is None
    result = active.post(ROOT + '/apply', json={'draft': preview['draft']})
    assert result.status_code == 200 and result.json()['id'] != original['id']
    assert active.get('/api/notes/' + original['id']).json()['version'] == original['version']


def test_refining_a_new_draft_stays_a_creation(active, monkeypatch):
    original = active.get('/api/notes/' + note(active)).json()
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('create', title='Book shelf', append='Long first version.'))
    first = suggest(active, target=ref(original))
    monkeypatch.setattr(ai, 'proposal', lambda *a, **kw: reply('edit', rewrite='Short version.'))
    final = suggest(active, target=ref(original), previous=first['draft'])
    assert final['draft']['target'] is None
    assert final['draft']['changes']['title'] == 'Book shelf'
    assert final['draft']['changes']['append'] == 'Short version.'
    result = active.post(ROOT + '/apply', json={'draft': final['draft']})
    assert result.status_code == 200 and result.json()['id'] != original['id']
    assert active.get('/api/notes/' + original['id']).json()['version'] == original['version']
