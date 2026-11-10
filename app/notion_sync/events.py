"""Signed notifications are hints; only freshly authorized API data is applied."""
import hashlib
import hmac
import json
import re
from datetime import datetime

from app.notion_sync.client import NotionError,page_id
from app.notion_sync.state import dump,enqueue,refresh_import

MAX_BODY=256*1024


def receive(service,kind,body,signature):
    if not service.config.configured:raise NotionError('notionNotConfigured',404)
    if kind!='read' or len(body)>MAX_BODY:raise NotionError('notionInvalidRequest',400)
    try:event=json.loads(body)
    except (ValueError,UnicodeError):raise NotionError('notionInvalidRequest',400) from None
    if not isinstance(event,dict):raise NotionError('notionInvalidRequest',400)
    if set(event)=={'verification_token'}:
        token=event['verification_token']
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{8,512}',token):raise NotionError('notionInvalidRequest',400)
        # Bootstrap is unsigned by Notion's protocol. Merely receiving a token
        # must never activate it. An unlocked site administrator retrieves it,
        # verifies it in Notion's developer portal, then configures the secret.
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE');key='notion-webhook-verification:'+kind
            previous=c.execute('SELECT value FROM app_settings WHERE key=?',(key,)).fetchone()
            if not previous or int(service.clock())-json.loads(previous[0])['created']>600:
                c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,dump({'token':token,'created':int(service.clock())})))
        return {'ok':True}
    secret=service.config.read_webhook_secret
    expected='sha256='+hmac.new(secret.encode(),body,hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(expected,str(signature or '')):raise NotionError('notionInvalidSignature',403)
    event_type=str(event.get('type',''))[:100]
    if not event_type.startswith(('page.','data_source.','database.','view.')):return {'ok':True}
    entity=event.get('entity') or {};workspace=event.get('workspace_id');identity=event.get('id')
    if not isinstance(workspace,str) or not 1<=len(workspace)<=100 or not isinstance(identity,str) or not 1<=len(identity)<=100:raise NotionError('notionInvalidRequest',400)
    try:entity_id=page_id(entity.get('id',''))
    except (ValueError,AttributeError):raise NotionError('notionInvalidRequest',400) from None
    stamp=int(service.clock())
    with service.db() as c:
        c.execute('BEGIN IMMEDIATE')
        event_id=kind+':'+identity
        inserted=c.execute('INSERT INTO notion_events(id,workspace_id,entity_id,payload,created) VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                           (event_id,workspace,entity_id,dump({'entity':entity_id,'type':event_type}),stamp))
        if inserted.rowcount==0:return {'ok':True}
        roots=c.execute("SELECT DISTINCT r.id,r.connection_id,p.status AS page_status FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id JOIN notion_connections n ON n.id=r.connection_id WHERE p.notion_id=? AND n.workspace_id=? AND n.kind='read' AND n.status='active' AND r.status IN ('active','importing')",(entity_id,workspace)).fetchall()
        for root in roots:
            if root['page_status']=='importing':refresh_import(c,root['id'],entity_id,stamp)
            else:enqueue(c,root['id'],entity_id,'pull',stamp,delay=2)
        found={root['connection_id'] for root in roots}
        candidates={row[0] for row in c.execute("SELECT DISTINCT n.id FROM notion_connections n JOIN notion_roots r ON r.connection_id=n.id WHERE n.workspace_id=? AND n.kind='read' AND n.status='active' AND r.status IN ('active','importing') AND EXISTS(SELECT 1 FROM notion_pages p WHERE p.root_id=r.id AND p.status!='importing')",(workspace,)).fetchall()}-found
        if candidates and not event_type.endswith(('.deleted','.trashed')):
            previous=c.execute('SELECT * FROM notion_events WHERE workspace_id=? AND entity_id=? AND pending IN (1,2) AND created>=? ORDER BY created DESC LIMIT 1',(workspace,entity_id,stamp-10)).fetchone()
            if previous and '.moved' not in event_type:
                value=json.loads(previous['payload']);value['pending']=sorted(set(value.get('pending',[]))|candidates)
                c.execute('UPDATE notion_events SET pending=1,payload=?,due=? WHERE id=?',(dump(value),stamp+2,previous['id']))
            else:
                value={'entity':entity_id,'type':event_type,'kind':entity.get('type','page'),'pending':sorted(candidates),'chain':[],'next':[entity.get('type','page'),entity_id]}
                c.execute('UPDATE notion_events SET pending=1,payload=?,due=? WHERE id=?',(dump(value),stamp+2,event_id))
    return {'ok':True}


def route_unknown(service):
    """Resolve new/moved pages without depending on Notion's search index.

An ancestor chain contains IDs only and is a short-lived routing hint. Every
resulting pull still uses its owner's grant and verifies current membership.
Different accounts never share cached page contents or authorization results.
"""
    if not service.config.configured:return False
    from app.process_locks import try_file_lock
    lease=try_file_lock(service.data_dir,'notion-worker.lock')
    if lease is None:return False
    with lease:return _route_unknown(service)


def _route_unknown(service):
    from app.notion_sync.hierarchy import metadata,reference
    stamp=int(service.clock())
    with service.db() as c:
        row=c.execute('SELECT * FROM notion_events WHERE pending=1 AND due<=? ORDER BY due,created LIMIT 1',(stamp,)).fetchone()
        if not row:return False
        row=dict(row);value=json.loads(row['payload']);pending=list(value.get('pending',[]))
        connection=None
        while pending:
            candidate=pending[0]
            grant=c.execute("SELECT 1 FROM notion_connections WHERE id=? AND kind='read' AND status='active'",(candidate,)).fetchone()
            if grant:connection=candidate;break
            pending.pop(0)
        value['pending']=pending
        if not connection:
            c.execute('UPDATE notion_events SET pending=2,payload=? WHERE id=?',(dump(value),row['id']));return True
    client=None
    try:
        chain=list(value.get('chain',[]));next_ref=value.get('next')
        def known(identity):
            with service.db() as c:return bool(c.execute("SELECT 1 FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id WHERE p.notion_id=? AND p.status!='importing' AND r.connection_id=? AND r.status IN ('active','importing')",(identity,connection)).fetchone())
        if not any(known(identity) for identity in chain):
            client=service.connections.client(connection)
            while next_ref:
                kind,identity=next_ref
                raw=metadata(client,kind,identity);parent=reference(raw)
                if not parent:next_ref=None;break
                if parent[1] in chain or len(chain)>=30:raise NotionError('notionPageTooLarge',422)
                chain.append(parent[1]);next_ref=list(parent)
                if known(parent[1]):break
        value.update(chain=chain,next=next_ref)
        # A bounded ID-only join routes all matching users in one query. The
        # order chooses the nearest already-copied ancestor for each root.
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT payload FROM notion_events WHERE id=?',(row['id'],)).fetchone()
            if not current:return True
            # More deliveries may have joined this aggregate during I/O.
            waiting=set(json.loads(current[0]).get('pending',[]))
            routed=set()
            if chain:
                rows=c.execute("SELECT DISTINCT p.notion_id,r.id AS root_id,r.connection_id FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id JOIN notion_connections n ON n.id=r.connection_id WHERE p.notion_id IN ("+','.join('?' for _ in chain)+") AND n.workspace_id=? AND n.kind='read' AND n.status='active' AND p.status!='importing' AND r.status IN ('active','importing')",(*chain,row['workspace_id'])).fetchall()
                for match in sorted(rows,key=lambda item:chain.index(item['notion_id'])):
                    if match['connection_id'] in waiting and match['connection_id'] not in routed:
                        enqueue(c,match['root_id'],match['notion_id'],'pull',stamp);routed.add(match['connection_id'])
            waiting-=routed
            if not next_ref:waiting.clear()
            else:waiting.discard(connection)
            value['pending']=sorted(waiting)
            c.execute('UPDATE notion_events SET payload=?,pending=?,due=? WHERE id=?',(dump(value),1 if waiting else 2,stamp+1,row['id']))
    except NotionError as error:
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE');current=c.execute('SELECT payload FROM notion_events WHERE id=?',(row['id'],)).fetchone()
            if not current:return True
            value=json.loads(current[0])
            if error.status in (401,403,404,422):value['pending']=[cid for cid in value.get('pending',[]) if cid!=connection]
            if error.status in (429,529):c.execute('UPDATE notion_connections SET cooldown=? WHERE workspace_id=?',(stamp+error.delay,row['workspace_id']))
            c.execute('UPDATE notion_events SET payload=?,pending=?,due=? WHERE id=?',(dump(value),1 if value.get('pending') else 2,stamp+max(5,error.delay or 30),row['id']))
    finally:
        if client:client.close()
    return True


def parent_id(raw):
    parent=raw.get('parent') or {}
    value=parent.get('page_id') or parent.get('data_source_id') or parent.get('database_id')
    try:return page_id(value) if value else None
    except ValueError:return None


def timestamp(value):
    try:return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,AttributeError):return 0


def reconcile(service):
    """A single search page per tick; a persisted cursor makes restarts cheap."""
    if not service.config.configured:return False
    stamp=int(service.clock())
    with service.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT n.* FROM notion_connections n WHERE n.kind='read' AND n.status='active' AND n.cooldown<=? AND n.next_scan<=? AND EXISTS(SELECT 1 FROM notion_roots r WHERE r.connection_id=n.id AND r.status IN ('active','importing') AND EXISTS(SELECT 1 FROM notion_pages p WHERE p.root_id=r.id AND p.status!='importing')) ORDER BY n.next_scan,n.created LIMIT 1",(stamp,stamp)).fetchone()
        if not row:return False
        row=dict(row);payload=json.loads(row['payload']);scan=payload.get('scan') or {'since':payload.get('watermark',row['created']-60),'started':stamp}
        c.execute('UPDATE notion_connections SET next_scan=? WHERE id=?',(stamp+120,row['id']))
        links={r['notion_id']:dict(r) for r in c.execute("SELECT p.notion_id,p.root_id,p.remote_stamp,p.status FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id WHERE r.connection_id=? AND r.status IN ('active','importing')",(row['id'],)).fetchall()}
        known={identity:link['root_id'] for identity,link in links.items() if link['status']!='importing'}
    client=None
    try:
        client=service.connections.client(row['id'])
        data={'sort':{'direction':'descending','timestamp':'last_edited_time'},'page_size':100}
        if scan.get('cursor'):data['start_cursor']=scan['cursor']
        response=client.request('POST','/v1/search',data)
        if not isinstance(response.get('results'),list):raise NotionError('notionInvalidResponse')
        from app.notion_sync.hierarchy import reference,metadata
        edits=[];pending_edits=[];parents={};exhausted=False
        for raw in response['results']:
            edited=timestamp(raw.get('last_edited_time'))
            if edited and edited<scan['since']:exhausted=True;break
            entity=page_id(raw['id']);root=known.get(entity)
            if entity in links and links[entity]['status']=='importing':
                if links[entity]['remote_stamp']!=raw.get('last_edited_time',''):pending_edits.append((links[entity]['root_id'],entity))
                continue
            if root:
                if links[entity]['remote_stamp']==raw.get('last_edited_time',''):continue
            if not root:
                parent=reference(raw);seen=set()
                while parent and parent[1] not in known and parent not in seen and len(seen)<30:
                    seen.add(parent)
                    if parent not in parents:
                        # Fixed-origin metadata lookup, only until a selected
                        # ancestor or workspace boundary is reached.
                        try:parents[parent]=reference(metadata(client,*parent))
                        except NotionError as error:
                            if error.status in (403,404):parents[parent]=None
                            else:raise
                    parent=parents[parent]
                ancestor=parent[1] if parent else None
                root=known.get(ancestor)
                if root:edits.append((root,ancestor))
            elif root:edits.append((root,entity))
        more=response.get('has_more') and not exhausted
        cursor=response.get('next_cursor')
        if more and (not isinstance(cursor,str) or cursor==scan.get('cursor')):raise NotionError('notionInvalidResponse')
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT payload,status,next_scan FROM notion_connections WHERE id=?',(row['id'],)).fetchone()
            if not current or current['status']!='active':return True
            updated=json.loads(current['payload'])
            rescan=current['next_scan']==0
            if rescan:updated.pop('scan',None)
            elif more:updated['scan']={**scan,'cursor':cursor}
            else:updated.pop('scan',None);updated['watermark']=max(0,scan['started']-service.config.reconcile_seconds-60)
            c.execute('UPDATE notion_connections SET payload=?,next_scan=? WHERE id=?',(dump(updated),stamp+(2 if more or rescan else service.config.reconcile_seconds),row['id']))
            if not more:
                # View settings can change without a record/schema timestamp.
                # Recheck database metadata once per reconciliation interval;
                # unchanged record bodies remain cached.
                for database in c.execute("SELECT DISTINCT p.root_id,COALESCE(parent.notion_id,p.notion_id) AS entity FROM notion_pages p JOIN note_databases d ON d.note_id=p.note_id JOIN notion_roots r ON r.id=p.root_id LEFT JOIN notion_pages parent ON parent.root_id=p.root_id AND parent.notion_id=p.parent_id WHERE r.connection_id=? AND r.status IN ('active','importing') AND p.status!='importing'",(row['id'],)):
                    edits.append((database['root_id'],database['entity']))
            eligible={r[0] for r in c.execute("SELECT id FROM notion_roots WHERE connection_id=? AND status IN ('active','importing')",(row['id'],))}
            for root,entity in set(edits):
                if root in eligible:enqueue(c,root,entity,'pull',stamp)
            for root,entity in set(pending_edits):refresh_import(c,root,entity,stamp)
    except NotionError as error:
        if error.status==401:service.connections.pause(row['id'],'reauthorize')
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE')
            if error.status in (429,529):c.execute('UPDATE notion_connections SET cooldown=? WHERE workspace_id=?',(stamp+error.delay,row['workspace_id']))
            c.execute('UPDATE notion_connections SET next_scan=?,cooldown=? WHERE id=?',(stamp+max(error.delay,60),stamp+error.delay if error.status in (429,529) else row['cooldown'],row['id']))
    finally:
        if client:client.close()
    return True
