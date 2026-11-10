"""Account/workspace-scoped reservations, checked under the SQL write lock.

Staged and paused links reserve their sources too. No source mutations, schema
changes, or destructive merging of old copies are needed to prevent duplicates.
"""
import json
from app.notion_sync.client import NotionError
from app.notion_sync.hierarchy import reference,metadata


def links(c,uid,workspace,exclude=''):
    rows=c.execute("SELECT r.* FROM notion_roots r JOIN notion_connections n ON n.id=r.connection_id WHERE r.user_id=? AND n.workspace_id=? AND r.status!='detached' AND r.id!=? ORDER BY r.created,r.id",(uid,workspace,exclude)).fetchall()
    result={r['id']:{'root':dict(r),'ids':{r['notion_id']}} for r in rows}
    if not result:return result
    for table in ('notion_pages','notion_staging'):
        for row in c.execute(f"SELECT p.root_id,p.notion_id FROM {table} p JOIN notion_roots r ON r.id=p.root_id JOIN notion_connections n ON n.id=r.connection_id WHERE r.user_id=? AND n.workspace_id=? AND r.status!='detached' AND r.id!=?",(uid,workspace,exclude)):
            result[row['root_id']]['ids'].add(row['notion_id'])
    return result


def reject(root):
    raise NotionError('notionSelectionOverlap',409,title=json.loads(root['payload']).get('title','Notion'))


def check(c,uid,workspace,ids,exclude='',*,selected=None):
    for value in links(c,uid,workspace,exclude).values():
        root=value['root']
        if value['ids'].intersection(ids) or selected in json.loads(root['payload']).get('selection_ancestors',[]):reject(root)


def source(client,identity):
    try:return client.page(identity)
    except NotionError as error:
        if error.status not in (400,404):raise
        return metadata(client,'database',identity)


def relationship(client,identity,existing):
    """Fresh parent metadata only; never treats the discovery cache as authority.

    One metadata response is shared by all chains in this selection. Budget and
    depth bounds protect requests involving broken/cyclic provider hierarchies.
    """
    cache={};request=client.request;budget=[100]
    class Reader:
        def request(self,method,path,data=None):
            if path not in cache:
                budget[0]-=1
                if budget[0]<0:raise NotionError('notionPageTooLarge',422)
                cache[path]=request(method,path,data)
            return cache[path]
        def page(self,identity):return self.request('GET','/v1/pages/'+identity)
    def parents(raw,stop_at=None):
        chain=[];seen=set();parent=reference(raw)
        while parent:
            kind,pid=parent
            if pid in seen or len(seen)>=30:raise NotionError('notionPageTooLarge',422)
            seen.add(pid);chain.append(pid)
            if pid==stop_at:break
            try:parent=reference(metadata(reader,kind,pid))
            except NotionError as error:
                # A directly shared page can have an inaccessible parent.
                if error.status not in (403,404):raise
                break
        return chain
    reader=Reader();raw=source(reader,identity);chain=set(parents(raw))|{identity}
    for value in existing.values():
        root=value['root']
        if root['notion_id'] in chain or identity in value['ids']:reject(root)
        try:other=source(reader,root['notion_id']);other_parents=parents(other,identity)
        except NotionError as error:
            if error.status in (403,404):continue
            raise
        if identity in other_parents:reject(root)
    from app.notion_sync.snapshot import title
    return chain,title(raw)
