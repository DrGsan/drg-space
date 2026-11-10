"""Bounded immutable chat uploads and authenticated image/video streaming."""
from app.i18n import message as localized_message
import asyncio
import mimetypes
import re
import secrets
from contextlib import suppress
from urllib.parse import quote
from app.http import Router,Request,Query
from app.http import StreamingResponse
from app.storage import blocking
from app.file_store import file_store
from app.chat_storage import attachment_room,serial_file,media_kind,VIDEO_MIMES,decode,cutoff,MAX_ATTACHMENT,DRAFT_SECONDS,retire
from app.modules.social import space_access
from app.modules.files import filename
from app.modules.media import inspect_image
from PIL import Image


def create_chat_attachments_router(db,guard,protection,now,fail,data_dir):
    r=Router();store=file_store(db,data_dir);uploads=asyncio.Semaphore(2)
    def scope(c,sid,fid,uid,pending=False):
        space_access(c,sid,uid,fail)
        row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
        if not row or attachment_room(fid)!=sid:fail(404,localized_message('attachmentIsUnavailable'))
        if row['version']==1:
            if not pending or row['user_id']!=uid or row['created']<=now()-DRAFT_SECONDS:fail(404,localized_message('attachmentDraftIsUnavailable'))
        else:
            message=c.execute('SELECT * FROM messages WHERE id=? AND space_id=? AND sender_id=? AND created>? AND (expires=0 OR expires>?)',(row['version']-1,sid,row['user_id'],cutoff(now()),now())).fetchone()
            if not message or fid not in decode(message['body'])[1]:fail(404,localized_message('messageDeletedOrTheStoragePeriodHasExpired'))
        return dict(row)
    def identify(identity,name,head):
        if head.startswith((b'\x89PNG',b'\xff\xd8',b'GIF8')) or head.startswith(b'RIFF') and head[8:12]==b'WEBP':
            try:return inspect_image(store.read(identity,MAX_ATTACHMENT))[0]
            except (ValueError,OSError,EOFError,Warning,Image.DecompressionBombError):return 'application/octet-stream'
        from app.audio_media import audio_mime
        if head.startswith((b'OggS',b'\x1aE\xdf\xa3')) or len(head)>=12 and head[4:8]==b'ftyp':
            audio=audio_mime(store.read(identity,MAX_ATTACHMENT))
            if audio:return audio
        guessed=mimetypes.guess_type(name)[0]
        if len(head)>=12 and head[4:8]==b'ftyp' and guessed in VIDEO_MIMES and guessed!='video/webm':return guessed
        if head[4:8] in (b'moov',b'mdat',b'wide') and guessed=='video/quicktime':return guessed
        if head.startswith(b'\x1aE\xdf\xa3') and guessed=='video/webm':return 'video/webm'
        return 'application/octet-stream'
    @r.post('/api/chat/{sid}/attachments')
    async def upload(sid:str,request:Request,name:str=Query(max_length=255),as_file:bool=False):
        s=guard(request);name=filename(name);protection.rate(request,'chat-upload',30,subject=s['user_id'])
        with db() as c:space_access(c,sid,s['user_id'],fail)
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>MAX_ATTACHMENT:fail(413,localized_message('theAttachmentShouldNotExceed50Mb'))
        identity=secrets.token_hex(16);fid='chat-'+sid+'-'+secrets.token_urlsafe(12);writer=None;head=b''
        async with uploads:
            try:
                writer=await blocking(store.writer,identity)
                async for chunk in request.stream():
                    if writer.size+len(chunk)>MAX_ATTACHMENT:fail(413,localized_message('theAttachmentShouldNotExceed50Mb'))
                    if len(head)<64:head=(head+chunk)[:64]
                    await blocking(writer.write,chunk)
                size,_=await blocking(writer.finish);mime='application/octet-stream' if as_file else await blocking(identify,identity,name,head)
                from app.chat_recordings import from_request,file_row
                recording=from_request(request,mime,fail)
                with db() as c:
                    c.execute('BEGIN IMMEDIATE');current=guard(request);space_access(c,sid,current['user_id'],fail)
                    c.execute('INSERT INTO files(id,user_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',(fid,s['user_id'],'file',name,mime,size,identity,now(),now()))
                    if recording:c.execute('INSERT INTO chat_recordings(file_id,payload) VALUES(?,?)',(fid,recording))
                    saved=serial_file(file_row(c,fid))
            except BaseException:
                if writer:
                    with suppress(Exception):await blocking(writer.abort)
                raise
        return saved
    @r.delete('/api/chat/{sid}/attachments/{fid}')
    def cancel(sid:str,fid:str,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');space_access(c,sid,s['user_id'],fail)
            if attachment_room(fid)!=sid:fail(404,localized_message('attachmentIsUnavailable'))
            row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not row:return {'ok':True}
            if row['user_id']!=s['user_id']:fail(404,localized_message('attachmentIsUnavailable'))
            if row['version']!=1:fail(409,localized_message('sentAttachmentIsDeletedAlongWithTheMessage'))
            retire(c,row,now())
        return {'ok':True}
    @r.get('/api/chat/{sid}/attachments/{fid}')
    def read(sid:str,fid:str,request:Request,download:bool=False,preview:bool=False):
        s=guard(request)
        with db() as c:row=scope(c,sid,fid,s['user_id'],True)
        if preview and not download and media_kind(row['mime'])=='file':row['mime']=preview_mime(row)
        media=media_kind(row['mime']) in ('image','video','animation','audio')
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
        headers['Content-Disposition']=('inline' if media and not download else 'attachment')+"; filename=\"download\"; filename*=UTF-8''"+quote(row['name'],safe='')
        def content():
            offset=0
            stream=store.iter_read(row['object_id'])
            try:
                for chunk in stream:
                    with db() as c:scope(c,sid,fid,guard(request)['user_id'],True)
                    next_offset=offset+len(chunk)
                    if next_offset>start and offset<=end:yield chunk[max(0,start-offset):min(len(chunk),end-offset+1)]
                    offset=next_offset
                    if offset>end and status==206:break
            finally:stream.close()
        return StreamingResponse(content(),status_code=status,media_type=row['mime'] if media and not download else 'application/octet-stream',headers=headers)
    def preview_mime(row):
        stream=store.iter_read(row['object_id'])
        try:head=next(stream,b'')[:64]
        finally:stream.close()
        return identify(row['object_id'],row['name'],head)
    @r.get('/api/chat/{sid}/attachments/{fid}/preview')
    def preview(sid:str,fid:str,request:Request):
        s=guard(request)
        with db() as c:row=scope(c,sid,fid,s['user_id'],True)
        row['mime']=preview_mime(row)
        with db() as c:scope(c,sid,fid,guard(request)['user_id'],True)
        result=serial_file(row)
        result.pop('preview_url',None)
        if result['kind']!='file':result['url']+='?preview=true'
        return result
    return r
