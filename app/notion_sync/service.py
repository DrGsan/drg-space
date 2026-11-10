"""Durable account-owned links. Network and immutable staging precede SQL writes."""
import json
import secrets
import time

from app.notion_sync import access
from app.notion_sync.assets import PreparedAssets
from app.notion_sync.blocks import RENDER_REVISION,convert,plain,semantic,needs_synced_refresh
from app.notion_sync.client import NotionError,page_id,digest
from app.notion_sync.connections import Connections
from app.notion_sync.snapshot import read_subtree,PageSnapshot
from app.notion_sync.state import enqueue,dump,claim
from app.notion_sync.views import semantic as view_semantic

class ScanYield(Exception):pass

DECORATIONS=('cover','icon')

def decoration_pending(value):return any(value.get(kind+'_pending') for kind in DECORATIONS)

def decoration_retry_pending(value):return any(value.get(kind+'_pending') and not value.get(kind+'_blocked') for kind in DECORATIONS)

def decoration_sources(payload):
    return {warning['notion_id'] for kind in DECORATIONS for warning in payload.get(kind+'_warnings',{}).values()}

def decoration_delay(value,stamp):
    return max(0,min(value.get(kind+'_retry_at',stamp+900) for kind in DECORATIONS if value.get(kind+'_pending') and not value.get(kind+'_blocked'))-stamp)


def local_hash(note):
    return digest({key:json.loads(note[key]) if key=='blocks' and note[key] else note[key] for key in ('title','blocks','icon','cover','parent_id')})


def remote_hash(page):
    raw=page.raw
    # Preserve metadata changes, but ignore volatile retrieval timestamps and
    # temporary signatures. Actual file identity is retained by block UUID/path.
    from app.notion_sync.assets import identity
    return digest({'title':page.title,'parent':page.parent,'blocks':semantic(page.blocks),'properties':page.properties,'values':page.values,'views':view_semantic(page.views),
                   'icon':raw.get('icon',{}).get('emoji') if (raw.get('icon') or {}).get('type')=='emoji' else identity(raw.get('icon')),'cover':identity(raw.get('cover'))})


def next_positions(c,uid,parents):
    """Reserve append offsets in batches, without decrypting unrelated notes."""
    parents=list(dict.fromkeys(parents));result=dict.fromkeys(parents,0)
    for start in range(0,len(parents),200):
        batch=parents[start:start+200];keys=[p for p in batch if p is not None]
        condition='parent_id IN ('+','.join('?' for _ in keys)+')' if keys else '0=1'
        if None in batch:condition='('+condition+' OR parent_id IS NULL)'
        for row in c.execute('SELECT parent_id,MAX(position)+1 AS next_position FROM notes WHERE user_id=? AND space_id IS NULL AND deleted IS NULL AND '+condition+' GROUP BY parent_id',(uid,*keys)):
            result[row['parent_id']]=row['next_position']
    return result


class SyncService:
    def __init__(self,db,config,data_dir,public_url,*,clock=time.time,transport=None,wait=time.sleep,fetch=None):
        self.db,self.config,self.data_dir,self.public_url=db,config,data_dir,public_url.rstrip('/')
        self.clock,self.fetch=clock,fetch
        self.connections=Connections(db,config,data_dir,clock=clock,transport=transport,wait=wait)
        from app.notion_sync.discovery import PagePicker
        self.picker=PagePicker(self.connections)
        self.worker_id=secrets.token_urlsafe(12)
        self.batch_seconds=5

    def recover_icons(self,after='',limit=100):
        """Repair older snapshots in bounded local batches, without API scans.

        Earlier versions silently treated custom emoji as the default icon.
        Their remote timestamp is already current, so search cannot discover
        the missing rendition. Queue a normal authorized pull once; ordinary
        pending-decoration retries take over if the download is unavailable.
        """
        from app.notion_sync.assets import identity
        stamp=int(self.clock())
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=c.execute("SELECT p.id,p.root_id,p.notion_id,p.payload FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id JOIN notion_connections n ON n.id=r.connection_id JOIN users u ON u.id=r.user_id JOIN notes note ON note.id=p.note_id WHERE p.id>? AND p.status='active' AND r.status IN ('active','importing') AND n.status='active' AND u.role='owner' AND note.deleted IS NULL ORDER BY p.id LIMIT ?",(after,limit)).fetchall()
            for row in rows:
                value=json.loads(row['payload']);icon=value.get('source',{}).get('raw',{}).get('icon') or {}
                if icon.get('type')!='custom_emoji' or value.get('icon_pending'):continue
                if (value.get('assets',{}).get('decoration:icon') or {}).get('fingerprint')==identity(icon):continue
                if not c.execute("SELECT 1 FROM notion_jobs WHERE root_id=? AND entity_id=? AND direction='pull'",(row['root_id'],row['notion_id'])).fetchone():
                    enqueue(c,row['root_id'],row['notion_id'],'pull',stamp)
        return rows[-1]['id'] if len(rows)==limit else None

    def owned(self,c,root,uid):
        row=c.execute('SELECT * FROM notion_roots WHERE id=? AND user_id=?',(root,uid)).fetchone()
        if not row:raise NotionError('notionLinkUnavailable',404)
        return row

    def status(self,uid):
        with self.db() as c:
            connections=[]
            for row in c.execute("SELECT * FROM notion_connections WHERE user_id=? AND kind='read' ORDER BY created,id",(uid,)).fetchall():
                payload=json.loads(row['payload'])
                connections.append({key:row[key] for key in ('id','kind','workspace_id','status','updated')}|{'name':payload.get('workspace_name','Notion')})
            rows=c.execute('SELECT r.*,p.status AS root_page_status FROM notion_roots r JOIN notion_connections n ON n.id=r.connection_id LEFT JOIN notion_pages p ON p.note_id=r.note_id AND p.root_id=r.id WHERE n.user_id=? AND r.user_id=? ORDER BY r.created,r.id',(uid,uid)).fetchall()
            pending=[row['id'] for row in rows if row['status'] in ('scanning','importing')]
            counts={}
            if pending:
                counts={row['root_id']:row['pages'] for row in c.execute('SELECT root_id,COUNT(*) AS pages FROM notion_staging WHERE root_id IN ('+','.join('?' for _ in pending)+') GROUP BY root_id',tuple(pending)).fetchall()}
            linked=[row['id'] for row in rows if row['note_id']]
            totals={}
            if linked:
                totals={row['root_id']:row['pages'] for row in c.execute('SELECT root_id,COUNT(*) AS pages FROM notion_pages WHERE root_id IN ('+','.join('?' for _ in linked)+') GROUP BY root_id',tuple(linked)).fetchall()}
            roots=[self.describe(c,row,pending_pages=counts.get(row['id'],0),page_count=totals.get(row['id'],0)) for row in rows]
        if self.config.write_configured and any(root['status']=='conflict' and root['mode']=='duplex' for root in roots):
            from app.notion_sync.outbound import recover_completed
            if recover_completed(self,uid):return self.status(uid)
        return {'configured':self.config.configured,'write_configured':self.config.write_configured,'connections':connections,'roots':roots}

    def describe(self,c,row,*,pending_pages=None,page_count=None):
        payload=json.loads(row['payload'])
        from app.notion_sync import reports
        from app.notion_sync.asset_retry import expose as asset_failures
        reports.ensure(c,row,payload)
        result={key:row[key] for key in ('id','connection_id','notion_id','note_id','mode','status','version','updated')}|{
            'title':payload.get('title','Notion'),'summary':payload.get('summary'),'error':payload.get('error'),
            'error_params':payload.get('error_params',{}),
            'asset_failures':asset_failures(payload),
            'has_asset_errors':bool(payload.get('asset_failures')) or payload.get('error')=='notionAssetUnavailable' or bool(decoration_sources(payload)),
            'has_conflict':bool(payload.get('has_conflict')),
            'preview_hash':payload.get('preview_hash'),'source_url':'https://www.notion.so/'+row['notion_id'].replace('-','')}
        if result['summary']:
            # The local link identities are authoritative, including hidden
            # pending imports. Old cached summaries need no provider rescan or
            # write-on-read repair; ready previews retain their staged count.
            if row['note_id']:
                if page_count is None:page_count=c.execute('SELECT COUNT(*) FROM notion_pages WHERE root_id=?',(row['id'],)).fetchone()[0]
                result['summary']['pages']=page_count
            warnings=[{'page':item['title'],'reason':'notion'+kind.title()+('RetryStopped' if item.get('blocked') else 'Unavailable')} for kind in DECORATIONS for item in payload.get(kind+'_warnings',{}).values()]
            warnings.extend({'page':item['title'],'reason':item['reason']} for item in payload.get('page_warnings',{}).values())
            result['summary']=reports.expose(result['summary'],warnings)
        result['copy_available']=False
        if row['note_id']:
            page_status=row['root_page_status'] if 'root_page_status' in row.keys() else None
            if page_status is None:
                page=c.execute('SELECT status FROM notion_pages WHERE root_id=? AND note_id=?',(row['id'],row['note_id'])).fetchone()
                page_status=page['status'] if page else None
            result['copy_available']=page_status is not None and page_status!='importing'
        if row['status'] in ('scanning','importing'):
            if pending_pages is None:pending_pages=c.execute('SELECT COUNT(*) FROM notion_staging WHERE root_id=?',(row['id'],)).fetchone()[0]
            total=(result.get('summary') or {}).get('pages',0)
            result['progress']={'checked_pages':pending_pages} if row['status']=='scanning' else {'copied_pages':max(0,total-pending_pages),'total_pages':total}
        if not self.config.configured and result['status'] not in ('detached','paused'):
            result.update(status='paused',error='notionNotConfigured')
        return result

    def preview(self,uid,connection,source):
        if not self.config.configured:raise NotionError('notionNotConfigured',409)
        source=page_id(source);stamp=int(self.clock())
        from app.notion_sync import selection
        with self.db() as c:
            grant=c.execute("SELECT * FROM notion_connections WHERE id=? AND user_id=? AND kind='read' AND status='active'",(connection,uid)).fetchone()
            if not grant:raise NotionError('notionConnectionPaused',409)
            existing=c.execute("SELECT * FROM notion_roots WHERE connection_id=? AND notion_id=? AND status!='detached'",(connection,source)).fetchone()
            if existing:return self.describe(c,existing)
            workspace=grant['workspace_id'];others=selection.links(c,uid,workspace)
        client=self.connections.client(connection)
        try:chain,selected_title=selection.relationship(client,source,others)
        finally:client.close()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            grant=c.execute("SELECT * FROM notion_connections WHERE id=? AND user_id=? AND kind='read' AND status='active'",(connection,uid)).fetchone()
            if not grant:raise NotionError('notionConnectionPaused',409)
            # Previously disconnected copies remain independent notes. Their
            # obsolete identities must not prevent creating a fresh new link.
            c.execute("DELETE FROM notion_roots WHERE connection_id=? AND notion_id=? AND status='detached'",(connection,source))
            existing=c.execute('SELECT * FROM notion_roots WHERE connection_id=? AND notion_id=?',(connection,source)).fetchone()
            if existing:return self.describe(c,existing)
            # Covers both concurrent orderings, even before either scan has
            # staged children. BEGIN IMMEDIATE serializes across PG workers too.
            selection.check(c,uid,workspace,chain,selected=source)
            if c.execute("SELECT COUNT(*) FROM notion_roots WHERE user_id=? AND status!='detached'",(uid,)).fetchone()[0]>=50:raise NotionError('notionLinkLimit',422)
            identity=secrets.token_urlsafe(12)
            c.execute("INSERT INTO notion_roots(id,user_id,connection_id,notion_id,status,payload,created,updated) VALUES(?,?,?,?,'scanning',?,?,?)",(identity,uid,connection,source,dump({'selection_ancestors':sorted(chain-{source}),'title':selected_title}),stamp,stamp))
            enqueue(c,identity,source,'scan',stamp)
            return self.describe(c,self.owned(c,identity,uid))

    def confirm(self,uid,identity,preview_hash):
        if not self.config.configured:raise NotionError('notionNotConfigured',409)
        from app.notion_sync import selection
        with self.db() as c:
            root=self.owned(c,identity,uid)
            if root['status'] in ('active','importing'):return self.describe(c,root)
            if root['status']!='ready' or preview_hash!=json.loads(root['payload']).get('preview_hash'):raise NotionError('notionPreviewChanged',409)
            workspace=c.execute('SELECT workspace_id FROM notion_connections WHERE id=?',(root['connection_id'],)).fetchone()[0]
            others=selection.links(c,uid,workspace,identity)
        client=self.connections.client(root['connection_id'])
        try:chain,_=selection.relationship(client,root['notion_id'],others)
        finally:client.close()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');root=self.owned(c,identity,uid);payload=json.loads(root['payload'])
            if root['status']=='importing' or root['status']=='active':return self.describe(c,root)
            if root['status']!='ready' or not preview_hash or preview_hash!=payload.get('preview_hash'):raise NotionError('notionPreviewChanged',409)
            pages=[json.loads(row['payload']) for row in c.execute('SELECT payload FROM notion_staging WHERE root_id=?',(identity,)).fetchall()]
            if not pages:raise NotionError('notionPreviewChanged',409)
            selection.check(c,uid,workspace,{page['id'] for page in pages}|chain,identity)
            mapping={page['id']:secrets.token_urlsafe(12) for page in pages};stamp=int(self.clock())
            positions=next_positions(c,uid,(mapping.get(page.get('parent')) for page in pages))
            # Insert without parents first; this is independent of SQL row order.
            for page in pages:
                nid=mapping[page['id']]
                parent=mapping.get(page.get('parent'));position=positions[parent];positions[parent]+=1
                c.execute('INSERT INTO notes(id,user_id,title,body,blocks,created,updated,position) VALUES(?,?,?,?,?,?,?,?)',(nid,uid,page['title'],'','[]',stamp,stamp,position))
                c.execute("INSERT INTO notion_pages(id,user_id,root_id,notion_id,note_id,parent_id,status,payload,remote_stamp,updated) VALUES(?,?,?,?,?,?,'importing',?,?,?)",(secrets.token_urlsafe(12),uid,identity,page['id'],nid,page.get('parent'),dump({'source_kind':page['raw'].get('object','page')}),page['raw'].get('last_edited_time',''),stamp))
                enqueue(c,identity,page['id'],'import',stamp)
            for page in pages:
                c.execute('UPDATE notes SET parent_id=? WHERE id=?',(mapping.get(page.get('parent')),mapping[page['id']]))
            c.execute("UPDATE notion_roots SET status='importing',note_id=?,updated=?,version=version+1 WHERE id=?",(mapping[root['notion_id']],stamp,identity))
            access.enabled()
            return self.describe(c,self.owned(c,identity,uid))

    def pull(self,root,client,entity):
        with self.db() as c:
            known={row['notion_id']:dict(row) for row in c.execute('SELECT id,notion_id,note_id,parent_id,remote_stamp,status FROM notion_pages WHERE root_id=?',(root['id'],)).fetchall()}
        link=known.get(entity)
        def source(identity):
            with self.db() as c:row=c.execute('SELECT payload FROM notion_pages WHERE root_id=? AND notion_id=?',(root['id'],identity)).fetchone()
            return json.loads(row[0]).get('source') if row else None
        saved=source(entity) if link else None
        kind=(saved or {}).get('raw',{}).get('object','page')
        parent=link['parent_id'] if link else None
        raw=None
        if entity!=root['notion_id']:
            from app.notion_sync.hierarchy import selected_parent
            from app.notion_sync.hierarchy import metadata
            raw=metadata(client,kind,entity)
            actual=selected_parent(client,raw,set(known),root['notion_id'])
            if actual is None:
                # A source moved outside the selected subtree remains a local
                # copy. It must not continue syncing arbitrary external pages.
                if link:
                    with self.db() as c:c.execute("UPDATE notion_pages SET status='unavailable',updated=? WHERE id=?",(int(self.clock()),link['id']))
                return
            parent=actual
        parent_source=source(parent) if parent else None
        fields=(parent_source or {}).get('properties')
        cached={key:{'raw':{'last_edited_time':value['remote_stamp']}} for key,value in known.items() if key!=entity}
        pages=read_subtree(client,entity,known_ids=set(known),parent_id=parent,root_kind=kind,root_properties=fields,cached=cached,root_raw=raw,load_cached=source,defer_ids={key for key,value in known.items() if value['status']=='importing'})
        if len(set(known)|set(pages))>2000:raise NotionError('notionPageTooLarge',422)
        # Establish all new page identities before rewriting internal links.
        stamp=int(self.clock())
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            active=c.execute('SELECT status,payload FROM notion_roots WHERE id=?',(root['id'],)).fetchone()
            if not active or active['status'] not in ('active','importing'):raise NotionError('notionConnectionPaused',409)
            mapping={key:value['note_id'] for key,value in known.items()}
            added=[page for page in pages.values() if page.id not in known]
            if added:
                from app.notion_sync import selection
                workspace=c.execute('SELECT workspace_id FROM notion_connections WHERE id=?',(root['connection_id'],)).fetchone()[0]
                selection.check(c,root['user_id'],workspace,{page.id for page in added},root['id'])
            mapping.update({page.id:secrets.token_urlsafe(12) for page in added})
            positions=next_positions(c,root['user_id'],(mapping.get(page.parent) for page in added))
            for page in added:
                nid=mapping[page.id];parent=mapping.get(page.parent);position=positions[parent];positions[parent]+=1
                c.execute('INSERT INTO notes(id,user_id,title,body,blocks,created,updated,position) VALUES(?,?,?,?,?,?,?,?)',(nid,root['user_id'],page.title,'','[]',stamp,stamp,position))
                c.execute("INSERT INTO notion_pages(id,user_id,root_id,notion_id,note_id,parent_id,status,payload,remote_stamp,updated) VALUES(?,?,?,?,?,?,'importing',?,?,?)",(secrets.token_urlsafe(12),root['user_id'],root['id'],page.id,nid,page.parent,dump({'source_kind':page.raw.get('object','page')}),page.raw.get('last_edited_time',''),stamp))
            for page in pages.values():
                if page.id not in known:
                    c.execute('UPDATE notes SET parent_id=? WHERE id=?',(mapping.get(page.parent),mapping[page.id]))
                    c.execute('INSERT INTO notion_staging(id,user_id,root_id,notion_id,parent_id,payload,updated) VALUES(?,?,?,?,?,?,?) ON CONFLICT(root_id,notion_id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated',
                              (secrets.token_urlsafe(12),root['user_id'],root['id'],page.id,page.parent,dump(page.value()),stamp))
                    enqueue(c,root['id'],page.id,'import',stamp)
            added=[page for page in pages.values() if page.id not in known]
            if added:
                from app.notion_sync.reports import record,ensure,REVISION
                payload=json.loads(active['payload'])
                ensure(c,root,payload)
                summary=payload['summary']
                summary['pages']=len(mapping);summary['databases']+=sum(page.kind=='database' for page in added)
                for page in added:
                    _,issues,assets=convert(page.blocks,lambda identity:self.public_url+'/page/'+mapping[identity] if identity in mapping else None,source_id=page.id);summary['files']+=len(assets)
                    if summary.get('report_revision')==REVISION:record(summary,page.value(),page.issues+issues)
                    else:
                        summary['issue_count']+=len(page.issues+issues)
                        summary['issues']=(summary['issues']+[{'page':page.title,**issue} for issue in page.issues+issues])[:500]
                c.execute('UPDATE notion_roots SET payload=?,updated=?,version=version+1 WHERE id=?',(dump(payload),stamp,root['id']))
        for page in pages.values():
            if page.id in known:self.apply(root,page)

    def import_page(self,root,client,entity,attempts):
        with self.db() as c:
            row=c.execute('SELECT p.parent_id,p.payload,s.payload AS staged_payload FROM notion_pages p LEFT JOIN notion_staging s ON s.root_id=p.root_id AND s.notion_id=p.notion_id WHERE p.root_id=? AND p.notion_id=?',(root['id'],entity)).fetchone()
        if not row:raise NotionError('notionLinkUnavailable',409)
        previous=json.loads(row['payload']);stage=json.loads(row['staged_payload']) if row['staged_payload'] else None;parent=row['parent_id']
        def fields(identity):
            if not identity:return None
            with self.db() as c:
                value=c.execute('SELECT payload FROM notion_pages WHERE root_id=? AND notion_id=?',(root['id'],identity)).fetchone()
                source=json.loads(value[0]).get('source') if value else None
                if not source:
                    value=c.execute('SELECT payload FROM notion_staging WHERE root_id=? AND notion_id=?',(root['id'],identity)).fetchone()
                    source=json.loads(value[0]) if value else None
            return (source or {}).get('properties')
        if stage and not attempts and not previous.get('refresh_required') and not previous.get('source_unavailable') and not needs_synced_refresh(stage['blocks']):
            page=PageSnapshot(**stage)
            # A published parent may have acquired a new schema while its
            # record was still waiting for files. Do not apply old values.
            if not (page.raw.get('parent') or {}).get('data_source_id') or page.properties==fields(parent):return page
        kind=(stage or previous.get('source') or {}).get('raw',{}).get('object',previous.get('source_kind','page'))
        from app.notion_sync.hierarchy import metadata,selected_parent
        raw=metadata(client,kind,entity)
        with self.db() as c:known={r[0] for r in c.execute('SELECT notion_id FROM notion_pages WHERE root_id=?',(root['id'],))}
        if entity!=root['notion_id']:
            parent=selected_parent(client,raw,known,root['notion_id'])
            if parent is None:raise NotionError('notionSourceUnavailable',404)
        # The initial mapping already contains the subtree. Refresh this page
        # only; normal reconciliation discovers newly added children later.
        return next(iter(read_subtree(client,entity,known_ids=known,parent_id=parent,root_kind=kind,root_raw=raw,root_properties=fields(parent),descend=False).values()))

    def experimental(self,uid,identity,version,enabled,write_connection=None):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');root=self.owned(c,identity,uid)
            if root['version']!=version:raise NotionError('notionLinkChanged',409)
            payload=json.loads(root['payload']);stamp=int(self.clock())
            retry=bool(enabled and root['status']=='paused' and payload.get('error') in ('notionUnsupportedWrite','notionWriteAccessRequired') and c.execute("SELECT 1 FROM notion_jobs WHERE root_id=? AND direction='push'",(identity,)).fetchone())
            if root['status']!='active' and not retry:raise NotionError('notionConnectionPaused',409)
            if enabled:
                if not self.config.write_configured:raise NotionError('notionWriteForbidden',403)
                if write_connection not in (None,root['connection_id']):raise NotionError('notionWriteForbidden',403)
                grant=c.execute("SELECT 1 FROM notion_connections WHERE id=? AND user_id=? AND kind='read' AND status='active'",(root['connection_id'],uid)).fetchone()
                if not grant:raise NotionError('notionWriteForbidden',403)
                payload.pop('write_connection_id',None)
                if retry:
                    payload.pop('error',None)
                    c.execute("UPDATE notion_jobs SET due=?,attempts=0,error='' WHERE root_id=? AND direction='push'",(stamp,identity))
                c.execute("UPDATE notion_roots SET mode='duplex',status='active',payload=?,updated=?,version=version+1 WHERE id=?",(dump(payload),stamp,identity))
                access.write_enabled()
            else:
                # Unsent local edits are retained. Resuming the incoming link
                # subsequently detects divergence and asks for a choice.
                pending=c.execute("SELECT 1 FROM notion_jobs WHERE root_id=? AND direction='push' LIMIT 1",(identity,)).fetchone()
                c.execute("UPDATE notion_roots SET mode='pull',status=?,updated=?,version=version+1 WHERE id=?",('paused' if pending else 'active',stamp,identity))
                c.execute("DELETE FROM notion_jobs WHERE root_id=? AND direction='push'",(identity,))
            if not retry:c.execute('UPDATE notes SET version=version+1,updated=? WHERE id IN (SELECT note_id FROM notion_pages WHERE root_id=?)',(stamp,identity))
            return self.describe(c,self.owned(c,identity,uid))

    def review_conflict(self,uid,identity,refresh=True):
        from app.notion_sync.outbound import recover_completed
        recover_completed(self,uid)
        with self.db() as c:
            root=dict(self.owned(c,identity,uid))
            link=c.execute("SELECT * FROM notion_pages WHERE root_id=? AND status='conflict' ORDER BY updated,id LIMIT 1",(identity,)).fetchone()
            if not link:
                if root['status']=='active':return {'resolved':True,'note_id':root['note_id']}
                raise NotionError('notionConflictUnresolved',409)
            link=dict(link);payload=json.loads(link['payload']);source=PageSnapshot(**payload['source'])
            note=c.execute('SELECT * FROM notes WHERE id=?',(link['note_id'],)).fetchone();local=note['body']
            known={row[0] for row in c.execute('SELECT notion_id FROM notion_pages WHERE root_id=?',(identity,)).fetchall()}
        cached=(payload.get('conflict') or {}).get('remote')
        if cached and not refresh:fresh=PageSnapshot(**cached)
        else:
            client=self.connections.client(root['connection_id'])
            try:fresh=next(iter(read_subtree(client,source.id,known_ids=known,parent_id=source.parent).values()))
            finally:client.close()
        blocks,_,_=convert(fresh.blocks,source_id=fresh.id)
        return {'note_id':link['note_id'],'title':fresh.title,'local':local,'remote':plain(blocks),'hash':remote_hash(fresh),'version':root['version'],'cached':bool(cached and not refresh)}

    def accept_source(self,uid,identity,nid,review_hash,version):
        from app.process_locks import try_file_lock
        lease=try_file_lock(self.data_dir,'notion-worker.lock')
        if lease is None:raise NotionError('notionRetryLater',503,2)
        with lease:
            with self.db() as c:
                root=dict(self.owned(c,identity,uid))
                link=c.execute('SELECT * FROM notion_pages WHERE root_id=? AND note_id=?',(identity,nid)).fetchone()
                if not link:raise NotionError('notionLinkUnavailable',404)
                link=dict(link);payload=json.loads(link['payload']);source=PageSnapshot(**payload['source'])
                from app.import_receipts import previous
                operation=identity+':'+nid+':'+review_hash
                copied=previous(c,uid,'notion-sync-copy',operation,review_hash,int(self.clock()))
                if link['status']=='active' and copied:return {'independent_copy':copied['note_id'],'note_id':nid}
                if root['version']!=version or link['status']!='conflict':raise NotionError('notionLinkChanged',409)
                known={row[0] for row in c.execute('SELECT notion_id FROM notion_pages WHERE root_id=?',(identity,)).fetchall()}
            client=self.connections.client(root['connection_id'])
            try:fresh=next(iter(read_subtree(client,source.id,known_ids=known,parent_id=source.parent).values()))
            finally:client.close()
            if remote_hash(fresh)!=review_hash:raise NotionError('notionPreviewChanged',409)
            from app.notion_sync.copy import independent
            copied=independent(self,uid,nid,{'operation':operation,'digest':review_hash})
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                current=c.execute('SELECT * FROM notes WHERE id=?',(nid,)).fetchone()
                current_root=self.owned(c,identity,uid)
                if current_root['version']!=version:raise NotionError('notionLinkChanged',409)
                payload['local_hash']=local_hash(current)
                payload.pop('remote_hash',None)
                c.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(payload),link['id']))
                root_payload=json.loads(current_root['payload']);root_payload.pop('has_conflict',None);root_payload.pop('error',None)
                c.execute("UPDATE notion_roots SET mode='pull',status='active',payload=?,updated=?,version=version+1 WHERE id=?",(dump(root_payload),int(self.clock()),identity))
                c.execute('DELETE FROM notion_jobs WHERE root_id=?',(identity,))
                for source in decoration_sources(root_payload):enqueue(c,identity,source,'cover',int(self.clock()),delay=900)
            try:self.apply({**root,'mode':'pull','status':'active'},fresh)
            except BaseException:
                with self.db() as c:c.execute("UPDATE notion_roots SET status='conflict',version=version+1 WHERE id=?",(identity,))
                raise
            return {'independent_copy':copied['note_id'],'note_id':nid}

    def action(self,uid,identity,action,version):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');root=self.owned(c,identity,uid)
            if root['version']!=version:raise NotionError('notionLinkChanged',409)
            stamp=int(self.clock());retry_write=False
            if action=='cancel':
                if root['note_id']:raise NotionError('notionInvalidRequest',409)
                c.execute('DELETE FROM notion_roots WHERE id=?',(identity,));return {'ok':True}
            if action=='pause':status='paused'
            elif action=='disconnect':status='detached'
            elif action=='retry_assets':
                if root['status'] not in ('active','importing') or not self.config.configured:raise NotionError('notionConnectionPaused',409)
                connection=c.execute('SELECT status FROM notion_connections WHERE id=?',(root['connection_id'],)).fetchone()
                if not connection or connection['status']!='active':raise NotionError('notionConnectionPaused',409)
                from app.notion_sync.asset_retry import retry
                retry(c,root,stamp);status=root['status']
            elif action=='resume':
                if not self.config.configured:raise NotionError('notionNotConfigured',409)
                connection=c.execute('SELECT status FROM notion_connections WHERE id=?',(root['connection_id'],)).fetchone()
                if not connection or connection['status']!='active':raise NotionError('notionConnectionPaused',409)
                if root['status']=='conflict' or json.loads(root['payload']).get('has_conflict'):raise NotionError('notionConflictUnresolved',409)
                if json.loads(root['payload']).get('asset_failures') or json.loads(root['payload']).get('error')=='notionAssetUnavailable':
                    from app.notion_sync.asset_retry import retry
                    retry(c,root,stamp,decorations=False);root=self.owned(c,identity,uid)
                pending=c.execute("SELECT notion_id FROM notion_pages WHERE root_id=? AND status='importing'",(identity,)).fetchall()
                status='importing' if pending else 'active' if root['note_id'] else 'scanning'
                value=json.loads(root['payload'])
                retry_write=root['mode']=='duplex' and bool(c.execute("SELECT 1 FROM notion_jobs WHERE root_id=? AND direction='push'",(identity,)).fetchone())
                if retry_write:
                    grant=c.execute("SELECT 1 FROM notion_connections WHERE id=? AND user_id=? AND kind='read' AND status='active'",(root['connection_id'],uid)).fetchone()
                    if not self.config.write_configured or not grant:raise NotionError('notionWriteForbidden',403)
                    c.execute("UPDATE notion_jobs SET due=?,attempts=0,error='' WHERE root_id=? AND direction='push'",(stamp,identity))
                    value.pop('error',None);c.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(value),identity))
                    status='active'
                if pending:
                    for page in pending:enqueue(c,identity,page['notion_id'],'import',stamp)
                elif not retry_write:enqueue(c,identity,root['notion_id'],'pull' if root['note_id'] else 'scan',stamp)
                for source in decoration_sources(json.loads(root['payload'])):enqueue(c,identity,source,'cover',stamp,delay=900)
            else:raise NotionError('notionInvalidRequest',422)
            if root['status']=='detached' and action!='disconnect':raise NotionError('notionLinkUnavailable',409)
            # Pause is not a revocation of the per-link checkbox. Keep unsent
            # jobs dormant; all network mutations still require active+duplex.
            c.execute('UPDATE notion_roots SET status=?,mode=?,updated=?,version=version+1 WHERE id=?',(status,'pull' if status=='detached' else root['mode'],stamp,identity))
            if status=='detached':
                from app.notion_sync.pending_assets import disconnect
                disconnect(c,identity,uid,stamp)
                c.execute('DELETE FROM notion_jobs WHERE root_id=?',(identity,))
                value=json.loads(c.execute('SELECT payload FROM notion_roots WHERE id=?',(identity,)).fetchone()[0])
                if value.pop('asset_failures',None):c.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(value),identity))
            elif status=='paused':
                c.execute("DELETE FROM notion_jobs WHERE root_id=? AND direction!='push'",(identity,))
            if status=='detached':
                # These are system-created placeholders, not copied revisions.
                # Only untouched empty placeholders may be discarded; already
                # published pages and every retained file stay with the owner.
                for page in c.execute("SELECT p.note_id,p.payload,n.body,n.blocks FROM notion_pages p JOIN notes n ON n.id=p.note_id WHERE p.root_id=? AND p.status='importing'",(identity,)).fetchall():
                    if set(json.loads(page['payload']))-{'source_kind','refresh_required','source_unavailable'} or page['body'] or json.loads(page['blocks'] or '[]'):raise NotionError('notionLocalChanged',409)
                    c.execute('DELETE FROM notes WHERE id=?',(page['note_id'],))
                c.execute('DELETE FROM notion_staging WHERE root_id=?',(identity,))
            if not retry_write:c.execute('UPDATE notes SET version=version+1,updated=? WHERE id IN (SELECT note_id FROM notion_pages WHERE root_id=?)',(stamp,identity))
            return self.describe(c,self.owned(c,identity,uid))

    def checkpoint(self,root,page):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT status FROM notion_roots WHERE id=?',(root['id'],)).fetchone()
            if not current or current['status'] in ('paused','detached'):raise NotionError('notionConnectionPaused',409)
            c.execute('INSERT INTO notion_staging(id,user_id,root_id,notion_id,parent_id,payload,updated) VALUES(?,?,?,?,?,?,?) ON CONFLICT(root_id,notion_id) DO UPDATE SET parent_id=excluded.parent_id,payload=excluded.payload,updated=excluded.updated',
                      (secrets.token_urlsafe(12),root['user_id'],root['id'],page.id,page.parent,dump(page.value()),int(self.clock())))

    def scan(self,root,client):
        with self.db() as c:cached={row['notion_id']:json.loads(row['payload']) for row in c.execute('SELECT notion_id,payload FROM notion_staging WHERE root_id=?',(root['id'],)).fetchall()}
        initial=cached.get(root['notion_id'],{}).get('raw')
        if initial is None:
            try:initial=client.page(root['notion_id'])
            except NotionError as error:
                # A visible database ID is a validation error (400) at the
                # page endpoint; an unshared database often appears as 404.
                if error.status not in (400,404):raise
                initial=client.request('GET','/v1/databases/'+root['notion_id'])
        deadline=time.monotonic()+self.batch_seconds
        def checkpoint(page):
            self.checkpoint(root,page)
            # Yield only after a complete content page. Retrying a database
            # header alone must not starve all its record pages on slow links.
            if page.raw.get('object')=='page' and time.monotonic()>=deadline:raise ScanYield()
        pages=read_subtree(client,root['notion_id'],cached=cached,root_kind=initial.get('object','page'),root_raw=initial,checkpoint=checkpoint)
        from app.notion_sync.reports import record
        files=0;summary={'pages':len(pages),'files':0,'databases':sum(page.kind=='database' for page in pages.values()),'issues':[],'issue_count':0}
        for page in pages.values():
            _,warnings,assets=convert(page.blocks,lambda identity:'https://www.notion.so/'+identity if identity in pages else None,source_id=page.id);files+=len(assets)
            record(summary,page.value(),page.issues+warnings)
        summary['files']=files
        payload={'title':pages[root['notion_id']].title,'summary':summary,'preview_hash':digest([page.value() for page in pages.values()])}
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            # Selecting a parent of another active link would otherwise create
            # multiple independent outbound writers for the same source page.
            from app.notion_sync import selection
            workspace=c.execute('SELECT workspace_id FROM notion_connections WHERE id=?',(root['connection_id'],)).fetchone()[0]
            selection.check(c,root['user_id'],workspace,set(pages),root['id'])
            changed=c.execute("UPDATE notion_roots SET status='ready',payload=?,updated=?,version=version+1 WHERE id=? AND status='scanning'",(dump(payload),int(self.clock()),root['id']))
            if not changed.rowcount:raise NotionError('notionConnectionPaused',409)

    def apply(self,root,page,*,recovery_version=None):
        with self.db() as c:
            link=c.execute('SELECT * FROM notion_pages WHERE root_id=? AND notion_id=?',(root['id'],page.id)).fetchone()
            if not link:raise NotionError('notionLinkUnavailable',409)
            link=dict(link);previous=json.loads(link['payload']);note=c.execute('SELECT * FROM notes WHERE id=?',(link['note_id'],)).fetchone()
            if not note or note['deleted'] is not None:raise NotionError('notionLinkUnavailable',409)
            mapping={row['notion_id']:row['note_id'] for row in c.execute('SELECT notion_id,note_id FROM notion_pages WHERE root_id=?',(root['id'],)).fetchall()}
            baseline=local_hash(note);version=note['version']
        remote=remote_hash(page)
        if recovery_version is None and link['status']=='active' and previous.get('remote_hash')==remote and not needs_synced_refresh(previous.get('source',{}).get('blocks',[])) and (root['mode']!='pull' or previous.get('render_revision',0)>=RENDER_REVISION) and not any(previous.get(kind+'_pending') and not previous.get(kind+'_blocked') and int(self.clock())>=previous.get(kind+'_retry_at',0) for kind in DECORATIONS):
            with self.db() as c:c.execute('UPDATE notion_pages SET remote_stamp=?,updated=? WHERE id=?',(page.raw.get('last_edited_time',''),int(self.clock()),link['id']))
            return
        if recovery_version is not None and version!=recovery_version:raise NotionError('notionLocalChanged',409)
        if recovery_version is None and previous.get('local_hash') not in (None,baseline):
            self.conflict(root,link,page);return
        resolve=lambda identity:self.public_url+'/page/'+mapping[page_id(identity)] if page_id(identity) in mapping else None
        blocks,issues,_=convert(page.blocks,resolve,source_id=page.id)
        inverse={remote:local for local,remote in previous.get('block_map',{}).items()}
        def stable_ids(items):
            for block in items:
                block['id']=inverse.get(block['id'],block['id']);stable_ids(block.get('children',[]))
        stable_ids(blocks)
        from app.file_store import file_store
        from app.data_crypto import media_store
        from app.notion_sync.assets import identity as asset_identity
        source_changed={kind:asset_identity(page.raw.get(kind))!=asset_identity(previous.get('source',{}).get('raw',{}).get(kind)) for kind in DECORATIONS}
        retries={kind:source_changed[kind] or not previous.get(kind+'_blocked') and (not previous.get(kind+'_pending') or int(self.clock())>=previous.get(kind+'_retry_at',0)) for kind in DECORATIONS}
        from app.notion_sync.pending_assets import previous as file_progress,checkpoint as file_checkpoint
        cached_files=file_progress(self,root,link,page.blocks)
        from app.notion_sync.decorations import manifest_ids,owned as owned_decorations
        valid_decorations={}
        if manifest_ids(cached_files):
            with self.db() as c:valid_decorations=owned_decorations(c,root['user_id'],link['note_id'],cached_files)
        assets=PreparedAssets(file_store(self.db,self.data_dir),media_store(self.db,self.data_dir),self.public_url,link['note_id'],previous=cached_files,fetch=self.fetch,retry_cover=retries['cover'],retry_icon=retries['icon'],checkpoint=lambda prepared:file_checkpoint(self,root,link,prepared,page.title),valid_decorations=valid_decorations,clock=self.clock)
        committed=False
        try:
            blocks,decorations=assets.prepare(blocks,page.blocks,page.raw)
            from app.modules.notes import validate_blocks
            def invalid(status,detail):raise NotionError('notionUnsupportedDocument',status)
            validate_blocks(blocks,invalid)
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                current=c.execute('SELECT status,mode,version FROM notion_roots WHERE id=?',(root['id'],)).fetchone()
                if recovery_version is not None:
                    if not current or current['version']!=root['version'] or current['status']=='detached':raise NotionError('notionLinkChanged',409)
                elif not current or current['status'] not in ('active','importing'):raise NotionError('notionConnectionPaused',409)
                note=c.execute('SELECT * FROM notes WHERE id=?',(link['note_id'],)).fetchone()
                if not note or note['deleted'] is not None:raise NotionError('notionLinkUnavailable',409)
                if note['version']!=version or local_hash(note)!=baseline:raise NotionError('notionLocalChanged',409,2)
                from app.note_history import snapshot
                snapshot(c,note,int(self.clock()))
                assets.insert(c,root['user_id'],page.title,int(self.clock()))
                body=plain(blocks);parent=mapping.get(page.parent)
                saved_document=c.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(link['note_id'],)).fetchone()
                if saved_document:
                    from app.collaboration import load_doc
                    from app.share_editing import replace_content
                    doc=load_doc(saved_document['state'])
                    _,body,_=replace_content(doc,page.title,blocks,invalid)
                    c.execute('UPDATE collaborative_docs SET state=?,updated=? WHERE note_id=?',(doc.get_update(),int(self.clock()),link['note_id']))
                c.execute('UPDATE notes SET title=?,body=?,blocks=?,icon=?,cover=?,parent_id=?,updated=?,version=version+1 WHERE id=?',
                          (page.title,body,dump(blocks),decorations['icon'],decorations['cover'],parent,int(self.clock()),link['note_id']))
                if page.kind=='database':
                    from app.modules.notion import validate_config
                    from app.notion_sync.views import convert as convert_views
                    views,_=convert_views(page.views,page.raw.get('properties',{}))
                    validate_config(page.properties,views,invalid)
                    c.execute('INSERT INTO note_databases(note_id,properties,views) VALUES(?,?,?) ON CONFLICT(note_id) DO UPDATE SET properties=excluded.properties,views=excluded.views',(link['note_id'],dump(page.properties),dump(views)))
                if page.parent in mapping and c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(mapping[page.parent],)).fetchone():
                    from app.modules.notion import validate_values
                    fields=json.loads(c.execute('SELECT properties FROM note_databases WHERE note_id=?',(mapping[page.parent],)).fetchone()[0])
                    validate_values(page.values,fields,invalid)
                    c.execute('INSERT INTO database_records(note_id,database_id,properties) VALUES(?,?,?) ON CONFLICT(note_id) DO UPDATE SET database_id=excluded.database_id,properties=excluded.properties',(link['note_id'],mapping[page.parent],dump(page.values)))
                saved=c.execute('SELECT * FROM notes WHERE id=?',(link['note_id'],)).fetchone()
                value={'source':page.value(),'baseline':{key:json.loads(saved[key]) if key=='blocks' and saved[key] else saved[key] for key in ('title','blocks','icon','cover','parent_id')},'remote_hash':remote,'local_hash':local_hash(saved),'issues':page.issues+issues,'assets':assets.manifest,'render_revision':RENDER_REVISION}
                from app.notion_sync.reports import record,REVISION
                saved_root=c.execute('SELECT payload FROM notion_roots WHERE id=?',(root['id'],)).fetchone()
                root_value=json.loads(saved_root[0])
                unavailable=root_value.get('page_warnings',{}).pop(page.id,None)
                asset_recovered=root_value.get('asset_failures',{}).pop(page.id,None)
                if root_value.get('summary',{}).get('report_revision')==REVISION:
                    record(root_value['summary'],page.value(),page.issues+issues)
                if unavailable or asset_recovered or root_value.get('summary',{}).get('report_revision')==REVISION:
                    c.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(root_value),root['id']))
                for kind in DECORATIONS:
                    value[kind+'_pending']=getattr(assets,kind+'_pending')
                    if value[kind+'_pending']:
                        value[kind+'_attempts']=(previous.get(kind+'_attempts',0) if not source_changed[kind] else 0)+int(retries[kind])
                        value[kind+'_blocked']=value[kind+'_attempts']>=5
                        value[kind+'_retry_at']=int(self.clock())+900 if retries[kind] else previous.get(kind+'_retry_at',int(self.clock()))
                if decoration_pending(value) or decoration_pending(previous):
                    saved_root=c.execute('SELECT payload FROM notion_roots WHERE id=?',(root['id'],)).fetchone()
                    root_value=json.loads(saved_root[0])
                    for kind in DECORATIONS:
                        warnings=root_value.setdefault(kind+'_warnings',{})
                        if value[kind+'_pending']:warnings[link['note_id']]={'title':page.title,'notion_id':page.id,'blocked':value.get(kind+'_blocked',False)}
                        else:warnings.pop(link['note_id'],None)
                    if decoration_retry_pending(value):
                        if not c.execute("SELECT 1 FROM notion_jobs WHERE root_id=? AND direction='cover' AND entity_id=?",(root['id'],page.id)).fetchone():enqueue(c,root['id'],page.id,'cover',int(self.clock()),delay=decoration_delay(value,int(self.clock())))
                    else:
                        c.execute("DELETE FROM notion_jobs WHERE root_id=? AND direction='cover' AND entity_id=? AND lease_owner=''",(root['id'],page.id))
                    c.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(root_value),root['id']))
                if previous.get('block_map'):value['block_map']=previous['block_map']
                writable=page.kind=='page' and page.raw.get('object')=='page' and not (page.raw.get('parent') or {}).get('data_source_id')
                c.execute("UPDATE notion_pages SET status='active',writable=?,parent_id=?,payload=?,remote_stamp=?,note_version=?,updated=? WHERE id=?",(int(writable),page.parent,dump(value),page.raw.get('last_edited_time',''),saved['version'],int(self.clock()),link['id']))
                if assets.has_checkpoint or previous.get('pending_assets'):
                    from app.notion_sync.pending_assets import published
                    published(c,link['note_id'])
                c.execute('DELETE FROM notion_staging WHERE root_id=? AND notion_id=?',(root['id'],page.id))
                pending=c.execute("SELECT 1 FROM notion_pages WHERE root_id=? AND status='importing' LIMIT 1",(root['id'],)).fetchone()
                if not pending:
                    activated=c.execute("UPDATE notion_roots SET status='active',updated=?,version=version+1 WHERE id=? AND status='importing'",(int(self.clock()),root['id']))
                    if activated.rowcount:
                        grant=c.execute('SELECT payload FROM notion_connections WHERE id=?',(root['connection_id'],)).fetchone()
                        scan=json.loads(grant[0]);scan['watermark']=min(scan.get('watermark',root['created']-60),root['created']-60);scan.pop('scan',None)
                        c.execute('UPDATE notion_connections SET payload=?,next_scan=0 WHERE id=?',(dump(scan),root['connection_id']))
            committed=True
        finally:
            if not committed:assets.abort()

    def conflict(self,root,link,page):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            actual=c.execute('SELECT status,payload FROM notion_roots WHERE id=?',(root['id'],)).fetchone()
            if not actual:return
            current=c.execute('SELECT payload FROM notion_pages WHERE id=?',(link['id'],)).fetchone()
            if not current:raise NotionError('notionLinkUnavailable',409)
            payload=json.loads(current[0]);payload['conflict']={'remote':page.value(),'created':int(self.clock())}
            c.execute("UPDATE notion_pages SET status='conflict',payload=? WHERE id=?",(dump(payload),link['id']))
            root_payload=json.loads(actual['payload']);root_payload['has_conflict']=True
            status=actual['status'] if actual['status'] in ('paused','detached') else 'conflict'
            c.execute('UPDATE notion_roots SET status=?,payload=?,updated=?,version=version+1 WHERE id=?',(status,dump(root_payload),int(self.clock()),root['id']))
            c.execute('UPDATE notes SET version=version+1,updated=? WHERE id IN (SELECT note_id FROM notion_pages WHERE root_id=?)',(int(self.clock()),root['id']))

    def work_once(self):
        if not self.config.configured:return False
        from app.process_locks import try_file_lock
        lease=try_file_lock(self.data_dir,'notion-worker.lock')
        if lease is None:return False
        with lease:return self._work_once()

    def _work_once(self):
        stamp=int(self.clock())
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');job=claim(c,stamp,self.worker_id)
            if not job:return False
            root=dict(c.execute('SELECT * FROM notion_roots WHERE id=?',(job['root_id'],)).fetchone())
        client=None
        try:
            if job['direction']=='push':
                from app.notion_sync.outbound import push
                push(self,root,job)
            else:client=self.connections.client(root['connection_id'],request_check=lambda:self.check_job(root,job))
            if job['direction']=='push':pass
            elif job['direction']=='scan':self.scan(root,client)
            elif job['direction']=='import':
                self.apply(root,self.import_page(root,client,job['entity_id'],job['attempts']))
            elif job['direction']=='pull':self.pull(root,client,job['entity_id'])
            elif job['direction']=='cover':
                with self.db() as c:
                    value=c.execute('SELECT payload FROM notion_pages WHERE root_id=? AND notion_id=?',(root['id'],job['entity_id'])).fetchone()
                if value and decoration_pending(json.loads(value[0])):self.pull(root,client,job['entity_id'])
            else:raise NotionError('notionInvalidRequest',422)
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                deleted=c.execute('DELETE FROM notion_jobs WHERE id=? AND lease_owner=? AND generation=?',(job['id'],self.worker_id,job['generation']))
                if not deleted.rowcount:c.execute("UPDATE notion_jobs SET lease_until=0,lease_owner='',attempts=0 WHERE id=? AND lease_owner=?",(job['id'],self.worker_id))
                elif job['direction']=='cover':
                    page=c.execute('SELECT payload FROM notion_pages WHERE root_id=? AND notion_id=?',(root['id'],job['entity_id'])).fetchone()
                    if page and decoration_retry_pending(json.loads(page[0])):enqueue(c,root['id'],job['entity_id'],'cover',int(self.clock()),delay=decoration_delay(json.loads(page[0]),int(self.clock())))
                self.clear_recovered_error(c,root)
        except ScanYield:
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute("UPDATE notion_jobs SET lease_until=0,lease_owner='',error='',due=? WHERE id=? AND lease_owner=?",(int(self.clock())+1,job['id'],self.worker_id))
                self.clear_recovered_error(c,root)
        except NotionError as error:self.failed(root,job,error)
        finally:
            if client:client.close()
        return True

    def clear_recovered_error(self,c,root):
        if not json.loads(root['payload']).get('error'):return
        recovered=c.execute("SELECT payload FROM notion_roots WHERE id=? AND status IN ('scanning','active','ready','importing') AND NOT EXISTS (SELECT 1 FROM notion_jobs WHERE root_id=? AND error!='')",(root['id'],root['id'])).fetchone()
        if recovered:
            value=json.loads(recovered[0]);value.pop('error',None)
            if value.get('asset_failures'):return
            c.execute('UPDATE notion_roots SET payload=?,updated=? WHERE id=?',(dump(value),int(self.clock()),root['id']))

    def check_job(self,root,job):
        stamp=int(self.clock())
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT j.lease_owner,j.lease_until,r.status FROM notion_jobs j JOIN notion_roots r ON r.id=j.root_id WHERE j.id=?',(job['id'],)).fetchone()
            if not current or current['lease_owner']!=self.worker_id or current['status'] not in ('scanning','importing','active'):raise NotionError('notionConnectionPaused',409)
            if current['lease_until']<stamp+60:c.execute('UPDATE notion_jobs SET lease_until=? WHERE id=? AND lease_owner=?',(stamp+90,job['id'],self.worker_id))

    def failed(self,root,job,error):
        stamp=int(self.clock())
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT payload FROM notion_roots WHERE id=?',(root['id'],)).fetchone()
            if not current:return
            from app.notion_sync.asset_retry import failed as asset_failed
            if asset_failed(c,root,job,error,stamp,self.worker_id):return
            if error.status in (403,404) and job['direction'] in ('pull','import') and job['entity_id']!=root['notion_id']:
                page=c.execute('SELECT p.id,p.payload,n.title FROM notion_pages p JOIN notes n ON n.id=p.note_id WHERE p.root_id=? AND p.notion_id=?',(root['id'],job['entity_id'])).fetchone()
                if page:
                    value=json.loads(page['payload']);published=bool(value.get('source'))
                    value['source_unavailable']=True;value.pop('refresh_required',None)
                    # Never publish an empty initial placeholder. A later source
                    # event or explicit resume may recheck restored access.
                    c.execute('UPDATE notion_pages SET status=?,payload=?,updated=? WHERE id=?',('unavailable' if published else 'importing',dump(value),stamp,page['id']))
                    payload=json.loads(current[0]);payload.setdefault('page_warnings',{})[job['entity_id']]={'title':page['title'],'reason':error.code}
                    c.execute('UPDATE notion_roots SET payload=?,updated=? WHERE id=?',(dump(payload),stamp,root['id']))
                c.execute('DELETE FROM notion_jobs WHERE id=? AND lease_owner=?',(job['id'],self.worker_id))
                return
            payload=json.loads(current[0]);payload['error']=error.code;payload['error_params']=error.params
            status='paused' if error.status in (401,403,404,422) or error.code in ('notionAlreadyLinked','notionSelectionOverlap') else root['status']
            c.execute('UPDATE notion_roots SET payload=?,status=?,updated=?,version=version+1 WHERE id=? AND status NOT IN (\'detached\',\'paused\',\'conflict\')',(dump(payload),status,stamp,root['id']))
            if error.status in (429,529):c.execute('UPDATE notion_connections SET cooldown=? WHERE workspace_id=(SELECT workspace_id FROM notion_connections WHERE id=?)',(stamp+error.delay,root['connection_id']))
            if error.status==401:c.execute("UPDATE notion_connections SET status='reauthorize' WHERE id=?",(root['connection_id'],))
            delay=max(error.delay,min(3600,5*2**min(job['attempts'],10)))+secrets.randbelow(5)
            c.execute("UPDATE notion_jobs SET lease_until=0,lease_owner='',due=?,attempts=attempts+1,error=? WHERE id=? AND lease_owner=?",(stamp+delay,error.code,job['id'],self.worker_id))
