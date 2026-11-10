import base64
import json

import pytest
from pycrdt import XmlElement, XmlFragment, XmlText

from tests.test_security import client, member, owner, reset
from app import main as m
from app.collaboration import load_doc, project
from app.database import Connection
from app.share_editing import replace_content
from tests.test_notion import database, permissions
from tests.test_social import family, hello, join, next_event


def paragraph(identity, text, children=None):
    return {'id': identity, 'type': 'paragraph', 'props': {}, 'content': [{'type': 'text', 'text': text, 'styles': {}}], 'children': children or []}


def page(c, title, blocks=None, scope=None, body=''):
    response = c.post('/api/notes', json={'title': title, 'body': body, 'blocks': blocks, 'space_id': scope})
    assert response.status_code == 200, response.text
    return response.json()


def move(c, source, target, ids, **extra):
    return c.post('/api/notes/'+source['id']+'/blocks/move', json={'target_id': target['id'], 'block_ids': ids, 'source_version': source['version'], 'target_version': target['version'], **extra})


def stored(ids):
    with m.db() as c:
        return {identity: (dict(c.execute('SELECT * FROM notes WHERE id=?', (identity,)).fetchone()), [dict(row) for row in c.execute('SELECT * FROM note_revisions WHERE note_id=? ORDER BY rowid', (identity,))], [dict(row) for row in c.execute('SELECT * FROM collaborative_docs WHERE note_id=?', (identity,))]) for identity in ids}


def initialize(c, note):
    doc = load_doc(None)
    replace_content(doc, note['title'], note['blocks'], m.fail)
    with c.websocket_connect('/api/live/notes/'+note['id']) as ws:
        hello(ws, c)
        init = next_event(ws, 'init')
        ws.send_json({'type': 'init', 'version': init['page']['version'], 'update': base64.b64encode(doc.get_update()).decode()})
        sync = next_event(ws, 'sync')
    return c.get('/api/notes/'+note['id']).json(), load_doc(base64.b64decode(sync['state'])), sync['epoch']


def test_nested_rich_subtrees_move_once_in_document_order_and_both_revisions_are_kept():
    c, _, _ = owner()
    linked = page(c, 'Подстраница', [paragraph('link-content', 'Связанный текст')])
    rich = {'id': 'heading', 'type': 'heading', 'props': {'level': 2, 'textColor': 'blue'}, 'content': [{'type': 'link', 'href': 'https://example.com', 'content': [{'type': 'text', 'text': 'Ссылка', 'styles': {'bold': True, 'italic': True}}]}], 'children': [
        {'id': 'table', 'type': 'table', 'content': {'type': 'tableContent', 'headerRows': 1, 'columnWidths': [123], 'rows': [{'cells': [{'type': 'tableCell', 'props': {'textColor': 'green'}, 'content': [{'type': 'text', 'text': 'Ячейка', 'styles': {'underline': True}}]}]}]}, 'children': []},
        {'id': 'code', 'type': 'codeBlock', 'props': {'language': 'python'}, 'content': 'print("Привет")', 'children': []},
        {'id': 'page-link', 'type': 'pageLink', 'props': {'pageId': linked['id'], 'title': 'Подстраница'}, 'children': []}]}
    source = page(c, 'Источник', [paragraph('keep', 'Останется'), paragraph('nest-parent', 'Родитель', [rich]), paragraph('last', 'Последний')], body='Before source')
    target = page(c, 'Назначение', [paragraph('target', 'Первый блок')], body='Before target')
    before = stored([source['id'], target['id']])
    response = move(c, source, target, ['last', 'table', 'heading'])
    assert response.status_code == 200, response.text
    moved = response.json()
    assert [block['id'] for block in moved['target']['blocks']] == ['target', 'heading', 'last']
    assert moved['target']['blocks'][1] == rich
    assert moved['source']['blocks'] == [source['blocks'][0], paragraph('nest-parent', 'Родитель')]
    assert 'Ячейка' in moved['target']['body'] and 'print("Привет")' in moved['target']['body']
    assert moved['source']['version'] == source['version'] + 1 and moved['target']['version'] == target['version'] + 1
    for identity, previous in before.items():
        history = c.get('/api/notes/'+identity+'/history').json()
        assert len(history) == 1
        revision = stored([identity])[identity][1][0]
        assert (revision['title'], revision['body'], revision['blocks']) == (previous[0]['title'], previous[0]['body'], previous[0]['blocks'])


def test_moving_all_source_blocks_leaves_empty_paragraph_and_history_can_restore():
    c, _, _ = owner()
    source = page(c, 'Источник', [paragraph('only', 'Весь текст')])
    target = page(c, 'Назначение', [paragraph('target', 'Приёмник')])
    response = move(c, source, target, ['only'])
    assert response.status_code == 200, response.text
    empty = response.json()['source']['blocks']
    assert len(empty) == 1 and empty[0]['type'] == 'paragraph' and empty[0]['content'] == []
    assert [block['id'] for block in response.json()['target']['blocks']] == ['target', 'only']
    revision = c.get('/api/notes/'+source['id']+'/history').json()[0]
    assert c.post('/api/notes/'+source['id']+'/history/'+revision['id']+'/restore').status_code == 200
    assert c.get('/api/notes/'+source['id']).json()['blocks'] == source['blocks']


@pytest.mark.parametrize('bad', [
    {'block_ids': []}, {'block_ids': ['source', 'source']}, {'block_ids': ['bad id']},
    {'block_ids': [1]}, {'block_ids': ['x' * 101]}, {'block_ids': ['x'] * 2001},
    {'source_version': None}, {'target_version': True}, {'source_version': '1'},
    {'target_id': 'bad/path'}, {'user_id': 'anything'}, {'body': 'overwrite'},
])
def test_strict_payload_rejects_invalid_ids_versions_and_extra_fields_without_writing(bad):
    c, _, _ = owner()
    source = page(c, 'Источник', [paragraph('source', 'Исходный текст')])
    target = page(c, 'Назначение', [paragraph('target', 'Другой текст')])
    before = stored([source['id'], target['id']])
    data = {'target_id': target['id'], 'block_ids': ['source'], 'source_version': source['version'], 'target_version': target['version'], **bad}
    assert c.post('/api/notes/'+source['id']+'/blocks/move', json=data).status_code == 422
    assert stored(before) == before


@pytest.mark.parametrize('case', ['source-version', 'target-version', 'missing-block', 'collision', 'same-page', 'deleted-target', 'database-target', 'markdown-source'])
def test_conflict_or_unsupported_page_preserves_both_documents_and_history(case):
    c, _, _ = owner()
    source = page(c, 'Источник', None if case == 'markdown-source' else [paragraph('source', 'Исходный текст')], body='Markdown source')
    target = page(c, 'Назначение', [paragraph('source' if case == 'collision' else 'target', 'Другой текст')])
    if case == 'source-version': source['version'] += 1
    if case == 'target-version': target['version'] += 1
    if case == 'same-page': target = source
    if case == 'deleted-target': assert c.delete('/api/notes/'+target['id']).status_code == 200
    if case == 'database-target': target = c.get('/api/notes/'+database(c)).json()
    before = stored([source['id'], target['id']])
    response = move(c, source, target, ['absent' if case == 'missing-block' else 'source'])
    expected = 422 if case in ('same-page', 'database-target') else 404 if case == 'deleted-target' else 409
    assert response.status_code == expected, response.text
    assert stored(before) == before


def test_scope_membership_writer_permissions_and_csrf_apply_to_both_pages():
    c, _, _ = owner(); guest, _ = member(c)
    private = page(c, 'Личное', [paragraph('private', 'Личный текст')])
    guest_private = page(guest, 'Чужое', [paragraph('guest', 'Чужой текст')])
    sid = family(c); join(c, guest, sid)
    source = page(c, 'Общее', [paragraph('shared', 'Общий текст')], sid)
    target = page(c, 'Приёмник', [paragraph('target', 'Другой текст')], sid)
    other = page(c, 'Другая семья', [paragraph('other', 'Другая семья')], family(c))
    before = stored([private['id'], guest_private['id'], source['id'], target['id'], other['id']])
    assert move(c, private, guest_private, ['private']).status_code == 404
    assert move(guest, private, guest_private, ['private']).status_code == 404
    assert move(c, private, source, ['private']).status_code == 422
    assert move(c, source, other, ['shared']).status_code == 422
    for identity in (source['id'], target['id']):
        permissions(c, identity, guest, 'viewer')
        assert move(guest, source, target, ['shared']).status_code == 403
        permissions(c, identity, guest, None)
    del guest.headers['x-csrf-token']
    assert move(guest, source, target, ['shared']).status_code == 403
    assert move(client(), source, target, ['shared']).status_code == 401
    assert stored(before) == before


@pytest.mark.parametrize('locked', ['source', 'target'])
def test_both_locks_are_enforced_and_session_scoped_temporary_unlock_allows_move(locked):
    c, _, _ = owner()
    source = page(c, 'Источник', [paragraph('source', 'Исходный текст')])
    target = page(c, 'Назначение', [paragraph('target', 'Другой текст')])
    identity = source['id'] if locked == 'source' else target['id']
    assert c.post('/api/notes/'+identity+'/lock', json={'mode': 'lock'}).status_code == 200
    before = stored([source['id'], target['id']])
    assert move(c, source, target, ['source']).status_code == 423
    assert stored(before) == before
    assert c.post('/api/notes/'+identity+'/lock', json={'mode': 'temporary'}).status_code == 200
    assert move(c, source, target, ['source']).status_code == 200
    assert c.get('/api/notes/'+identity).json()['locked']


def test_target_markdown_conversion_is_explicit_versioned_and_cannot_replace_canonical_target():
    c, _, _ = owner()
    source = page(c, 'Источник', [paragraph('source', 'Переносимый текст')])
    target = page(c, 'Markdown', None, body='Существующий текст')
    before = stored([source['id'], target['id']])
    assert move(c, source, target, ['source']).status_code == 422
    assert stored(before) == before
    imported = [paragraph('converted', 'Существующий текст')]
    response = move(c, source, target, ['source'], target_blocks=imported)
    assert response.status_code == 200, response.text
    assert response.json()['target']['blocks'] == imported + source['blocks']
    fresh_source, fresh_target = response.json()['source'], response.json()['target']
    before = stored([source['id'], target['id']])
    assert move(c, fresh_target, fresh_source, ['source'], target_blocks=[]).status_code == 422
    assert stored(before) == before


def test_unsafe_content_links_limits_and_final_write_failure_roll_back_both_pages(monkeypatch):
    c, _, _ = owner()
    source = page(c, 'Источник', [paragraph('source', 'Исходный текст')])
    target = page(c, 'Назначение', None, body='Другой текст')
    before = stored([source['id'], target['id']])
    for imported in ([{'id': 'bad', 'type': 'paragraph', 'content': [{'type': 'link', 'href': 'javascript:alert(1)', 'content': []}]}],
                     [{'id': 'bad-link', 'type': 'pageLink', 'props': {'pageId': 'absent'}}],
                     [paragraph('big', 'x' * 500001)]):
        assert move(c, source, target, ['source'], target_blocks=imported).status_code in (404, 422)
        assert stored(before) == before
    original = Connection.execute
    def fail_target(self, query, parameters=()):
        if query.startswith('UPDATE notes SET title=?,body=?,blocks=?') and parameters[-1] == target['id']:
            m.fail(409, 'Simulated final-write conflict')
        return original(self, query, parameters)
    monkeypatch.setattr(Connection, 'execute', fail_target)
    assert move(c, source, target, ['source'], target_blocks=[paragraph('target', 'Другой текст')]).status_code == 409
    assert stored(before) == before


def test_history_keeps_more_than_fifty_recent_checkpoints_for_each_page():
    c, _, _ = owner()
    source = page(c, 'Источник', [paragraph('source', 'Исходный текст')])
    target = page(c, 'Назначение', [paragraph('target', 'Другой текст')])
    with m.db() as db:
        for item in (source, target):
            for index in range(50):
                db.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)', (item['id']+'-'+str(index), item['id'], 'Old '+str(index), '', '[]', m.now()))
    assert move(c, source, target, ['source']).status_code == 200
    for item in (source, target):
        history = c.get('/api/notes/'+item['id']+'/history').json()
        assert len(history) == 51 and history[0]['title'] == item['title']
        assert any(row['title'] == 'Old 0' for row in history)


def test_shared_move_updates_both_online_crdts_without_changing_epochs():
    c, _, _ = owner(); sid = family(c)
    source = page(c, 'Источник', [paragraph('source', 'Переносимый текст', [paragraph('nested', 'Вложенное 🕐')])], sid)
    target = page(c, 'Назначение', [paragraph('target', 'Другой текст')], sid)
    source, source_doc, source_epoch = initialize(c, source)
    target, target_doc, target_epoch = initialize(c, target)
    with c.websocket_connect('/api/live/notes/'+source['id']) as source_socket, c.websocket_connect('/api/live/notes/'+target['id']) as target_socket:
        hello(source_socket, c, 901); hello(target_socket, c, 902)
        assert next_event(source_socket, 'sync')['epoch'] == source_epoch
        assert next_event(target_socket, 'sync')['epoch'] == target_epoch
        response = move(c, source, target, ['source'])
        assert response.status_code == 200, response.text
        for socket, doc, epoch, name in ((source_socket, source_doc, source_epoch, 'source'), (target_socket, target_doc, target_epoch, 'target')):
            event = next_event(socket, 'update')
            assert event['epoch'] == epoch and event['version'] == response.json()[name]['version']
            doc.apply_update(base64.b64decode(event['update']))
            title, body, blocks = project(doc, m.fail)
            assert (title, body, blocks) == tuple(response.json()[name][key] for key in ('title', 'body', 'blocks'))
            with m.db() as db:
                saved = db.execute('SELECT * FROM collaborative_docs WHERE note_id=?', (response.json()[name]['id'],)).fetchone()
                assert saved['epoch'] == epoch and project(load_doc(saved['state']), m.fail) == (title, body, blocks)
    assert response.json()['target']['blocks'][1]['children'][0]['content'][0]['text'] == 'Вложенное 🕐'


@pytest.mark.parametrize('initialized', [False, True])
def test_shared_documents_are_initialized_atomically_and_rollback_preserves_crdts(monkeypatch, initialized):
    c, _, _ = owner(); sid = family(c)
    source = page(c, 'Источник', [paragraph('source', 'Исходный текст')], sid)
    target = page(c, 'Назначение', [paragraph('target', 'Другой текст')] if initialized else None, sid, body='Другой текст')
    if initialized:
        source, _, _ = initialize(c, source)
        target, _, _ = initialize(c, target)
    conversion = {} if initialized else {'target_blocks': [paragraph('target', 'Другой текст')]}
    before = stored([source['id'], target['id']])
    original = Connection.execute
    def fail_target(self, query, parameters=()):
        if query.startswith('UPDATE notes SET title=?,body=?,blocks=?') and parameters[-1] == target['id']:
            m.fail(409, 'Simulated final-write conflict')
        return original(self, query, parameters)
    with monkeypatch.context() as patch:
        patch.setattr(Connection, 'execute', fail_target)
        assert move(c, source, target, ['source'], **conversion).status_code == 409
    assert stored(before) == before
    response = move(c, source, target, ['source'], **conversion)
    assert response.status_code == 200, response.text
    with m.db() as db:
        for name in ('source', 'target'):
            result = response.json()[name]
            saved = db.execute('SELECT * FROM collaborative_docs WHERE note_id=?', (result['id'],)).fetchone()
            assert saved and saved['epoch']
            assert project(load_doc(saved['state']), m.fail) == tuple(result[key] for key in ('title', 'body', 'blocks'))


def test_stale_shared_json_projection_cannot_overwrite_the_canonical_document():
    c, _, _ = owner(); sid = family(c)
    source = page(c, 'Источник', [paragraph('source', 'Исходный текст')], sid)
    target = page(c, 'Назначение', [paragraph('target', 'Другой текст')], sid)
    source, _, _ = initialize(c, source)
    with m.db() as db:
        db.execute('UPDATE notes SET blocks=? WHERE id=?', (json.dumps([paragraph('source', 'Stale projection')]), source['id']))
    before = stored([source['id'], target['id']])
    assert move(c, source, target, ['source']).status_code == 409
    assert stored(before) == before


def block_text(doc, identity):
    def find(group):
        for container in group.children:
            children = list(container.children)
            if dict(container.attributes).get('id') == identity:
                return next(child for child in children[0].children if isinstance(child, XmlText))
            if len(children) == 2:
                result = find(children[1])
                if result is not None:
                    return result
    return find(list(doc.get('prosemirror', type=XmlFragment).children)[0])


@pytest.mark.parametrize('nested', [False, True])
def test_late_client_edits_to_untouched_source_and_target_nodes_survive_shared_move(nested):
    c, _, _ = owner(); sid = family(c)
    source = page(c, 'Источник', [paragraph('keep-parent', 'Оставшийся родитель', [paragraph('keep-child', 'Оставшийся ребёнок')]),
        {'id': 'move-parent', 'type': 'heading', 'props': {'level': 2, 'backgroundColor': 'yellow'}, 'content': [{'type': 'link', 'href': 'https://example.com', 'content': [{'type': 'text', 'text': 'Ж🕐 Rich', 'styles': {'bold': True, 'italic': True}}]}], 'children': [
            {'id': 'move-table', 'type': 'table', 'content': {'type': 'tableContent', 'headerRows': 1, 'columnWidths': [123], 'rows': [{'cells': [{'type': 'tableCell', 'props': {'textColor': 'green'}, 'content': [{'type': 'text', 'text': 'Клетка 🕐', 'styles': {'underline': True}}]}]}]}, 'children': []},
            {'id': 'move-code', 'type': 'codeBlock', 'props': {'language': 'python'}, 'content': 'print("Ж🕐")', 'children': []}]}], sid)
    if nested:
        with m.db() as db:
            blocks = [source['blocks'][0], paragraph('keep-holder', 'Родитель перемещаемого блока', [source['blocks'][1]])]
            db.execute('UPDATE notes SET blocks=? WHERE id=?', (json.dumps(blocks, ensure_ascii=False), source['id']))
        source = c.get('/api/notes/'+source['id']).json()
    target = page(c, 'Назначение', [paragraph('target-existing', 'Существующий текст')], sid)
    source, source_doc, source_epoch = initialize(c, source)
    target, target_doc, target_epoch = initialize(c, target)
    offline_source = load_doc(source_doc.get_update())
    offline_target = load_doc(target_doc.get_update())
    source_state, target_state = offline_source.get_state(), offline_target.get_state()
    block_text(offline_source, 'keep-parent').insert(0, 'Поздний родитель: ', {})
    block_text(offline_source, 'keep-child').insert(0, 'Поздний ребёнок: ', {})
    block_text(offline_target, 'target-existing').insert(0, 'Поздний приёмник: ', {})
    source_root = list(offline_source.get('prosemirror', type=XmlFragment).children)[0]
    target_root = list(offline_target.get('prosemirror', type=XmlFragment).children)[0]
    source_root.children.append(XmlElement('blockContainer', {'id': 'late-source'}, [XmlElement('paragraph', {}, [XmlText('Поздний новый блок источника')])]))
    target_root.children.append(XmlElement('blockContainer', {'id': 'late-target'}, [XmlElement('paragraph', {}, [XmlText('Поздний новый блок приёмника')])]))
    if nested:
        holder = next(container for container in source_root.children if dict(container.attributes).get('id') == 'keep-holder')
        group = list(holder.children)[1]
        group.children.append(XmlElement('blockContainer', {'id': 'late-nested'}, [XmlElement('paragraph', {}, [XmlText('Поздний ребёнок в опустевшей группе')])]))
    with c.websocket_connect('/api/live/notes/'+source['id']) as source_socket, c.websocket_connect('/api/live/notes/'+target['id']) as target_socket:
        hello(source_socket, c, 971); hello(target_socket, c, 972)
        next_event(source_socket, 'sync'); next_event(target_socket, 'sync')
        response = move(c, source, target, ['move-parent', 'move-table'])
        assert response.status_code == 200, response.text
        assert response.json()['target']['blocks'][1] == (source['blocks'][1]['children'][0] if nested else source['blocks'][1])
        next_event(source_socket, 'update'); next_event(target_socket, 'update')
        for socket, doc, before, epoch in ((source_socket, offline_source, source_state, source_epoch), (target_socket, offline_target, target_state, target_epoch)):
            socket.send_json({'type': 'update', 'epoch': epoch, 'seq': 1, 'update': base64.b64encode(doc.get_update(before)).decode()})
            next_event(socket, 'ack')
    current_source = c.get('/api/notes/'+source['id']).json()
    current_target = c.get('/api/notes/'+target['id']).json()
    assert 'Поздний родитель: Оставшийся родитель' in current_source['body']
    assert 'Поздний ребёнок: Оставшийся ребёнок' in current_source['body']
    assert 'Поздний приёмник: Существующий текст' in current_target['body']
    assert 'Поздний новый блок источника' in current_source['body']
    assert 'Поздний новый блок приёмника' in current_target['body']
    if nested:
        assert 'Поздний ребёнок в опустевшей группе' in current_source['body']
    for result, epoch in ((current_source, source_epoch), (current_target, target_epoch)):
        with m.db() as db:
            saved = db.execute('SELECT * FROM collaborative_docs WHERE note_id=?', (result['id'],)).fetchone()
            assert saved['epoch'] == epoch
            assert project(load_doc(saved['state']), m.fail) == tuple(result[key] for key in ('title', 'body', 'blocks'))


def test_shared_move_into_an_empty_imported_destination_preserves_an_empty_root_until_append():
    c, _, _ = owner(); sid = family(c)
    source = page(c, 'Источник', [paragraph('source', 'Переносимый текст')], sid)
    target = page(c, 'Пустая страница', None, sid)
    response = move(c, source, target, ['source'], target_blocks=[])
    assert response.status_code == 200, response.text
    assert [block['id'] for block in response.json()['target']['blocks']] == ['source']


def comment(c, note, body, block_id=None, parent_id=None):
    response = c.post('/api/notes/'+note['id']+'/comments', json={'body': body, 'block_id': block_id, 'parent_id': parent_id})
    assert response.status_code == 200, response.text
    return next(item for item in c.get('/api/notes/'+note['id']+'/comments').json() if item['body'] == body)


def comment_storage():
    with m.db() as db:
        comments = {row['id']: dict(row) for row in db.execute('SELECT * FROM note_comments ORDER BY rowid')}
        mentions = {row['id']: dict(row) for row in db.execute("SELECT * FROM notifications WHERE kind='mention' ORDER BY id")}
        return comments, mentions


def test_nested_block_threads_move_with_replies_authors_resolution_mentions_and_inbox_links():
    c, _, _ = owner(); guest, _ = member(c); sid = family(c); join(c, guest, sid)
    source = page(c, 'Источник', [paragraph('stay', 'Останется'), paragraph('move-parent', 'Переносимый', [paragraph('move-child', 'Вложенный')])], sid)
    target = page(c, 'Назначение', [paragraph('target', 'Приёмник')], sid)
    root = comment(c, source, '@friend Ветка переносимого блока', 'move-child')
    reply = comment(guest, source, '@owner Ответ другого автора', parent_id=root['id'])
    resolved = comment(guest, source, '@owner Завершённая ветка', 'move-parent')
    comment(c, source, 'Ответ в завершённой ветке', parent_id=resolved['id'])
    assert guest.patch('/api/notes/'+source['id']+'/comments/'+resolved['id'], json={'resolved': True}).status_code == 200
    keep = comment(c, source, '@friend Другой блок', 'stay')
    comment(guest, source, 'Ответ другому блоку', parent_id=keep['id'])
    unanchored = comment(c, source, '@friend Вся страница')
    comment(guest, source, 'Ответ странице', parent_id=unanchored['id'])
    comment(c, target, 'Существующее обсуждение', 'target')
    before_comments, before_notifications = comment_storage()
    moved_ids = {identity for identity, row in before_comments.items() if row['note_id'] == source['id'] and row['block_id'] in {'move-parent', 'move-child'}}
    response = move(c, source, target, ['move-parent', 'move-child'])
    assert response.status_code == 200, response.text
    after_comments, after_notifications = comment_storage()
    assert set(after_comments) == set(before_comments)
    assert set(after_notifications) == set(before_notifications)
    for identity, before in before_comments.items():
        assert after_comments[identity] == {**before, 'note_id': target['id']} if identity in moved_ids else after_comments[identity] == before
    for identity, before in before_notifications.items():
        payload = json.loads(before['payload'])
        if payload['comment_id'] in moved_ids:
            payload.update(note_id=target['id'], title=target['title'])
            assert after_notifications[identity] == {**before, 'payload': json.dumps(payload, ensure_ascii=False)}
        else:
            assert after_notifications[identity] == before
    target_threads = guest.get('/api/notes/'+target['id']+'/comments').json()
    assert any(item['id'] == reply['id'] and item['parent_id'] == root['id'] for item in target_threads)
    assert any(item['id'] == resolved['id'] and item['resolved'] for item in target_threads)
    assert guest.post('/api/notes/'+target['id']+'/comments', json={'body': 'Новый ответ после переноса', 'parent_id': root['id']}).status_code == 200
    assert guest.post('/api/notes/'+source['id']+'/comments', json={'body': 'Старый адрес', 'parent_id': root['id']}).status_code == 422
    assert guest.post('/api/notes/'+target['id']+'/comments', json={'body': 'Закрытая ветка', 'parent_id': resolved['id']}).status_code == 409
    assert any(item['id'] == root['id'] and item['note_id'] == target['id'] for item in guest.get('/api/note-mentions').json())


@pytest.mark.parametrize('existing_count', [998, 999])
def test_destination_comment_limit_includes_replies_and_aborts_the_whole_move(existing_count):
    c, _, _ = owner()
    source = page(c, 'Источник', [paragraph('source', 'Переносимый текст')])
    target = page(c, 'Назначение', [paragraph('target', 'Приёмник')])
    root = comment(c, source, 'Переносимая ветка', 'source')
    comment(c, source, 'Переносимый ответ', parent_id=root['id'])
    uid = c.get('/api/me').json()['id']
    with m.db() as db:
        db.executemany('INSERT INTO note_comments VALUES(?,?,?,?,?,?,?,?,?)', [('filler-'+str(index), target['id'], uid, None, None, 'Существующая ветка', '[]', m.now(), 0) for index in range(existing_count)])
    before_notes, before_discussions = stored([source['id'], target['id']]), comment_storage()
    response = move(c, source, target, ['source'])
    if existing_count == 999:
        assert response.status_code == 422, response.text
        assert stored(before_notes) == before_notes and comment_storage() == before_discussions
    else:
        assert response.status_code == 200, response.text
        assert len(c.get('/api/notes/'+target['id']+'/comments').json()) == 1000
        assert c.get('/api/notes/'+source['id']+'/comments').json() == []


def test_comment_or_notification_write_failure_rolls_back_documents_crdts_history_and_threads(monkeypatch):
    c, _, _ = owner(); guest, _ = member(c); sid = family(c); join(c, guest, sid)
    source = page(c, 'Источник', [paragraph('source', 'Переносимый текст')], sid)
    target = page(c, 'Назначение', [paragraph('target', 'Приёмник')], sid)
    source, _, _ = initialize(c, source)
    target, _, _ = initialize(c, target)
    root = comment(c, source, '@friend Переносимая ветка', 'source')
    comment(guest, source, 'Ответ', parent_id=root['id'])
    before_notes, before_discussions = stored([source['id'], target['id']]), comment_storage()
    original = Connection.execute
    def fail_notification(self, query, parameters=()):
        if query.startswith('UPDATE notifications SET payload='):
            m.fail(409, 'Simulated discussion-write conflict')
        return original(self, query, parameters)
    monkeypatch.setattr(Connection, 'execute', fail_notification)
    assert move(c, source, target, ['source']).status_code == 409
    assert stored(before_notes) == before_notes
    assert comment_storage() == before_discussions
