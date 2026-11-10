"""Linked copies remain protected even when server integration is disabled."""
import json
import time
from app.notion_sync.state import enqueue
_CHECK_LINKS=True
_AVAILABLE=False
_WRITE_AVAILABLE=False


def prepare(db,configured,write=False):
    global _CHECK_LINKS,_AVAILABLE,_WRITE_AVAILABLE
    _AVAILABLE=bool(configured)
    _WRITE_AVAILABLE=bool(configured and write)
    with db() as c:_CHECK_LINKS=bool(configured or c.execute('SELECT 1 FROM notion_pages LIMIT 1').fetchone())


def enabled():
    global _CHECK_LINKS,_AVAILABLE
    _CHECK_LINKS=True;_AVAILABLE=True

def write_enabled():
    global _WRITE_AVAILABLE
    _WRITE_AVAILABLE=True


def preload(c,notes):
    if not _CHECK_LINKS:return
    cache=getattr(c,'notion_sync_access',{})
    identities=list(dict.fromkeys(n['id'] for n in notes if n['id'] not in cache))
    if not identities:return
    for start in range(0,len(identities),200):
        batch=identities[start:start+200]
        rows=c.execute('SELECT p.note_id,p.notion_id,COALESCE(CASE WHEN d.note_id IS NOT NULL THEN p.parent_id END,p.notion_id) AS source_notion_id,p.status AS page_status,p.writable,r.id AS root_id,r.user_id AS owner_id,r.note_id AS root_note_id,r.mode,r.status,r.updated,u.role AS owner_role FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id JOIN users u ON u.id=r.user_id LEFT JOIN note_databases d ON d.note_id=p.note_id WHERE p.note_id IN ('+','.join('?' for _ in batch)+')',tuple(batch)).fetchall()
        cache.update({identity:None for identity in batch});cache.update({row['note_id']:dict(row) for row in rows})
    root_cache=getattr(c,'notion_sync_root_pending',{})
    warnings=getattr(c,'notion_sync_cover_warnings',{})
    icon_warnings=getattr(c,'notion_sync_icon_warnings',{})
    errors=getattr(c,'notion_sync_errors',{})
    roots=list({row['root_id'] for row in cache.values() if row and row['root_id'] not in root_cache})
    for start in range(0,len(roots),200):
        batch=roots[start:start+200]
        for root in c.execute("SELECT r.id,r.payload,EXISTS(SELECT 1 FROM notion_pages p WHERE p.root_id=r.id AND p.status='importing') AS pending FROM notion_roots r WHERE r.id IN ("+','.join('?' for _ in batch)+')',tuple(batch)):
            payload=json.loads(root['payload']);root_cache[root['id']]=bool(root['pending']);warnings.update(payload.get('cover_warnings',{}));icon_warnings.update(payload.get('icon_warnings',{}));errors[root['id']]=payload.get('error')
    for row in cache.values():
        if row:
            row['transfer_pending']=root_cache[row['root_id']]
            row['cover_warning']=('notionCoverRetryStopped' if warnings[row['note_id']].get('blocked') else 'notionCoverUnavailable') if row['note_id'] in warnings else None
            row['icon_warning']=('notionIconRetryStopped' if icon_warnings[row['note_id']].get('blocked') else 'notionIconUnavailable') if row['note_id'] in icon_warnings else None
            row['error']=errors.get(row['root_id'])
    c.notion_sync_root_pending=root_cache
    c.notion_sync_cover_warnings=warnings
    c.notion_sync_icon_warnings=icon_warnings
    c.notion_sync_errors=errors
    c.notion_sync_access=cache
    from app.notion_sync.presentation import index
    for uid in {row['owner_id'] for row in cache.values() if row}:index(c,uid)


def info(c,n):
    if not _CHECK_LINKS:return None
    preload(c,[n]);value=c.notion_sync_access.get(n['id'])
    if value:
        from app.notion_sync.presentation import index
        parent=index(c,value['owner_id'])['containers'].get(n['id'])
        if parent:
            value=dict(value)
            for kind in ('icon','cover'):
                if parent['id'] in getattr(c,'notion_sync_'+kind+'_warnings',{}):value[kind+'_warning']='notion'+kind.title()+'Unavailable'
    return value


def readonly(c,n):
    value=info(c,n)
    return bool(value and value['status']!='detached' and (value['owner_role']!='owner' or not _AVAILABLE or not _WRITE_AVAILABLE or not value['writable'] or value['mode']!='duplex' or value['status']!='active' or value['page_status']!='active'))


def pending(c,n):
    value=info(c,n)
    return bool(value and value['page_status']=='importing')


def can_reorder(c,n,uid):
    value=info(c,n)
    return bool(value and value['owner_role']=='owner' and n['user_id']==uid and n['space_id'] is None and value['page_status']!='importing')


def require_writable(c,n,fail):
    value=info(c,n)
    if not value or value['status']=='detached':return
    if readonly(c,n):
        from app.i18n import message
        fail(423,message('notionCopyReadOnly'))
    # The same transaction as the actual mutation owns this intent. A failed
    # version/ACL check rolls the job back; retries coalesce by source page ID.
    enqueue(c,value['root_id'],value['notion_id'],'push',int(time.time()),delay=2)


def serialized(c,n):
    value=info(c,n)
    if not value or value['status']=='detached':return None
    status=value['status']
    if status=='active' and value['page_status']=='unavailable':status='unavailable'
    return {**{key:item for key,item in value.items() if key not in ('owner_id','owner_role','root_note_id','source_notion_id')},'can_reorder':can_reorder(c,n,n['user_id']),'status':status if _AVAILABLE and value['owner_role']=='owner' else 'paused','read_only':readonly(c,n),'source_url':'https://www.notion.so/'+value['source_notion_id'].replace('-','')}
