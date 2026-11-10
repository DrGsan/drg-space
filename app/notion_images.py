"""Copy public exported image URLs into the same private attachment namespace."""
import asyncio
import base64
import binascii
import hashlib
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit, unquote, unquote_to_bytes

import httpx
from PIL import UnidentifiedImageError, Image
from app.modules.media import inspect_image
from app.safe_fetch import fetch_public
from app.storage import blocking

MAX_IMAGE = 20*1024**2
MAX_TOTAL = 128*1024**2
MAX_IMAGES = 1000


def embedded_image(value):
    """Decode an exported data image without network access or unbounded input."""
    if not value.startswith('data:'):
        return None
    limit = 5*1024**2
    if len(value) > limit*3+256:
        raise ValueError('Embedded image too large')
    header, separator, payload = value[5:].partition(',')
    parts = header.lower().split(';')
    if not separator or parts[0] not in ('image/png','image/jpeg','image/webp','image/gif','image/svg+xml') or parts[1:] not in ([],['base64']):
        raise ValueError('Unsupported embedded image')
    try:
        body = base64.b64decode(payload,validate=True) if parts[1:] else unquote_to_bytes(payload)
    except (ValueError,binascii.Error) as error:
        raise ValueError('Invalid embedded image') from error
    if not body or len(body)>limit:
        raise ValueError('Embedded image too large')
    return body


@dataclass
class Download:
    body: bytes
    mime: str
    name: str
    digest: str


def candidates(url,*,prefer_current=False):
    try:
        parsed = urlsplit(url);port=parsed.port
    except ValueError:
        return [url]
    result = [url]
    # The old Zen CDN now responds 410; the same immutable image path survives.
    if parsed.hostname == 'avatars.mds.yandex.net' and parsed.path.startswith('/get-zen_doc/') and not parsed.username and not parsed.password and port in (None, 80, 443):
        moved=urlunsplit(('https', 'avatars.dzeninfra.ru', parsed.path, parsed.query, ''))
        if prefer_current:result.insert(0,moved)
        else:result.append(moved)
    if parsed.hostname == 'games.mail.ru' and parsed.path.startswith('/hotbox/content_files/') and not parsed.username and not parsed.password and port in (None,80,443):
        moved=urlunsplit(('https','media.vkplay.ru',parsed.path,parsed.query,''))
        if prefer_current:result.insert(0,moved)
        else:result.append(moved)
    # Flaticon's old /icons/png CDN has moved; size and numeric asset identity
    # are unchanged. Keep the original source URL and its query in the note.
    if parsed.scheme in ('http','https') and parsed.hostname == 'image.flaticon.com' and re.fullmatch(r'/icons/png/(16|24|32|64|128|256|512)/[0-9]+/[0-9]+\.png',parsed.path) and not parsed.username and not parsed.password and port in (None,80,443):
        moved=urlunsplit(('https','cdn-icons-png.flaticon.com',parsed.path.removeprefix('/icons/png'),parsed.query,''))
        if prefer_current:result.insert(0,moved)
        else:result.append(moved)
    if parsed.scheme == 'http':result.append(urlunsplit(('https', parsed.netloc, parsed.path, parsed.query, '')))
    return list(dict.fromkeys(result))


async def download_images(urls):
    semaphore = asyncio.Semaphore(6)
    downloaded, failures = {}, set(urls)
    total = 0
    async def fetch(url):
        nonlocal total
        async with semaphore:
            for candidate in candidates(url):
                try:
                    body, _, _ = await fetch_public(candidate, max_bytes=MAX_IMAGE, accept='image/*')
                    mime, _, _, _ = await blocking(inspect_image, body)
                    if total+len(body)>MAX_TOTAL:return
                    total += len(body)
                    extension={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/gif':'.gif'}[mime]
                    name=unquote(urlsplit(url).path.rsplit('/',1)[-1])[:180] or 'image'
                    name=''.join(char for char in name if char not in '/\\\x00' and ord(char)>=32) or 'image'
                    if not name.lower().endswith(('.png','.jpg','.jpeg','.webp','.gif')):name+=extension
                    downloaded[url]=Download(body,mime,name,hashlib.sha256(body).hexdigest())
                    failures.discard(url)
                    return
                except (ValueError, OSError, httpx.HTTPError, TimeoutError, UnidentifiedImageError, Image.DecompressionBombWarning, Image.DecompressionBombError):
                    continue
    try:
        async with asyncio.timeout(120):
            await asyncio.gather(*(fetch(url) for url in sorted(failures)[:MAX_IMAGES]))
    except TimeoutError:
        pass
    return downloaded, sorted(failures)


def replace_images(value, links):
    if isinstance(value,list):return [replace_images(item,links) for item in value]
    if isinstance(value,dict):
        return {key:links.get(item,item) if key in ('url','href') and isinstance(item,str) else replace_images(item,links) for key,item in value.items()}
    return value


def document_urls(blocks, body=''):
    from app.notion_import import rewrite_markdown_links
    urls=set()
    def walk(value):
        if isinstance(value,list):
            for item in value:walk(item)
        elif isinstance(value,dict):
            for key,item in value.items():
                if key in ('url','href') and isinstance(item,str):urls.add(item)
                else:walk(item)
    walk(blocks)
    rewrite_markdown_links(body,lambda url,image:urls.add(url) or url)
    return urls


def stage_images(downloaded, owners, store, writers, existing=None):
    links, items = {}, []
    hashes = dict(existing or {})
    for url in sorted(owners):
        image=downloaded.get(url)
        if image is None:continue
        item=hashes.get(image.digest)
        if item is None:
            identity=secrets.token_hex(16);writer=store.writer(identity);writers.append(writer)
            writer.write(image.body);size,digest=writer.finish()
            if (size,digest)!=(len(image.body),image.digest):raise ValueError('Image integrity verification failed.')
            item={'id':secrets.token_urlsafe(12),'object_id':identity,'page':owners[url], 'size':size,'mime':image.mime,'name':image.name}
            hashes[image.digest]=item;items.append(item)
        links[url]='/api/notes/'+item['page'].identity+'/files/'+item['id']
    return links,items


def insert_images(c,items,uid,now):
    for item in items:
        page=item['page'];folder=c.execute('SELECT user_id,kind,mime,deleted FROM files WHERE id=?',(page.identity,)).fetchone()
        if folder and (folder['user_id']!=uid or folder['kind']!='folder' or folder['deleted'] is not None or folder['mime']!='application/x-drg-note;space=private'):
            raise ValueError('Page attachment namespace is unavailable.')
        if not folder:c.execute('INSERT INTO files(id,user_id,kind,name,mime,created,updated) VALUES(?,?,?,?,?,?,?)',(page.identity,uid,'folder','Вложения — '+page.title[:200],'application/x-drg-note;space=private',now(),now()))
        c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (item['id'],uid,page.identity,'file',item['name'],item['mime'],item['size'],item['object_id'],now(),now()))


def repair_images(db,store,uid,downloaded,now,public_url):
    """Relocate URLs in one account, preserving text, page IDs and history content."""
    import json
    from app.notion_import import rewrite_markdown_links
    writers=[]
    try:
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            pages=c.execute('SELECT * FROM notes WHERE user_id=? AND space_id IS NULL',(uid,)).fetchall()
            owners={}
            for page in pages:
                urls=document_urls(json.loads(page['blocks']) if page['blocks'] else None,page['body'])
                for url in downloaded.keys() & urls:
                    owners.setdefault(url,SimpleNamespace(identity=page['id'],title=page['title']))
            links,items=stage_images(downloaded,owners,store,writers)
            links={key:public_url.rstrip('/')+value for key,value in links.items()}
            insert_images(c,items,uid,now)
            changed=[]
            for page in pages:
                blocks=replace_images(json.loads(page['blocks']),links) if page['blocks'] else None
                body=rewrite_markdown_links(page['body'],lambda url,image:links.get(url,url))
                encoded=json.dumps(blocks,ensure_ascii=False) if blocks is not None else None
                if blocks==(json.loads(page['blocks']) if page['blocks'] else None) and body==page['body']:continue
                updated=c.execute('UPDATE notes SET blocks=?,body=?,updated=?,version=version+1 WHERE id=? AND version=?',(encoded,body,now(),page['id'],page['version']))
                if updated.rowcount!=1:raise ValueError('Page changed during image repair.')
                c.execute('DELETE FROM collaborative_docs WHERE note_id=?',(page['id'],))
                changed.append(page['id'])
            for row in c.execute('SELECT r.* FROM note_revisions r JOIN notes n ON n.id=r.note_id WHERE n.user_id=? AND n.space_id IS NULL',(uid,)).fetchall():
                blocks=replace_images(json.loads(row['blocks']),links) if row['blocks'] else None
                body=rewrite_markdown_links(row['body'],lambda url,image:links.get(url,url))
                if blocks!=(json.loads(row['blocks']) if row['blocks'] else None) or body!=row['body']:
                    c.execute('UPDATE note_revisions SET blocks=?,body=? WHERE id=?',(json.dumps(blocks,ensure_ascii=False) if blocks is not None else None,body,row['id']))
            return {'pages':changed,'files':len(items),'localized_urls':len(links)}
    except BaseException:
        for writer in writers:
            with suppress(Exception):writer.abort()
        raise
