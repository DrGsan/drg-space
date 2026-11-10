"""Durable download budgets and opt-in retention, independent of link expiry."""
import hashlib
import json
import secrets
from app.http import HTTPError
from app.i18n import message

FILE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS file_share_limits(share_token TEXT PRIMARY KEY REFERENCES file_shares(token) ON DELETE CASCADE,download_limit INTEGER CHECK(download_limit IS NULL OR download_limit>0),used INTEGER NOT NULL DEFAULT 0 CHECK(used>=0),version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS file_download_grants(token TEXT PRIMARY KEY,share_token TEXT NOT NULL REFERENCES file_shares(token) ON DELETE CASCADE,fingerprint TEXT NOT NULL,expires INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS file_download_grant_expiry_idx ON file_download_grants(expires);
CREATE TABLE IF NOT EXISTS file_retention(file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,not_before INTEGER NOT NULL,due_at INTEGER,version INTEGER NOT NULL DEFAULT 1,updated INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS file_retention_due_idx ON file_retention(due_at);
'''
FOLDER_SCHEMA = '''
CREATE TABLE IF NOT EXISTS file_folder_retention(file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,not_before INTEGER NOT NULL,due_at INTEGER,version INTEGER NOT NULL DEFAULT 1,updated INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS file_folder_retention_due_idx ON file_folder_retention(due_at);
'''
SCHEMA = FILE_SCHEMA + FOLDER_SCHEMA
TABLES = ('file_share_limits','file_download_grants','file_retention','file_folder_retention')

def schedule_table(kind):return 'file_folder_retention' if kind=='folder' else 'file_retention'

def clear_schedule(c,fid):
    for table in ('file_retention','file_folder_retention'):c.execute('DELETE FROM '+table+' WHERE file_id=?',(fid,))
GRANT_SECONDS = 86400

def fail(key,status=409):raise HTTPError(status,message(key))

def lock(c,uid):
    # All link/move/timer mutations and scheduled deletion take the same lock.
    # SQLite writers are already serialized by BEGIN IMMEDIATE.
    if c.backend.kind=='postgres':c.raw.execute('SELECT id FROM users WHERE id=%s FOR UPDATE',(uid,))

def budget(c,token):
    row=c.execute('SELECT download_limit,used,version FROM file_share_limits WHERE share_token=?',(token,)).fetchone()
    return dict(row) if row else {'download_limit':None,'used':0,'version':0}

def preview_allowed(c,token):
    row=budget(c,token)
    if row['download_limit'] is not None and row['used']>=row['download_limit']:fail('fileDownloadLimitReached',410)

def fingerprint(rows,selection):
    return hashlib.sha256(json.dumps([selection,sorted((r['id'],r['version'],r['object_id']) for r in rows)],sort_keys=True).encode()).hexdigest()

def ticket(tokens,share,key,stamp):
    return tokens.encode('file-download',share['token'],min(stamp+GRANT_SECONDS,share['expires'] or stamp+GRANT_SECONDS),key=key,nonce=secrets.token_urlsafe(18))

def consume(c,tokens,share,key,grant,stamp):
    state=budget(c,share['token'])
    if state['version']==0:return
    try:data=tokens.decode(grant,'file-download') if grant else None
    except HTTPError:fail('fileDownloadRetryChanged')
    if not data or data['sub']!=share['token'] or data.get('key')!=key:fail('fileDownloadRetryChanged')
    identity=tokens.digest(grant)
    inserted=c.execute('INSERT OR IGNORE INTO file_download_grants VALUES(?,?,?,?)',(identity,share['token'],key,data['exp']))
    if inserted.rowcount:
        changed=c.execute('UPDATE file_share_limits SET used=used+1 WHERE share_token=? AND (download_limit IS NULL OR used<download_limit)',(share['token'],))
        if changed.rowcount!=1:fail('fileDownloadLimitReached',410)
    else:
        old=c.execute('SELECT * FROM file_download_grants WHERE token=?',(identity,)).fetchone()
        if not old or old['expires']<=stamp or old['share_token']!=share['token'] or old['fingerprint']!=key:fail('fileDownloadRetryChanged')

def context(c,uid,stamp):
    from app.file_scopes import file_module_rows
    rows=[dict(r) for r in file_module_rows(c,c.execute('SELECT * FROM files WHERE user_id=? AND deleted IS NULL ORDER BY id',(uid,)),uid)]
    files={r['id']:r for r in rows}
    shares=[dict(r) for r in c.execute('SELECT s.token,s.file_id,s.expires FROM file_shares s JOIN files f ON f.id=s.file_id WHERE f.user_id=? AND s.revoked=0 AND (s.expires IS NULL OR s.expires>?) ORDER BY s.token',(uid,stamp)) if r['file_id'] in files]
    schedules=list_schedules(c,uid)
    return files,shares,schedules

def subtree(files,fid):
    children={}
    for row in files.values():children.setdefault(row['parent_id'],[]).append(row['id'])
    result=set();pending=[fid]
    while pending:
        current=pending.pop()
        if current in result:continue
        result.add(current);pending.extend(children.get(current,[]))
    return result & files.keys()

def ancestors(files,fid):
    result=set();current=fid
    while current in files and current not in result:
        result.add(current);current=files[current]['parent_id']
    return result

def deadline(files,shares,fid,minimum):
    relevant=[s for s in shares if s['file_id'] in ancestors(files,fid)]
    if any(s['expires'] is None for s in relevant):return None
    return max([minimum]+[s['expires'] for s in relevant])

def retention_deadline(files,shares,schedules,fid,minimum):
    if files[fid]['kind']!='folder':return deadline(files,shares,fid,minimum)
    chosen=subtree(files,fid);scope=chosen|ancestors(files,fid)
    relevant=[s for s in shares if s['file_id'] in scope]
    if any(s['expires'] is None for s in relevant):return None
    return max([minimum]+[s['expires'] for s in relevant]+[schedules[x]['not_before'] for x in chosen & schedules.keys()])

def plan(c,uid,fid,end,auto_delete,stamp):
    files,shares,schedules=context(c,uid,stamp)
    chosen=subtree(files,fid)
    if fid not in files:fail('fileOrFolderNotFound',404)
    if auto_delete and end is None:fail('fileRetentionNeedsExpiry',422)
    targets={x for x in chosen if auto_delete or x in schedules}
    targets.update(x for x in ancestors(files,fid) & schedules.keys() if files[x]['kind']=='folder')
    proposed=shares+[{'token':'new','file_id':fid,'expires':end}]
    changes=[]
    for identity in sorted(targets):
        old=schedules.get(identity)
        minimum=max(old['not_before'] if old else 0,end if auto_delete else 0)
        at=retention_deadline(files,proposed,schedules,identity,minimum)
        if at is None:fail('fileRetentionUnlimitedLink',422)
        # Never silently shorten a previously confirmed date after a revoke/move.
        at=max(at,old['not_before'] if old else 0)
        if auto_delete or not old or old['due_at']!=at:
            changes.append({'id':identity,'kind':files[identity]['kind'],'name':files[identity]['name'],'version':files[identity]['version'],'retention_version':old['version'] if old else 0,'delete_at':at,'previous_delete_at':old['due_at'] if old else None})
    if auto_delete and not targets:fail('fileRetentionEmpty',422)
    affected=chosen|set().union(*(subtree(files,x) for x in targets if files[x]['kind']=='folder'))
    lineage=set().union(*(ancestors(files,x) for x in affected)) if affected else set()
    relevant=[s for s in shares if s['file_id'] in lineage]
    snapshot=hashlib.sha256(json.dumps([fid,end,auto_delete,sorted((x,files[x]['version']) for x in affected),relevant,{x:schedules[x] for x in affected & schedules.keys()},changes],sort_keys=True).encode()).hexdigest()
    return {'files':changes,'snapshot':snapshot,'expires':end,'auto_delete':auto_delete,'requires_confirmation':bool(changes)}

def apply_plan(c,plan,stamp):
    for item in plan['files']:
        table=schedule_table(item.get('kind','file'))
        if item['retention_version']:
            c.execute('UPDATE '+table+' SET not_before=?,due_at=?,version=version+1,updated=? WHERE file_id=?',(item['delete_at'],item['delete_at'],stamp,item['id']))
        else:c.execute('INSERT INTO '+table+' VALUES(?,?,?,1,?)',(item['id'],item['delete_at'],item['delete_at'],stamp))

def list_schedules(c,uid):
    return {r['file_id']:dict(r) for r in c.execute('SELECT r.* FROM (SELECT * FROM file_retention UNION ALL SELECT * FROM file_folder_retention) r JOIN files f ON f.id=r.file_id WHERE f.user_id=?',(uid,))}

def decorate(row,schedules):
    row=dict(row);schedule=schedules.get(row['id'])
    if schedule:row.update(delete_at=schedule['due_at'],retention_version=schedule['version'],retention_blocked=schedule['due_at'] is None)
    return row

def check_move(c,uid,items,parent,stamp):
    files,shares,schedules=context(c,uid,stamp)
    moving=set();affected=set()
    for fid in items:
        moving.update(subtree(files,fid));affected.update(ancestors(files,fid))
    for fid in items:
        if fid in files:files[fid]['parent_id']=parent
        affected.update(ancestors(files,fid))
    affected=(moving|{x for x in affected if files[x]['kind']=='folder'}) & schedules.keys()
    changes=[]
    for fid in sorted(affected):
        old=schedules[fid];at=retention_deadline(files,shares,schedules,fid,old['not_before'])
        if at is None:fail('fileRetentionUnlimitedLink',422)
        if old['due_at'] is None or at>old['not_before']:changes.append({'id':fid,'kind':files[fid]['kind'],'name':files[fid]['name'],'delete_at':at,'retention_version':old['version']})
    return changes

def resume_blocked(c,uid,stamp):
    pending=c.execute('SELECT 1 FROM (SELECT file_id FROM file_retention WHERE due_at IS NULL UNION ALL SELECT file_id FROM file_folder_retention WHERE due_at IS NULL) r JOIN files f ON f.id=r.file_id WHERE f.user_id=? LIMIT 1',(uid,)).fetchone()
    if not pending:return
    files,shares,schedules=context(c,uid,stamp)
    for fid,old in schedules.items():
        if old['due_at'] is not None or fid not in files:continue
        row=files[fid];table=schedule_table(row['kind'])
        if row['kind']=='folder' and c.execute('SELECT 1 FROM files WHERE parent_id=? AND deleted IS NULL LIMIT 1',(fid,)).fetchone():continue
        at=retention_deadline(files,shares,schedules,fid,old['not_before'])
        if at is not None:c.execute('UPDATE '+table+' SET due_at=?,version=version+1,updated=? WHERE file_id=?',(max(stamp,at),stamp,fid))

def purge(c,stamp):
    # Indexed queue lookups; no scan of accounts or parked folders on idle ticks.
    due=c.execute('SELECT r.file_id,f.user_id,f.kind FROM (SELECT file_id,due_at FROM file_retention WHERE due_at<=? UNION ALL SELECT file_id,due_at FROM file_folder_retention WHERE due_at<=?) r JOIN files f ON f.id=r.file_id ORDER BY f.kind,r.due_at,r.file_id LIMIT 64',(stamp,stamp)).fetchall()
    owners={row['user_id'] for row in due}
    for uid in sorted(owners):
        lock(c,uid);files,shares,schedules=context(c,uid,stamp)
        ordered=sorted((item for item in due if item['user_id']==uid),key=lambda item:(item['kind']=='folder',-len(ancestors(files,item['file_id']))))
        for item in ordered:
            fid=item['file_id'];old=schedules.get(fid);table=schedule_table(item['kind'])
            if not old:continue
            if fid not in files:
                c.execute('DELETE FROM '+table+' WHERE file_id=?',(fid,));continue
            at=retention_deadline(files,shares,schedules,fid,old['not_before'])
            if at is None or at>stamp:
                c.execute('UPDATE '+table+' SET due_at=?,version=version+1,updated=? WHERE file_id=?',(at,stamp,fid));continue
            if item['kind']=='folder' and c.execute('SELECT 1 FROM files WHERE parent_id=? AND deleted IS NULL LIMIT 1',(fid,)).fetchone():
                c.execute('UPDATE '+table+' SET due_at=NULL,version=version+1,updated=? WHERE file_id=?',(stamp,fid));continue
            c.execute('UPDATE files SET deleted=?,trash_batch=id,updated=?,version=version+1 WHERE id=? AND deleted IS NULL',(stamp,stamp,fid))
            c.execute('DELETE FROM file_share_sessions WHERE share_token IN (SELECT token FROM file_shares WHERE file_id=?)',(fid,))
            c.execute('UPDATE file_shares SET revoked=1 WHERE file_id=?',(fid,))
            c.execute('DELETE FROM '+table+' WHERE file_id=?',(fid,))
            files.pop(fid,None);schedules.pop(fid,None)
        # An emptied, parked parent can resume after its scheduled child disappeared.
        resume_blocked(c,uid,stamp)
    c.execute('DELETE FROM file_download_grants WHERE expires<=?',(stamp,))

def validate_archive(c,invalid):
    for row in c.execute('SELECT * FROM file_share_limits'):
        if row['download_limit'] is not None and (type(row['download_limit']) is not int or not 1<=row['download_limit']<=1000000):invalid()
        if type(row['used']) is not int or row['used']<0 or type(row['version']) is not int or row['version']<1:invalid()
    from app.file_scopes import note_attachment_ids,tracker_file_ids
    from app.chat_storage import attachment_room
    protected=note_attachment_ids(c)|tracker_file_ids(c)
    for row in c.execute("SELECT r.*,f.kind,f.deleted,'file' AS expected_kind FROM file_retention r JOIN files f ON f.id=r.file_id UNION ALL SELECT r.*,f.kind,f.deleted,'folder' AS expected_kind FROM file_folder_retention r JOIN files f ON f.id=r.file_id"):
        if row['kind']!=row['expected_kind'] or row['deleted'] is not None or row['file_id'] in protected or attachment_room(row['file_id']):invalid()
        if type(row['not_before']) is not int or row['not_before']<0 or row['due_at'] is not None and (type(row['due_at']) is not int or row['due_at']<row['not_before']):invalid()
