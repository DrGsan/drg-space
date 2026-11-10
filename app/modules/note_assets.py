"""Scoped page images and licensed cover search; gallery switches do not apply."""
from app.i18n import message as localized_message
import asyncio
from app.storage import blocking
import html
import io
import json
import os
import re
import secrets
from collections import OrderedDict
from urllib.parse import urlencode,urlsplit
from PIL import Image,ImageOps,UnidentifiedImageError
import httpx
from app.http import Router,Request,Query
from app.http import Response
from pydantic import BaseModel,Field,ConfigDict
from app.data_crypto import media_store
from app.modules.media import inspect_image
from app.modules.social import note_access
from app.modules.notion import require_role,page_role
from app.safe_fetch import fetch_public

IMAGE_LIMIT=20*1024*1024
COMMONS_HOSTS=('upload.wikimedia.org','thumb.wikimedia.org')
IMAGE_MIMES=('image/jpeg','image/png','image/webp','image/gif')
ASSET=re.compile(r'^image:([a-f0-9]{32})$')

def asset_id(value):
    match=ASSET.fullmatch(value or '')
    return match[1] if match else None

def thumbnail(body,kind):
    inspect_image(body)
    with Image.open(io.BytesIO(body)) as source:
        source.seek(0);image=ImageOps.exif_transpose(source).convert('RGBA')
        image=ImageOps.fit(image,(96,96) if kind=='icon' else (1600,600),method=Image.Resampling.LANCZOS)
        stream=io.BytesIO();image.save(stream,format='WEBP',quality=85)
        return stream.getvalue(),image.width,image.height

def asset_payload(c,n,url_prefix=None):
    output={}
    for kind in ('icon','cover'):
        aid=asset_id(n[kind])
        if not aid:continue
        cache=getattr(c,'note_catalog_assets',None)
        row=cache.get((aid,n['id'],kind)) if cache is not None else c.execute('SELECT payload FROM note_assets WHERE id=? AND note_id=? AND kind=?',(aid,n['id'],kind)).fetchone()
        if not row:continue
        output[kind+'_url']=(url_prefix or '/api/notes/'+n['id']+'/assets')+'/'+aid
        info=json.loads(row['payload']);output[kind+'_attribution']=info.get('attribution');output[kind+'_position']=info.get('position',50)
    return output

def image_reference(c,nid,value,kind,fail):
    aid=asset_id(value)
    if aid and not c.execute('SELECT 1 FROM note_assets WHERE id=? AND note_id=? AND kind=?',(aid,nid,kind)).fetchone():fail(422,localized_message('theImageDoesNotBelongToThisPage'))
    return aid

def clear_markup(value):return html.unescape(re.sub(r'<[^>]*>','',str(value or '')))[:400]

def commons_cover_url(info):
    """Prefer a bounded rendition; never fall back to an oversized original."""
    width,height=info.get('width',0),info.get('height',0)
    if width<=0 or height<=0 or width<height*1.3:return None
    if info.get('thumburl'):
        url=info['thumburl'];width=info.get('thumbwidth',0);height=info.get('thumbheight',0)
        mime=info.get('thumbmime') or info.get('mime')
    else:
        if not 0<info.get('size',0)<=IMAGE_LIMIT:return None
        url=info.get('url','');mime=info.get('mime')
    if width<=0 or height<=0 or width*height>20000000 or mime not in IMAGE_MIMES:return None
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in COMMONS_HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):return None
    return url

def validate_attribution(info,fail):
    if not isinstance(info,dict) or set(info)-{'attribution','position'}:fail()
    if 'position' in info and (type(info['position']) is not int or not 0<=info['position']<=100):fail()
    a=info.get('attribution')
    if a is not None:
        if not isinstance(a,dict) or set(a)!={'author','license','source_url'} or any(not isinstance(v,str) or len(v)>2048 for v in a.values()):fail()
        url=urlsplit(a['source_url'])
        if url.scheme!='https' or not url.hostname or url.username or url.password:fail()

def purge_page_assets(c,nids,stamp):
    for nid in nids:
        ids=[r[0] for r in c.execute('SELECT id FROM note_assets WHERE note_id=?',(nid,))]
        for aid in ids:
            owner=c.execute('SELECT u.settings FROM users u JOIN media m ON m.user_id=u.id WHERE m.id=?',(aid,)).fetchone()
            settings=json.loads(owner[0]) if owner else {}
            used=settings.get('profile',{}).get('avatar_id')==aid or any(item.get('asset_id')==aid for widget in settings.get('dashboard',{}).get('instances',[]) if widget.get('module_id')=='media' for item in widget.get('config',{}).get('items',[]))
            if used:continue
            c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(aid,'media',stamp))
            c.execute('DELETE FROM media WHERE id=?',(aid,))

class CoverSelection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token:str=Field(max_length=8192)
class Position(BaseModel):
    position:int=Field(ge=0,le=100)


def cover_grant(tokens, token, uid, fail):
    grant=tokens.decode(token,'note-cover')
    if grant.get('owner')!=uid:fail(403,localized_message('theSearchResultBelongsToADifferentAccount'))
    url=grant.get('url','');host=urlsplit(url).hostname
    hosts=COMMONS_HOSTS if grant.get('provider')=='commons' else ('images.unsplash.com',) if grant.get('provider')=='unsplash' else ()
    if host not in hosts or urlsplit(url).scheme!='https':fail(422,localized_message('invalidImageAddress'))
    return grant

async def selected_cover(grant):
    body,_,_=await fetch_public(grant['url'],max_bytes=IMAGE_LIMIT,accept='image/*')
    return body

async def track_cover(grant):
    location=grant.get('download_location','')
    if grant.get('provider')=='unsplash' and urlsplit(location).hostname=='api.unsplash.com':
        try:
            async with httpx.AsyncClient(timeout=10) as client:await client.get(location,headers={'Authorization':'Client-ID '+os.getenv('UNSPLASH_ACCESS_KEY','').strip()})
        except httpx.HTTPError:pass


def create_note_assets_router(db,ready,tokens,rate,now,fail,data_dir):
    r=Router();store=media_store(db,data_dir);lock=asyncio.Semaphore(2);cache=OrderedDict()
    def access(c,nid,s,write=False):
        n=note_access(c,nid,s['user_id'],fail)
        if write:require_role(c,n,s['user_id'],fail,session=s,now=now)
        return n
    def persist(nid,s,kind,body,info=None,request=None):
        data,width,height=thumbnail(body,kind);aid=secrets.token_hex(16)
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                if request is not None:s=ready(request)
                n=access(c,nid,s,True)
                store.write(aid,data)
                c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)',(aid,s['user_id'],'image/webp',len(data),width,height,1,now()))
                c.execute('INSERT INTO note_assets VALUES(?,?,?,?,?)',(aid,nid,kind,json.dumps(info or {}),now()))
                c.execute('UPDATE notes SET '+kind+'=?,updated=?,version=version+1 WHERE id=?',('image:'+aid,now(),nid))
                n=c.execute('SELECT * FROM notes WHERE id=?',(nid,)).fetchone()
                result={'icon':n['icon'],'cover':n['cover'],'version':n['version'],**asset_payload(c,n)}
        except BaseException:store.delete(aid);raise
        return result
    @r.get('/api/notes/{nid}/assets/{aid}')
    def read(nid:str,aid:str,request:Request):
        s=ready(request)
        with db() as c:
            n=note_access(c,nid,s['user_id'],fail,include_deleted=request.args.get('trash')=='true')
            if n['deleted'] and page_role(c,n,s['user_id'])!='editor':fail(403,localized_message('insufficientPermissionsToViewTheTrash'))
            row=c.execute('SELECT m.id,m.mime FROM media m JOIN note_assets a ON a.id=m.id WHERE a.id=? AND a.note_id=?',(aid,nid)).fetchone()
        if not row:fail(404,localized_message('thePageImageWasNotFound'))
        return Response(store.read(row['id']),media_type=row['mime'])
    @r.post('/api/notes/{nid}/assets/{kind}')
    async def upload(nid:str,kind:str,request:Request):
        s=ready(request);rate(request,'note-image',30,subject=s['user_id'])
        if kind not in ('icon','cover'):fail(422,localized_message('selectAnIconOrCoverImage'))
        with db() as c:access(c,nid,s,True)
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>IMAGE_LIMIT:fail(413,localized_message('theImageIsNoMoreThan20Mb'))
        async with lock:
            parts=[];size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>IMAGE_LIMIT:fail(413,localized_message('theImageIsNoMoreThan20Mb'))
                parts.append(chunk)
            try:return await blocking(persist,nid,s,kind,b''.join(parts),None,request)
            except (ValueError,UnidentifiedImageError,OSError,EOFError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(422,localized_message('youNeedPngJpegWebpOrAGif'))
    @r.patch('/api/notes/{nid}/cover-position')
    def position(nid:str,data:Position,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=access(c,nid,s,True);aid=asset_id(n['cover'])
            if not aid:fail(404,localized_message('thePageDoesNotHaveACoverImage'))
            row=c.execute('SELECT payload FROM note_assets WHERE id=? AND note_id=?',(aid,nid)).fetchone()
            info=json.loads(row[0]);info['position']=data.position
            c.execute('UPDATE note_assets SET payload=? WHERE id=?',(json.dumps(info),aid))
        return {'position':data.position}
    @r.get('/api/note-cover-search')
    async def search(request:Request,q:str=Query(default='',max_length=120),provider:str='commons'):
        s=ready(request);rate(request,'cover-search',60,subject=s['user_id'])
        if provider not in ('commons','unsplash'):fail(422,localized_message('unknownPhotoCatalog'))
        secret=os.getenv('UNSPLASH_ACCESS_KEY','').strip()
        if provider=='unsplash' and not secret:fail(503,localized_message('unsplashIsNotConfiguredTheAdministratorNeedsThe'))
        query=q.strip() or 'landscape';key=(provider,query)
        cached=cache.get(key)
        if cached and cached[0]>now():items=cached[1]
        else:
            items=[]
            try:
                if provider=='commons':
                    params={'action':'query','generator':'search','gsrsearch':query+' filetype:bitmap','gsrnamespace':6,'gsrlimit':40,'prop':'imageinfo','iiprop':'url|size|mime|thumbmime|extmetadata','iiurlwidth':1600,'iiurlheight':1200,'format':'json'}
                    body,_,_=await fetch_public('https://commons.wikimedia.org/w/api.php?'+urlencode(params),accept='application/json')
                    pages=json.loads(body).get('query',{}).get('pages',{})
                    for page in pages.values():
                        info=(page.get('imageinfo') or [{}])[0]
                        url=commons_cover_url(info)
                        if not url:continue
                        meta=info.get('extmetadata',{});get=lambda key:clear_markup(meta.get(key,{}).get('value',''))
                        items.append({'id':str(page['pageid']),'url':url,'thumbnail':url,'title':page.get('title',''),'attribution':{'author':get('Artist'),'license':get('LicenseShortName'),'source_url':info.get('descriptionurl','https://commons.wikimedia.org/')}})
                        if len(items)>=18:break
                else:
                    async with httpx.AsyncClient(timeout=15,follow_redirects=False) as client:
                        response=await client.get('https://api.unsplash.com/search/photos',params={'query':query,'orientation':'landscape','per_page':18},headers={'Authorization':'Client-ID '+secret});response.raise_for_status()
                        for photo in response.json().get('results',[]):
                            items.append({'id':photo['id'],'url':photo['urls']['regular'],'thumbnail':photo['urls']['small'],'title':photo.get('alt_description') or '', 'download_location':photo['links']['download_location'],'attribution':{'author':photo['user']['name'],'license':'Unsplash License','source_url':photo['links']['html']+'?utm_source=drg_site&utm_medium=referral'}})
            except httpx.HTTPStatusError as error:
                if provider=='unsplash' and error.response.status_code in (401,403):
                    fail(503,localized_message('unsplashRejectedTheApiKeyOrApplicationAccess'))
                if error.response.status_code==429:fail(503,localized_message('thePhotoCatalogRequestLimitHasBeenExceeded'))
                fail(503,localized_message('thePhotoCatalogIsTemporarilyUnavailableTryAgain'))
            except (httpx.HTTPError,ValueError,KeyError,TypeError,TimeoutError):fail(503,localized_message('thePhotoCatalogIsTemporarilyUnavailableTryAgain'))
            cache[key]=(now()+600,items)
            while len(cache)>100:cache.popitem(last=False)
        results=[]
        for item in items:
            token=tokens.encode('note-cover',item['id'],now()+1800,owner=s['user_id'],provider=provider,url=item['url'],attribution=item['attribution'],download_location=item.get('download_location'))
            results.append({k:v for k,v in item.items() if k!='download_location'}|{'selection_token':token})
        return {'items':results,'providers':['commons']+(['unsplash'] if secret else [])}
    @r.post('/api/notes/{nid}/select-cover')
    async def select(nid:str,data:CoverSelection,request:Request):
        s=ready(request);rate(request,'note-image',30,subject=s['user_id']);grant=cover_grant(tokens,data.token,s['user_id'],fail)
        with db() as c:access(c,nid,s,True)
        async with lock:
            try:
                body=await selected_cover(grant)
                saved=await blocking(persist,nid,s,'cover',body,{'attribution':grant['attribution'],'position':50},request)
                await track_cover(grant)
                return saved
            except (httpx.HTTPError,TimeoutError,ValueError,UnidentifiedImageError,OSError,EOFError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(422,localized_message('thisPhotoIsUnavailableOrExceedsTheMaximum'))
    r.store=store
    return r


def purge_unused_assets(c, stamp):
    """Retire replaced decorations after a grace day, preserving document links."""
    candidates={r[0] for r in c.execute('SELECT id FROM note_assets WHERE created<=?',(stamp-86400,))}
    if not candidates:return
    referenced=set()
    for row in c.execute('SELECT icon,cover,body,blocks FROM notes'):
        referenced.update(re.findall(r'[a-f0-9]{32}', ''.join(value or '' for value in row)))
    for row in c.execute('SELECT body,blocks FROM note_revisions'):
        referenced.update(re.findall(r'[a-f0-9]{32}', ''.join(value or '' for value in row)))
    for row in c.execute('SELECT settings FROM users'):
        referenced.update(re.findall(r'[a-f0-9]{32}', row[0]))
    from app.notion_sync.decorations import retained
    referenced.update(retained(c,candidates-referenced))
    for aid in candidates-referenced:
        c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(aid,'media',stamp))
        c.execute('DELETE FROM media WHERE id=?',(aid,))
