import copy
import json
import pytest
from tests.test_security import reset
from tests.test_notion_sync_outbound import enabled,edit
from tests.test_notion_sync_service import ROOT,rt
from app import main as m
from app.notion_sync.client import NotionError
from app.notion_sync.recovery import review,resolve
from app.notion_sync.state import dump


def conflict():
    c,s,p,service,stamp,rid,mapping,patches=enabled()
    edit(c,mapping[ROOT],'My local version')
    p.blocks[ROOT][0]['paragraph']['rich_text']=rt('Their source version')
    stamp[0]+=10;assert service.work_once()
    assert service.status(s['user_id'])['roots'][0]['status']=='conflict'
    return c,s,p,service,stamp,rid,mapping,patches


def request(value,direction='pull',keep=True):
    return {key:value[key] for key in ('note_id','version','note_version','local_hash')}|{'review_hash':value['hash'],'direction':direction,'keep_copy':keep}


@pytest.mark.parametrize('direction',['pull','push'])
@pytest.mark.parametrize('keep',[False,True])
def test_selected_direction_keeps_mode_and_optional_replaced_version_without_duplicate_retries(direction,keep):
    c,s,p,service,stamp,rid,mapping,patches=conflict()
    value=review(service,s['user_id'],rid);assert value['can_push']
    payload=request(value,direction,keep);count=len(patches)
    result=resolve(service,s['user_id'],rid,payload)
    assert resolve(service,s['user_id'],rid,payload)==result
    text='My local version' if direction=='push' else 'Their source version'
    assert c.get('/api/notes/'+mapping[ROOT]).json()['blocks'][0]['content'][0]['text']==text
    assert p.blocks[ROOT][0]['paragraph']['rich_text'][0]['text']['content']==text
    assert len(patches)==count+(direction=='push')
    state=service.status(s['user_id'])['roots'][0]
    assert state['mode']=='duplex' and state['status']=='active'
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM notion_jobs WHERE root_id=?',(rid,)).fetchone()
        assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==1+keep
    if keep:
        saved=c.get('/api/notes/'+result['independent_copy']).json()
        assert saved.get('notion_sync') is None
        assert saved['blocks'][0]['content'][0]['text']==('Their source version' if direction=='push' else 'My local version')
    else:assert result['independent_copy'] is None


@pytest.mark.parametrize('change',['source','local','root','checkbox','role','grant'])
def test_review_is_not_authorization_and_rechecks_every_version_and_access(change):
    c,s,p,service,stamp,rid,mapping,patches=conflict()
    value=review(service,s['user_id'],rid);before=len(patches)
    with m.db() as db:
        if change=='source':p.blocks[ROOT][0]['paragraph']['rich_text']=rt('Newer source')
        elif change=='local':db.execute('UPDATE notes SET body=?,version=version+1 WHERE id=?',('Another local edit',mapping[ROOT]))
        elif change=='root':db.execute('UPDATE notion_roots SET version=version+1 WHERE id=?',(rid,))
        elif change=='checkbox':db.execute("UPDATE notion_roots SET mode='pull' WHERE id=?",(rid,))
        elif change=='role':db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
        elif change=='grant':db.execute("UPDATE notion_connections SET status='reauthorize' WHERE id='read'")
    with pytest.raises(NotionError):resolve(service,s['user_id'],rid,request(value,'push'))
    assert len(patches)==before
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==1


def test_paused_unsupported_edits_can_be_replaced_from_source_with_full_local_blocks_saved():
    c,s,p,service,stamp,rid,mapping,patches=enabled()
    note=c.get('/api/notes/'+mapping[ROOT]).json();blocks=copy.deepcopy(note['blocks']);blocks[0].setdefault('props',{})['textColor']='red'
    assert c.put('/api/notes/'+note['id'],json={'title':note['title'],'body':'Hello','blocks':blocks,'version':note['version']}).status_code==200
    stamp[0]+=10;assert service.work_once()
    assert service.status(s['user_id'])['roots'][0]['status']=='paused'
    value=review(service,s['user_id'],rid)
    assert not value['can_push'] and value['push_reason']=='notionUnsupportedWrite'
    with pytest.raises(NotionError,match='notionUnsupportedWrite'):resolve(service,s['user_id'],rid,request(value,'push'))
    result=resolve(service,s['user_id'],rid,request(value))
    assert c.get('/api/notes/'+result['independent_copy']).json()['blocks'][0]['props']['textColor']=='red'
    assert not patches and service.status(s['user_id'])['roots'][0]['mode']=='duplex'


def test_read_only_recovery_never_sends_to_notion_even_when_forged():
    c,s,p,service,stamp,rid,mapping,patches=conflict()
    with m.db() as db:db.execute("UPDATE notion_roots SET mode='pull' WHERE id=?",(rid,))
    value=review(service,s['user_id'],rid)
    assert value['push_reason']=='notionWriteForbidden'
    with pytest.raises(NotionError,match='notionWriteForbidden'):resolve(service,s['user_id'],rid,request(value,'push'))
    resolve(service,s['user_id'],rid,request(value,'pull',False))
    assert not patches and service.status(s['user_id'])['roots'][0]['mode']=='pull'


def test_recovery_does_not_acknowledge_or_discard_local_text_when_incoming_file_preparation_fails(monkeypatch):
    from app.notion_sync.assets import PreparedAssets
    c,s,p,service,stamp,rid,mapping,patches=conflict();value=review(service,s['user_id'],rid)
    with m.db() as db:before=db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0]
    def fail(*args):raise NotionError('notionAssetUnavailable',503)
    monkeypatch.setattr(PreparedAssets,'prepare',fail)
    with pytest.raises(NotionError):resolve(service,s['user_id'],rid,request(value,'pull',False))
    with m.db() as db:assert db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0]==before
    assert c.get('/api/notes/'+mapping[ROOT]).json()['blocks'][0]['content'][0]['text']=='My local version'


def test_copy_and_review_do_not_touch_another_pages_failure():
    from app.notion_sync.state import enqueue
    c,s,p,service,stamp,rid,mapping,patches=conflict()
    with m.db() as db:
        enqueue(db,rid,'22222222-2222-2222-2222-222222222222','pull',stamp[0])
        db.execute("UPDATE notion_jobs SET error='notionSourceUnavailable' WHERE entity_id='22222222-2222-2222-2222-222222222222'")
    value=review(service,s['user_id'],rid);resolve(service,s['user_id'],rid,request(value,'pull',False))
    state=service.status(s['user_id'])['roots'][0]
    assert state['status']=='paused' and state['error']=='notionSourceUnavailable'


def test_api_requires_owner_strict_checkbox_and_versioned_review(monkeypatch):
    c,s,p,service,stamp,rid,mapping,patches=conflict();value=review(service,s['user_id'],rid)
    url='/api/account/notion-sync/roots/'+rid+'/recovery'
    monkeypatch.setattr(m.notion_sync_service,'config',service.config)
    monkeypatch.setattr(m.notion_sync_service,'connections',service.connections)
    assert c.get(url).status_code==200
    assert c.post(url,json=request(value)|{'keep_copy':'false'}).status_code==422
    assert c.post(url,json=request(value)|{'direction':'force'}).status_code==422
    with m.db() as db:db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
    assert c.get(url).status_code==403 and c.post(url,json=request(value)).status_code==403
    assert not patches


def test_large_recovery_chooser_uses_constant_sql_and_reads_only_selected_source(monkeypatch):
    from app.database import Connection
    from app.notion_sync.recovery import context
    c,s,p,service,stamp,rid,mapping,patches=conflict()
    with m.db() as db:
        payload=db.execute('SELECT payload FROM notion_pages WHERE root_id=?',(rid,)).fetchone()[0]
        for index in range(200):
            nid=f'copy-{index:011d}';source=f'{index:08d}-1234-4567-8901-123456789012'
            db.execute('INSERT INTO notes(id,user_id,title,body,blocks,created,updated) VALUES(?,?,?,?,?,?,?)',(nid,s['user_id'],'Large catalog '+str(index),'Unrelated private body','[]',stamp[0],stamp[0]))
            db.execute("INSERT INTO notion_pages(id,user_id,root_id,notion_id,note_id,status,payload,updated) VALUES(?,?,?,?,?,'active',?,?)",('link-'+str(index),s['user_id'],rid,source,nid,payload,stamp[0]))
    queries=[];execute=Connection._execute
    def counted(connection,sql,parameters=()):queries.append(sql);return execute(connection,sql,parameters)
    monkeypatch.setattr(Connection,'_execute',counted)
    result=context(service,s['user_id'],rid);assert len(result[-1])==201
    assert len(queries)<=8
    before=len(p.requests);value=review(service,s['user_id'],rid)
    assert value['note_id']==mapping[ROOT]
    assert len(p.requests)-before<=4
    assert not patches
