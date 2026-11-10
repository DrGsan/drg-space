"""Page attachments in ordinary encrypted file folders; no new schema."""
from app.i18n import message as localized_message
import json
import asyncio
import re
import mimetypes
import secrets
from contextlib import suppress
from urllib.parse import quote
from app.http import Router, Request, Query, StreamingResponse
from app.storage import blocking
from app.file_store import file_store
from app.modules.files import filename
from app.modules.social import note_access
from app.modules.notion import require_role

LIMIT = 50 * 1024 * 1024

def page_file(c, page, fid, fail, referenced=False, uid=None):
    row=c.execute("SELECT * FROM files WHERE id=? AND kind='file' AND deleted IS NULL",(fid,)).fetchone()
    if not row:fail(404,localized_message('attachmentIsUnavailable'))
    source=c.execute('SELECT * FROM notes WHERE id=?',(row['parent_id'],)).fetchone()
    folder=c.execute("SELECT mime FROM files WHERE id=? AND user_id=? AND kind='folder' AND deleted IS NULL",(row['parent_id'],row['user_id'])).fetchone()
    if not folder:fail(404,localized_message('attachmentFolderIsUnavailable'))
    scope=source['space_id'] if source else folder['mime'].removeprefix('application/x-drg-note;space=')
    same_private=page['space_id'] is None and row['user_id']==page['user_id']
    same_shared=page['space_id'] is not None and scope==page['space_id']
    if not (same_private or same_shared):fail(404,localized_message('attachmentIsUnavailable'))
    if uid is not None and row['parent_id']!=page['id']:
        from app.note_access import can_view
        from app.personal_data import NOTE_FILE_LINK
        linked={match[2] for match in NOTE_FILE_LINK.finditer((page['blocks'] or '')+page['body'])}
        if not ((source and can_view(c,source,uid)) or fid in linked):fail(404,localized_message('attachmentIsUnavailable'))
    if referenced:
        from app.personal_data import NOTE_FILE_LINK
        if fid not in {match[2] for match in NOTE_FILE_LINK.finditer((page['blocks'] or '')+page['body'])}:fail(404,localized_message('noAttachmentsInThisPost'))
    return dict(row)

def file_response(store,row,request,authorize=None):
    inline=row['mime'] in ('image/png','image/jpeg','image/webp','image/gif','video/mp4','video/webm','audio/mpeg','audio/ogg','audio/wav')
    total=row['size'];start=0;end=total-1;status=200
    headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Accept-Ranges':'bytes','Content-Security-Policy':"sandbox; default-src 'none'"}
    value=request.headers.get('range')
    if value:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
        if len(value)>200 or not match or not total or not any(match.groups()):return StreamingResponse(iter(()),status_code=416,headers=headers|{'Content-Range':'bytes */'+str(total)})
        first,last=match.groups()
        if first:start=int(first);end=min(int(last),total-1) if last else total-1
        else:start=max(0,total-int(last))
        if start>=total or start>end:return StreamingResponse(iter(()),status_code=416,headers=headers|{'Content-Range':'bytes */'+str(total)})
        status=206;headers['Content-Range']=f'bytes {start}-{end}/{total}'
    headers['Content-Length']=str(max(0,end-start+1))
    headers['Content-Disposition']=('inline' if inline else 'attachment')+"; filename=\"download\"; filename*=UTF-8''"+quote(row['name'],safe='')
    def content():
        offset=0;stream=store.iter_read(row['object_id'])
        try:
            for chunk in stream:
                if authorize:authorize()
                next_offset=offset+len(chunk)
                if next_offset>start and offset<=end:yield chunk[max(0,start-offset):min(len(chunk),end-offset+1)]
                offset=next_offset
                if offset>end:break
        finally:stream.close()
    return StreamingResponse(content(),status_code=status,media_type=row['mime'] if inline else 'application/octet-stream',headers=headers)

def create_note_files_router(db,ready,rate,now,fail,data_dir):
    r=Router();store=file_store(db,data_dir);uploads=asyncio.Semaphore(2)
    @r.post('/api/notes/{nid}/files')
    async def upload(nid:str,request:Request,name:str=Query(max_length=255)):
        s=ready(request);name=filename(name);rate(request,'note-file-upload',60,subject=s['user_id'])
        with db() as c:
            n=note_access(c,nid,s['user_id'],fail);require_role(c,n,s['user_id'],fail,session=s,now=now)
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>LIMIT:fail(413,localized_message('theFileMustBeNoMoreThan50'))
        identity=secrets.token_hex(16);fid=secrets.token_urlsafe(12);writer=None
        async with uploads:
            try:
                writer=await blocking(store.writer,identity)
                async for chunk in request.stream():
                    if writer.size+len(chunk)>LIMIT:fail(413,localized_message('theFileMustBeNoMoreThan50'))
                    await blocking(writer.write,chunk)
                size,_=await blocking(writer.finish)
                # Do not trust a user-supplied MIME for active content.
                mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
                with db() as c:
                    c.execute('BEGIN IMMEDIATE');s=ready(request);n=note_access(c,nid,s['user_id'],fail);require_role(c,n,s['user_id'],fail,session=s,now=now)
                    folder=c.execute('SELECT * FROM files WHERE id=?',(nid,)).fetchone()
                    if folder and (folder['kind']!='folder' or folder['user_id']!=n['user_id'] or folder['deleted'] is not None):fail(409,localized_message('attachmentFolderIsUnavailable2'))
                    if not folder:c.execute('INSERT INTO files(id,user_id,kind,name,mime,created,updated) VALUES(?,?,?,?,?,?,?)',(nid,n['user_id'],'folder','Вложения — '+n['title'][:200],'application/x-drg-note;space='+(n['space_id'] or 'private'),now(),now()))
                    c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',(fid,n['user_id'],nid,'file',name,mime,size,identity,now(),now()))
            except BaseException:
                if writer:
                    with suppress(Exception):await blocking(writer.abort)
                raise
        return {'id':fid,'name':name,'url':'/api/notes/'+nid+'/files/'+fid}
    @r.get('/api/notes/{nid}/files/{fid}')
    def read(nid:str,fid:str,request:Request):
        s=ready(request);trash=request.args.get('trash')=='true'
        def readable(c,uid):
            n=note_access(c,nid,uid,fail,include_deleted=trash)
            if n['deleted']:
                from app.modules.notion import page_role
                if page_role(c,n,uid)!='editor':fail(403,localized_message('attachmentsFromTheRemotePageAreAvailableTo'))
            return n
        with db() as c:row=page_file(c,readable(c,s['user_id']),fid,fail,uid=s['user_id'])
        def authorize():
            with db() as c:
                current=ready(request)['user_id'];page_file(c,readable(c,current),fid,fail,uid=current)
        return file_response(store,row,request,authorize)
    return r


def purge_unused_attachments(c, stamp):
    """Retain every live/trash/history reference; allow a day for unfinished edits."""
    from app.personal_data import NOTE_FILE_LINK
    from app.file_scopes import note_attachment_ids
    from app.file_trash import purge_file_rows
    scope = note_attachment_ids(c)
    if not scope:return
    references = set()
    from app.notion_sync.pending_assets import references as pending_files
    references.update(pending_files(c))
    for table in ('notes', 'note_revisions'):
        for row in c.execute('SELECT body,blocks FROM '+table):
            references.update(match[2] for match in NOTE_FILE_LINK.finditer((row['body'] or '')+(row['blocks'] or '')))
    rows = [dict(row) for row in c.execute('SELECT id,parent_id,kind,created FROM files') if row['id'] in scope]
    note_ids = {r[0] for r in c.execute('SELECT id FROM notes')}
    remove = {r['id'] for r in rows if r['kind']=='file' and r['id'] not in references and r['created']<=stamp-86400}
    # An empty reserved folder without a page or retained child is service debris.
    for row in rows:
        if row['kind']=='folder' and row['id'] not in note_ids and not any(child['parent_id']==row['id'] and child['id'] not in remove for child in rows):remove.add(row['id'])
    purge_file_rows(c,remove,stamp)

def validate_file_links(c,nid,body,blocks,uid,fail,previous=''):
    from app.personal_data import NOTE_FILE_LINK
    from app.note_access import can_view
    existing={match[2] for match in NOTE_FILE_LINK.finditer(previous)}
    incoming={match[2] for match in NOTE_FILE_LINK.finditer((body or '')+json.dumps(blocks))}
    for fid in incoming-existing:
        row=c.execute('SELECT parent_id,user_id FROM files WHERE id=?',(fid,)).fetchone()
        if not row:continue
        if row['parent_id']==nid:continue
        source=c.execute('SELECT * FROM notes WHERE id=?',(row['parent_id'],)).fetchone()
        if (source and not can_view(c,source,uid)) or (not source and row['user_id']!=uid):fail(403,localized_message('noAccessToThePageWithThisAttachment'))
