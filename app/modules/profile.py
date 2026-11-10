"""Private account avatar, independent of the optional gallery module."""
from app.i18n import message as localized_message
import asyncio
import io
import json
import secrets
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from app.http import Router, Request
from app.http import Response
from pydantic import BaseModel,ConfigDict,Field
from app.data_crypto import media_store
from app.modules.widget_models import dashboard
from app.avatar_presets import PRESETS, artwork, catalog

AVATAR_LIMIT=10*1024*1024

class ProfileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    display_name:str=Field(default='',max_length=80)

class PersonalNameInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    nickname:str=Field(default='',max_length=80)

class PresetInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    preset:str=Field(max_length=24)

def valid_profile(profile,asset_exists):
    return (isinstance(profile,dict) and not set(profile)-{'avatar_id','avatar_preset','display_name'}
            and not ('avatar_id' in profile and 'avatar_preset' in profile)
            and ('avatar_preset' not in profile or isinstance(profile['avatar_preset'],str) and profile['avatar_preset'] in PRESETS)
            and ('display_name' not in profile or isinstance(profile['display_name'],str) and len(profile['display_name'])<=80 and profile['display_name']==profile['display_name'].strip() and not any(ord(x)<32 for x in profile['display_name']))
            and ('avatar_id' not in profile or isinstance(profile['avatar_id'],str) and asset_exists(profile['avatar_id'])))

def thumbnail(body):
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(body)) as source:
            # Phone JPEGs can contain extra MPO/HDR frames. An avatar needs
            # only the primary image, not validation/decoding of every frame.
            if source.format not in {'PNG','JPEG','MPO','WEBP','GIF'}:raise ValueError('Unsupported avatar format')
            if source.width*source.height>80000000:raise ValueError('Avatar source exceeds 80 megapixels')
            source.seek(0)
            source.thumbnail((1024,1024),Image.Resampling.LANCZOS)
            image=ImageOps.exif_transpose(source).convert('RGBA')
            image=ImageOps.fit(image,(256,256),method=Image.Resampling.LANCZOS)
            output=io.BytesIO();image.save(output,format='WEBP',quality=85)
            return output.getvalue()

def profile_name(value,fail):
    name=value.strip()
    if any(ord(x)<32 for x in name):fail(422,localized_message('theNameMustBeASingleLine'))
    return name

def retire_avatar(c,uid,settings,asset_id):
    if not asset_id:return None
    used=settings.get('appearance',{}).get('wallpaper')==asset_id or any(asset.get('asset_id')==asset_id for item in dashboard(settings)['instances'] if item['module_id']=='media' for asset in item['config'].get('items',[]))
    if not used:
        c.execute('DELETE FROM media WHERE id=? AND user_id=?',(asset_id,uid))
        return asset_id

def save_avatar(c,store,uid,settings,body,stamp,fail):
    old=settings.get('profile',{}).get('avatar_id');retired=retire_avatar(c,uid,settings,old)
    count=c.execute('SELECT COUNT(*) FROM media WHERE user_id=? AND NOT EXISTS (SELECT 1 FROM note_assets a WHERE a.id=media.id)',(uid,)).fetchone()[0]
    if count>=100:fail(413,localized_message('thereIsNoSpaceInTheLibraryFor'))
    aid=secrets.token_hex(16)
    try:
        store.write(aid,body)
        c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)',(aid,uid,'image/webp',len(body),256,256,1,stamp))
    except BaseException:store.delete(aid);raise
    settings.setdefault('profile',{})['avatar_id']=aid
    settings['profile'].pop('avatar_preset',None)
    return aid,retired

def select_preset(c,uid,settings,preset):
    profile=settings.setdefault('profile',{})
    retired=retire_avatar(c,uid,settings,profile.pop('avatar_id',None))
    profile.pop('avatar_preset',None)
    if preset:profile['avatar_preset']=preset
    return retired

def create_profile_router(db,ready,rate,now,fail,data_dir):
    router=Router();store=media_store(db,data_dir);lock=asyncio.Semaphore(2)
    @router.get('/api/avatar-presets')
    def presets(request:Request):
        ready(request)
        return {'categories':catalog()}

    @router.get('/api/avatar-presets/{preset}')
    def preset_image(preset:str,request:Request):
        ready(request)
        if preset not in PRESETS:fail(404,localized_message('avatarIsUnavailable'))
        return Response(artwork(preset),media_type='image/webp',headers={'Cache-Control':'private, max-age=86400'})

    @router.put('/api/account/avatar/preset')
    def choose_preset(data:PresetInput,request:Request):
        s=ready(request)
        if data.preset not in PRESETS:fail(422,localized_message('chooseAnAvatarFromTheAvailableSet'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
            retired=select_preset(c,s['user_id'],settings,data.preset)
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
        if retired:store.delete(retired)
        return settings['profile']
    @router.get('/api/account/people-names')
    def personal_names(request:Request):
        from app.people_names import names
        s=ready(request)
        return names(json.loads(s['settings']))

    def save_personal_name(uid,name,request):
        from app.notifications import connected
        from app.note_access import connected_by_page
        from app.people_names import KEY,MAX_NAMES,names
        s=ready(request);rate(request,'personal-name',60,60,subject=s['user_id'])
        name=profile_name(name,fail)
        if any(ord(char)==127 for char in name):fail(422,localized_message('theNicknameMustBeASingleLine'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if uid==s['user_id']:fail(422,localized_message('youCanChangeYourNameInYourProfile'))
            if not c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():fail(404,localized_message('userIsUnavailable'))
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
            value=dict(names(settings))
            # A former contact may always have their saved nickname cleared.
            if name and s['role']!='owner' and not connected(c,s['user_id'],uid,now()) and not connected_by_page(c,s['user_id'],uid):fail(404,localized_message('userIsUnavailable'))
            if name:
                if uid not in value and len(value)>=MAX_NAMES:fail(422,localized_message('tooManySavedNicknames'))
                value[uid]=name
            else:value.pop(uid,None)
            if value:settings[KEY]=value
            else:settings.pop(KEY,None)
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
        return value

    @router.put('/api/users/{uid}/nickname')
    def rename_person(uid:str,data:PersonalNameInput,request:Request):
        return save_personal_name(uid,data.nickname,request)

    @router.delete('/api/users/{uid}/nickname')
    def reset_person(uid:str,request:Request):
        return save_personal_name(uid,'',request)

    @router.put('/api/account/profile')
    def update_profile(data:ProfileInput,request:Request):
        s=ready(request);name=profile_name(data.display_name,fail)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
            settings.setdefault('profile',{})['display_name']=name
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
        return settings['profile']
    @router.get('/api/account/avatar')
    def read(request:Request):
        s=ready(request)
        with db() as c:
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
            preset=settings.get('profile',{}).get('avatar_preset')
            if preset in PRESETS:return Response(artwork(preset),media_type='image/webp',headers={'Cache-Control':'private, no-store'})
            aid=settings.get('profile',{}).get('avatar_id')
            row=c.execute('SELECT id,mime FROM media WHERE id=? AND user_id=?',(aid,s['user_id'])).fetchone()
        if not row:fail(404,localized_message('avatarNotSet'))
        return Response(store.read(row['id']),media_type=row['mime'],headers={'Cache-Control':'private, no-store'})
    @router.get('/api/users/{uid}/avatar')
    def contact_avatar(uid:str,request:Request):
        from app.notifications import connected
        from app.note_access import connected_by_page
        s=ready(request)
        with db() as c:
            if s['role']!='owner' and not connected(c,s['user_id'],uid,now()) and not connected_by_page(c,s['user_id'],uid):fail(404,localized_message('avatarIsUnavailable'))
            row=c.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()
            preset=json.loads(row[0]).get('profile',{}).get('avatar_preset') if row else None
            if preset in PRESETS:return Response(artwork(preset),media_type='image/webp',headers={'Cache-Control':'private, no-store'})
            aid=json.loads(row[0]).get('profile',{}).get('avatar_id') if row else None
            media=c.execute('SELECT id,mime FROM media WHERE id=? AND user_id=?',(aid,uid)).fetchone()
        if not media:fail(404,localized_message('avatarNotSet'))
        return Response(store.read(media['id']),media_type=media['mime'],headers={'Cache-Control':'private, no-store'})
    @router.post('/api/account/avatar')
    async def upload(request:Request):
        s=ready(request);rate(request,'avatar-upload',20)
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>AVATAR_LIMIT:fail(413,localized_message('theAvatarMustBeNoMoreThan10'))
        async with lock:
            chunks=[];size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>AVATAR_LIMIT:fail(413,localized_message('theAvatarMustBeNoMoreThan10'))
                chunks.append(chunk)
            try:body=await asyncio.to_thread(thumbnail,b''.join(chunks))
            except (ValueError,EOFError,UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(422,localized_message('youNeedPngJpegWebpOrAGif'))
            aid=None;retired=None
            try:
                with db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
                    aid,retired=save_avatar(c,store,s['user_id'],settings,body,now(),fail)
                    c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
            except BaseException:
                if aid:store.delete(aid)
                raise
            if retired:store.delete(retired)
        return settings['profile']
    @router.delete('/api/account/avatar')
    def remove(request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
            retired=select_preset(c,s['user_id'],settings,None)
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
        if retired:store.delete(retired)
        return {'ok':True}
    return router
