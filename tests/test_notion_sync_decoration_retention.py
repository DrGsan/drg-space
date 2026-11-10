"""Recoverable Notion decorations survive GC and never reuse invalid ownership."""
import io
import json
import pytest
from PIL import Image

from tests.test_security import reset,member
from tests.test_notion_sync_service import initialized,imported,ROOT,rt
from app import main as m
from app.data_crypto import MediaStore,media_store,StorageError
from app.modules.note_assets import purge_unused_assets
from app.notion_sync import access
from app.notion_sync.client import NotionError
from app.notion_sync.decorations import owned,retained
from app.notion_sync.recovery import review,resolve


def linked(kind):
    c,s,p,service,stamp=initialized();downloads=[]
    image=io.BytesIO();Image.new('RGB',(32,32),(28,80,120)).save(image,format='PNG')
    p.pages[ROOT][kind]={'type':'external','external':{'url':'https://synthetic.example/'+kind+'.png'}}
    service.fetch=lambda url,limit:(downloads.append(url) or image.getvalue())
    rid,mapping=imported(service,s);nid=mapping[ROOT]
    note=c.get('/api/notes/'+nid).json();aid=note[kind].removeprefix('image:')
    return c,s,p,service,stamp,rid,nid,aid,downloads


def conflict(state,kind):
    c,s,p,service,stamp,rid,nid,aid,downloads=state
    with m.db() as db:db.execute("UPDATE notion_roots SET mode='duplex' WHERE id=?",(rid,))
    access.write_enabled();note=c.get('/api/notes/'+nid).json()
    changed=c.patch('/api/notes/'+nid,json={kind:'🧪' if kind=='icon' else '', 'version':note['version']})
    assert changed.status_code==200,changed.text
    p.blocks[ROOT][0]['paragraph']['rich_text']=rt('Changed at source')
    with m.db() as db:root=dict(db.execute('SELECT * FROM notion_roots WHERE id=?',(rid,)).fetchone())
    client=service.connections.client('read')
    try:service.pull(root,client,ROOT)
    finally:client.close()
    with m.db() as db:assert db.execute('SELECT status FROM notion_roots WHERE id=?',(rid,)).fetchone()[0]=='conflict'


def recover(state,keep=False):
    c,s,p,service,stamp,rid,nid,aid,downloads=state
    value=review(service,s['user_id'],rid,nid)
    request={key:value[key] for key in ('note_id','version','note_version','local_hash')}
    request.update(review_hash=value['hash'],direction='pull',keep_copy=keep)
    return resolve(service,s['user_id'],rid,request)


@pytest.mark.parametrize('kind',['icon','cover'])
@pytest.mark.parametrize('keep',[False,True])
def test_conflict_decoration_survives_retention_and_real_recovery(kind,keep):
    state=linked(kind);c,s,p,service,stamp,rid,nid,aid,downloads=state
    conflict(state,kind);stamp[0]+=86410
    before=len(downloads)
    with m.db() as db:
        purge_unused_assets(db,int(stamp[0]))
        assert db.execute('SELECT 1 FROM media WHERE id=?',(aid,)).fetchone()
        assert not db.execute("SELECT 1 FROM blob_gc WHERE id=? AND kind='media'",(aid,)).fetchone()
    result=recover(state,keep)
    assert bool(result['independent_copy'])==keep
    note=c.get('/api/notes/'+nid).json();assert note[kind]=='image:'+aid
    assert c.get(note[kind+'_url']).status_code==200
    assert len(downloads)==before
    assert service.status(s['user_id'])['roots'][0]['mode']=='duplex'


@pytest.mark.parametrize('kind',['icon','cover'])
@pytest.mark.parametrize('damage',['asset-row','media-row','drained-gc','physical'])
def test_deleted_sql_or_physical_decoration_is_reloaded_not_reused(kind,damage):
    state=linked(kind);c,s,p,service,stamp,rid,nid,aid,downloads=state
    conflict(state,kind)
    store=media_store(m.db,m.DATA)
    with m.db() as db:
        if damage=='asset-row':db.execute('DELETE FROM note_assets WHERE id=?',(aid,))
        if damage in ('media-row','drained-gc'):
            db.execute('DELETE FROM media WHERE id=?',(aid,))
            db.execute("INSERT INTO blob_gc VALUES(?,'media',?)",(aid,int(stamp[0])))
    if damage=='drained-gc':m.files_router.cleanup()
    if damage=='physical':store.delete(aid)
    assert store.exists(aid)==(damage in ('asset-row','media-row'))
    before=len(downloads);recover(state)
    note=c.get('/api/notes/'+nid).json();new=note[kind].removeprefix('image:')
    assert new!=aid and len(downloads)==before+1
    assert c.get(note[kind+'_url']).status_code==200
    with m.db() as db:
        row=db.execute('SELECT a.note_id,a.kind,m.user_id FROM note_assets a JOIN media m ON m.id=a.id WHERE a.id=?',(new,)).fetchone()
        assert row['note_id']==nid and row['kind']==kind and row['user_id']==s['user_id']


@pytest.mark.parametrize('kind',['icon','cover'])
@pytest.mark.parametrize('foreign',['owner','page','kind'])
def test_foreign_decoration_is_neither_probed_nor_used_as_failure_fallback(kind,foreign,monkeypatch):
    state=linked(kind);c,s,p,service,stamp,rid,nid,aid,downloads=state
    conflict(state,kind)
    guest,_=member(c);other_uid=guest.get('/api/me').json()['id']
    other=c.post('/api/notes',json={'title':'Other local page'}).json()['id']
    with m.db() as db:
        if foreign=='owner':db.execute('UPDATE media SET user_id=? WHERE id=?',(other_uid,aid))
        if foreign=='page':db.execute('UPDATE note_assets SET note_id=? WHERE id=?',(other,aid))
        if foreign=='kind':db.execute('UPDATE note_assets SET kind=? WHERE id=?',('cover' if kind=='icon' else 'icon',aid))
        payload=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(nid,)).fetchone()[0])
        assert owned(db,s['user_id'],nid,payload['assets'])=={}
        assert aid not in retained(db,{aid})
    original=MediaStore.exists
    def checked(self,identity):
        assert identity!=aid,'A foreign image must not be probed'
        return original(self,identity)
    monkeypatch.setattr(MediaStore,'exists',checked)
    def unavailable(*_):raise NotionError('notionAssetUnavailable',502)
    service.fetch=unavailable;recover(state)
    note=c.get('/api/notes/'+nid).json();assert note[kind]==('📄' if kind=='icon' else '')
    assert kind+'_url' not in note
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(nid,)).fetchone()[0])
        assert value[kind+'_pending'] and 'decoration:'+kind not in value['assets']
        assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE root_id=? AND direction='cover'",(rid,)).fetchone()[0]==1


@pytest.mark.parametrize('kind',['icon','cover'])
@pytest.mark.parametrize('release',['supersede','disconnect'])
def test_obsolete_manifest_decorations_are_eventually_collected_automatically(kind,release):
    state=linked(kind);c,s,p,service,stamp,rid,nid,aid,downloads=state
    conflict(state,kind);stamp[0]+=86410
    with m.db() as db:
        purge_unused_assets(db,int(stamp[0]));assert db.execute('SELECT 1 FROM media WHERE id=?',(aid,)).fetchone()
    if release=='disconnect':
        current=service.status(s['user_id'])['roots'][0]
        service.action(s['user_id'],rid,'disconnect',current['version'])
    else:
        p.pages[ROOT][kind]={'type':'emoji','emoji':'🌍'} if kind=='icon' else None
        recover(state)
    with m.db() as db:
        purge_unused_assets(db,int(stamp[0]))
        assert not db.execute('SELECT 1 FROM media WHERE id=?',(aid,)).fetchone()
        assert db.execute("SELECT 1 FROM blob_gc WHERE id=? AND kind='media'",(aid,)).fetchone()
    m.files_router.cleanup();assert not media_store(m.db,m.DATA).exists(aid)


@pytest.mark.parametrize('kind',['icon','cover'])
def test_storage_failure_does_not_overwrite_last_good_revision_or_trigger_provider_retries(kind,monkeypatch):
    state=linked(kind);c,s,p,service,stamp,rid,nid,aid,downloads=state
    conflict(state,kind);before=len(downloads)
    def unavailable(*_):raise StorageError('synthetic storage unavailable')
    monkeypatch.setattr(MediaStore,'exists',unavailable)
    with pytest.raises(NotionError,match='notionAssetUnavailable'):recover(state)
    assert len(downloads)==before
    assert c.get('/api/notes/'+nid).json()[kind]==('🧪' if kind=='icon' else '')
    with m.db() as db:
        assert db.execute('SELECT 1 FROM media WHERE id=?',(aid,)).fetchone()
        assert db.execute('SELECT status FROM notion_roots WHERE id=?',(rid,)).fetchone()[0]=='conflict'


def test_manifest_retention_batches_candidates_without_reading_unrelated_page_snapshots(monkeypatch):
    state=linked('icon');c,s,p,service,stamp,rid,nid,aid,downloads=state
    with m.db() as db:
        unrelated='unrelated-page-1'
        db.execute('INSERT INTO notes(id,user_id,title,body,blocks,created,updated) VALUES(?,?,?,?,?,?,?)',(unrelated,s['user_id'],'Unrelated','', '[]',int(stamp[0]),int(stamp[0])))
        db.execute("INSERT INTO notion_pages(id,user_id,root_id,notion_id,note_id,status,payload,updated) VALUES(?,?,?,?,?,'active',?,?)",('unrelated-link-1',s['user_id'],rid,'77777777-7777-4777-8777-777777777777',unrelated,'not a JSON snapshot',int(stamp[0])))
    from app.database import Connection
    original=Connection.execute;queries=[]
    def counted(self,query,*args,**kwargs):
        queries.append(query);return original(self,query,*args,**kwargs)
    monkeypatch.setattr(Connection,'execute',counted)
    candidates={f'{i:032x}' for i in range(401)}|{aid}
    with m.db() as db:assert retained(db,candidates)=={aid}
    assert len(queries)==3
    assert all('a.id IN (' in query and "r.status!='detached'" in query for query in queries)


def test_pages_without_manifest_decorations_skip_canonical_storage_check(monkeypatch):
    from app.notion_sync import decorations
    def unexpected(*_):raise AssertionError('No decoration requires no ownership connection')
    monkeypatch.setattr(decorations,'owned',unexpected)
    c,s,p,service,stamp=initialized();rid,mapping=imported(service,s)
    p.blocks[ROOT][0]['paragraph']['rich_text']=rt('Updated plain page')
    with m.db() as db:root=dict(db.execute('SELECT * FROM notion_roots WHERE id=?',(rid,)).fetchone())
    client=service.connections.client('read')
    try:service.pull(root,client,ROOT)
    finally:client.close()
    assert c.get('/api/notes/'+mapping[ROOT]).json()['body']=='Updated plain page'
