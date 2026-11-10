"""One navigable database for a Notion container with one data source.

Provider identities remain intact for sync, history and files. This shared read
model removes only the technical container level; it never merges data sources.
"""
import copy
import json


def index(c,uid):
    cache=getattr(c,'notion_database_presentation',{})
    if uid in cache:return cache[uid]
    from app.notion_sync.access import _CHECK_LINKS
    aliases={};containers={}
    if _CHECK_LINKS:
        rows=c.execute("SELECT p.note_id AS source_id,p.notion_id,parent.note_id AS container_id,parent.payload FROM notion_pages p JOIN notion_pages parent ON parent.root_id=p.root_id AND parent.notion_id=p.parent_id JOIN notion_roots r ON r.id=p.root_id JOIN note_databases d ON d.note_id=p.note_id JOIN notes n ON n.id=p.note_id JOIN notes owner ON owner.id=parent.note_id WHERE r.user_id=? AND r.status!='detached' AND p.status!='importing' AND parent.status!='importing' AND n.user_id=r.user_id AND owner.user_id=r.user_id AND n.space_id IS NULL AND owner.space_id IS NULL AND n.deleted IS NULL AND owner.deleted IS NULL AND n.locked=0 AND owner.locked=0 AND n.parent_id=owner.id AND NOT EXISTS(SELECT 1 FROM notion_pages sibling WHERE sibling.root_id=p.root_id AND sibling.parent_id=p.parent_id AND sibling.notion_id!=p.notion_id)",(uid,)).fetchall()
        for row in rows:
            raw=json.loads(row['payload']).get('source',{}).get('raw',{})
            sources=raw.get('data_sources') or []
            if raw.get('object')!='database' or len(sources)!=1:continue
            if sources[0].get('id','').replace('-','')!=row['notion_id'].replace('-',''):continue
            aliases[row['container_id']]=row['source_id']
        ids=list(aliases)
        for start in range(0,len(ids),200):
            batch=ids[start:start+200]
            for row in c.execute('SELECT id,title,parent_id,icon,cover,position,updated,version,full_width FROM notes WHERE id IN ('+','.join('?' for _ in batch)+')',tuple(batch)):
                containers[aliases[row['id']]]=dict(row)
    value={'aliases':aliases,'containers':containers};cache[uid]=value;c.notion_database_presentation=cache
    return value


def canonical(c,nid,uid):return index(c,uid)['aliases'].get(nid,nid)


def hidden(c,nid,uid):return nid in index(c,uid)['aliases']


def storage_id(c,nid,uid):
    parent=index(c,uid)['containers'].get(nid)
    return parent['id'] if parent else nid


def clear(c):c.notion_database_presentation={}


def page(c,n,uid):
    """Resolve old container links and project display metadata, not stored data."""
    identity=canonical(c,n['id'],uid)
    if identity!=n['id']:n=c.execute('SELECT * FROM notes WHERE id=?',(identity,)).fetchone()
    result=dict(n);result['navigation_aliases']=[];parent=index(c,uid)['containers'].get(identity)
    if parent:
        for key in ('title','parent_id','position','full_width'):result[key]=parent[key]
        for key in ('icon','cover'):
            if parent[key]:result[key]=parent[key]
        result['updated']=max(n['updated'],parent['updated'])
        if 'version' in result:result['version']+=parent['version']
        result['navigation_aliases']=[parent['id']]
    return result


def blocks(c,items,uid):
    aliases=index(c,uid)['aliases']
    if not aliases or items is None:return items
    result=copy.deepcopy(items)
    def visit(values):
        for block in values:
            if block.get('type')=='pageLink':
                props=block.get('props',{});props['pageId']=aliases.get(props.get('pageId'),props.get('pageId'))
            visit(block.get('children',[]))
    visit(result);return result


def assets(c,n,uid):
    from app.modules.note_assets import asset_payload
    result=asset_payload(c,n);parent=index(c,uid)['containers'].get(n['id'])
    if parent:
        override=asset_payload(c,parent)
        for kind in ('icon','cover'):
            if parent[kind]:
                for key in (kind+'_url',kind+'_attribution',kind+'_position'):result.pop(key,None)
                result.update({key:value for key,value in override.items() if key.startswith(kind+'_')})
    return result
