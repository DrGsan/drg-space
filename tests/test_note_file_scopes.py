import io
from zipfile import ZipFile
from tests.test_security import owner, reset, note
from tests.test_widgets import enable
from tests.test_community_files import upload
from app import main as m


def test_note_attachments_stay_outside_files_api_and_legacy_file_publications():
    c, _, _ = owner();enable(c, 'files')
    ordinary = upload(c, 'ordinary.txt', b'ordinary')
    nid = note(c)
    response = c.post('/api/notes/'+nid+'/files?name=manual.txt', content=b'note-only')
    assert response.status_code == 200, response.text
    attached = response.json();fid = attached['id']
    for view in ('active', 'recent', 'shared'):
        result = c.get('/api/files?view='+view).json()
        assert not any(row['id'] in (nid, fid) for row in result['items'])
        if view != 'shared':assert [row['id'] for row in result['items']] == [ordinary['id']]
    assert c.get('/api/files?parent_id='+nid).status_code == 404
    for path in ('content', 'text', 'shares'):
        assert c.get('/api/files/'+fid+'/'+path).status_code == 404
    assert c.post('/api/files/'+fid+'/shares', json={'mode': 'public'}).status_code == 404
    assert c.patch('/api/files/'+fid, json={'name': 'renamed.txt', 'version': 1}).status_code == 404
    assert c.post('/api/files/move', json={'items': [{'id': fid, 'version': 1}]}).status_code == 404
    assert c.post('/api/files/move', json={'items': [{'id': ordinary['id'], 'version': 1}], 'parent_id': nid}).status_code == 404
    assert c.post('/api/files/upload?parent_id='+nid+'&name=extra.txt', content=b'extra').status_code == 404
    assert c.delete('/api/files/'+fid).status_code == 404
    assert c.delete('/api/files/'+nid).status_code == 404
    assert c.post('/api/files/download', json={'ids': [fid]}).status_code == 404
    archive = c.get('/api/files/download.zip');assert archive.status_code == 200
    with ZipFile(io.BytesIO(archive.content)) as files:
        assert files.namelist() == ['ordinary.txt']
        assert files.read('ordinary.txt') == b'ordinary'
    with m.db() as db:
        db.execute('INSERT INTO file_shares VALUES(?,?,?,?,?,?,0,?,?)', ('legacy-note-file-link', nid, 'public', None, '[]', None, m.now(), 'view'))
    for path in ('', '/download.zip', '/'+fid+'/content'):
        assert c.get('/api/shared-files/legacy-note-file-link'+path).status_code == 404
    assert c.get(attached['url']).content == b'note-only'
    assert c.get('/api/notes/'+nid).status_code == 200


def test_files_trash_never_purges_note_attachments():
    c, _, _ = owner();enable(c, 'files');nid = note(c)
    attached = c.post('/api/notes/'+nid+'/files?name=keep.txt', content=b'keep').json()
    stamp = m.now()-8*86400
    with m.db() as db:
        db.execute('UPDATE files SET deleted=?,trash_batch=? WHERE id=? OR parent_id=?', (stamp, nid, nid, nid))
    result = c.get('/api/files/trash').json()
    assert result['items'] == []
    assert c.get('/api/files').json()['trash_count'] == 0
    with m.db() as db:
        assert db.execute('SELECT 1 FROM files WHERE id=?', (attached['id'],)).fetchone()


def test_note_attachment_cleanup_keeps_duplicate_and_history_references():
    import json
    from app.modules.note_files import purge_unused_attachments
    c,_,_=owner();nid=note(c);other=note(c)
    attached=c.post('/api/notes/'+nid+'/files?name=keep.txt',content=b'keep').json()
    stale=c.post('/api/notes/'+nid+'/files?name=discard.txt',content=b'discard').json()
    c.put('/api/notes/'+other,json={'title':'Reference','body':'[keep](http://testserver'+attached['url']+')'})
    c.put('/api/notes/'+other,json={'title':'New','body':'no attachment'})
    with m.db() as db:
        db.execute('UPDATE files SET created=? WHERE parent_id=?',(m.now()-2*86400,nid))
        db.execute('DELETE FROM notes WHERE id=?',(nid,))
        purge_unused_attachments(db,m.now())
        assert db.execute('SELECT 1 FROM files WHERE id=?',(attached['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM files WHERE id=?',(stale['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM blob_gc WHERE kind=?',('file',)).fetchone()
        db.execute('DELETE FROM note_revisions WHERE note_id=?',(other,))
        purge_unused_attachments(db,m.now())
        assert not db.execute('SELECT 1 FROM files WHERE id=? OR id=?',(nid,attached['id'])).fetchone()
