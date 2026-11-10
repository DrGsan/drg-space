"""Small family portraits in encrypted shared settings, included in full backups."""
from app.i18n import message as localized_message
import asyncio
import base64
import hashlib
import io
from urllib.parse import quote
from PIL import Image, UnidentifiedImageError
from app.http import Request, Response
from app.modules.profile import AVATAR_LIMIT, thumbnail

PREFIX='family-avatar:'

def avatar_url(c,sid):
    row=c.execute('SELECT value FROM app_settings WHERE key=?',(PREFIX+sid,)).fetchone()
    if not row:return None
    version=hashlib.sha256(row[0].encode()).hexdigest()[:16]
    return f'/api/social/families/{quote(sid,safe="")}/avatar?v={version}'

def purge_avatars(c):
    families={r[0] for r in c.execute("SELECT id FROM spaces WHERE kind='family'")}
    for row in c.execute('SELECT key FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)).fetchall():
        if row[0][len(PREFIX):] not in families:c.execute('DELETE FROM app_settings WHERE key=?',(row[0],))

def validate_avatars(c,fail):
    families={r[0] for r in c.execute("SELECT id FROM spaces WHERE kind='family'")}
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)):
        try:
            if row[0][len(PREFIX):] not in families or len(row[1])>1024*1024:raise ValueError()
            with Image.open(io.BytesIO(base64.b64decode(row[1],validate=True))) as image:
                if image.format!='WEBP' or image.size!=(256,256) or image.n_frames!=1:raise ValueError()
                image.verify()
        except (ValueError,OSError):fail('Некорректное фото пространства в резервной копии.')

def register_avatars(router,db,ready,space_access,family_owner,rate,fail):
    lock=asyncio.Semaphore(2)
    @router.get('/api/social/families/{sid}/avatar')
    @router.get('/api/social/spaces/{sid}/avatar')
    def read(sid:str,request:Request):
        session=ready(request)
        with db() as c:
            space=space_access(c,sid,session['user_id'],fail,allow_pending=True)
            if space['kind']!='family':fail(404,localized_message('workspaceNotFound'))
            row=c.execute('SELECT value FROM app_settings WHERE key=?',(PREFIX+sid,)).fetchone()
        if not row:fail(404,localized_message('spacePhotoIsNotSet'))
        return Response(base64.b64decode(row[0],validate=True),media_type='image/webp',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @router.post('/api/social/families/{sid}/avatar')
    @router.post('/api/social/spaces/{sid}/avatar')
    async def upload(sid:str,request:Request):
        session=ready(request);rate(request,'family-avatar',20,subject=session['user_id'])
        with db() as c:family_owner(c,sid,session['user_id'])
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>AVATAR_LIMIT:fail(413,localized_message('thePhotoShouldBeNoMoreThan10'))
        async with lock:
            chunks=[];size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>AVATAR_LIMIT:fail(413,localized_message('thePhotoShouldBeNoMoreThan10'))
                chunks.append(chunk)
            try:body=await asyncio.to_thread(thumbnail,b''.join(chunks))
            except (ValueError,EOFError,UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(422,localized_message('youNeedPngJpegWebpOrAGif'))
            with db() as c:
                c.execute('BEGIN IMMEDIATE');family_owner(c,sid,session['user_id'])
                c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(PREFIX+sid,base64.b64encode(body).decode('ascii')))
                url=avatar_url(c,sid)
        return {'avatar_url':url}

    @router.delete('/api/social/families/{sid}/avatar')
    @router.delete('/api/social/spaces/{sid}/avatar')
    def remove(sid:str,request:Request):
        session=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');family_owner(c,sid,session['user_id'])
            c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+sid,))
        return {'avatar_url':None}
