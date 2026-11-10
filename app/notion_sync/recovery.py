"""Explicit, page-scoped recovery. Reviewing never authorizes a source write."""
import copy
import json
from dataclasses import replace

from app.notion_sync.blocks import convert,plain,file_url
from app.notion_sync.client import NotionError,digest,page_id
from app.notion_sync.service import local_hash,remote_hash
from app.notion_sync.snapshot import PageSnapshot
from app.notion_sync.state import dump,enqueue


def context(service,uid,rid,nid=None):
    with service.db() as c:
        root=dict(service.owned(c,rid,uid))
        if root['status']=='detached':raise NotionError('notionLinkUnavailable',404)
        if not c.execute("SELECT 1 FROM users WHERE id=? AND role='owner'",(uid,)).fetchone():raise NotionError('notionWriteForbidden',403)
        rows=c.execute('SELECT p.note_id,p.notion_id,p.status,n.title FROM notion_pages p JOIN notes n ON n.id=p.note_id WHERE p.root_id=? AND p.user_id=? AND n.user_id=? AND n.space_id IS NULL AND n.deleted IS NULL ORDER BY n.title,p.id',(rid,uid,uid)).fetchall()
        failures={job['entity_id']:job['error'] for job in c.execute("SELECT entity_id,error FROM notion_jobs WHERE root_id=? AND error!=''",(rid,)).fetchall()}
        # Load only catalog metadata for the chooser, not every encrypted body
        # and complete source snapshot in a large tree. No per-page SQL loop.
        available=[row for row in rows if row['status']!='importing']
        available.sort(key=lambda row:(0 if row['status']=='conflict' or row['notion_id'] in failures else 1 if row['note_id']==root['note_id'] else 2))
        pages=[{'note_id':row['note_id'],'title':row['title'],'reason':failures.get(row['notion_id'],'')} for row in available]
        nid=nid or (pages[0]['note_id'] if pages else None)
        if not any(row['note_id']==nid for row in available):raise NotionError('notionLinkUnavailable',404)
        link=dict(c.execute('SELECT * FROM notion_pages WHERE root_id=? AND note_id=?',(rid,nid)).fetchone());payload=json.loads(link['payload'])
        if not payload.get('source'):raise NotionError('notionLinkUnavailable',404)
        root['_recovery_jobs']=[dict(job) for job in c.execute("SELECT id,generation FROM notion_jobs WHERE root_id=? AND entity_id=? AND direction!='cover'",(rid,link['notion_id'])).fetchall()]
        note=dict(c.execute('SELECT * FROM notes WHERE id=?',(nid,)).fetchone())
        mapping={row['notion_id']:row['note_id'] for row in rows}
        from app.notion_sync.outbound import source_document,current_document
        target=source_document(c,root,note,current_document(note))
    return root,link,payload,note,mapping,target,pages


def incoming(service,root,link,client):
    # Reuse the normal single-page reader, including descendant membership and
    # database-kind checks. Never discover or replace a whole tree here.
    return service.import_page(root,client,link['notion_id'],1)


def source_document(service,payload,fresh,mapping,nid):
    """Rebase only content supported by the existing conditional writer.

    New metadata, database values and assets cannot be silently projected onto
    old local files. Such changes must first be accepted in the read direction.
    """
    source=PageSnapshot(**payload['source'])
    if remote_hash(replace(fresh,blocks=source.blocks))!=remote_hash(source):raise NotionError('notionUnsupportedWrite',422)
    resolve=lambda value:service.public_url+'/page/'+mapping[page_id(value)] if page_id(value) in mapping else None
    blocks,_,_=convert(fresh.blocks,resolve,source_id=fresh.id)
    inverse={remote:local for local,remote in payload.get('block_map',{}).items()}
    def stable(items):
        for block in items:
            block['id']=inverse.get(block['id'],block['id']);stable(block.get('children',[]))
    stable(blocks)
    links={}
    def media(items):
        for block in items:
            kind=block.get('type');saved=payload.get('assets',{}).get('block:'+block.get('id',''))
            if kind in ('image','video','audio','file','pdf'):
                if not saved:raise NotionError('notionUnsupportedWrite',422)
                from app.notion_sync.assets import identity
                old=next((value for value in source.blocks if value.get('id')==block.get('id')),None)
                if old is None or identity(old.get(kind))!=identity(block.get(kind)):raise NotionError('notionUnsupportedWrite',422)
                links[file_url(block[kind])]=service.public_url+'/api/notes/'+nid+'/files/'+saved['id']
            media(block.get('children',[]))
    media(fresh.blocks)
    from app.notion_images import replace_images
    blocks=replace_images(blocks,links)
    return {**copy.deepcopy(payload['baseline']),'blocks':blocks}


def writable(service,root,link,payload,fresh,mapping,target,client):
    if root['mode']!='duplex' or not service.config.write_configured:raise NotionError('notionWriteForbidden',403)
    if not link['writable']:raise NotionError('notionUnsupportedWrite',422)
    with service.db() as c:
        if not c.execute("SELECT 1 FROM notion_connections WHERE id=? AND user_id=? AND status='active' AND kind='read'",(root['connection_id'],root['user_id'])).fetchone():raise NotionError('notionWriteForbidden',403)
    from app.notion_sync.outbound import compiled_operations
    baseline=source_document(service,payload,fresh,mapping,link['note_id'])
    compiled_operations(client,{**payload,'source':fresh.value(),'baseline':baseline},target)
    return baseline


def review(service,uid,rid,nid=None):
    root,link,payload,note,mapping,target,pages=context(service,uid,rid,nid)
    client=service.connections.client(root['connection_id'])
    try:
        fresh=incoming(service,root,link,client)
        reason=''
        try:writable(service,root,link,payload,fresh,mapping,target,client)
        except NotionError as error:reason=error.code
    finally:client.close()
    blocks,_,_=convert(fresh.blocks,source_id=fresh.id)
    return {'pages':pages,'note_id':note['id'],'title':fresh.title,'local':note['body'],'remote':plain(blocks),'hash':remote_hash(fresh),'version':root['version'],'note_version':note['version'],'local_hash':local_hash(note),'can_push':not reason,'push_reason':reason}


def finish(service,c,root,entity,*,version):
    """Resume only if every other failure is also resolved; retain the mode."""
    current=c.execute('SELECT version FROM notion_roots WHERE id=?',(root['id'],)).fetchone()
    if not current or current['version']!=version:raise NotionError('notionLinkChanged',409)
    for job in root['_recovery_jobs']:c.execute('DELETE FROM notion_jobs WHERE id=? AND generation=?',(job['id'],job['generation']))
    value=json.loads(c.execute('SELECT payload FROM notion_roots WHERE id=?',(root['id'],)).fetchone()[0])
    conflict=c.execute("SELECT 1 FROM notion_pages WHERE root_id=? AND status='conflict' LIMIT 1",(root['id'],)).fetchone()
    error=c.execute("SELECT error FROM notion_jobs WHERE root_id=? AND error!='' LIMIT 1",(root['id'],)).fetchone()
    pending=c.execute("SELECT 1 FROM notion_pages WHERE root_id=? AND status='importing' LIMIT 1",(root['id'],)).fetchone()
    value.pop('has_conflict',None);value.pop('error',None)
    value.get('page_warnings',{}).pop(entity,None)
    if conflict:value['has_conflict']=True
    if error:value['error']=error[0]
    status='conflict' if conflict else 'paused' if error else 'importing' if pending else 'active'
    c.execute('UPDATE notion_roots SET status=?,payload=?,updated=?,version=version+1 WHERE id=?',(status,dump(value),int(service.clock()),root['id']))


def resolve(service,uid,rid,request):
    from app.process_locks import try_file_lock
    from app.import_receipts import previous,committed
    lease=try_file_lock(service.data_dir,'notion-worker.lock')
    if lease is None:raise NotionError('notionRetryLater',503,2)
    operation=digest({'root':rid,**request})
    with lease:
        with service.db() as c:
            service.owned(c,rid,uid)
            if not c.execute("SELECT 1 FROM users WHERE id=? AND role='owner'",(uid,)).fetchone():raise NotionError('notionWriteForbidden',403)
            done=previous(c,uid,'notion-recovery',operation,operation,int(service.clock()))
            if done:return done
        root,link,payload,note,mapping,target,_=context(service,uid,rid,request['note_id'])
        def check(c):
            current=service.owned(c,rid,uid)
            row=c.execute('SELECT * FROM notes WHERE id=?',(note['id'],)).fetchone()
            if current['version']!=request['version']:raise NotionError('notionLinkChanged',409)
            if not row or row['deleted'] is not None or row['version']!=request['note_version'] or local_hash(row)!=request['local_hash']:raise NotionError('notionLocalChanged',409)
        with service.db() as c:check(c)
        client=service.connections.client(root['connection_id'])
        try:
            fresh=incoming(service,root,link,client)
            if remote_hash(fresh)!=request['review_hash']:raise NotionError('notionPreviewChanged',409)
            baseline=writable(service,root,link,payload,fresh,mapping,target,client) if request['direction']=='push' else None
        finally:client.close()
        # Preparation and comparison precede the optional durable copy. A retry
        # reuses its receipt, and never manufactures duplicate recovery notes.
        with service.db() as c:check(c)
        result={'note_id':note['id'],'independent_copy':None,'direction':request['direction']}
        if request['keep_copy']:
            from app.notion_sync.copy import independent
            result['independent_copy']=independent(service,uid,note['id'],{'operation':operation,'digest':operation},document=baseline,shallow=True)['note_id']
        with service.db() as c:check(c)
        if request['direction']=='pull':
            service.apply(root,fresh,recovery_version=note['version'])
            with service.db() as c:
                c.execute('BEGIN IMMEDIATE');finish(service,c,root,link['notion_id'],version=root['version'])
                committed(c,uid,'notion-recovery',operation,operation,result,int(service.clock()))
            return result
        # This is an explicit selection of a freshly reviewed source baseline,
        # not a blind retry of an uncertain journal. The existing writer still
        # checks the source, checkbox and conditional fragment before each PATCH.
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE');check(c)
            value=copy.deepcopy(payload);value.pop('outbound',None);value.pop('conflict',None)
            value.pop('source_unavailable',None)
            value.update(source=fresh.value(),baseline=baseline,remote_hash=remote_hash(fresh))
            # Force semantic comparison even when a stale acknowledgement hash
            # happens to equal the chosen local document.
            value['local_hash']='recovery:'+operation
            c.execute("UPDATE notion_pages SET status='active',payload=? WHERE id=?",(dump(value),link['id']))
            c.execute("UPDATE notion_roots SET status='active',version=version+1 WHERE id=? AND mode='duplex'",(rid,))
            enqueue(c,rid,link['notion_id'],'push',int(service.clock()))
            row=c.execute("SELECT * FROM notion_jobs WHERE root_id=? AND entity_id=? AND direction='push'",(rid,link['notion_id'])).fetchone();job=dict(row)
            c.execute('UPDATE notion_jobs SET lease_owner=?,lease_until=? WHERE id=?',(service.worker_id,int(service.clock())+90,job['id']))
        from app.notion_sync.outbound import push
        try:
            push(service,root,job)
            with service.db() as c:
                row=c.execute('SELECT payload,status FROM notion_pages WHERE id=?',(link['id'],)).fetchone();value=json.loads(row['payload'])
                if row['status']!='active' or value.get('outbound') or value.get('local_hash')!=local_hash(note):raise NotionError('notionConflictUnresolved',409)
                c.execute('BEGIN IMMEDIATE')
                c.execute('DELETE FROM notion_jobs WHERE id=? AND generation=? AND lease_owner=?',(job['id'],job['generation'],service.worker_id))
                finish(service,c,root,link['notion_id'],version=root['version']+1)
                committed(c,uid,'notion-recovery',operation,operation,result,int(service.clock()))
        except NotionError as error:
            service.failed(root,job,error)
            # A failed explicit recovery must stay stopped, even for a timeout.
            with service.db() as c:c.execute("UPDATE notion_roots SET status='paused',version=version+1 WHERE id=? AND status='active'",(rid,))
            raise
        return result
