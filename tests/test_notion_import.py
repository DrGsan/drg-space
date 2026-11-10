import io
import json
import zipfile

import pytest
from tests.test_security import owner, member, reset
from app import main as m
from app.file_store import file_store

ROOT = 'a'*32
CHILD = 'b'*32
DB = 'c'*32
RECORD = 'd'*32


@pytest.fixture(autouse=True)
def no_external_image_network(monkeypatch):
    from app import notion_images
    async def unavailable(*args,**kwargs):raise OSError('Offline fixture')
    monkeypatch.setattr(notion_images,'fetch_public',unavailable)


def packed(files):
    body = io.BytesIO()
    with zipfile.ZipFile(body, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return body.getvalue()


def html(title, body):
    return '<html><article class="page sans"><header><h1 class="page-title">'+title+'</h1></header><div class="page-body">'+body+'</div></article></html>'


def sample():
    root = 'Export/Root '+ROOT
    database = root+'/Projects '+DB
    return packed({
        root+'.html': html('Root', '<figure class="link-to-page"><a href="Root%20'+ROOT+'/Child%20'+CHILD+'.html">Child</a></figure><p><a href="Root%20'+ROOT+'/manual.pdf">Manual<img src="https://example.org/preview.png"/></a></p><script>evil()</script>'),
        root+'/Child '+CHILD+'.html': html('Child', '<ul class="toggle"><li><details><summary>Commands</summary><pre><code class="language-bash">echo &lt;hello&gt;\n# keep me</code></pre><p><strong>Bold</strong><br/>tail</p></details></li></ul><ul class="to-do-list"><li><span class="checkbox checkbox-on"></span>Done</li></ul><figure class="image"><img src="picture.png"/><figcaption>Caption</figcaption></figure>'),
        root+'/picture.png': b'original-image-bytes',
        root+'/manual.pdf': b'%PDF-test-attachment',
        database+'.html': html('Projects', '<table class="collection-content"><thead><tr><th><svg class="typesTitle"></svg>Name</th><th><svg class="typesCheckbox"></svg>Done</th><th><svg class="typesNumber"></svg>Cost</th><th><svg class="typesMultipleSelect"></svg>Tags</th></tr></thead><tbody><tr><td><a href="Projects%20'+DB+'/Task%20'+RECORD+'.html">Task</a></td></tr></tbody></table>'),
        database+'.csv': 'Name,Done,Cost,Tags\nTask,Yes,42,"red, blue"\nHidden,No,3,red\n',
        database+'/Task '+RECORD+'.html': html('Task', '<p>Record body</p>'),
        root+'/unlinked.conf': b'preserve-unlinked',
    })


def preview(c, archive):
    response = c.post('/api/account/notion/preview', content=archive)
    assert response.status_code == 200, response.text
    return response.json()


def import_archive(c, archive, digest):
    return c.post('/api/account/notion/import', content=archive, headers={'x-import-confirmation': 'IMPORT', 'x-notion-preview': digest})


def test_nested_html_archive_preserves_tree_files_rich_blocks_database_and_isolation():
    c, _, _ = owner()
    other, _ = member(c)
    existing = c.post('/api/notes', json={'title': 'Existing'}).json()['id']
    archive = packed({'Export-Part-1.zip': sample()})
    summary = preview(c, archive)
    assert (summary['notes'], summary['databases'], summary['files'], summary['missing_links']) == (5, 1, 3, 0)
    assert len(c.get('/api/notes').json()) == 1
    result = import_archive(c, archive, summary['sha256'])
    assert result.status_code == 200, result.text
    pages = {p['title']: p for p in c.get('/api/notes').json()}
    assert existing == pages['Existing']['id']
    assert pages['Child']['parent_id'] == pages['Root']['id']
    assert pages['Task']['parent_id'] == pages['Projects']['id']
    assert pages['Hidden']['parent_id'] == pages['Projects']['id']
    blocks = pages['Child']['blocks']
    assert blocks[0]['type'] == 'toggleListItem'
    assert blocks[0]['children'][0]['type'] == 'codeBlock'
    assert blocks[0]['children'][0]['content'][0]['text'] == 'echo <hello>\n# keep me'
    assert blocks[0]['children'][1]['content'][0]['styles']['bold']
    assert blocks[1]['props']['checked'] is True
    assert blocks[2]['type'] == 'image' and blocks[2]['props']['caption'] == 'Caption'
    assert pages['Root']['blocks'][0]['props']['pageId'] == pages['Child']['id']
    assert all(part['type'] == 'text' for part in pages['Root']['blocks'][1]['content'][0]['content'])
    assert 'evil()' not in pages['Root']['body']
    database = c.get('/api/databases/'+pages['Projects']['id']).json()
    record = next(item for item in database['records'] if item['title'] == 'Task')
    assert record['properties'] == {'col_1': True, 'col_2': 42, 'col_3': ['red', 'blue']}
    with m.db() as conn:
        files = conn.execute("SELECT id,parent_id,name FROM files WHERE kind='file'").fetchall()
        assert len(files) == 3
        assert conn.execute('PRAGMA foreign_key_check').fetchone() is None
    for item in files:
        url = '/api/notes/'+item['parent_id']+'/files/'+item['id']
        assert c.get(url).status_code == 200
        assert other.get(url).status_code == 404
    assert len(other.get('/api/notes').json()) == 0
    assert not list(m.DATA.glob('.drg-notion-work-*'))


def test_modern_uuid_free_directories_keep_hierarchy_records_and_attachment_owners():
    c, _, _ = owner()
    archive = packed({
        'Работа '+ROOT+'.md': '# Работа\n\n[Child](Работа/Child%20'+CHILD+'.md)\n\n[Shared](Работа/Child/manual.txt)',
        'Работа/Child '+CHILD+'.md': '# Child\n\n[Manual](Child/manual.txt)',
        'Работа/Child/manual.txt': b'private manual',
        'Работа/Child/unlinked.conf': b'keep this too',
        'Работа/Data '+DB+'.csv': 'Name,Done\nTask,Yes\n,No\n',
        'Работа/Data '+DB+'_all.csv': 'Name,Done\nTask,Yes\n,No\n',
        'Работа/Data/Task '+RECORD+'.md': '# Task\n\nRecord body',
        'Работа/Data/Untitled '+'e'*32+'.md': '# Untitled\n\nEmpty title record',
    })
    summary = preview(c, archive)
    assert (summary['notes'], summary['databases'], summary['files']) == (5, 1, 2)
    assert import_archive(c, archive, summary['sha256']).status_code == 200
    pages = {p['title']: p for p in c.get('/api/notes').json()}
    assert pages['Child']['parent_id'] == pages['Работа']['id']
    assert pages['Data']['parent_id'] == pages['Работа']['id']
    assert pages['Task']['parent_id'] == pages['Data']['id']
    assert pages['Untitled']['parent_id'] == pages['Data']['id']
    assert len(c.get('/api/databases/'+pages['Data']['id']).json()['records']) == 2
    with m.db() as conn:
        files = [dict(row) for row in conn.execute("SELECT id,parent_id,name FROM files WHERE kind='file'")]
    assert len(files) == 2 and all(f['parent_id'] == pages['Child']['id'] for f in files)
    for file in files:
        assert c.get('/api/notes/'+pages['Child']['id']+'/files/'+file['id']).status_code == 200
    manual = next(f for f in files if f['name'] == 'manual.txt')
    assert manual['id'] in pages['Работа']['body']
    assert '/page/'+pages['Child']['id'] in pages['Работа']['body']


def test_combined_formats_resolve_parent_from_all_aliases_and_insert_in_tree_order():
    c, _, _ = owner()
    # ZIP path depth is unrelated to actual ancestry when formats/parts merge.
    archive = packed({
        'Export-html/Outer/Root '+ROOT+'.html': html('Root', '<p>Rich root</p>'),
        'Root '+ROOT+'.md': '# Root\n',
        'Root/Child '+CHILD+'.md': '# Child\n\n[Root](https://app.notion.com/p/'+ROOT+')',
    })
    summary = preview(c, archive)
    assert summary['notes'] == 2
    response = import_archive(c, archive, summary['sha256'])
    assert response.status_code == 200, response.text
    pages = {p['title']: p for p in c.get('/api/notes').json()}
    assert pages['Child']['parent_id'] == pages['Root']['id']
    assert '/page/'+pages['Root']['id'] in pages['Child']['body']


def test_ambiguous_directory_names_are_reported_without_guessing_a_parent():
    from contextlib import closing
    from app.notion_import import NotionArchive
    archive = packed({'Same '+ROOT+'.md': '# Same', 'Same '+DB+'.md': '# Same',
                      'Same/Child '+CHILD+'.md': '# Child'})
    with closing(NotionArchive(io.BytesIO(archive), m.DATA)) as parsed:
        assert parsed.by_uuid[CHILD].parent is None
        assert any('неоднозначные' in warning for warning in parsed.warnings)


def test_html_columns_styles_svg_icons_and_block_links_survive_fresh_import():
    c, _, _ = owner()
    anchor = '12345678-1234-1234-1234-123456789abc'
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><path fill="#ea1234" d="M0 0H32V32H0Z"/></svg>'
    root = html('Root', '<div class="column-list"><div class="column" style="width:40%"><p id="'+anchor+'" class="block-color-gray_background" style="text-align:center">Left</p><hr/></div><div class="column" style="width:60%"><figure class="link-to-page"><a href="Root/Child '+CHILD+'.html">Child</a></figure><p>Right</p></div></div><p><a href="Root '+ROOT+'.html#'+anchor+'">Jump</a></p>')
    child = html('Child', '<p>Body</p>').replace('<header>', '<header><span class="page-header-icon"><img src="Child/icon.svg"/></span>')
    archive = packed({'Root '+ROOT+'.html': root, 'Root/Child '+CHILD+'.html': child, 'Root/Child/icon.svg': svg})
    summary = preview(c, archive)
    response = import_archive(c, archive, summary['sha256'])
    assert response.status_code == 200, response.text
    pages = {p['title']: p for p in c.get('/api/notes').json()}
    columns = pages['Root']['blocks'][0]
    assert columns['type'] == 'columnList'
    assert [column['props']['width'] for column in columns['children']] == [40, 60]
    left = columns['children'][0]['children']
    assert left[0]['props'] == {'backgroundColor':'gray', 'textAlignment':'center'}
    assert left[0]['id'] == anchor.replace('-', '')
    assert left[1]['type'] == 'divider'
    assert columns['children'][1]['children'][0]['props']['pageId'] == pages['Child']['id']
    assert '#block='+anchor.replace('-', '') in pages['Root']['blocks'][1]['content'][0]['href']
    assert pages['Child']['icon'].startswith('image:')
    assert c.get(pages['Child']['icon_url']).status_code == 200
    # Native edits pass through the same canonical CRDT conversion as shared
    # pages. Reopening must retain layout and identities, not flatten the tree.
    saved = c.put('/api/notes/'+pages['Root']['id']+'/document', json={
        'version':pages['Root']['version'], 'title':'Root', 'body':'', 'blocks':pages['Root']['blocks']})
    assert saved.status_code == 200, saved.text
    assert c.get('/api/notes/'+pages['Root']['id']).json()['blocks'] == pages['Root']['blocks']


@pytest.mark.parametrize('svg', [
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///etc/passwd"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><use href="https://example.org/private"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><path fill="url(https://example.org/private)"/></svg>',
    '<!DOCTYPE svg [<!ENTITY leak SYSTEM "file:///etc/passwd">]><svg>&leak;</svg>',
])
def test_imported_svg_icons_never_resolve_external_resources(svg):
    from app.notion_svg import icon_png
    with pytest.raises(ValueError):
        icon_png(svg.encode())


def test_retry_operation_returns_committed_result_without_duplicate_notes_or_files():
    c, _, _ = owner()
    other, _ = member(c)
    archive = sample()
    summary = preview(c, archive)
    assert summary['idempotent_import'] is True
    headers = {'x-import-confirmation':'IMPORT', 'x-notion-preview':summary['sha256'], 'x-import-operation':'same-operation-after-lost-response'}
    first = c.post('/api/account/notion/import', content=archive, headers=headers)
    assert first.status_code == 200, first.text
    before = c.get('/api/notes').json()
    with m.db() as conn:
        count = conn.execute('SELECT COUNT(*) FROM files').fetchone()[0]
    second = c.post('/api/account/notion/import', content=archive, headers=headers)
    assert second.status_code == 200 and second.json()['replayed'] is True
    assert second.json()['imported'] == first.json()['imported']
    assert c.get('/api/notes').json() == before
    with m.db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM files').fetchone()[0] == count
    # Operation identities are scoped to the authenticated account.
    own_copy = other.post('/api/account/notion/import', content=archive, headers=headers)
    assert own_copy.status_code == 200 and 'replayed' not in own_copy.json()
    assert len(other.get('/api/notes').json()) == len(before)
    changed = packed({'Other '+ROOT+'.md':'# Other'})
    digest = preview(c, changed)['sha256']
    reused = c.post('/api/account/notion/import', content=changed, headers=headers | {'x-notion-preview':digest})
    assert reused.status_code == 409
    from app.import_receipts import purge,LIFETIME
    with m.db() as conn:
        purge(conn,m.now()+LIFETIME+1)
        assert conn.execute('SELECT COUNT(*) FROM app_settings WHERE key LIKE ?', ('notion-import-receipt:%',)).fetchone()[0] == 0
    assert c.get('/api/notes').json() == before


def test_notions_exported_emoji_metadata_is_used_when_header_has_no_text():
    from contextlib import closing
    from app.notion_import import NotionArchive
    content = html('Root', '<p>Body</p>').replace('class="page sans"', 'class="page sans" data-notion-page-icon="👨🏻‍💻"').replace('<header>', '<header><div class="page-header-icon"><svg></svg></div>')
    with closing(NotionArchive(io.BytesIO(packed({'Root '+ROOT+'.html':content})), m.DATA)) as parsed:
        assert parsed.by_uuid[ROOT].icon == '👨🏻‍💻'


def test_mixed_list_toggles_quotes_and_callouts_keep_all_sibling_code_blocks():
    from contextlib import closing
    from app.notion_import import NotionArchive
    body = '<ol><li>Lead<pre><code>before</code></pre><details><summary>Details</summary><pre><code>inside</code></pre></details><pre><code>after</code></pre></li></ol><blockquote><p>Quote</p><pre><code>quoted</code></pre><p>Tail</p></blockquote><figure class="callout"><span class="icon">💡</span><div><p>Callout</p><pre><code>callout code</code></pre></div></figure>'
    with closing(NotionArchive(io.BytesIO(packed({'Root '+ROOT+'.html':html('Root',body)})), m.DATA)) as parsed:
        blocks = parsed.by_uuid[ROOT].blocks
        assert blocks[0]['content'][0]['text'] == 'Lead'
        assert [b['type'] for b in blocks[0]['children']] == ['codeBlock','toggleListItem','codeBlock']
        assert blocks[1]['type'] == 'quote' and blocks[1]['children'][0]['type'] == 'codeBlock'
        assert blocks[2]['type'] == 'callout' and blocks[2]['children'][0]['type'] == 'codeBlock'
        def codes(items):
            return [b['content'][0]['text'] for b in items if b['type']=='codeBlock'] + [value for b in items for value in codes(b['children'])]
        assert sorted(codes(blocks)) == ['after','before','callout code','inside','quoted']


def test_html_database_without_csv_keeps_typed_properties_and_record_uuid_links():
    c, _, _ = owner()
    database = 'Projects '+DB
    table = '<table class="collection-content"><thead><tr><th><svg class="typesTitle"></svg>Name</th><th><svg class="typesCheckbox"></svg>Done</th><th><svg class="typesNumber"></svg>Cost</th></tr></thead><tbody><tr><td><a href="Projects/Task '+RECORD+'.html">Task</a></td><td><span class="checkbox checkbox-on"></span></td><td>42</td></tr><tr><td>Other</td><td><span class="checkbox checkbox-off"></span></td><td>7</td></tr></tbody></table>'
    archive = packed({database+'.html':html('Projects',table), 'Projects/Task '+RECORD+'.html':html('Task','<p>Record content</p>')})
    summary = preview(c,archive)
    assert summary['databases'] == 1 and summary['notes'] == 3
    response = import_archive(c,archive,summary['sha256'])
    assert response.status_code == 200,response.text
    pages = {p['title']:p for p in c.get('/api/notes').json()}
    assert pages['Task']['parent_id'] == pages['Projects']['id']
    records = c.get('/api/databases/'+pages['Projects']['id']).json()['records']
    assert {r['title']:r['properties'] for r in records} == {'Task':{'col_1':True,'col_2':42}, 'Other':{'col_1':False,'col_2':7}}
    assert pages['Task']['blocks'][0]['content'][0]['text'] == 'Record content'


def test_anchor_bookmark_keeps_text_boundaries_metadata_and_private_thumbnail():
    c, _, _ = owner()
    archive = packed({
        'Root '+ROOT+'.html': html('Root', '<figure><a class="bookmark source" href="https://example.org/manual"><div class="bookmark-title">Manual title</div><div class="bookmark-description">Useful description</div><div class="bookmark-href">example.org/manual</div><img class="icon bookmark-icon" src="icon.png"/><img class="bookmark-image" src="preview.png"/></a></figure>'),
        'preview.png': b'bookmark-thumbnail',
        'icon.png': b'favicon',
    })
    response = import_archive(c, archive, preview(c, archive)['sha256'])
    assert response.status_code == 200, response.text
    page = c.get('/api/notes').json()[0]
    card = page['blocks'][0]
    assert card['type'] == 'paragraph'
    assert card['props']['title'] == 'Manual title'
    assert card['props']['description'] == 'Useful description'
    assert card['props']['linkDisplay'] == 'bookmark'
    assert card['content'][0]['content'][0]['text'] == 'Manual title\nUseful description\nexample.org/manual'
    assert c.get(card['props']['url']).content == b'bookmark-thumbnail'


def test_import_report_translates_warnings_without_changing_user_titles():
    c, _, _ = owner()
    c.headers['x-drg-language'] = 'en'
    response = c.post('/api/account/notion/preview', content=packed({'Русское название '+ROOT+'.md':'# Русское название\n\nТекст'}))
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['roots'] == ['Русское название']
    assert any('Markdown export' in warning for warning in value['warnings'])


def test_markdown_balanced_links_fences_title_column_and_all_csv():
    c, _, _ = owner()
    root = 'Root '+ROOT
    child = root+'/Child (test) '+CHILD
    database = root+'/Data '+DB
    archive = packed({root+'.md': '# Root\n\n[Child]('+child+'.md)\n\n```bash\n[x](missing.conf)\n```',
                      child+'.md': '# Child (test)\n\n![Picture](picture.png "caption")',
                      root+'/picture.png': b'png', database+'.csv': 'Slot,Model\n1,Task\n',
                      database+'_all.csv': 'Slot,Model\n1,Task\n2,Hidden\n',
                      database+'/Task '+RECORD+'.md': '# Task\n\nDescription'})
    summary = preview(c, archive)
    assert summary['missing_links'] == 0
    assert import_archive(c, archive, summary['sha256']).status_code == 200
    pages = {p['title']: p for p in c.get('/api/notes').json()}
    assert '[Child](http://testserver/page/'+pages['Child (test)']['id']+')' in pages['Root']['body']
    assert '[x](missing.conf)' in pages['Root']['body']
    assert len(c.get('/api/databases/'+pages['Data']['id']).json()['records']) == 2
    assert pages['Child (test)']['blocks'] is None


@pytest.mark.parametrize('name', ['../escape.md', '/absolute', 'C:/file', 'dir/../../escape', 'dir\\escape'])
def test_rejects_zip_paths_before_writing_any_data(name):
    c, _, _ = owner()
    response = c.post('/api/account/notion/preview', content=packed({name: 'evil', 'Root '+ROOT+'.md': '# Root'}))
    assert response.status_code == 422, response.text
    assert c.get('/api/notes').json() == []
    assert not list(m.DATA.glob('.drg-notion-work-*'))


def test_digest_confirmation_corruption_and_quota_leave_no_partial_import(monkeypatch):
    c, _, _ = owner()
    archive = sample()
    value = preview(c, archive)
    assert c.post('/api/account/notion/import', content=archive).status_code == 422
    assert import_archive(c, archive, '0'*64).status_code == 409
    assert c.post('/api/account/notion/preview', content=b'not a zip').status_code == 422
    uid = c.get('/api/me').json()['id']
    store = file_store(m.db, m.DATA)
    before = store.ids()
    with m.db() as conn:
        conn.execute('UPDATE user_limits SET limit_bytes=used_bytes+10 WHERE user_id=?', (uid,))
    response = import_archive(c, archive, value['sha256'])
    assert response.status_code == 413, response.text
    assert c.get('/api/notes').json() == []
    assert store.ids() == before


def test_inline_database_does_not_replace_parent_document():
    c, _, _ = owner()
    root = 'Root '+ROOT
    database = root+'/Data '+DB
    archive = packed({root+'.html': html('Root', '<h2>Before</h2><table class="collection-content"><tr><td><a href="'+database+'/Task '+RECORD+'.html">Task</a></td></tr></table><p>After</p>'),
                      database+'.csv': 'Name\nTask\n', database+'/Task '+RECORD+'.html': html('Task', '<p>Body</p>')})
    value = preview(c, archive)
    assert value['databases'] == 1
    assert import_archive(c, archive, value['sha256']).status_code == 200
    pages = {p['title']: p for p in c.get('/api/notes').json()}
    assert pages['Root']['kind'] == 'page'
    assert [b['type'] for b in pages['Root']['blocks']] == ['heading', 'pageLink', 'paragraph']
    assert pages['Root']['blocks'][1]['props']['pageId'] == pages['Data']['id']


def test_two_formats_merge_uuid_pages_and_equal_files_keep_distinct_attachments():
    c, _, _ = owner()
    html_root = 'Export-html/Root '+ROOT
    md_root = 'Export-md/Root '+ROOT
    first = packed({html_root+'.html': html('Root', '<p>Rich <strong>HTML</strong></p>'),
                    html_root+'/same.txt': 'same', html_root+'/changed.txt': 'HTML version'})
    second = packed({md_root+'.md': '# Root\nMarkdown fallback', md_root+'/same.txt': 'same',
                     md_root+'/changed.txt': 'Markdown version', 'Export-md/Extra '+CHILD+'.md': '# Extra\nBody'})
    archive = first+second
    headers = {'x-notion-parts': json.dumps([len(first), len(second)])}
    value = c.post('/api/account/notion/preview', content=archive, headers=headers).json()
    assert value['notes'] == 2 and value['files'] == 3
    response = c.post('/api/account/notion/import', content=archive, headers=headers | {'x-import-confirmation': 'IMPORT', 'x-notion-preview': value['sha256']})
    assert response.status_code == 200, response.text
    pages = {p['title']: p for p in c.get('/api/notes').json()}
    assert pages['Root']['blocks'][0]['content'][1]['styles']['bold']
    assert pages['Extra']['body'] == 'Body'
    assert c.post('/api/account/notion/preview', content=archive, headers={'x-notion-parts': '[1]'}).status_code == 422


def test_export_parts_with_equal_paths_and_cross_part_parent_links_merge_safely():
    c, _, _ = owner()
    first = packed({'Root '+ROOT+'.md':'# Root\n\n[Child](Root/Child%20'+CHILD+'.md)\n\n[File](Root/manual.txt)',
                    'Root/shared.txt':'same bytes'})
    second = packed({'Root '+ROOT+'.html':html('Root','<figure class="link-to-page"><a href="Root/Child '+CHILD+'.md">Child</a></figure><p><a href="Root/manual.txt">Manual</a></p>'),
                     'Root/shared.txt':'same bytes'})
    third = packed({'Root/Child '+CHILD+'.md':'# Child\n\nBody', 'Root/manual.txt':'in another export part'})
    archive = first+second+third
    headers = {'x-notion-parts':json.dumps([len(first),len(second),len(third)])}
    summary = c.post('/api/account/notion/preview',content=archive,headers=headers).json()
    assert (summary['notes'],summary['files'],summary['missing_links']) == (2,2,0)
    response = c.post('/api/account/notion/import',content=archive,headers=headers | {'x-import-confirmation':'IMPORT','x-notion-preview':summary['sha256']})
    assert response.status_code == 200,response.text
    pages = {p['title']:p for p in c.get('/api/notes').json()}
    assert pages['Child']['parent_id'] == pages['Root']['id']
    assert pages['Root']['blocks'][0]['props']['pageId'] == pages['Child']['id']
    with m.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM files WHERE kind='file'").fetchone()[0] == 2


def test_export_workspace_indexes_are_skipped_but_user_html_is_kept():
    c,_,_=owner()
    service='<html><head><title>Export-1234</title></head><body><ul id="id::workspace"><li>Workspace identifier: 1234</li></ul></body></html>'
    archive=packed({'Export-a/index.html':service,'Export-a/Root '+ROOT+'.html':html('Root','<p>Content</p>'),'Export-a/Root '+ROOT+'/index.html':'<html>My own file</html>'})
    summary=preview(c,archive);assert summary['files']==1
    result=import_archive(c,archive,summary['sha256']);assert result.status_code==200,result.text
    p=c.get('/api/notes').json()[0]
    assert p['blocks'][-1]['props']['name']=='index.html'
    assert c.get(p['blocks'][-1]['props']['url']).content==b'<html>My own file</html>'


def test_remote_images_become_private_files_deduplicate_and_report_unavailable(monkeypatch):
    from app import notion_images
    from tests.test_widgets import image_bytes
    body=image_bytes();calls=[]
    original='https://avatars.mds.yandex.net/get-zen_doc/1/pub_image/scale_600'
    alternative=original.replace('avatars.mds.yandex.net','avatars.dzeninfra.ru')
    async def fetch(url,**kwargs):
        calls.append(url)
        if url in (alternative,'https://images.example/copy.png'):return body,url,'text/plain'
        raise OSError('No image')
    monkeypatch.setattr(notion_images,'fetch_public',fetch)
    c,_,_=owner();g,_=member(c);root='Root '+ROOT
    archive=packed({root+'.html':html('Root','<figure class="image"><img src="'+original+'"><figcaption>Original caption</figcaption></figure><img src="https://images.example/copy.png"><img src="https://images.example/missing.png">')})
    value=preview(c,archive);assert value['remote_images']==3 and value['files']==0 and calls==[]
    result=import_archive(c,archive,value['sha256']);assert result.status_code==200,result.text
    summary=result.json()['imported'];assert summary['downloaded_images']==2 and summary['files']==1
    assert summary['unavailable_images']==['https://images.example/missing.png']
    assert original in calls and alternative in calls
    note=c.get('/api/notes').json()[0];first,second,missing=note['blocks']
    assert first['props']['url']==second['props']['url'] and first['props']['url'].startswith('http://testserver/api/notes/')
    assert first['props']['caption']=='Original caption'
    assert missing['props']['url']=='https://images.example/missing.png'
    response=c.get(first['props']['url']);assert response.content==body and response.headers['content-type'].startswith('image/png')
    assert g.get(first['props']['url']).status_code==404
    assert not list(m.DATA.glob('.drg-notion-work-*'))


def test_remote_markdown_images_preserve_code_and_failed_quota_removes_objects(monkeypatch):
    from app import notion_images
    from tests.test_widgets import image_bytes
    body=image_bytes();url='https://images.example/image'
    async def fetch(value,**kwargs):return body,value,'image/png'
    monkeypatch.setattr(notion_images,'fetch_public',fetch)
    c,_,_=owner();root='Root '+ROOT
    archive=packed({root+'.md':'# Root\n\n![Caption]('+url+')\n\n```markdown\n![Code]('+url+')\n```'})
    value=preview(c,archive);store=file_store(m.db,m.DATA);before=store.ids();uid=c.get('/api/me').json()['id']
    with m.db() as db:db.execute('UPDATE user_limits SET limit_bytes=used_bytes+10 WHERE user_id=?',(uid,))
    assert import_archive(c,archive,value['sha256']).status_code==413
    assert store.ids()==before and c.get('/api/notes').json()==[]
    with m.db() as db:db.execute('UPDATE user_limits SET limit_bytes=? WHERE user_id=?',(1024**3,uid))
    result=import_archive(c,archive,value['sha256']);assert result.status_code==200,result.text
    note=c.get('/api/notes').json()[0]
    assert '![Caption](http://testserver/api/notes/' in note['body']
    assert '![Code]('+url+')' in note['body']


def test_duplicate_zip_and_remote_image_uses_one_object(monkeypatch):
    from app import notion_images
    from tests.test_widgets import image_bytes
    body=image_bytes();url='https://images.example/same.png'
    async def fetch(value,**kwargs):return body,value,'image/png'
    monkeypatch.setattr(notion_images,'fetch_public',fetch)
    c,_,_=owner();root='Root '+ROOT
    archive=packed({root+'.html':html('Root','<img src="'+url+'">'),root+'/original.png':body})
    value=preview(c,archive);result=import_archive(c,archive,value['sha256']);assert result.status_code==200,result.text
    assert result.json()['imported']['files']==1 and result.json()['imported']['downloaded_images']==1
    with m.db() as db:assert db.execute("SELECT COUNT(*) FROM files WHERE kind='file'").fetchone()[0]==1


def test_import_lease_prevents_concurrent_process_work_and_cleans_staging():
    from app.process_locks import try_file_lock
    c,_,_=owner();archive=sample()
    with try_file_lock(m.DATA,'notion-import.lock'):
        assert c.post('/api/account/notion/preview',content=archive).status_code==423
    assert not list(m.DATA.glob('.drg-notion-work-*'))
    assert c.post('/api/account/notion/preview',content=archive).status_code==200


def test_html_decoration_toggle_sections_and_file_blocks_survive_combined_markdown_export():
    from PIL import Image
    c, _, _ = owner()
    image = io.BytesIO()
    Image.new('RGB', (48, 24), 'blue').save(image, format='PNG')
    root = 'Page '+ROOT
    content = ('<article class="page"><header><span class="page-header-icon">🛠️</span>'
               '<img class="page-cover-image" src="cover.png"><h1 class="page-title">Page</h1></header>'
               '<div class="page-body"><details><summary><h2>Section</h2></summary>'
               '<figure class="file"><a href="config.yaml">config.yaml</a><figcaption>Configuration</figcaption></figure>'
               '<details><summary>Nested</summary><pre><code class="language-bash">echo hello</code></pre></details>'
               '</details></div></article>')
    archive = packed({root+'.html': content, root+'.md': '# Page\n\n## Section',
                      'config.yaml': b'original-config', 'cover.png': image.getvalue()})
    summary = preview(c, archive)
    assert summary['notes'] == 1
    assert import_archive(c, archive, summary['sha256']).status_code == 200
    page = c.get('/api/notes').json()[0]
    assert page['icon'] == '🛠️'
    assert page['cover'].startswith('image:')
    assert c.get(page['cover_url']).status_code == 200
    section = page['blocks'][0]
    assert section['type'] == 'heading' and section['props'] == {'level': 2, 'isToggleable': True}
    attachment, nested = section['children']
    assert attachment['type'] == 'file' and attachment['props']['name'] == 'config.yaml'
    assert attachment['props']['caption'] == 'Configuration'
    assert c.get(attachment['props']['url']).content == b'original-config'
    assert nested['type'] == 'toggleListItem'
    assert nested['children'][0]['content'][0]['text'] == 'echo hello'


def test_markdown_preview_reports_missing_notion_decoration_and_toggle_metadata():
    c, _, _ = owner()
    summary = preview(c, packed({'Page '+ROOT+'.md': '# Page\n\n### Section\n\nBody'}))
    assert any('HTML-экспорт' in message for message in summary['warnings'])


def test_embedded_html_icon_and_cover_are_saved_without_network():
    import base64
    from urllib.parse import quote_from_bytes
    from PIL import Image
    image=io.BytesIO()
    Image.new('RGB',(48,24),'green').save(image,format='PNG')
    icon='data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()
    cover='data:image/png,'+quote_from_bytes(image.getvalue(),safe='')
    content='<article class="page"><header><img class="page-header-icon" src="'+icon+'"><img class="page-cover-image" src="'+cover+'"><h1 class="page-title">Inline</h1></header><div class="page-body"><p>Body</p></div></article>'
    c,_,_=owner()
    archive=packed({'Inline '+ROOT+'.html':content})
    result=import_archive(c,archive,preview(c,archive)['sha256'])
    assert result.status_code==200,result.text
    assert result.json()['imported']['issues']==[]
    page=c.get('/api/notes').json()[0]
    for kind in ('icon','cover'):
        assert page[kind].startswith('image:')
        assert c.get(page[kind+'_url']).status_code==200


def test_modern_file_figures_keep_download_names_and_text_links_stay_text():
    from urllib.parse import quote
    c,_,_=owner()
    filename='Расходы за месяц.pdf'
    stored_name=quote(filename.replace(' ','_'))
    content=html('Files','<figure id="'+CHILD+'"><div class="source"><a href="'+quote(stored_name)+'" data-notion-file="'+quote(stored_name)+'" data-notion-file-alias="'+filename+'">'+filename+'</a></div></figure><p><a href="https://example.org/guide">Guide</a></p><p>https://example.org/plain</p><figure><a class="bookmark source" href="https://example.org/card"><div class="bookmark-title">Card</div></a></figure>')
    archive=packed({'Files '+ROOT+'.html':content,stored_name:b'%PDF-original'})
    result=import_archive(c,archive,preview(c,archive)['sha256'])
    assert result.status_code==200,result.text
    page=c.get('/api/notes').json()[0]
    attachment,link,plain,card=page['blocks']
    assert attachment['id']==CHILD and attachment['type']=='file'
    assert attachment['props']['name']==filename
    download=c.get(attachment['props']['url'])
    assert download.content==b'%PDF-original'
    assert quote(filename) in download.headers['content-disposition']
    assert link['props']['linkDisplay']==plain['props']['linkDisplay']=='url'
    assert card['props']['linkDisplay']=='bookmark'


@pytest.mark.parametrize('value',[
    'data:text/html;base64,PHNjcmlwdD4=',
    'data:image/png;base64,not base64!',
    'data:image/png;base64,',
    'data:image/png;charset=utf-8;base64,YQ==',
    'data:image/png,'+'x'*(5*1024**2+1),
])
def test_embedded_decoration_rejects_invalid_or_oversized_data(value):
    from app.notion_images import embedded_image
    with pytest.raises(ValueError):embedded_image(value)
