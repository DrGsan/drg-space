"""Bounded account/grant-scoped metadata cache; never traverses page bodies."""
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
import copy,json,threading
from itertools import islice
from typing import NamedTuple

from app.notion_sync.client import NotionError,page_id,digest
from app.notion_sync.connections import grant_digest
from app.notion_sync.snapshot import title


class CatalogScope(NamedTuple):
    workspace: str
    key: str
    grant: str


class PagePicker:
    def __init__(self,connections,catalog_cache=None):
        self.connections=connections;self.entries=OrderedDict();self.lock=threading.Lock()
        self.catalog_cache=catalog_cache

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

    def _tree_scope(self,c,uid,connection):
        row=c.execute("SELECT n.payload,n.workspace_id,n.bot_id,n.updated FROM notion_connections n JOIN users u ON u.id=n.user_id WHERE n.id=? AND n.user_id=? AND n.kind='read' AND n.status='active' AND u.role='owner'",(connection,uid)).fetchone()
        if not row:raise NotionError('notionAccessDenied',403)
        grant=grant_digest(json.loads(row['payload']))
        workspace=row['workspace_id']
        return CatalogScope(workspace,digest([workspace,row['bot_id'],row['updated'],grant]),grant)

    def tree(self,uid,connection,refresh=False):
        owner=self.connections
        if not owner.config.configured:raise NotionError('notionNotConfigured',409)

        def read_scope():
            with owner.db() as c:return self._tree_scope(c,uid,connection)

        def build():
            def verify_scope():
                if read_scope()!=scope:
                    raise NotionError('notionRetryLater',503,5)

            client=owner.client(connection,request_check=verify_scope)
            try:
                # client() may rotate an expired grant before the first read.
                scope=read_scope()
                if scope.grant!=client.grant_digest:
                    raise NotionError('notionRetryLater',503,5)

                def provider_error(error):
                    if error.status==401 and error.code=='notionAuthorizationExpired':
                        owner.pause(connection,'reauthorize',expected_grant=scope.grant)
                    if error.status in (429,529):
                        until=int(owner.clock())+error.delay
                        with owner.db() as c:c.execute('UPDATE notion_connections SET cooldown=? WHERE workspace_id=? AND cooldown<?',
                                                        (until,scope.workspace,until))

                result=catalog(client,on_error=provider_error)
                verify_scope()
                return scope,result
            finally:
                client.close()

        scope=read_scope()
        if self.catalog_cache is None:
            scope,result=build()
        else:
            slot=digest([uid,connection])
            scope,result=self.catalog_cache.get(slot,scope,refresh,read_scope,build)
        # Link state is current SQL data, deliberately outside the metadata cache.
        from app.notion_sync.selection import links
        with owner.db() as c:
            if self._tree_scope(c,uid,connection)!=scope:
                raise NotionError('notionRetryLater',503,5)
            reserved=links(c,uid,scope.workspace)
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


def _overlap(executor, values, run):
    iterator=iter(values)
    pending=deque(executor.submit(run,value) for value in islice(iterator,2))
    while pending:
        yield pending.popleft().result()
        for value in islice(iterator,1):
            pending.append(executor.submit(run,value))


def catalog(client, *, on_error=None):
    from app.notion_sync.hierarchy import reference,metadata
    raw={};budget=100;failure=None;lock=threading.Lock()
    # Includes pagination, ancestor resolution and record queries in one budget.
    def bounded(method,path,data=None):
        nonlocal budget,failure
        with lock:
            if failure:raise NotionError(failure.code,failure.status,failure.delay,**failure.params)
            if not budget:raise NotionError('notionPageTooLarge',422)
            budget-=1
        try:
            return client.request(method,path,data)
        except NotionError as error:
            if method!='GET' or error.status not in (403,404):
                with lock:
                    first=failure is None
                    if first:failure=error
                if first and on_error:on_error(error)
            raise
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
    def records(identity):
        return collect('/v1/data_sources/'+identity+'/query','POST')

    def resolve(parent):
        kind,pid=parent
        try:
            return parent,metadata(reader,kind,pid)
        except NotionError as error:
            if error.status in (403,404):return parent,None
            raise

    with ThreadPoolExecutor(max_workers=2) as executor:
        # Preserve pagination within each source and combine responses in
        # search order. All requests still pass through the workspace budget.
        sources=[identity for identity,value in raw.items() if value['object']=='data_source']
        for batch in _overlap(executor,sources,records):
            for record in batch:add(record)

        checked=set(raw);queue=list(raw.values())
        while queue:
            pending={}
            for item in queue:
                parent=reference(item)
                if parent and parent[1] not in checked:
                    pending[parent[1]]=parent
                    checked.add(parent[1])
            queue=[]
            for (kind,pid),value in _overlap(executor,pending.values(),resolve):
                if value is None:continue
                if value.get('object') in ('page','database','data_source'):
                    add(value)
                elif kind=='block':
                    # Blocks are transparent navigation ancestors.
                    raw[pid]=value
                    if len(raw)>2000:raise NotionError('notionPageTooLarge',422)
                if pid in raw:queue.append(raw[pid])
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
