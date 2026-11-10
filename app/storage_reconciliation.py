"""Two-way reconciliation, under the exclusive storage gate.

Object enumeration must finish successfully before any changes. A missing SQL
object needs two definitive absence checks separated by a day; network and
permission failures never count. User document text and history are retained.
"""
import json
import logging

MISSING_KEY='storage-missing:v1'
MISSING_GRACE=86400
MAX_PROBES=128
logger=logging.getLogger(__name__)


def inventory(db,objects,media):
    with db() as c:
        files={r['object_id'] or 'row:'+r['id']:dict(r) for r in c.execute("SELECT id,object_id,size,created FROM files WHERE kind='file'")}
        images={r['id']:dict(r) for r in c.execute('SELECT id,size,created FROM media')}
    # Complete both listings before allowing any cleanup, including SQL cleanup.
    return {'file':(files,objects.ids()),'media':(images,media.ids())}


def summary(items):
    return {kind:{'records':len(rows),'objects':len(stored),'unreferenced':len(stored-rows.keys()),
                  'missing':len(rows.keys()-stored),'logical_bytes':sum(r['size'] for r in rows.values())}
            for kind,(rows,stored) in items.items()}


def remove_media_references(c,ids,stamp):
    """Clear structural references to confirmed lost media, never document text."""
    values={'image:'+aid for aid in ids}
    for note in c.execute('SELECT id,icon,cover FROM notes').fetchall():
        icon='📄' if note['icon'] in values else note['icon']
        cover='' if note['cover'] in values else note['cover']
        if (icon,cover)!=(note['icon'],note['cover']):
            c.execute('UPDATE notes SET icon=?,cover=?,updated=?,version=version+1 WHERE id=?',(icon,cover,stamp,note['id']))
    for user in c.execute('SELECT id,settings FROM users').fetchall():
        settings=json.loads(user['settings']);before=json.dumps(settings)
        profile=settings.get('profile',{})
        if profile.get('avatar_id') in ids:profile.pop('avatar_id')
        for widget in settings.get('dashboard',{}).get('instances',[]):
            if widget.get('module_id')!='media':continue
            config=widget.get('config',{});items=config.get('items',[])
            removed={item['id'] for item in items if item.get('asset_id') in ids}
            if removed:
                config['items']=[item for item in items if item['id'] not in removed]
                config['selected']=[identity for identity in config.get('selected',[]) if identity not in removed]
        if json.dumps(settings)!=before:c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),user['id']))
    for event in c.execute('SELECT id,payload FROM expense_events').fetchall():
        payload=json.loads(event['payload']);photos=payload.get('photos',[])
        kept=[aid for aid in photos if aid not in ids]
        if kept!=photos:
            payload['photos']=kept
            c.execute('UPDATE expense_events SET payload=?,version=version+1 WHERE id=?',(json.dumps(payload),event['id']))


def reconcile(db,objects,media,stamp):
    items=inventory(db,objects,media);report=summary(items)
    with db() as c:
        row=c.execute('SELECT value FROM app_settings WHERE key=?',(MISSING_KEY,)).fetchone()
    try:
        saved=json.loads(row[0]) if row else {}
        if not isinstance(saved,dict):saved={}
    except (ValueError,TypeError):saved={}
    pending={};candidates=[]
    for kind,(rows,stored) in items.items():
        for identity in rows.keys()-stored:
            if rows[identity]['created']>stamp-MISSING_GRACE:continue
            key=kind+':'+identity;old=saved.get(key)
            if (isinstance(old,dict) and all(type(old.get(k)) is int and 0<=old[k]<=stamp for k in ('first','checked'))
                    and old['first']<=old['checked']):pending[key]=old
            candidates.append((kind,identity,key))
    candidates.sort(key=lambda v:(0 if v[2] in pending and pending[v[2]]['first']<=stamp-MISSING_GRACE else 1,
                                  pending.get(v[2],{}).get('checked',0),v[2]))
    lost={'file':set(),'media':set()}
    # Probe before all writes: an unavailable backend must not advance timers.
    for kind,identity,key in candidates[:MAX_PROBES]:
        has_identity=kind!='file' or items['file'][0][identity]['object_id'] is not None
        if has_identity and (objects if kind=='file' else media).exists(identity):pending.pop(key,None);continue
        old=pending.get(key)
        if old and old['checked']<stamp and old['first']<=stamp-MISSING_GRACE:
            lost[kind].add(identity);pending.pop(key,None)
        else:pending[key]={'first':old['first'] if old else stamp,'checked':stamp}
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        from app.file_trash import purge_file_rows
        purge_file_rows(c,{items['file'][0][identity]['id'] for identity in lost['file']},stamp)
        if lost['media']:
            remove_media_references(c,lost['media'],stamp)
            for identity in lost['media']:
                c.execute('DELETE FROM media WHERE id=?',(identity,))
                c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(identity,'media',stamp))
        if pending:
            encoded=json.dumps(pending,sort_keys=True)
            if not row or row[0]!=encoded:
                c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(MISSING_KEY,encoded))
        elif row:c.execute('DELETE FROM app_settings WHERE key=?',(MISSING_KEY,))
    failures=0
    for kind,(rows,stored) in items.items():
        for identity in stored-rows.keys():
            try:(objects if kind=='file' else media).delete(identity)
            except Exception:
                failures+=1
                with db() as c:c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(identity,kind,stamp))
        report[kind]['removed_records']=len(lost[kind])
    if pending or any(lost.values()) or failures:
        logger.warning('Storage reconciliation: %d missing objects pending, %d lost records retired, %d object deletions queued.',len(pending),sum(map(len,lost.values())),failures)
    return report
