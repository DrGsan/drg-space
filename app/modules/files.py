"""Private file trees, optimistic text editing and expiring publications."""
from app.i18n import message as localized_message
from app.account_lifecycle import pending as pending_account
import asyncio
from app.storage import blocking
import calendar
import json
import mimetypes
import os
import secrets
from datetime import datetime,timezone
from urllib.parse import quote
from contextlib import suppress
from app.http import Router,Request,Response,HTTPError,Query
from app.http import StreamingResponse
from pydantic import BaseModel,ConfigDict,Field
from app.file_store import file_store
from app.chat_storage import attachment_room
from app.file_scopes import is_note_attachment,file_module_rows,is_tracker_file,tracker_file_ids,lock_tracker_file_projects
from app.file_trash import FILE_TRASH_SECONDS,purge_file_trash,purge_file_rows
from app import file_sharing as sharing
import hashlib
from itertools import chain

MAX_FILE_SIZE_MB=int(os.getenv('MAX_FILE_SIZE_MB','10240'))
if not 1<=MAX_FILE_SIZE_MB<=25600:raise RuntimeError('MAX_FILE_SIZE_MB must be between 1 and 25600.')
MAX_FILE_SIZE=MAX_FILE_SIZE_MB*1024*1024
TEXT_LIMIT=2*1024*1024

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Folder(Strict):
    name:str=Field(min_length=1,max_length=255)
    parent_id:str|None=None
class FolderImport(Strict):
    name:str=Field(min_length=1,max_length=255)
    parent_id:str|None=None
    folders:list[str]=Field(default_factory=list,max_length=2048)

class Rename(Strict):
    name:str=Field(min_length=1,max_length=255)
    version:int=Field(ge=1)
class MoveItem(Strict):
    id:str=Field(min_length=1,max_length=100)
    version:int=Field(ge=1)
class Move(Strict):
    items:list[MoveItem]=Field(min_length=1,max_length=500)
    parent_id:str|None=None
class Text(Strict):
    text:str=Field(max_length=TEXT_LIMIT)
    version:int=Field(ge=1)
class Selection(Strict):ids:list[str]=Field(default_factory=list,max_length=500)
class Share(Strict):
    mode:str
    password:str|None=Field(default=None,max_length=256)
    allowed:list[str]=Field(default_factory=list,max_length=50)
    duration:int|None=Field(default=None,ge=1,le=10000)
    unit:str='days'
    permission:str='view'
    download_limit:int|None=Field(default=None,ge=1,le=1000000,strict=True)
    auto_delete:bool=False
    confirmation:str|None=Field(default=None,max_length=4096)
class LimitUpdate(Strict):
    download_limit:int|None=Field(default=None,ge=1,le=1000000,strict=True)
    version:int=Field(ge=0)
class RetentionUpdate(Strict):
    delete_at:int=Field(ge=1,le=32503680000)
    version:int=Field(ge=1)
    confirmation:str|None=Field(default=None,max_length=4096)
class Unlock(Strict):password:str=Field(max_length=256)

def filename(value):
    if not isinstance(value,str) or not value.strip() or len(value)>255 or value.strip() in ('.','..') or any(ord(ch)<32 or ch in '/\\:' for ch in value):raise HTTPError(422,localized_message('nameUpTo255CharactersWithoutSlashesAnd'))
    return value.strip()

def expiry(data,stamp,fail):
    if data.duration is None:return None
    scale={'minutes':60,'hours':3600,'days':86400}
    if data.unit in scale:end=stamp+data.duration*scale[data.unit]
    elif data.unit in ('months','years'):
        months=data.duration*(12 if data.unit=='years' else 1);start=datetime.fromtimestamp(stamp,timezone.utc);number=start.year*12+start.month-1+months;year,month=divmod(number,12);month+=1
        if year>9999:fail(422,localized_message('dateIsTooLong'))
        end=int(start.replace(year=year,month=month,day=min(start.day,calendar.monthrange(year,month)[1])).timestamp())
    else:fail(422,localized_message('unknownTimeUnit'))
    if end>stamp+366*86400*10:fail(422,localized_message('publicationDateMustBeNoMoreThan10'))
    return end

def create_files_router(db,ready,guard,tokens,protection,now,fail,data_dir,public_url,ph):
    r=Router();store=file_store(db,data_dir);uploads=asyncio.Semaphore(2)
    def own(c,fid,uid,trashed=False):
        row=c.execute('SELECT * FROM files WHERE id=? AND user_id=?',(fid,uid)).fetchone()
        if not row or attachment_room(fid) or is_note_attachment(c,row) or is_tracker_file(c,row) or (row['deleted'] is not None)!=trashed:fail(404,localized_message('fileOrFolderNotFound'))
        return row
    def private_check(request,uid,fid=None):
        guard(request)
        if fid:
            with db() as c:own(c,fid,uid)
    def children(c,root):return [dict(x) for x in file_module_rows(c,c.execute('WITH RECURSIVE tree(id) AS (SELECT id FROM files WHERE id=? AND deleted IS NULL UNION ALL SELECT f.id FROM files f JOIN tree t ON f.parent_id=t.id WHERE f.deleted IS NULL) SELECT f.* FROM files f JOIN tree t ON f.id=t.id',(root,)))]
    def within(c,fid,root):return any(x['id']==fid for x in children(c,root))
    def parent(c,pid,uid):
        if pid:
            row=own(c,pid,uid)
            if row['kind']!='folder':fail(422,localized_message('parentMustBeAFolder'))
    def serial(row):
        result={k:row[k] for k in ('id','parent_id','kind','name','mime','size','version','created','updated')}
        if 'delete_at' in row.keys():result.update(delete_at=row['delete_at'],retention_version=row['retention_version'],retention_blocked=row['delete_at'] is None)
        if row['deleted'] is not None:result.update(deleted=row['deleted'],purge_at=row['deleted']+FILE_TRASH_SECONDS)
        return result
    def with_quota(result,uid):
        from app.quota import summary
        with db() as c:return result|{'quota':summary(c,uid)}
    def download_header(name):return 'attachment; filename="download"; filename*=UTF-8\'\''+quote(name,safe='')
    def publication(c,token,request,write=False):
        share=c.execute('SELECT * FROM file_shares WHERE token=? AND revoked=0 AND (expires IS NULL OR expires>?)',(token,now())).fetchone()
        if not share:fail(404,localized_message('linkNotFoundOrExpired'))
        row=c.execute('SELECT * FROM files WHERE id=?',(share['file_id'],)).fetchone()
        if not row or row['deleted'] is not None or attachment_room(row['id']) or is_note_attachment(c,row) or is_tracker_file(c,row):fail(404,localized_message('fileIsNoLongerAvailable'))
        if share['mode'] in ('authenticated','users'):
            s=ready(request)
            if share['mode']=='users' and s['login'] not in json.loads(share['allowed']):fail(403,localized_message('thisLoginIsNotInTheAccessList'))
        if share['mode']=='password':
            cookie=request.cookies.get('drg_file_'+tokens.digest(token)[:12])
            try:grant=tokens.decode(cookie,'file-share') if cookie else None
            except HTTPError:fail(423,localized_message('passwordAccessExpiryHasExpiredEnterYourPassword'))
            if not grant or grant['sub']!=token or not c.execute('SELECT 1 FROM file_share_sessions WHERE token=? AND share_token=? AND expires>?',(tokens.digest(grant.get('grant','')),token,now())).fetchone():fail(423,localized_message('aPasswordIsRequiredForAccess'))
        if write:
            from app.account_lifecycle import pending
            if pending(c,row['user_id']):fail(423,localized_message('theOwnerSAccountIsAwaitingDeletionChanges'))
            if share['permission']!='edit':fail(403,localized_message('publicationIsReadOnly'))
            grant=request.headers.get('x-file-edit-token') or request.cookies.get('drg_file_edit_'+tokens.digest(token)[:12])
            data=tokens.decode(grant,'file-edit') if grant else None
            if not data or data['sub']!=token:fail(403,localized_message('firstOpenTheEditingSession'))
            tokens.check_csrf(request,data.get('csrf',''))
        return share,row
    def scope(c,token,fid,request,write=False):
        share,root=publication(c,token,request,write)
        if not within(c,fid,root['id']):fail(404,localized_message('fileDoesNotBelongToThisPublication'))
        return share,own(c,fid,root['user_id'])
    async def write_upload(request,uid,parent_id,name,replace=None,version=None,authorize=None,batch=False):
        name=filename(name);protection.rate(request,'folder-file-upload' if batch else 'file-upload',600,period=60,subject=uid)
        size_error=f'Файл превышает допустимый размер: {MAX_FILE_SIZE_MB} МБ.'
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>MAX_FILE_SIZE:fail(413,size_error)
        identity=secrets.token_hex(16);writer=None;retired=None
        async with uploads:
            try:
                writer=await blocking(store.writer,identity)
                async for chunk in request.stream():
                    if writer.size+len(chunk)>MAX_FILE_SIZE:fail(413,size_error)
                    await blocking(writer.write,chunk)
                size,_=await blocking(writer.finish)
                with db() as c:
                    c.execute('BEGIN IMMEDIATE');sharing.lock(c,uid);parent(c,parent_id,uid)
                    if authorize:authorize(c)
                    mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
                    if replace:
                        old=own(c,replace,uid)
                        if old['kind']!='file':fail(422,localized_message('onlyTheFileCanBeReplaced'))
                        if old['version']!=version:fail(409,localized_message('fileHasBeenModifiedOpenItAgain'))
                        retired=old['object_id'];fid=replace
                        c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(retired,'file',now()))
                        c.execute('UPDATE files SET object_id=?,size=?,mime=?,updated=?,version=version+1 WHERE id=?',(identity,size,mime,now(),fid))
                    else:
                        fid=secrets.token_urlsafe(12)
                        c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',(fid,uid,parent_id,'file',name,mime,size,identity,now(),now()))
                    saved=serial(own(c,fid,uid))
            except BaseException:
                if writer:
                    with suppress(Exception):await blocking(writer.abort)
                raise
            if retired:
                with suppress(Exception):await blocking(collect_blob,retired,'file')
        return saved
    def read_text(row):
        if row['kind']!='file' or row['size']>TEXT_LIMIT:fail(415,localized_message('textEditorUtf8FilesUpTo2'))
        body=store.read(row['object_id'],TEXT_LIMIT)
        try:text=body.decode('utf-8')
        except UnicodeError:fail(415,localized_message('thisIsABinaryFileOrAnotherEncoding'))
        if '\0' in text:fail(415,localized_message('binaryFileCannotBeOpenedAsText'))
        return {'text':text,'version':row['version'],'name':row['name']}
    def save_text(fid,uid,data,authorize=None):
        body=data.text.encode()
        if len(body)>TEXT_LIMIT:fail(413,localized_message('textFileMustBeNoMoreThan2'))
        identity=secrets.token_hex(16);writer=store.writer(identity);old_id=None
        try:
            writer.write(body);writer.finish()
            with db() as c:
                c.execute('BEGIN IMMEDIATE');sharing.lock(c,uid);row=own(c,fid,uid)
                if authorize:authorize(c)
                if row['kind']!='file' or row['version']!=data.version:fail(409,localized_message('fileHasBeenModifiedOpenItAgain'))
                old_id=row['object_id'];c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(old_id,'file',now()));c.execute('UPDATE files SET object_id=?,size=?,updated=?,version=version+1 WHERE id=?',(identity,len(body),now(),fid))
                saved=serial(own(c,fid,uid))
        except BaseException:
            with suppress(Exception):writer.abort()
            raise
        if old_id:
            with suppress(Exception):collect_blob(old_id,'file')
        return saved
    def response(row,check=lambda:None,request=None,begin=None,extra=None):
        if row['kind']!='file':fail(422,localized_message('forTheFolderChooseToDownloadZip'))
        start,end=0,row['size']-1;partial=False
        if request and request.headers.get('range'):
            import re
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',request.headers['range'])
            if not match or not any(match.groups()):raise HTTPError(416,'Invalid range',headers={'Content-Range':'bytes */'+str(row['size'])})
            first,last=match.groups()
            if first:start=int(first);end=min(int(last) if last else end,end)
            else:start=max(0,row['size']-int(last))
            if start>end or start>=row['size']:raise HTTPError(416,'Invalid range',headers={'Content-Range':'bytes */'+str(row['size'])})
            partial=True
        headers={'Content-Length':str(max(0,end-start+1)),'Content-Disposition':download_header(row['name']),'Cache-Control':'no-store',**(extra or {})}
        if request:headers['Accept-Ranges']='bytes'
        if partial:headers['Content-Range']=f"bytes {start}-{end}/{row['size']}"
        def content():
            source=store.iter_range(row['object_id'],start,end,row['size']) if partial and (start or end<row['size']-1) else store.iter_read(row['object_id'])
            for chunk in source:check();yield chunk
        body=content()
        if request and request.method!='HEAD':
            try:
                first=next(body,b'')
                if begin:begin()
            except BaseException:body.close();raise
            output=chain((first,),body)
        else:output=body
        return StreamingResponse(output,status_code=206 if partial else 200,media_type='application/octet-stream',headers=headers,background=body.close)
    def zip_response(rows,selection,check=lambda fid=None:None,request=None,begin=None,extra=None):
        import zipstream
        ids=set(selection.ids)
        available={x['id']:x for x in rows}
        if not ids.issubset(available):fail(404,localized_message('selectedFileIsNotAvailable'))
        selected=set(available) if not ids else set(ids)
        for root in list(selected):
            if available[root]['kind']=='folder':
                changed=True
                while changed:
                    old=len(selected);selected.update(x['id'] for x in rows if x['parent_id'] in selected);changed=len(selected)!=old
        z=zipstream.ZipStream(compress_type=zipstream.ZIP_DEFLATED)
        names={}
        for item in rows:names[(item['parent_id'],item['name'])]=names.get((item['parent_id'],item['name']),0)+1
        def label(item):return (item['id'][:6]+'_' if names[(item['parent_id'],item['name'])]>1 else '')+item['name']
        for item in rows:
            if item['id'] not in selected:continue
            parts=[label(item)];current=item;seen={current['id']}
            while current['parent_id'] in available:
                current=available[current['parent_id']]
                if current['id'] in seen:fail(422,localized_message('damagedFolderStructure'))
                seen.add(current['id']);parts.insert(0,label(current))
            path='/'.join(parts)
            if item['kind']=='folder':z.add(b'',path+'/')
            else:
                def content(identity=item['object_id'],fid=item['id']):
                    for chunk in store.iter_read(identity):check(fid);yield chunk
                z.add(content(),path,size=item['size'])
        stream=iter(z)
        if request and request.headers.get('range'):raise HTTPError(416,localized_message('fileZipRestartDownload'))
        if request and request.method!='HEAD':
            try:
                first=next(stream,b'')
                if begin:begin()
            except BaseException:
                if hasattr(stream,'close'):stream.close()
                raise
            stream=chain((first,),stream)
        def output():
            for chunk in stream:check();yield chunk
        return StreamingResponse(output(),media_type='application/zip',headers={'Content-Disposition':download_header('files.zip'),'Cache-Control':'no-store',**(extra or {})})
    @r.get('/api/files')
    def listing(request:Request,parent_id:str|None=None,view:str='active'):
        s=guard(request)
        if view not in ('active','recent','shared'):fail(422,localized_message('unknownFileSection'))
        with db() as c:
            stamp=now();c.execute('BEGIN IMMEDIATE');purge_file_trash(c,stamp,s['user_id'])
            parent(c,parent_id,s['user_id'])
            if view=='active':rows=c.execute('SELECT * FROM files WHERE user_id=? AND parent_id IS ? AND deleted IS NULL ORDER BY kind DESC,created DESC',(s['user_id'],parent_id)).fetchall()
            elif view=='recent':rows=c.execute('SELECT * FROM files WHERE user_id=? AND deleted IS NULL ORDER BY updated DESC,id',(s['user_id'],)).fetchall()
            else:rows=c.execute('SELECT * FROM files WHERE user_id=? AND deleted IS NULL AND id IN (SELECT file_id FROM file_shares WHERE revoked=0 AND (expires IS NULL OR expires>?)) ORDER BY updated DESC,id',(s['user_id'],now())).fetchall()
            schedules=sharing.list_schedules(c,s['user_id'])
            shared={};deadlines=[x['due_at'] for x in schedules.values() if x['due_at'] is not None]
            for link in c.execute('SELECT file_id,expires FROM file_shares WHERE revoked=0 AND (expires IS NULL OR expires>?) AND file_id IN (SELECT id FROM files WHERE user_id=?)',(now(),s['user_id'])):
                fid,end=link['file_id'],link['expires']
                if end is not None:deadlines.append(end)
                if fid not in shared:shared[fid]=end
                elif end is None or shared[fid] is not None:shared[fid]=None if end is None else max(shared[fid],end)
            crumbs=[];pid=parent_id
            while pid:
                row=own(c,pid,s['user_id']);crumbs.insert(0,serial(row));pid=row['parent_id']
            from app.quota import summary
            rows=file_module_rows(c,rows,s['user_id'])
            if view=='recent':rows=rows[:200]
            count=len(file_module_rows(c,c.execute('SELECT * FROM files WHERE user_id=? AND deleted IS NOT NULL AND id=trash_batch',(s['user_id'],)),s['user_id']))
        # Expiry may release quota; read the ledger after transaction finalization.
        with db() as c:quota=summary(c,s['user_id'])
        from app.secret_vault import exists
        with db() as c:secret_enabled=exists(c,s['user_id'])
        return {'items':[dict(serial(sharing.decorate(x,schedules)),shared=x['id'] in shared,**({'shared_until':shared[x['id']]} if view=='shared' else {})) for x in rows if not attachment_room(x['id']) and (view!='shared' or x['id'] in shared)],'breadcrumbs':crumbs,'quota':quota,'max_file_bytes':MAX_FILE_SIZE,'trash_count':count,'secret_enabled':secret_enabled,'refresh_at':min(deadlines,default=None),'server_time':stamp}
    @r.post('/api/files/move')
    def move(data:Move,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);parent(c,data.parent_id,s['user_id'])
            if len({x.id for x in data.items})!=len(data.items):fail(422,localized_message('objectsShouldNotBeDuplicated'))
            rows=[own(c,item.id,s['user_id']) for item in data.items]
            for item,row in zip(data.items,rows):
                if item.version!=row['version']:fail(409,localized_message('objectHasBeenModifiedRefreshTheFolder'))
                if data.parent_id and row['kind']=='folder' and within(c,data.parent_id,row['id']):fail(422,localized_message('cannotMoveTheFolderIntoItselfOrA'))
            # A selection in Recent can include both a folder and its descendants.
            # Move only its roots so the selected tree retains its structure.
            selected={row['id'] for row in rows};roots=[]
            for row in rows:
                pid=row['parent_id']
                while pid and pid not in selected:pid=own(c,pid,s['user_id'])['parent_id']
                if not pid:roots.append(row)
            retention_changes=sharing.check_move(c,s['user_id'],[r['id'] for r in roots],data.parent_id,now())
            sharing.apply_plan(c,{'files':retention_changes},now())
            for row in roots:
                if row['parent_id']!=data.parent_id:
                    c.execute('UPDATE files SET parent_id=?,updated=?,version=version+1 WHERE id=?',(data.parent_id,now(),row['id']))
            sharing.resume_blocked(c,s['user_id'],now())
        return {'ok':True,'moved':len(roots),'retention_changes':retention_changes}
    @r.get('/api/files/trash')
    def trash(request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge_file_trash(c,now(),s['user_id'])
            rows=file_module_rows(c,c.execute('SELECT * FROM files WHERE user_id=? AND deleted IS NOT NULL AND id=trash_batch ORDER BY deleted DESC,created DESC',(s['user_id'],)),s['user_id'])
        from app.quota import summary
        with db() as c:quota=summary(c,s['user_id'])
        return {'items':[serial(x) for x in rows if not attachment_room(x['id'])],'quota':quota,'retention_days':7}
    @r.post('/api/files/folders')
    def folder(data:Folder,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);parent(c,data.parent_id,s['user_id']);fid=secrets.token_urlsafe(12)
            c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,created,updated) VALUES(?,?,?,?,?,?,?)',(fid,s['user_id'],data.parent_id,'folder',filename(data.name),now(),now()))
            result=serial(own(c,fid,s['user_id']))
        return with_quota(result,s['user_id'])
    @r.post('/api/files/folder-import')
    def folder_import(data:FolderImport,request:Request):
        s=guard(request);protection.rate(request,'folder-import',30,period=60,subject=s['user_id'])
        paths={''}
        for path in data.folders:
            if not isinstance(path,str) or len(path)>4096 or path.startswith('/') or path.endswith('/') or '\\' in path:fail(422,localized_message('relativePathsForNestedFoldersAreRequired'))
            if not path:continue
            pieces=path.split('/')
            if len(pieces)>32:fail(422,localized_message('noMoreThan32LevelsOfNesting'))
            pieces=[filename(piece) for piece in pieces]
            for depth in range(1,len(pieces)+1):paths.add('/'.join(pieces[:depth]))
        if len(paths)>2048:fail(422,localized_message('noMoreThan2048FoldersPerUpload'))
        name=filename(data.name);mapping={}
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);parent(c,data.parent_id,s['user_id'])
            existing={row[0] for row in c.execute('SELECT name FROM files WHERE user_id=? AND parent_id IS ? AND deleted IS NULL',(s['user_id'],data.parent_id))}
            base=name;number=2
            while name in existing:
                suffix=' ('+str(number)+')';name=base[:255-len(suffix)]+suffix;number+=1
            for path in sorted(paths,key=lambda value:(value.count('/')+(1 if value else 0),value)):
                fid=secrets.token_urlsafe(12);mapping[path]=fid
                pid=mapping[path.rsplit('/',1)[0] if '/' in path else ''] if path else data.parent_id
                title=path.rsplit('/',1)[-1] if path else name
                c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,created,updated) VALUES(?,?,?,?,?,?,?)',(fid,s['user_id'],pid,'folder',title,now(),now()))
            result=serial(own(c,mapping[''],s['user_id']))
        return {'root':result,'folders':mapping,**upload_grant(s,mapping[''])}

    def upload_grant(s,fid):
        end=min(now()+3600,s['expires'])
        return {'upload_token':tokens.encode('folder-upload',fid,end,owner=s['user_id'],session=s['token']),'upload_ttl':max(0,end-now())}

    @r.post('/api/files/folder-import/{fid}/renew')
    def renew_folder_upload(fid:str,request:Request):
        s=guard(request);protection.rate(request,'folder-upload-renew',30,period=3600,subject=s['user_id'])
        with db() as c:
            root=own(c,fid,s['user_id'])
            if root['kind']!='folder':fail(422,localized_message('aFolderIsRequiredForUploading'))
        return upload_grant(s,fid)

    def batch_authorization(request,s,pid):
        raw=request.headers.get('x-folder-upload-token')
        if not raw:return False
        grant=tokens.decode(raw,'folder-upload')
        if grant.get('owner')!=s['user_id'] or grant.get('session')!=s['token']:fail(403,localized_message('uploadSessionBelongsToAnotherAccountOrDevice'))
        if not pid:fail(403,localized_message('chooseTheFolderForThisUpload'))
        with db() as c:
            root=own(c,grant['sub'],s['user_id']);own(c,pid,s['user_id'])
            # Follow the target's ancestors rather than scan the whole imported tree.
            current=pid;seen=set()
            while current!=root['id']:
                if not current or current in seen:fail(403,localized_message('theFolderDoesNotBelongToThisUpload'))
                seen.add(current);current=own(c,current,s['user_id'])['parent_id']
        return True

    @r.post('/api/files/upload')
    async def upload(request:Request,parent_id:str|None=None,name:str=Query(max_length=255)):
        s=guard(request);batch=batch_authorization(request,s,parent_id)
        def authorize(c):
            current=guard(request)
            if batch:batch_authorization(request,current,parent_id)
        return await write_upload(request,s['user_id'],parent_id,name,authorize=authorize,batch=batch)
    @r.post('/api/files/download')
    def download_selected(data:Selection,request:Request):
        s=guard(request)
        with db() as c:rows=[dict(x) for x in file_module_rows(c,c.execute('SELECT * FROM files WHERE user_id=? AND deleted IS NULL',(s['user_id'],)),s['user_id'])]
        return zip_response(rows,data,lambda fid=None:private_check(request,s['user_id'],fid))
    @r.get('/api/files/download.zip')
    def zip_download(request:Request,ids:str=Query(default='',max_length=9000)):
        s=guard(request);selection=Selection(ids=[x for x in ids.split(',') if x])
        with db() as c:rows=[dict(x) for x in file_module_rows(c,c.execute('SELECT * FROM files WHERE user_id=? AND deleted IS NULL',(s['user_id'],)),s['user_id'])]
        return zip_response(rows,selection,lambda fid=None:private_check(request,s['user_id'],fid))
    @r.get('/api/files/{fid}/content')
    def content(fid:str,request:Request):
        s=guard(request)
        with db() as c:row=dict(own(c,fid,s['user_id']))
        return response(row,lambda:private_check(request,s['user_id'],fid))
    def thumbnail_response(row,authorize=None):
        from app.file_thumbnails import thumbnail
        limit=32*1024*1024
        if row['kind']!='file' or not (row['mime'] or '').startswith('image/') or row['size']>limit:fail(415,localized_message('thumbnailIsNotAvailableForThisFile'))
        body=store.read(row['object_id'],limit)
        rendered=thumbnail(body,fail)
        if authorize:authorize()
        return Response(rendered,media_type='image/webp',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
    @r.get('/api/files/{fid}/thumbnail')
    def thumbnail(fid:str,request:Request):
        s=guard(request)
        with db() as c:row=dict(own(c,fid,s['user_id']))
        return thumbnail_response(row,lambda:private_check(request,s['user_id'],fid))
    @r.get('/api/files/{fid}/text')
    def text(fid:str,request:Request):
        s=guard(request)
        with db() as c:row=dict(own(c,fid,s['user_id']))
        return read_text(row)
    @r.put('/api/files/{fid}/text')
    def edit(fid:str,data:Text,request:Request):
        s=guard(request);return save_text(fid,s['user_id'],data)
    @r.put('/api/files/{fid}/content')
    async def replace(fid:str,request:Request,version:int=Query(ge=1)):
        s=guard(request)
        with db() as c:row=own(c,fid,s['user_id']);name=row['name'];pid=row['parent_id']
        return await write_upload(request,s['user_id'],pid,name,fid,version,authorize=lambda c:guard(request))
    @r.patch('/api/files/{fid}')
    def rename(fid:str,data:Rename,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');old=own(c,fid,s['user_id'])
            if old['version']!=data.version:fail(409,localized_message('objectHasBeenModifiedRefreshTheFolder'))
            c.execute('UPDATE files SET name=?,updated=?,version=version+1 WHERE id=?',(filename(data.name),now(),fid));result=serial(own(c,fid,s['user_id']))
        return with_quota(result,s['user_id'])
    @r.delete('/api/files/{fid}')
    def delete(fid:str,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);own(c,fid,s['user_id']);stamp=now()
            selected=children(c,fid);selected_ids={row['id'] for row in selected}
            protected=tracker_file_ids(c,s['user_id'])
            for row in c.execute('SELECT id,parent_id FROM files WHERE user_id=?',(s['user_id'],)).fetchall():
                if row['id'] in protected and row['parent_id'] in selected_ids:
                    c.execute('UPDATE files SET parent_id=NULL WHERE id=?',(row['id'],))
            for row in selected:
                sharing.clear_schedule(c,row['id'])
                c.execute('UPDATE files SET deleted=?,trash_batch=?,updated=?,version=version+1 WHERE id=?',(stamp,fid,stamp,row['id']))
                for share in c.execute('SELECT token FROM file_shares WHERE file_id=?',(row['id'],)).fetchall():
                    c.execute('DELETE FROM file_share_sessions WHERE share_token=?',(share['token'],))
                c.execute('UPDATE file_shares SET revoked=1 WHERE file_id=?',(row['id'],))
            sharing.resume_blocked(c,s['user_id'],stamp)
        return {'ok':True}
    @r.post('/api/files/{fid}/restore')
    def restore(fid:str,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);stamp=now();purge_file_trash(c,stamp,s['user_id']);root=own(c,fid,s['user_id'],True)
            if root['trash_batch']!=fid:fail(404,localized_message('trashItemNotFound'))
            pid=root['parent_id']
            if pid:
                parent_row=c.execute('SELECT kind,deleted FROM files WHERE id=? AND user_id=?',(pid,s['user_id'])).fetchone()
                if not parent_row or parent_row['deleted'] is not None or parent_row['kind']!='folder':pid=None
            c.execute('UPDATE files SET parent_id=? WHERE id=?',(pid,fid))
            restored=c.execute('SELECT id FROM files WHERE user_id=? AND trash_batch=?',(s['user_id'],fid)).fetchall()
            lock_tracker_file_projects(c,restored)
            protected=tracker_file_ids(c,s['user_id'])
            for item in restored:
                if item['id'] not in protected:
                    c.execute('UPDATE files SET deleted=NULL,trash_batch=NULL,updated=?,version=version+1 WHERE id=?',(stamp,item['id']))
        return {'ok':True,'parent_id':pid}
    @r.delete('/api/files/{fid}/permanent')
    def permanent(fid:str,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');root=own(c,fid,s['user_id'],True)
            if root['trash_batch']!=fid:fail(404,localized_message('trashItemNotFound'))
            rows=c.execute('SELECT id,object_id FROM files WHERE user_id=? AND trash_batch=?',(s['user_id'],fid)).fetchall()
            lock_tracker_file_projects(c,rows)
            protected=tracker_file_ids(c,s['user_id'])
            rows=[row for row in rows if row['id'] not in protected]
            objects=[row['object_id'] for row in rows if row['object_id']]
            purge_file_rows(c,(row['id'] for row in rows),now())
        for identity in objects:
            with suppress(Exception):collect_blob(identity,'file')
        return {'ok':True}
    @r.get('/api/files/{fid}/shares')
    def shares(fid:str,request:Request):
        s=guard(request)
        with db() as c:
            own(c,fid,s['user_id']);return [{k:row[k] for k in ('token','mode','allowed','expires','created','permission')}|{'url':public_url+'/f/'+row['token'],'download_limit':row['download_limit'],'used':row['used'] or 0,'version':row['limit_version'] or 0} for row in c.execute('SELECT s.*,l.download_limit,l.used,l.version AS limit_version FROM file_shares s LEFT JOIN file_share_limits l ON l.share_token=s.token WHERE s.file_id=? AND s.revoked=0 AND (s.expires IS NULL OR s.expires>?)',(fid,now()))]
    def retention_confirmation(raw,kind):
        if not raw:return None
        try:return tokens.decode(raw,kind)
        except HTTPError:fail(409,localized_message('fileRetentionChanged'))
    def applied_changes(plan):
        return [{k:item[k] for k in ('id','name','kind','delete_at')}|{'version':item['retention_version']+1} for item in plan['files']]
    def confirmation_key(data,plan):
        public=data.model_dump(exclude={'confirmation'})
        return hashlib.sha256(json.dumps([public,plan['snapshot']],sort_keys=True).encode()).hexdigest()
    @r.post('/api/files/{fid}/share-preview')
    def share_preview(fid:str,data:Share,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);own(c,fid,s['user_id'])
            plan=sharing.plan(c,s['user_id'],fid,expiry(data,now(),fail),data.auto_delete,now())
            # Freeze the exact expiry, not a duration recomputed after a dialog.
            plan['confirmation']=tokens.encode('file-retention',s['user_id'],now()+300,key=confirmation_key(data,plan),end=plan['expires'])
            return plan
    @r.post('/api/files/{fid}/shares')
    def share(fid:str,data:Share,request:Request):
        s=guard(request);protection.rate(request,'file-share',30,subject=s['user_id'])
        if data.mode not in ('public','password','authenticated','users') or data.permission not in ('view','edit'):fail(422,localized_message('chooseThePublicationModeAndPermissions'))
        if data.mode=='password' and (not data.password or len(data.password)<8):fail(422,localized_message('publicationPasswordMinimum8Characters'))
        token=secrets.token_urlsafe(24);end=expiry(data,now(),fail);allowed=[]
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);own(c,fid,s['user_id'])
            confirmation=retention_confirmation(data.confirmation,'file-retention')
            if confirmation:
                if confirmation['sub']!=s['user_id']:fail(409,localized_message('fileRetentionChanged'))
                end=confirmation.get('end')
            plan=sharing.plan(c,s['user_id'],fid,end,data.auto_delete,now())
            if plan['requires_confirmation'] and (not confirmation or confirmation.get('key')!=confirmation_key(data,plan)):fail(409,localized_message('fileRetentionChanged'))
            if data.mode=='users':
                if not data.allowed:fail(422,localized_message('specifyAllowedLogins'))
                for name in data.allowed:
                    row=c.execute('SELECT login FROM users WHERE login=?',(name.strip().lower(),)).fetchone()
                    if not row:fail(422,localized_message('theSpecifiedLoginWasNotFound'))
                    if row[0] not in allowed:allowed.append(row[0])
            c.execute('INSERT INTO file_shares VALUES(?,?,?,?,?,?,0,?,?)',(token,fid,data.mode,ph.hash(data.password) if data.mode=='password' else None,json.dumps(allowed),end,now(),data.permission))
            if data.download_limit is not None:c.execute('INSERT INTO file_share_limits VALUES(?,?,0,1)',(token,data.download_limit))
            sharing.apply_plan(c,plan,now())
        return {'token':token,'url':public_url+'/f/'+token,'expires':end,'retention_changes':applied_changes(plan)}
    @r.patch('/api/file-shares/{token}/limit')
    def update_limit(token:str,data:LimitUpdate,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id'])
            row=c.execute('SELECT s.file_id FROM file_shares s JOIN files f ON f.id=s.file_id WHERE s.token=? AND f.user_id=? AND s.revoked=0',(token,s['user_id'])).fetchone()
            if not row:fail(404,localized_message('publicationNotFound'))
            state=sharing.budget(c,token)
            if state['version']!=data.version:fail(409,localized_message('fileShareLimitChanged'))
            if state['version']:c.execute('UPDATE file_share_limits SET download_limit=?,version=version+1 WHERE share_token=?',(data.download_limit,token))
            else:c.execute('INSERT INTO file_share_limits VALUES(?,?,0,1)',(token,data.download_limit))
            return sharing.budget(c,token)
    @r.get('/api/files/{fid}/retention')
    def retention(fid:str,request:Request):
        s=guard(request)
        with db() as c:
            own(c,fid,s['user_id']);files,shares,schedules=sharing.context(c,s['user_id'],now());chosen=sharing.subtree(files,fid)
            return {'files':[{'id':x,'kind':files[x]['kind'],'name':files[x]['name'],'delete_at':schedules[x]['due_at'],'version':schedules[x]['version']} for x in sorted(chosen & schedules.keys())]}
    def retention_plan(c,uid,fid,data):
        files,shares,schedules=sharing.context(c,uid,now());old=schedules.get(fid)
        if not old or old['version']!=data.version:fail(409,localized_message('fileRetentionChanged'))
        at=sharing.retention_deadline(files,shares,schedules,fid,data.delete_at)
        if at is None:fail(422,localized_message('fileRetentionUnlimitedLink'))
        changes=[{'id':fid,'kind':files[fid]['kind'],'name':files[fid]['name'],'delete_at':at,'retention_version':old['version']}]
        parents={x for x in sharing.ancestors(files,fid) & schedules.keys() if x!=fid and files[x]['kind']=='folder'}
        simulated=dict(schedules);simulated[fid]=old|{'not_before':at,'due_at':at}
        for parent_id in sorted(parents,key=lambda x:len(sharing.ancestors(files,x)),reverse=True):
            parent=schedules[parent_id];end=sharing.retention_deadline(files,shares,simulated,parent_id,parent['not_before'])
            if end is None:fail(422,localized_message('fileRetentionUnlimitedLink'))
            if parent['due_at']!=end:
                changes.append({'id':parent_id,'kind':'folder','name':files[parent_id]['name'],'delete_at':end,'retention_version':parent['version']})
                simulated[parent_id]=parent|{'not_before':end,'due_at':end}
        chosen=set().union(*(sharing.subtree(files,x) for x in {fid}|parents))
        relevant=[s for s in shares if s['file_id'] in chosen|sharing.ancestors(files,fid)]
        key=hashlib.sha256(json.dumps([fid,data.delete_at,data.version,changes,relevant,[(x,files[x]['version'],schedules.get(x)) for x in sorted(chosen)]],sort_keys=True).encode()).hexdigest()
        return {'files':changes,'snapshot':key}
    @r.post('/api/files/{fid}/retention-preview')
    def retention_preview(fid:str,data:RetentionUpdate,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);own(c,fid,s['user_id'])
            if data.delete_at<=now():fail(422,localized_message('fileRetentionFutureDate'))
            plan=retention_plan(c,s['user_id'],fid,data)
            plan['confirmation']=tokens.encode('file-retention-change',s['user_id'],now()+300,key=plan['snapshot'])
            return plan
    @r.patch('/api/files/{fid}/retention')
    def retention_update(fid:str,data:RetentionUpdate,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);own(c,fid,s['user_id'])
            if data.delete_at<=now():fail(422,localized_message('fileRetentionFutureDate'))
            plan=retention_plan(c,s['user_id'],fid,data);grant=retention_confirmation(data.confirmation,'file-retention-change')
            if not grant or grant['sub']!=s['user_id'] or grant.get('key')!=plan['snapshot']:fail(409,localized_message('fileRetentionChanged'))
            sharing.apply_plan(c,plan,now());return {'ok':True,'retention_changes':applied_changes(plan)}
    @r.delete('/api/files/{fid}/retention')
    def retention_cancel(fid:str,data:LimitUpdate,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id']);row=own(c,fid,s['user_id'])
            changed=c.execute('DELETE FROM '+sharing.schedule_table(row['kind'])+' WHERE file_id=? AND version=?',(fid,data.version))
            if changed.rowcount!=1:fail(409,localized_message('fileRetentionChanged'))
            return {'ok':True}
    @r.delete('/api/file-shares/{token}')
    def revoke(token:str,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');sharing.lock(c,s['user_id'])
            share=c.execute('SELECT f.user_id FROM file_shares s JOIN files f ON f.id=s.file_id WHERE s.token=?',(token,)).fetchone()
            if not share or share[0]!=s['user_id']:fail(404,localized_message('publicationNotFound'))
            c.execute('UPDATE file_shares SET revoked=1 WHERE token=?',(token,));c.execute('DELETE FROM file_share_sessions WHERE share_token=?',(token,));sharing.resume_blocked(c,s['user_id'],now())
        return {'ok':True}
    @r.post('/api/shared-files/{token}/unlock')
    def unlock(token:str,data:Unlock,request:Request,response:Response):
        protection.check(request,'file-password',token);protection.rate(request,'file-unlock',30,subject=token)
        with db() as c:
            share=c.execute("SELECT * FROM file_shares WHERE token=? AND mode='password' AND revoked=0 AND (expires IS NULL OR expires>?)",(token,now())).fetchone()
            if not share:fail(404,localized_message('publicationIsUnavailable'))
            row=c.execute('SELECT * FROM files WHERE id=?',(share['file_id'],)).fetchone()
            if not row or is_note_attachment(c,row) or attachment_room(row['id']) or is_tracker_file(c,row):fail(404,localized_message('publicationIsUnavailable'))
            try:valid=ph.verify(share['password'],data.password)
            except Exception:valid=False
            if not valid:
                protection.failure(request,'file-password',token);fail(403,localized_message('incorrectPassword'))
            protection.success(request,'file-password',token)
            grant=secrets.token_urlsafe(32);end=min(now()+3600,share['expires'] or now()+3600)
            c.execute('INSERT INTO file_share_sessions VALUES(?,?,?)',(tokens.digest(grant),token,end))
        cookie=tokens.encode('file-share',token,end,grant=grant)
        response.set_cookie('drg_file_'+tokens.digest(token)[:12],cookie,max_age=end-now(),httponly=True,secure=tokens.secure,samesite='strict',path='/api/shared-files/'+token)
        return {'ok':True}
    @r.get('/api/shared-files/{token}')
    def shared(token:str,request:Request,parent_id:str|None=None):
        with db() as c:
            share,root=publication(c,token,request)
            current=root
            if parent_id:
                _,current=scope(c,token,parent_id,request)
                if current['kind']!='folder':fail(422,localized_message('selectAFolder'))
            rows=c.execute('SELECT * FROM files WHERE parent_id=? AND deleted IS NULL ORDER BY kind DESC,created',(current['id'],)).fetchall() if current['kind']=='folder' else [current]
            crumbs=[];node=current;seen=set()
            while node['id'] not in seen:
                seen.add(node['id']);crumbs.insert(0,serial(node))
                if node['id']==root['id']:break
                node=own(c,node['parent_id'],root['user_id'])
            return {'root':serial(root),'current':serial(current),'items':[serial(x) for x in file_module_rows(c,rows,root['user_id'])],'breadcrumbs':crumbs,'permission':'view' if pending_account(c,root['user_id']) else share['permission'],'expires':share['expires'],'max_file_bytes':MAX_FILE_SIZE,**sharing.budget(c,token)}
    @r.get('/api/shared-files/{token}/budget')
    def shared_budget(token:str,request:Request,download_grant:str|None=Query(default=None,max_length=4096)):
        # Access checks are identical to the publication; no catalogue, ZIP or file reads.
        with db() as c:
            publication(c,token,request)
            state=sharing.budget(c,token)
            if download_grant:
                state['started']=bool(c.execute('SELECT 1 FROM file_download_grants WHERE token=? AND share_token=? AND expires>?',(tokens.digest(download_grant),token,now())).fetchone())
            return state
    def download_access(share,rows,selection,request,grant):
        key=sharing.fingerprint(rows,selection)
        with db() as c:state=sharing.budget(c,share['token'])
        if not state['version']:return {},None
        signed=grant or sharing.ticket(tokens,share,key,now())
        if request.method=='HEAD':
            with db() as c:
                existing=c.execute('SELECT fingerprint,expires FROM file_download_grants WHERE token=? AND share_token=?',(tokens.digest(signed),share['token'])).fetchone() if grant else None
                if not existing or existing['fingerprint']!=key or existing['expires']<=now():sharing.preview_allowed(c,share['token'])
        def begin():
            with db() as c:
                c.execute('BEGIN IMMEDIATE');publication(c,share['token'],request)
                sharing.consume(c,tokens,share,key,signed,now())
        return {'X-DrG-Download-Grant':signed},begin
    @r.get('/api/shared-files/{token}/download.zip')
    def shared_zip_get(token:str,request:Request,ids:str=Query(default='',max_length=9000),download_grant:str|None=Query(default=None,max_length=4096)):
        with db() as c:share,root=publication(c,token,request);share=dict(share);rows=children(c,root['id'])
        selection=Selection(ids=[x for x in ids.split(',') if x])
        available={r['id']:r for r in rows};chosen=set(available) if not selection.ids else set(selection.ids)
        if not chosen.issubset(available):fail(404,localized_message('selectedFileIsNotAvailable'))
        for identity in list(chosen):chosen.update(sharing.subtree(available,identity))
        headers,begin=download_access(share,[r for r in rows if r['id'] in chosen],['zip',sorted(selection.ids)],request,download_grant)
        def check(fid=None):
            with db() as c:scope(c,token,fid,request) if fid else publication(c,token,request)
        return zip_response(rows,selection,check,request,begin,headers)
    @r.get('/api/shared-files/{token}/{fid}/content')
    def shared_content(token:str,fid:str,request:Request,preview:bool=False,download_grant:str|None=Query(default=None,max_length=4096)):
        with db() as c:
            share,row=scope(c,token,fid,request);share=dict(share);row=dict(row)
            if preview:sharing.preview_allowed(c,token)
        headers,begin=({},None) if preview else download_access(share,[row],['file',fid],request,download_grant)
        def check():
            with db() as c:
                scope(c,token,fid,request)
                if preview:sharing.preview_allowed(c,token)
        return response(row,check,request,begin,headers)
    @r.get('/api/shared-files/{token}/{fid}/thumbnail')
    def shared_thumbnail(token:str,fid:str,request:Request):
        with db() as c:_,row=scope(c,token,fid,request);row=dict(row)
        def check():
            with db() as c:scope(c,token,fid,request)
        return thumbnail_response(row,check)
    @r.get('/api/shared-files/{token}/{fid}/text')
    def shared_text(token:str,fid:str,request:Request):
        with db() as c:
            _,row=scope(c,token,fid,request);sharing.preview_allowed(c,token);row=dict(row)
        return read_text(row)
    @r.post('/api/shared-files/{token}/edit-session')
    def edit_session(token:str,request:Request,response:Response):
        with db() as c:
            share,root=publication(c,token,request)
            from app.account_lifecycle import pending
            if pending(c,root['user_id']):fail(423,localized_message('theOwnerSAccountIsAwaitingDeletionChanges'))
            if share['permission']!='edit':fail(403,localized_message('readOnlyAccess'))
            end=min(now()+900,share['expires'] or now()+900)
        csrf=secrets.token_urlsafe(24);grant=tokens.encode('file-edit',token,end,csrf=csrf)
        response.set_cookie('drg_file_edit_'+tokens.digest(token)[:12],grant,max_age=end-now(),httponly=True,secure=tokens.secure,samesite='strict',path='/api/shared-files/'+token)
        return {'csrf':csrf,'expires':end,'edit_token':grant}
    @r.put('/api/shared-files/{token}/{fid}/text')
    def shared_edit(token:str,fid:str,data:Text,request:Request):
        with db() as c:_,row=scope(c,token,fid,request,True);uid=row['user_id']
        protection.rate(request,'shared-file-edit',60,subject=token)
        return save_text(fid,uid,data,lambda c:scope(c,token,fid,request,True))
    @r.post('/api/shared-files/{token}/download')
    def shared_zip(token:str,data:Selection,request:Request):
        return shared_zip_get(token,request,','.join(data.ids),None)
    @r.post('/api/shared-files/{token}/upload')
    async def shared_upload(token:str,request:Request,parent_id:str=Query(),name:str=Query(max_length=255)):
        with db() as c:_,folder=scope(c,token,parent_id,request,True);uid=folder['user_id']
        if folder['kind']!='folder':fail(422,localized_message('uploadAvailableInTheFolder'))
        return await write_upload(request,uid,parent_id,name,authorize=lambda c:scope(c,token,parent_id,request,True))
    def collect_blob(identity,kind):
        if kind in ('vault-file','vault-media'):
            from app.secret_vault import collect_source
            from app.data_crypto import media_store
            return collect_source(db,store,media_store(db,data_dir),identity,kind)
        with db() as c:
            referenced=(kind=='file' and c.execute('SELECT 1 FROM files WHERE object_id=?',(identity,)).fetchone()
                        or kind=='media' and c.execute('SELECT 1 FROM media WHERE id=?',(identity,)).fetchone())
            if referenced:
                c.execute('DELETE FROM blob_gc WHERE id=? AND kind=?',(identity,kind));return
        if kind=='file':store.delete(identity)
        else:
            from app.data_crypto import media_store
            media_store(db,data_dir).delete(identity)
        with db() as c:c.execute('DELETE FROM blob_gc WHERE id=? AND kind=?',(identity,kind))
    def cleanup():
        with db() as c:objects=c.execute('SELECT id,kind FROM blob_gc ORDER BY created LIMIT 100').fetchall()
        for row in objects:
            try:collect_blob(row['id'],row['kind'])
            except Exception:
                # Failed objects go behind the remaining queue, so one broken
                # backend/object cannot starve unrelated pending deletions.
                with db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    last=c.execute('SELECT COALESCE(MAX(created),0) FROM blob_gc').fetchone()[0]
                    c.execute('UPDATE blob_gc SET created=? WHERE id=? AND kind=?',(max(now(),last+1),row['id'],row['kind']))
                import logging
                logging.getLogger(__name__).warning('Private object deletion failed; queued for retry.')
    r.cleanup=cleanup
    r.store=store
    return r
