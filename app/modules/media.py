from app.i18n import message as localized_message
import asyncio
import io
import json
import secrets
import warnings
from PIL import Image, UnidentifiedImageError
from app.http import Router, Request
from app.http import Response
from app.data_crypto import media_store
from app.modules.widget_models import dashboard

MAX_SIZE=50*1024*1024

def inspect_image(body):
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(body)) as image:
            mime={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp','GIF':'image/gif'}.get(image.format)
            if not mime:raise ValueError(localized_message('pngJpegWebpAndGifAreAllowed'))
            width,height=image.size;frames=getattr(image,'n_frames',1)
            if width*height>20000000 or frames>200 or width*height*frames>80000000:
                raise ValueError(localized_message('theImageOrAnimationIsTooLarge'))
            for frame in range(frames):image.seek(frame);image.load()
            return mime,width,height,frames

def create_media_router(db,ready,rate,now,fail,data_dir):
    router=Router();store=media_store(db,data_dir);lock=asyncio.Semaphore(2)
    def payload(row):
        result=dict(row);result.pop('user_id',None);return result|{'url':'/api/media/'+row['id']}
    @router.get('/api/media')
    def library(request:Request):
        s=ready(request)
        from app.modules.expenses import referenced_photos
        with db() as c:
            photos=referenced_photos(c,s['user_id'])
            return [payload(row) for row in c.execute('SELECT * FROM media WHERE user_id=? AND NOT EXISTS (SELECT 1 FROM note_assets a WHERE a.id=media.id) ORDER BY created DESC',(s['user_id'],)) if row['id'] not in photos]
    @router.post('/api/media')
    async def upload(request:Request):
        s=ready(request);rate(request,'media-upload',20)
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>MAX_SIZE:fail(413,localized_message('theFileMustBeNoMoreThan50'))
        async with lock:
            chunks=[];size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>MAX_SIZE:fail(413,localized_message('theFileMustBeNoMoreThan50'))
                chunks.append(chunk)
            body=b''.join(chunks)
            try:mime,width,height,frames=await asyncio.to_thread(inspect_image,body)
            except (ValueError,EOFError,UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(422,localized_message('theImageCouldNotBeReadAPng'))
            asset_id=secrets.token_hex(16)
            try:
                with db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    count,total=c.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM media WHERE user_id=? AND NOT EXISTS (SELECT 1 FROM note_assets a WHERE a.id=media.id)',(s['user_id'],)).fetchone()
                    if count>=100:fail(413,localized_message('libraryLimit100FilesTheTotalVolumeIs'))
                    store.write(asset_id,body)
                    c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)',(asset_id,s['user_id'],mime,size,width,height,frames,now()))
                    result=payload(c.execute('SELECT * FROM media WHERE id=?',(asset_id,)).fetchone())
            except Exception:store.delete(asset_id);raise
        return result
    @router.get('/api/media/{asset_id}')
    def read(asset_id:str,request:Request):
        s=ready(request)
        with db() as c:row=c.execute('SELECT * FROM media WHERE id=? AND user_id=?',(asset_id,s['user_id'])).fetchone()
        if not row:fail(404,localized_message('imageNotFound'))
        return Response(store.read(row['id']),media_type=row['mime'])
    @router.delete('/api/media/{asset_id}')
    def delete(asset_id:str,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT id FROM media WHERE id=? AND user_id=?',(asset_id,s['user_id'])).fetchone()
            if not row:fail(404,localized_message('imageNotFound'))
            if c.execute('SELECT 1 FROM note_assets WHERE id=?',(asset_id,)).fetchone():fail(409,localized_message('theFileIsUsedByAPageModify'))
            from app.modules.expenses import referenced_photos
            if asset_id in referenced_photos(c,s['user_id']):fail(409,localized_message('theFileIsAttachedToABillDelete'))
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
            if settings.get('profile',{}).get('avatar_id')==asset_id:fail(409,localized_message('theFileIsUsedAsAnAvatarChange'))
            if settings.get('appearance',{}).get('wallpaper')==asset_id:fail(409,localized_message('theFileIsUsedAsABackgroundFirst'))
            if any(asset.get('asset_id')==asset_id for item in dashboard(settings)['instances'] if item['module_id']=='media' for asset in item['config'].get('items',[])):
                fail(409,localized_message('theFileIsUsedByAWidgetFirst'))
            c.execute('DELETE FROM media WHERE id=?',(asset_id,))
        store.delete(row['id'])
        return {'ok':True}
    return router
