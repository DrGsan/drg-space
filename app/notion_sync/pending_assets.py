"""Durable private file checkpoints using normal encrypted files and GC.

These files belong to an unpublished revision, not to a second object store.
Their quota and backup ownership are the same as other note attachments.
"""
import json

from app.notion_sync.assets import identity
from app.notion_sync.client import NotionError
from app.notion_sync.state import dump

PREFIX='notion-pending-files:'


def references(c):
    # A small encrypted ownership index avoids decrypting every source snapshot
    # during the ordinary attachment sweep. It changes with the same transaction.
    rows=c.execute("SELECT s.value FROM app_settings s JOIN notion_pages p ON s.key=?||p.note_id JOIN notion_roots r ON r.id=p.root_id WHERE r.status!='detached'",(PREFIX,))
    return {fid for row in rows for fid in json.loads(row['value'])}


def published(c,nid):c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+nid,))


def discard(c,link,uid,stamp,keep=None):
    """Retire only proven pending files; published/history assets stay intact."""
    keep=keep or {};value=json.loads(link['payload']);old=value.get('pending_assets',{})
    retired={item['id']:item for key,item in old.items() if item['id']!=keep.get(key,{}).get('id')}
    ids=set()
    for fid,item in retired.items():
        row=c.execute('SELECT parent_id,user_id,object_id FROM files WHERE id=?',(fid,)).fetchone()
        if row and row['parent_id']==link['note_id'] and row['user_id']==uid and row['object_id']==item['object_id']:ids.add(fid)
    if ids:
        from app.file_trash import purge_file_rows
        purge_file_rows(c,ids,stamp)
    if keep:value['pending_assets']=keep
    else:value.pop('pending_assets',None)
    if old!=keep:c.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(value),link['id']))
    if keep:c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(PREFIX+link['note_id'],dump(sorted(item['id'] for item in keep.values()))))
    else:published(c,link['note_id'])
    if not keep and not c.execute('SELECT 1 FROM files WHERE parent_id=? LIMIT 1',(link['note_id'],)).fetchone():
        c.execute("DELETE FROM files WHERE id=? AND user_id=? AND kind='folder' AND mime='application/x-drg-note;space=private'",(link['note_id'],uid))
    return value


def previous(service,root,link,blocks):
    saved=json.loads(link['payload'])
    if not saved.get('pending_assets'):return saved.get('assets',{})
    sources={}
    def walk(items):
        for block in items:
            if block.get('type') in ('image','video','audio','file','pdf'):sources['block:'+block['id']]=identity(block.get(block['type']))
            walk(block.get('children',[]))
    walk(blocks)
    with service.db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM notion_pages WHERE id=?',(link['id'],)).fetchone()
        value=json.loads(row['payload']);old=value.get('pending_assets',{})
        ids=list({item['id'] for item in old.values()});files={}
        for start in range(0,len(ids),200):
            batch=ids[start:start+200]
            files.update({r['id']:r for r in c.execute('SELECT id,user_id,parent_id,object_id,size FROM files WHERE id IN ('+','.join('?' for _ in batch)+')',tuple(batch))})
        # A portable restore retains file IDs but assigns new encrypted object
        # identities. Resolve ownership through the canonical file row again.
        keep={key:{**item,'object_id':files[item['id']]['object_id']} for key,item in old.items() if sources.get(key)==item['fingerprint'] and item['id'] in files and files[item['id']]['user_id']==root['user_id'] and files[item['id']]['parent_id']==link['note_id'] and files[item['id']]['size']==item['size'] and files[item['id']]['object_id']}
        if keep!=old:value=discard(c,row,root['user_id'],int(service.clock()),keep)
        return {**value.get('assets',{}),**keep}


def checkpoint(service,root,link,assets,title):
    with service.db() as c:
        c.execute('BEGIN IMMEDIATE')
        allowed=c.execute("SELECT 1 FROM notion_roots r JOIN notion_connections connection ON connection.id=r.connection_id AND connection.user_id=r.user_id JOIN users u ON u.id=r.user_id JOIN notes n ON n.id=? WHERE r.id=? AND r.user_id=? AND r.status IN ('active','importing') AND connection.status='active' AND u.role='owner' AND n.user_id=r.user_id AND n.space_id IS NULL AND n.deleted IS NULL",(link['note_id'],root['id'],root['user_id'])).fetchone()
        if not allowed:raise NotionError('notionConnectionPaused',409)
        row=c.execute('SELECT * FROM notion_pages WHERE id=? AND root_id=?',(link['id'],root['id'])).fetchone()
        if not row:raise NotionError('notionLinkUnavailable',409)
        value=json.loads(row['payload']);pending=dict(value.get('pending_assets',{}))
        for key,item in assets.manifest.items():
            if key.startswith('block:') and item['id']!=value.get('assets',{}).get(key,{}).get('id'):pending[key]=item
        assets.insert(c,root['user_id'],title,int(service.clock()))
        discard(c,row,root['user_id'],int(service.clock()),pending)
    # The rows now durably own these objects. abort() must only remove objects
    # that haven't passed this transaction, even when a later file fails.
    assets.files.clear();assets.writers.clear()
    assets.has_checkpoint=True


def disconnect(c,rid,uid,stamp):
    for row in c.execute('SELECT * FROM notion_pages WHERE root_id=?',(rid,)).fetchall():
        if json.loads(row['payload']).get('pending_assets'):discard(c,row,uid,stamp)
