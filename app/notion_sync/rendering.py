"""Bounded, local-only upgrade of existing read-only copies from saved sources."""
import json
from app.notion_sync.blocks import RENDER_REVISION,convert,file_url,plain,needs_synced_refresh
from app.notion_sync.state import dump

def recover(service,after='',limit=100):
    from app.process_locks import try_file_lock
    lease=try_file_lock(service.data_dir,'notion-worker.lock')
    if lease is None:return after
    with lease:return batch(service,after,limit)

def batch(service,after,limit):
    with service.db() as c:
        rows=c.execute("SELECT p.id,p.root_id,p.payload FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id JOIN notion_connections n ON n.id=r.connection_id JOIN users u ON u.id=r.user_id WHERE p.id>? AND p.status='active' AND r.mode='pull' AND r.status IN ('active','importing') AND n.status='active' AND n.kind='read' AND n.user_id=r.user_id AND p.user_id=r.user_id AND u.role='owner' ORDER BY p.id LIMIT ?",(after,limit)).fetchall()
    for row in rows:
        value=json.loads(row['payload'])
        if value.get('render_revision',0)>=RENDER_REVISION and not needs_synced_refresh(value.get('source',{}).get('blocks',[])):continue
        repair(service,row['id'])
    return rows[-1]['id'] if len(rows)==limit else None

def repair(service,identity):
    from app.notion_sync.service import local_hash
    from app.notion_images import replace_images
    with service.db() as c:
        c.execute('BEGIN IMMEDIATE')
        link=c.execute("SELECT p.* FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id JOIN notion_connections n ON n.id=r.connection_id JOIN users u ON u.id=r.user_id WHERE p.id=? AND p.status='active' AND r.mode='pull' AND r.status IN ('active','importing') AND n.status='active' AND n.kind='read' AND n.user_id=r.user_id AND p.user_id=r.user_id AND u.role='owner' AND NOT EXISTS(SELECT 1 FROM notion_jobs j WHERE j.root_id=r.id AND j.direction='push')",(identity,)).fetchone()
        if not link:return
        value=json.loads(link['payload']);source=value.get('source')
        if not source or value.get('conflict') or value.get('outbound'):return
        if value.get('render_revision',0)>=RENDER_REVISION and not needs_synced_refresh(source['blocks']):return
        note=c.execute('SELECT * FROM notes WHERE id=?',(link['note_id'],)).fetchone()
        if not note or note['deleted'] is not None or note['locked'] or note['space_id'] is not None or note['user_id']!=link['user_id'] or local_hash(note)!=value.get('local_hash'):return
        stored_blocks=json.loads(note['blocks'] or '[]')
        # Local root reordering changes version, but not synchronized content.
        # Also compare the text projection: local_hash intentionally excludes
        # body, so a separate local body edit must still block recovery.
        if note['version']<link['note_version'] or note['body']!=plain(stored_blocks):return
        note_version=note['version']
        if needs_synced_refresh(source['blocks']):
            # Old snapshots did not follow synced_from. Refresh only this page,
            # through the ordinary access-checked, rate-limited pull queue.
            from app.notion_sync.state import enqueue
            root_payload=json.loads(c.execute('SELECT payload FROM notion_roots WHERE id=?',(link['root_id'],)).fetchone()[0])
            if root_payload.get('asset_failures',{}).get(link['notion_id'],{}).get('blocked'):return
            if not c.execute("SELECT 1 FROM notion_jobs WHERE root_id=? AND entity_id=? AND direction='pull'",(link['root_id'],link['notion_id'])).fetchone():
                enqueue(c,link['root_id'],link['notion_id'],'pull',int(service.clock()))
            return
        mapping={row['notion_id']:row['note_id'] for row in c.execute('SELECT notion_id,note_id FROM notion_pages WHERE root_id=?',(link['root_id'],))}
        blocks,issues,_=convert(source['blocks'],lambda remote:service.public_url+'/page/'+mapping[remote] if remote in mapping else None,source_id=source['id'])
        links={};missing=False
        def files(items):
            nonlocal missing
            for block in items:
                kind=block.get('type')
                if kind in ('image','video','audio','file','pdf'):
                    asset=value.get('assets',{}).get('block:'+block['id']);url=file_url(block.get(kind))
                    if not asset or not url:missing=True
                    else:links[url]=service.public_url+'/api/notes/'+note['id']+'/files/'+asset['id']
                files(block.get('children',[]))
        files(source['blocks'])
        if missing:return
        blocks=replace_images(blocks,links)
        inverse={remote:local for local,remote in value.get('block_map',{}).items()}
        def ids(items):
            for block in items:block['id']=inverse.get(block['id'],block['id']);ids(block.get('children',[]))
        ids(blocks)
        if blocks!=stored_blocks:
            from app.modules.notes import validate_blocks
            from app.notion_sync.client import NotionError
            def invalid(status,detail):raise NotionError('notionUnsupportedDocument',status)
            validate_blocks(blocks,invalid)
            from app.note_history import snapshot
            stamp=int(service.clock());snapshot(c,note,stamp);body=plain(blocks)
            doc=c.execute('SELECT state FROM collaborative_docs WHERE note_id=?',(note['id'],)).fetchone()
            if doc:
                from app.collaboration import load_doc
                from app.share_editing import replace_content
                state=load_doc(doc['state']);_,body,_=replace_content(state,note['title'],blocks,invalid)
                c.execute('UPDATE collaborative_docs SET state=?,updated=? WHERE note_id=?',(state.get_update(),stamp,note['id']))
            c.execute('UPDATE notes SET blocks=?,body=?,version=version+1,updated=? WHERE id=?',(dump(blocks),body,stamp,note['id']))
            saved=c.execute('SELECT * FROM notes WHERE id=?',(note['id'],)).fetchone()
            value['local_hash']=local_hash(saved)
            value['baseline']={key:json.loads(saved[key]) if key=='blocks' else saved[key] for key in ('title','blocks','icon','cover','parent_id')}
            note_version=saved['version']
        value.update(render_revision=RENDER_REVISION,issues=source.get('issues',[])+issues)
        c.execute('UPDATE notion_pages SET payload=?,note_version=? WHERE id=?',(dump(value),note_version,identity))
