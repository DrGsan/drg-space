"""Bounded account/grant-scoped metadata cache; never traverses page bodies."""
from collections import OrderedDict
from concurrent.futures import Future,TimeoutError
import copy,json,threading

from app.notion_sync.client import NotionError,page_id,digest
from app.notion_sync.snapshot import title


class PagePicker:
    def __init__(self,connections):
        self.connections=connections;self.entries=OrderedDict();self.lock=threading.Lock()

    def list(self,uid,connection,query='',cursor=''):
        owner=self.connections
        if not owner.config.configured:raise NotionError('notionNotConfigured',409)
        with owner.db() as c:
            row=c.execute("SELECT n.payload,n.workspace_id FROM notion_connections n JOIN users u ON u.id=n.user_id WHERE n.id=? AND n.user_id=? AND n.kind='read' AND n.status='active' AND u.role='owner'",(connection,uid)).fetchone()
        if not row:raise NotionError('notionAccessDenied',403)
        # OAuth reauthorization or token rotation invalidates cached grants.
        grant=json.loads(row['payload']);query=query.strip()
        key=(uid,connection,digest([grant.get('access_token'),grant.get('refresh_token')]),query,cursor)
        stamp=owner.clock()
        with self.lock:
            for stale,(expires,future) in list(self.entries.items()):
                if expires<=stamp and future.done():self.entries.pop(stale)
            cached=self.entries.get(key);leader=cached is None
            if leader:
                while len(self.entries)>=128:
                    stale=next((key for key,(_,future) in self.entries.items() if future.done()),None)
                    if stale is None:raise NotionError('notionRetryLater',503,5)
                    self.entries.pop(stale)
                future=Future();self.entries[key]=(stamp+300,future)
            else:future=cached[1];self.entries.move_to_end(key)
        if not leader:
            try:return copy.deepcopy(future.result(timeout=35))
            except TimeoutError:raise NotionError('notionRetryLater',503,5) from None
        client=None
        try:
            client=owner.client(connection)
            data={'page_size':100,'sort':{'direction':'descending','timestamp':'last_edited_time'}}
            if query:data['query']=query
            if cursor:data['start_cursor']=cursor
            response=client.request('POST','/v1/search',data)
            if not isinstance(response.get('results'),list):raise NotionError('notionInvalidResponse')
            items={}
            for raw in response['results']:
                if raw.get('archived') or raw.get('in_trash') or raw.get('object') not in ('page','database','data_source'):continue
                identity=page_id((raw.get('parent') or {}).get('database_id',raw['id'])) if raw.get('object')=='data_source' else page_id(raw['id'])
                icon=raw.get('icon') or {}
                items.setdefault(identity,{'id':identity,'title':title(raw),'kind':'page' if raw.get('object')=='page' else 'database','emoji':icon.get('emoji') if icon.get('type')=='emoji' else None})
            next_cursor=response.get('next_cursor') if response.get('has_more') else None
            if next_cursor is not None and (not isinstance(next_cursor,str) or not next_cursor or next_cursor==cursor):raise NotionError('notionInvalidResponse')
            result={'items':list(items.values()),'cursor':next_cursor}
            future.set_result(result);return copy.deepcopy(result)
        except BaseException as error:
            try:
                if isinstance(error,NotionError):
                    if error.status==401:owner.pause(connection,'reauthorize')
                    if error.status in (429,529):
                        with owner.db() as c:c.execute('UPDATE notion_connections SET cooldown=? WHERE workspace_id=?',(int(owner.clock())+error.delay,row['workspace_id']))
            finally:
                future.set_exception(error)
                with self.lock:self.entries.pop(key,None)
            raise
        finally:
            if client:client.close()

    def tree(self,uid,connection,refresh=False):
        """One bounded metadata snapshot, shared by expansion and local search.

        Notion search is not a complete inventory. Data-source records and
        missing accessible ancestors are resolved once, never page bodies.
        Explicit refresh is coalesced and throttled to 30 seconds per grant.
        """
        owner=self.connections
        if not owner.config.configured:raise NotionError('notionNotConfigured',409)
        with owner.db() as c:
            row=c.execute("SELECT n.payload,n.workspace_id FROM notion_connections n JOIN users u ON u.id=n.user_id WHERE n.id=? AND n.user_id=? AND n.kind='read' AND n.status='active' AND u.role='owner'",(connection,uid)).fetchone()
        if not row:raise NotionError('notionAccessDenied',403)
        grant=json.loads(row['payload']);stamp=owner.clock()
        key=(uid,connection,digest([grant.get('access_token'),grant.get('refresh_token')]),'tree')
        with self.lock:
            for stale,(expires,future) in list(self.entries.items()):
                if future.done() and (expires<=stamp or stale==key and refresh and expires-270<=stamp):self.entries.pop(stale)
            entry=self.entries.get(key);leader=entry is None
            if leader:
                while len(self.entries)>=128:
                    stale=next((k for k,(_,f) in self.entries.items() if f.done()),None)
                    if stale is None:raise NotionError('notionRetryLater',503,5)
                    self.entries.pop(stale)
                future=Future();self.entries[key]=(stamp+300,future)
            else:future=entry[1];self.entries.move_to_end(key)
        if leader:
            client=None
            try:
                client=owner.client(connection);result=catalog(client)
                future.set_result(result)
            except BaseException as error:
                if isinstance(error,NotionError):
                    if error.status==401:owner.pause(connection,'reauthorize')
                    if error.status in (429,529):
                        with owner.db() as c:c.execute('UPDATE notion_connections SET cooldown=? WHERE workspace_id=?',(int(owner.clock())+error.delay,row['workspace_id']))
                future.set_exception(error)
                with self.lock:self.entries.pop(key,None)
                raise
            finally:
                if client:client.close()
        try:result=copy.deepcopy(future.result(timeout=35))
        except TimeoutError:raise NotionError('notionRetryLater',503,5) from None
        # Link state is current SQL data, deliberately outside the metadata cache.
        from app.notion_sync.selection import links
        with owner.db() as c:reserved=links(c,uid,row['workspace_id'])
        by_id={item['id']:item for item in result['items']}
        for item in result['items']:
            chain=set(item['ancestors'])|{item['id']}
            for value in reserved.values():
                root=value['root'];source=by_id.get(root['notion_id'],{})
                relation='included' if root['notion_id'] in chain else 'reserved' if item['id'] in value['ids'] else 'contains' if item['id'] in source.get('ancestors',[]) else None
                if relation:
                    item['linked']={'id':root['id'],'title':json.loads(root['payload']).get('title',source.get('title','Notion')),'relation':relation}
                    break
        return result


def catalog(client):
    from app.notion_sync.hierarchy import reference,metadata
    raw={};budget=[100];request=client.request
    # Includes pagination, ancestor resolution and record queries in one budget.
    def bounded(method,path,data=None):
        budget[0]-=1
        if budget[0]<0:raise NotionError('notionPageTooLarge',422)
        return request(method,path,data)
    class Reader:
        request=staticmethod(bounded)
    reader=Reader()
    def collect(path,method='GET',data=None):
        # Reuse the client's pagination validation with a bounded request wrapper.
        from app.notion_sync.client import NotionClient
        return NotionClient.all(reader,path,method,data,maximum=2000)
    def add(value):
        if value.get('object') not in ('page','database','data_source') or value.get('archived') or value.get('in_trash'):return
        identity=page_id(value['id']);raw[identity]=value
        if len(raw)>2000:raise NotionError('notionPageTooLarge',422)
    for value in collect('/v1/search','POST',{'sort':{'direction':'descending','timestamp':'last_edited_time'}}):add(value)
    # Search omits database records; read their metadata with the documented API.
    for identity,value in list(raw.items()):
        if value['object']=='data_source':
            for record in collect('/v1/data_sources/'+identity+'/query','POST'):add(record)
    checked=set();missing=set();queue=list(raw)
    while queue:
        identity=queue.pop()
        if identity in checked:continue
        checked.add(identity);parent=reference(raw[identity])
        if not parent or parent[1] in raw or parent[1] in missing:continue
        kind,pid=parent
        try:value=metadata(reader,kind,pid)
        except NotionError as error:
            if error.status in (403,404):missing.add(pid);continue
            raise
        if value.get('object') in ('page','database','data_source'):
            add(value)
            if pid in raw:queue.append(pid)
        elif kind=='block':
            # Blocks are transparent navigation ancestors, never selectable pages.
            raw[pid]=value;queue.append(pid)
    def parents(identity):
        seen={identity};chain=[];value=raw[identity]
        while (parent:=reference(value)) and parent[1] in raw:
            pid=parent[1]
            if pid in seen or len(seen)>=30:raise NotionError('notionInvalidResponse',422)
            seen.add(pid);value=raw[pid]
            if value.get('object')!='block':chain.append(pid)
        return chain
    items=[]
    for identity,value in raw.items():
        if value.get('object')=='block':continue
        # One source is transparent, multiple sources stay separate containers.
        if value['object']=='data_source':
            parent=reference(value)
            database=raw.get(parent[1],{}) if parent else {}
            sources=database.get('data_sources',[])
            if len(sources)==1:continue
        chain=parents(identity)
        chain=[pid for pid in chain if not(raw[pid].get('object')=='data_source' and len(raw.get((reference(raw[pid]) or ('',''))[1],{}).get('data_sources',[]))==1)]
        icon=value.get('icon') or {}
        items.append({'id':identity,'title':title(value),'kind':'page' if value['object']=='page' else 'database','emoji':icon.get('emoji') if icon.get('type')=='emoji' else None,
                      'parent_id':chain[0] if chain else None,'ancestors':chain,'path':[title(raw[pid]) for pid in reversed(chain)]})
    children={item['parent_id'] for item in items}
    for item in items:item['has_children']=item['id'] in children
    items.sort(key=lambda item:(item['title'].casefold(),item['id']))
    return {'items':items,'partial':True}
