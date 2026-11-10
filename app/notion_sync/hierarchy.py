"""Parent IDs are routing hints, never a substitute for content authorization."""
from app.notion_sync.client import page_id,NotionError


def reference(raw):
    parent=raw.get('parent') or {}
    for key,kind in (('page_id','page'),('data_source_id','data_source'),('database_id','database'),('block_id','block')):
        if parent.get(key):return kind,page_id(parent[key])
    return None


def metadata(client,kind,identity):
    path={'page':'pages','database':'databases','data_source':'data_sources','block':'blocks','view':'views'}.get(kind)
    if not path:raise NotionError('notionInvalidResponse')
    return client.request('GET','/v1/'+path+'/'+page_id(identity))


def ancestors(client,raw,*,stop_at=None):
    chain=[];seen=set();current=reference(raw)
    while current:
        kind,identity=current
        if identity in seen or len(seen)>=30:raise NotionError('notionPageTooLarge',422)
        chain.append(identity);seen.add(identity)
        if stop_at is not None and identity in stop_at:break
        current=reference(metadata(client,kind,identity))
    return chain


def selected_parent(client,raw,known,root_id):
    # A known parent may itself have moved out of the selected tree. Verify
    # the full current chain to the selected root before trusting that parent.
    chain=ancestors(client,raw,stop_at={root_id})
    if root_id not in chain:return None
    return next((identity for identity in chain if identity in known),None)
