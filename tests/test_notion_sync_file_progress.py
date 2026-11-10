import json
import uuid
from collections import Counter
import pytest
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,ROOT
from app import main as m
from app.notion_sync.client import NotionError
from app.notion_sync.state import dump


def pending_files():
    c,s,p,service,stamp=initialized();downloads=[]
    p.blocks[ROOT]=[{'id':str(uuid.uuid5(uuid.NAMESPACE_URL,'checkpoint-file-'+str(i))),'type':'file','file':{'type':'external','external':{'url':'https://files.example/'+name}}} for i,name in enumerate(('good.pdf','missing.pdf'))]
    def fetch(url,limit):
        downloads.append(url.rsplit('/',1)[-1])
        if url.endswith('missing.pdf'):raise NotionError('notionAssetUnavailable',502)
        return b'private encrypted file checkpoint'
    service.fetch=fetch;rid=service.preview(s['user_id'],'read',ROOT)['id'];assert service.work_once()
    state=service.status(s['user_id'])['roots'][0];service.confirm(s['user_id'],rid,state['preview_hash']);assert service.work_once()
    return c,s,p,service,stamp,rid,downloads


def retry(service,stamp,rid):
    with m.db() as db:stamp[0]=db.execute('SELECT MIN(due) FROM notion_jobs WHERE root_id=?',(rid,)).fetchone()[0]+1
    assert service.work_once()


def test_successful_files_survive_failure_restart_and_eventual_publication():
    c,s,p,service,stamp,rid,downloads=pending_files()
    retry(service,stamp,rid);assert Counter(downloads)=={'good.pdf':1,'missing.pdf':2}
    with m.db() as db:
        page=db.execute('SELECT payload,note_id FROM notion_pages WHERE root_id=?',(rid,)).fetchone();assets=json.loads(page['payload'])['pending_assets']
        cached=next(iter(assets.values()));row=db.execute('SELECT object_id FROM files WHERE id=?',(cached['id'],)).fetchone();assert row['object_id']==cached['object_id']
    assert c.get('/api/notes').json()==[]
    from app.file_store import file_store
    store=file_store(m.db,m.DATA);assert store.exists(cached['object_id'])
    assert b''.join(store.iter_read(cached['object_id']))==b'private encrypted file checkpoint'
    # A new service/process reads the durable progress, not an in-memory cache.
    from app.notion_sync.service import SyncService
    service=SyncService(m.db,service.config,m.DATA,m.PUBLIC_URL,clock=lambda:stamp[0],transport=service.connections.transport,wait=service.connections.wait,fetch=lambda url,limit:(downloads.append(url.rsplit('/',1)[-1]) or b'now available'))
    retry(service,stamp,rid)
    assert Counter(downloads)=={'good.pdf':1,'missing.pdf':3}
    notes=c.get('/api/notes').json();assert len(notes)==1
    assert c.get('/api/notes/'+page['note_id']+'/files/'+cached['id']).content==b'private encrypted file checkpoint'
    with m.db() as db:
        assert 'pending_assets' not in json.loads(db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(rid,)).fetchone()[0])
        assert db.execute("SELECT COUNT(*) FROM files WHERE kind='file'").fetchone()[0]==2


def test_disconnect_discards_only_unpublished_file_progress_and_queues_gc():
    c,s,p,service,stamp,rid,downloads=pending_files()
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(rid,)).fetchone()[0]);cached=next(iter(value['pending_assets'].values()))
    state=service.status(s['user_id'])['roots'][0];result=service.action(s['user_id'],rid,'disconnect',state['version']);assert result['status']=='detached'
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM files WHERE id=?',(cached['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM blob_gc WHERE id=? AND kind=?',(cached['object_id'],'file')).fetchone()
        assert not db.execute('SELECT 1 FROM notion_pages WHERE root_id=?',(rid,)).fetchone()


def test_changed_source_file_retires_old_pending_object_without_reusing_other_content():
    c,s,p,service,stamp,rid,downloads=pending_files()
    with m.db() as db:
        cached=next(iter(json.loads(db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(rid,)).fetchone()[0])['pending_assets'].values()))
    p.blocks[ROOT][0]['file']['external']['url']='https://files.example/replaced.pdf'
    retry(service,stamp,rid)
    assert downloads==['good.pdf','missing.pdf','replaced.pdf','missing.pdf']
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM files WHERE id=?',(cached['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM blob_gc WHERE id=?',(cached['object_id'],)).fetchone()


@pytest.mark.parametrize('stop',['pause','disconnect','role','grant'])
def test_pause_during_download_cannot_commit_a_checkpoint(stop):
    c,s,p,service,stamp=initialized()
    p.blocks[ROOT]=[{'id':str(uuid.uuid4()),'type':'file','file':{'type':'external','external':{'url':'https://files.example/private.pdf'}}}]
    rid=service.preview(s['user_id'],'read',ROOT)['id'];assert service.work_once();state=service.status(s['user_id'])['roots'][0];service.confirm(s['user_id'],rid,state['preview_hash'])
    def fetch(*_):
        if stop in ('pause','disconnect'):
            state=service.status(s['user_id'])['roots'][0];service.action(s['user_id'],rid,stop,state['version'])
        else:
            with m.db() as db:
                if stop=='role':db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
                else:db.execute("UPDATE notion_connections SET status='reauthorize' WHERE id='read'")
        return b'private file'
    service.fetch=fetch;assert service.work_once()
    with m.db() as db:
        assert not db.execute("SELECT 1 FROM files WHERE kind='file'").fetchone()
        row=db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(rid,)).fetchone()
        assert not row or 'pending_assets' not in json.loads(row[0])


def test_old_pending_files_survive_attachment_retention_but_remain_outside_files_module():
    from app.modules.note_files import purge_unused_attachments
    c,s,p,service,stamp,rid,downloads=pending_files()
    with m.db() as db:
        cached=next(iter(json.loads(db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(rid,)).fetchone()[0])['pending_assets'].values()))
        db.execute('UPDATE files SET created=?',(int(stamp[0])-3*86400,));purge_unused_attachments(db,int(stamp[0]))
        assert db.execute('SELECT 1 FROM files WHERE id=?',(cached['id'],)).fetchone()
    items=c.get('/api/files').json()['items'];assert all(row['id']!=cached['id'] for row in items)
    state=service.status(s['user_id'])['roots'][0];service.action(s['user_id'],rid,'pause',state['version'])
    with m.db() as db:
        purge_unused_attachments(db,int(stamp[0])+7*86400)
        assert db.execute('SELECT 1 FROM files WHERE id=?',(cached['id'],)).fetchone()


def test_full_backup_preserves_encrypted_pending_files_and_restore_pauses_sync():
    from tests.test_admin import backup,headers
    from app.file_store import file_store
    from app.notion_sync.pending_assets import references
    c,s,p,service,stamp,rid,downloads=pending_files()
    with m.db() as db:cached=next(iter(json.loads(db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(rid,)).fetchone()[0])['pending_assets'].values()))
    archive=backup(c);result=c.post('/api/admin/backups/restore',content=archive,headers=headers());assert result.status_code==200,result.text
    with m.db() as db:
        root=dict(db.execute('SELECT * FROM notion_roots WHERE id=?',(rid,)).fetchone());assert root['status']=='paused' and root['mode']=='pull'
        assert cached['id'] in references(db)
        object_id=db.execute('SELECT object_id FROM files WHERE id=?',(cached['id'],)).fetchone()[0]
        link=dict(db.execute('SELECT * FROM notion_pages WHERE root_id=?',(rid,)).fetchone())
    from app.notion_sync.pending_assets import previous
    resumed=previous(service,root,link,p.blocks[ROOT]);assert next(iter(resumed.values()))['object_id']==object_id
    assert b''.join(file_store(m.db,m.DATA).iter_read(object_id))==b'private encrypted file checkpoint'
