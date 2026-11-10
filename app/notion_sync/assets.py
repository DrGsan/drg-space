"""Prepare immutable private objects before publishing a complete page revision."""
import asyncio
import hashlib
import mimetypes
import secrets
import httpx
import time
from datetime import datetime,timezone
from contextlib import suppress
from urllib.parse import unquote,urlsplit,urlunsplit,parse_qsl,urlencode

from app.notion_sync.blocks import file_url,url as safe_url
from app.notion_sync.client import NotionError,digest
from app.notion_images import replace_images,candidates
from app.safe_fetch import fetch_public

MAX_FILE=50*1024**2
MAX_PAGE_FILES=200*1024**2


def decoration_url(url,kind):
    """Use Unsplash's bounded rendition, keeping provider tracking parameters.

    The imported decoration is a thumbnail, not the multi-megapixel original.
    Signed uploads and unrelated hosts must retain their exact query strings.
    """
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='images.unsplash.com' or parsed.port not in (None,443):return url
    size=(96,96) if kind=='icon' else (1600,600)
    controls={'w','h','dpr','fit','fm','q','auto'}
    query=[(key,value) for key,value in parse_qsl(parsed.query,keep_blank_values=True) if key not in controls]
    query.extend([('w',str(size[0])),('h',str(size[1])),('fit','crop'),('fm','jpg'),('q','90'),('dpr','1')])
    return urlunsplit(parsed._replace(query=urlencode(query)))


def identity(source):
    kind=(source or {}).get('type')
    if kind=='custom_emoji':
        emoji=source.get('custom_emoji') or {}
        return digest([kind,emoji.get('id'),safe_url(emoji.get('url',''))])
    value=file_url(source)
    # Uploaded Notion URLs rotate their signature; external query parameters
    # may identify different content and are therefore retained.
    return digest([kind,value.split('?',1)[0] if kind=='file' else value])


def check_expiry(source,stamp):
    expires=(source.get('file') or {}).get('expiry_time') if source.get('type')=='file' else None
    try:
        moment=datetime.fromisoformat(expires) if expires else None
        expiry=(moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp() if moment else None
    except (ValueError,TypeError,OverflowError):expiry=None
    if expiry is not None and expiry<=stamp:
        from app.notion_sync.asset_errors import AssetError
        raise AssetError('notionAssetLinkExpired',delay=0)


async def download(url,limit):
    # Reuse the same reviewed CDN relocations as ZIP import. Exact host/path
    # matching never rewrites unrelated hosts or signed Notion uploads.
    from app.notion_sync.asset_errors import classify
    first_error=None
    for candidate in candidates(url,prefer_current=True):
        try:
            body,_,_=await fetch_public(candidate,max_bytes=limit,accept='*/*')
            return body
        except (ValueError,OSError,httpx.HTTPError,TimeoutError) as error:
            if first_error is None:
                first_error=classify(error);first_error.params['host']=(urlsplit(candidate).hostname or '')[:253]
    # Never expose a signed URL in a provider/network exception.
    raise first_error from None


class PreparedAssets:
    def __init__(self,store,media,public_url,note_id,*,previous=None,fetch=None,retry_cover=True,retry_icon=True,checkpoint=None,valid_decorations=None,clock=time.time):
        self.store,self.media,self.public_url=store,media,public_url.rstrip('/')
        self.note_id=note_id;self.previous=previous or {};self.fetch=fetch or (lambda url,limit:asyncio.run(download(url,limit)))
        self.writers=[];self.images=[];self.files=[];self.decorations=[];self.manifest={};self.total=0
        self.retry_cover=retry_cover;self.retry_icon=retry_icon;self.cover_pending=False;self.icon_pending=False
        self.checkpoint=checkpoint;self.has_checkpoint=False
        self.valid_decorations=valid_decorations or {}
        self.clock=clock

    def bytes(self,url,limit):
        body=self.fetch(url,limit)
        if not isinstance(body,bytes) or len(body)>limit or self.total+len(body)>MAX_PAGE_FILES:raise NotionError('notionPageTooLarge',422)
        self.total+=len(body)
        return body

    def prepare(self,blocks,source_blocks,raw):
        sources={}
        def walk(items):
            for block in items:
                kind=block.get('type')
                if kind in ('image','video','audio','file','pdf'):sources[block['id']]=block.get(kind) or {}
                walk(block.get('children',[]))
        walk(source_blocks)
        links={}
        for bid,source in sources.items():
            key='block:'+bid;fingerprint=identity(source);old=self.previous.get(key)
            url=file_url(source)
            if not url:raise NotionError('notionAssetUnavailable',502,30)
            if old and old.get('fingerprint')==fingerprint and self.store.exists(old['object_id']):
                item=old
                self.total+=item['size']
                if self.total>MAX_PAGE_FILES:raise NotionError('notionPageTooLarge',422)
            else:
                name=source.get('name') or unquote(urlsplit(url).path.rsplit('/',1)[-1]) or 'Notion file'
                name=''.join(char for char in name if ord(char)>=32 and char not in '/\\')[:200] or 'Notion file'
                try:
                    check_expiry(source,self.clock())
                    body=self.bytes(url,MAX_FILE)
                except NotionError as error:
                    error.params.update(file=name,block_id=bid,fingerprint=fingerprint)
                    error.params.setdefault('host',(urlsplit(url).hostname or '')[:253])
                    raise
                mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
                if body.startswith(b'\x89PNG'):mime='image/png'
                elif body.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
                elif body.startswith((b'GIF87a',b'GIF89a')):mime='image/gif'
                elif body.startswith(b'RIFF') and body[8:12]==b'WEBP':mime='image/webp'
                oid=secrets.token_hex(16);writer=self.store.writer(oid);self.writers.append(writer)
                writer.write(body);size,checksum=writer.finish()
                if checksum!=hashlib.sha256(body).hexdigest() or size!=len(body):raise NotionError('notionAssetUnavailable')
                item={'fingerprint':fingerprint,'id':secrets.token_urlsafe(12),'object_id':oid,'name':name,'mime':mime,'size':size}
                self.files.append(item)
            self.manifest[key]=item
            if self.files and self.checkpoint:self.checkpoint(self)
            links[url]=self.public_url+'/api/notes/'+self.note_id+'/files/'+item['id']
        result=replace_images(blocks,links)
        values={'icon':'📄','cover':''}
        for kind in ('icon','cover'):
            source=raw.get(kind) or {}
            if kind=='icon' and source.get('type')=='emoji':values[kind]=source.get('emoji','📄');continue
            url=safe_url((source.get('custom_emoji') or {}).get('url','')) if kind=='icon' and source.get('type')=='custom_emoji' else file_url(source)
            if not url:continue
            key='decoration:'+kind;fingerprint=identity(source);old=self.previous.get(key)
            # An obsolete manifest can outlive its SQL rows or physical object.
            # Canonical ownership precedes existence; foreign assets must never
            # become a last-good fallback, even when the provider is unavailable.
            if old:
                if self.valid_decorations.get(kind)!=old.get('id'):old=None
                else:
                    from app.data_crypto import StorageError
                    try:
                        if not self.media.exists(old['id']):old=None
                    except StorageError:
                        # A storage outage is not confirmed absence. Preserve
                        # the current revision for a bounded queued retry.
                        raise NotionError('notionAssetUnavailable',502,30) from None
            if old and old.get('fingerprint')==fingerprint:
                item=old
            else:
                from app.modules.note_assets import thumbnail
                try:
                    if not getattr(self,'retry_'+kind):raise NotionError('notionAssetUnavailable',502)
                    check_expiry(source,self.clock())
                    body=self.bytes(decoration_url(url,kind),20*1024**2)
                    if kind=='icon':
                        from app.notion_icons import raster_icon
                        body=raster_icon(body)
                    body,width,height=thumbnail(body,kind)
                except (NotionError,ValueError,OSError) as error:
                    # Keep the source in the page snapshot and the last good
                    # decoration, while publishing the actual page content.
                    setattr(self,kind+'_pending',True)
                    if old:
                        self.manifest[key]=old;values[kind]='image:'+old['id']
                    continue
                aid=secrets.token_hex(16);self.images.append(aid);self.media.write(aid,body)
                item={'fingerprint':fingerprint,'id':aid,'kind':kind,'size':len(body),'width':width,'height':height}
                self.decorations.append(item)
            self.manifest[key]=item;values[kind]='image:'+item['id']
        return result,values

    def insert(self,c,uid,title,stamp):
        if self.files:
            folder=c.execute('SELECT kind,user_id,deleted FROM files WHERE id=?',(self.note_id,)).fetchone()
            if folder and (folder['kind']!='folder' or folder['user_id']!=uid or folder['deleted'] is not None):raise NotionError('notionAttachmentUnavailable',409)
            if not folder:c.execute('INSERT INTO files(id,user_id,kind,name,mime,created,updated) VALUES(?,?,?,?,?,?,?)',(self.note_id,uid,'folder','Notion — '+title[:200],'application/x-drg-note;space=private',stamp,stamp))
            for item in self.files:
                c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (item['id'],uid,self.note_id,'file',item['name'],item['mime'],item['size'],item['object_id'],stamp,stamp))
        for item in self.decorations:
            c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)',(item['id'],uid,'image/webp',item['size'],item['width'],item['height'],1,stamp))
            c.execute('INSERT INTO note_assets VALUES(?,?,?,?,?)',(item['id'],self.note_id,item['kind'],'{}',stamp))

    def abort(self):
        for writer in self.writers:
            with suppress(Exception):writer.abort()
        for aid in self.images:
            with suppress(Exception):self.media.delete(aid)
