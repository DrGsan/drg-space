"""Real note revisions, unchanged source metadata and no provider traffic."""
import json
import pytest
from tests.test_security import reset
from app import main as m
from app.notion_sync.blocks import convert,RENDER_REVISION
from app.notion_sync.reports import expose
from app.notion_sync.rendering import recover
from app.notion_sync.service import local_hash
from app.notion_sync.state import dump,enqueue
from tests.test_notion_sync_service import initialized,imported,ROOT,CHILD,TEXT,NEW,paragraph

def reference(target=CHILD):
    return {'id':NEW,'type':'link_to_page','link_to_page':{'type':'page_id','page_id':target}}

def test_reference_uses_target_id_and_keeps_an_external_fallback_without_discovering_pages():
    raw=reference();raw['link_to_page']['page_id']=CHILD.replace('-','')
    blocks,issues,_=convert([raw],lambda identity:'https://drg.test/page/local-child' if identity==CHILD else None)
    assert blocks[0]['type']=='pageLink' and blocks[0]['props']['pageId']=='local-child'
    assert not issues
    for data in ({'type':'database_id','database_id':CHILD},{'type':'page_id','page_id':'invalid'}):
        fallback,warnings,_=convert([{**raw,'link_to_page':data}])
        assert fallback[0]['content'][0]['href'].startswith('https://www.notion.so/')
        assert warnings[0]['reason']=='notionPageOutsideSync'

def test_bookmarks_are_cards_not_embed_limitations_and_write_limits_are_separate():
    blocks,issues,_=convert([{'id':TEXT,'type':'bookmark','bookmark':{'url':'https://example.org/manual','caption':[{'plain_text':'Manual'}]}},
                           {'id':NEW,'type':'synced_block','synced_block':{'synced_from':None},'children':[paragraph('Shared content')]}])
    assert blocks[0]['props']['linkDisplay']=='bookmark' and blocks[0]['content'][0]['href']=='https://example.org/manual'
    assert blocks[1]['type']=='paragraph' and blocks[1]['content'][0]['text']=='Shared content'
    summary=expose({'issues':issues,'issue_count':len(issues)},[{'page':'Root','reason':'notionIconUnavailable'}])
    assert summary['group_counts']=={'content':0,'appearance':1,'write':1}

def old_rendition(mapping):
    with m.db() as db:
        note=dict(db.execute('SELECT * FROM notes WHERE id=?',(mapping[ROOT],)).fetchone())
        blocks=json.loads(note['blocks']);blocks[-1]={'id':NEW,'type':'paragraph','props':{'linkDisplay':'url'},'content':[],'children':[]}
        db.execute('UPDATE notes SET blocks=? WHERE id=?',(dump(blocks),note['id']))
        note=dict(db.execute('SELECT * FROM notes WHERE id=?',(note['id'],)).fetchone())
        row=db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(note['id'],)).fetchone();payload=json.loads(row['payload']);payload.pop('render_revision',None)
        payload['local_hash']=local_hash(note);payload['baseline']['blocks']=blocks
        db.execute('UPDATE notion_pages SET payload=? WHERE note_id=?',(dump(payload),note['id']))
        return note,payload

def test_old_copies_repair_automatically_without_http_files_source_writes_or_repeated_versions():
    c,s,provider,service,stamp=initialized();provider.blocks[ROOT].append(reference());identity,mapping=imported(service,s)
    before,payload=old_rendition(mapping);http=len(provider.requests);downloads=len(provider.downloads)
    assert recover(service) is None
    saved=c.get('/api/notes/'+mapping[ROOT]).json()
    assert saved['blocks'][-1]['type']=='pageLink' and saved['blocks'][-1]['props']['pageId']==mapping[CHILD]
    assert saved['version']==before['version']+1
    assert saved['parent_id']==before['parent_id'] and saved['icon']==before['icon'] and saved['cover']==before['cover']
    with m.db() as db:
        updated=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0])
        assert updated['source']==payload['source'] and updated['remote_hash']==payload['remote_hash']
        assert updated['render_revision']==RENDER_REVISION and updated['assets']==payload['assets']
        assert db.execute('SELECT 1 FROM note_revisions WHERE note_id=?',(mapping[ROOT],)).fetchone()
        assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==0
    recover(service)
    assert c.get('/api/notes/'+mapping[ROOT]).json()['version']==saved['version']
    assert len(provider.requests)==http and len(provider.downloads)==downloads

@pytest.mark.parametrize('changed_blocks',[False,True])
def test_local_root_reorder_does_not_block_render_recovery_or_change_its_local_order(changed_blocks):
    c,s,provider,service,stamp=initialized();provider.blocks[ROOT].append(reference());identity,mapping=imported(service,s)
    if changed_blocks:old_rendition(mapping)
    else:
        with m.db() as db:
            payload=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0]);payload.pop('render_revision',None)
            db.execute('UPDATE notion_pages SET payload=? WHERE note_id=?',(dump(payload),mapping[ROOT]))
    c.post('/api/notes',json={'title':'Local neighbour','body':'Keep this note'})
    before=c.get('/api/notes/'+mapping[ROOT]).json()
    response=c.post('/api/notes/'+mapping[ROOT]+'/move',json={'parent_id':None,'before_id':None,'version':before['version']})
    assert response.status_code==200,response.text
    moved=response.json();http=len(provider.requests);downloads=len(provider.downloads)
    assert moved['version']>before['version']
    recover(service)
    saved=c.get('/api/notes/'+mapping[ROOT]).json()
    assert saved['position']==moved['position'] and saved['parent_id'] is None
    assert saved['blocks'][-1]['type']=='pageLink' and saved['blocks'][-1]['props']['pageId']==mapping[CHILD]
    assert saved['version']==moved['version']+int(changed_blocks)
    with m.db() as db:
        link=db.execute('SELECT note_version,payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()
        assert link['note_version']==saved['version']
        assert json.loads(link['payload'])['render_revision']==RENDER_REVISION
    recover(service)
    assert c.get('/api/notes/'+mapping[ROOT]).json()['version']==saved['version']
    assert len(provider.requests)==http and len(provider.downloads)==downloads

@pytest.mark.parametrize('case',['duplex','paused','detached','conflict','local-edit','revoked','member','pending','locked','foreign-grant'])
def test_render_upgrade_does_not_overwrite_local_intents_or_cross_authorization(case):
    c,s,provider,service,stamp=initialized();provider.blocks[ROOT].append(reference());identity,mapping=imported(service,s)
    note,payload=old_rendition(mapping)
    with m.db() as db:
        if case=='duplex':db.execute("UPDATE notion_roots SET mode='duplex' WHERE id=?",(identity,))
        elif case in ('paused','detached','conflict'):db.execute('UPDATE notion_roots SET status=? WHERE id=?',(case,identity))
        elif case=='local-edit':db.execute("UPDATE notes SET body='Unsaved local intent',version=version+1 WHERE id=?",(note['id'],))
        elif case=='revoked':db.execute("UPDATE notion_connections SET status='reauthorize'")
        elif case=='member':db.execute("UPDATE users SET role='member' WHERE id=?",(s['user_id'],))
        elif case=='pending':enqueue(db,identity,ROOT,'push',int(stamp[0]))
        elif case=='locked':db.execute('UPDATE notes SET locked=1 WHERE id=?',(note['id'],))
        elif case=='foreign-grant':db.execute("INSERT INTO users(id,login,password,role,created) VALUES('foreign','foreign','unusable','owner',0)");db.execute("UPDATE notion_connections SET user_id='foreign'")
        before=dict(db.execute('SELECT * FROM notes WHERE id=?',(note['id'],)).fetchone())
    http=len(provider.requests);recover(service)
    with m.db() as db:assert dict(db.execute('SELECT * FROM notes WHERE id=?',(note['id'],)).fetchone())==before
    assert len(provider.requests)==http

def test_legacy_summary_rebuilds_once_and_later_source_edits_update_just_its_page_report():
    c,s,provider,service,stamp=initialized();provider.blocks[ROOT].append(reference());identity,mapping=imported(service,s)
    with m.db() as db:
        row=db.execute('SELECT payload FROM notion_roots WHERE id=?',(identity,)).fetchone();value=json.loads(row[0]);value['summary']={'pages':2,'files':0,'databases':0,'issues':[{'page':'Root','id':NEW,'type':'link_to_page','reason':'notionExternalEmbed'}],'issue_count':1}
        db.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(value),identity))
    http=len(provider.requests)
    assert service.status(s['user_id'])['roots'][0]['summary']['issue_count']==0
    provider.blocks[ROOT].append({'id':'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','type':'unsupported','unsupported':{'block_type':'button'}})
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once()
    summary=service.status(s['user_id'])['roots'][0]['summary']
    assert summary['issue_count']==1 and summary['issue_groups'][0]['kind']=='content'
    provider.blocks[ROOT].pop()
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    service.work_once()
    assert service.status(s['user_id'])['roots'][0]['summary']['issue_count']==0
    assert len(provider.requests)-http==4


def test_unsupported_block_link_opens_its_original_page_at_the_block_anchor():
    blocks,issues,_=convert([{'id':NEW,'type':'unsupported','unsupported':{'block_type':'alias'}}],source_id=ROOT)
    assert blocks[0]['content'][0]['href']=='https://www.notion.so/'+ROOT.replace('-','')+'#'+NEW.replace('-','')
    assert issues[0]['reason']=='notionUnsupportedBlock'

def test_synced_occurrences_are_transparent_and_keep_distinct_stable_ids():
    shared={'id':NEW,'type':'synced_block','synced_block':{'synced_from':None},'synced_resolved':True,'children':[paragraph('Shared content')]}
    blocks,issues,_=convert([paragraph('Ordinary'),shared,{**shared,'id':CHILD}])
    assert [b['type'] for b in blocks]==['paragraph']*3
    assert len({b['id'] for b in blocks})==3
    assert convert([paragraph('Ordinary'),shared,{**shared,'id':CHILD}])[0]==blocks
    assert {i['reason'] for i in issues}=={'notionSyncedBlockReadonly'}

def test_legacy_synced_copy_refreshes_only_its_page_once_without_resetting_retry():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    old_rendition(mapping)
    with m.db() as db:
        row=db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone();value=json.loads(row[0])
        value['source']['blocks'].append({'id':NEW,'type':'synced_block','synced_block':{'synced_from':{'block_id':CHILD}},'has_children':False})
        db.execute('UPDATE notion_pages SET payload=? WHERE note_id=?',(dump(value),mapping[ROOT]))
    http=len(provider.requests);recover(service)
    with m.db() as db:
        job=dict(db.execute('SELECT * FROM notion_jobs').fetchone())
        assert job['direction']=='pull' and job['entity_id']==ROOT
        db.execute('UPDATE notion_jobs SET due=due+600')
    recover(service)
    with m.db() as db:
        later=dict(db.execute('SELECT * FROM notion_jobs').fetchone())
        assert later['due']==job['due']+600 and later['generation']==job['generation']
    assert len(provider.requests)==http


def test_unresolved_synced_copy_is_recovered_even_with_current_render_revision():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    block={'id':NEW,'type':'synced_block','synced_block':{'synced_from':{'block_id':CHILD}},'has_children':False}
    provider.blocks[ROOT].append(block);provider.blocks[CHILD]=[paragraph('Shared original')]
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0])
        value['render_revision']=RENDER_REVISION
        value['source']['blocks'].append(block)
        db.execute('UPDATE notion_pages SET payload=? WHERE note_id=?',(dump(value),mapping[ROOT]))
    before=len(provider.requests);recover(service)
    with m.db() as db:
        job=db.execute('SELECT direction,entity_id FROM notion_jobs').fetchone()
        assert job and tuple(job)==('pull',ROOT)
    assert len(provider.requests)==before
    assert service.work_once()
    assert 'Shared original' in c.get('/api/notes/'+mapping[ROOT]).json()['body']
    after=len(provider.requests);recover(service)
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==0
    assert len(provider.requests)==after
    assert all(method=='GET' for method,path in provider.requests)


def test_staged_unresolved_synced_copy_is_refreshed_before_first_publication():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    provider.blocks[CHILD]=[paragraph('Shared original')]
    block={'id':NEW,'type':'synced_block','synced_block':{'synced_from':{'block_id':CHILD}},'has_children':False}
    provider.blocks[ROOT].append(block)
    with m.db() as db:
        source=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0])['source']
        source['blocks'].append(block)
        db.execute('INSERT INTO notion_staging(id,user_id,root_id,notion_id,parent_id,payload,updated) VALUES(?,?,?,?,?,?,?)',('stale-synced-stage',s['user_id'],identity,ROOT,None,dump(source),int(stamp[0])))
        root=dict(db.execute('SELECT * FROM notion_roots WHERE id=?',(identity,)).fetchone())
    client=service.connections.client('read')
    try:page=service.import_page(root,client,ROOT,0)
    finally:client.close()
    mirrored=page.blocks[-1]
    assert mirrored.get('synced_resolved') and mirrored['children']
    assert not any(method in ('PATCH','DELETE') for method,path in provider.requests)


@pytest.mark.parametrize('available',[True,False])
def test_synced_resolution_state_is_saved_even_when_semantic_content_is_unchanged(available):
    import httpx
    from app.notion_sync.service import remote_hash
    from app.notion_sync.snapshot import PageSnapshot
    original='66666666-6666-4666-8666-666666666666'
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    block={'id':NEW,'type':'synced_block','synced_block':{'synced_from':{'block_id':original}},'has_children':False}
    provider.blocks[ROOT].append(block);provider.blocks[original]=[]
    handle=provider.handle
    def respond(request):
        if not available and request.url.path=='/v1/blocks/'+original+'/children':return httpx.Response(403)
        return handle(request)
    service.connections.transport=httpx.MockTransport(respond)
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0])
        value['source']['blocks'].append(block);value['render_revision']=RENDER_REVISION
        value['remote_hash']=remote_hash(PageSnapshot(**value['source']))
        db.execute('UPDATE notion_pages SET payload=? WHERE note_id=?',(dump(value),mapping[ROOT]))
    recover(service);assert service.work_once()
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0])
        assert value['source']['blocks'][-1].get('synced_resolved' if available else 'synced_unavailable')
    recover(service)
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==0
