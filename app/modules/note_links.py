"""Transient link cards; document storage and exported content remain unchanged."""
from app.i18n import message as localized_message
import asyncio
import base64
import io
import re
import warnings
from collections import OrderedDict
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from app.http import Router, Request
from app.safe_fetch import fetch_public
from app.modules.widget_models import web_url

class LinkInput(BaseModel):
    url: str = Field(min_length=1, max_length=2000)

class Metadata(HTMLParser):
    def __init__(self):
        super().__init__(); self.meta = {}; self.title = []; self.in_title = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'title': self.in_title = True
        if tag == 'meta':
            key = (attrs.get('property') or attrs.get('name') or '').lower()
            if key not in self.meta: self.meta[key] = attrs.get('content', '')[:4000]
    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False
    def handle_data(self, data):
        if self.in_title: self.title.append(data)

def clean(value, limit):
    return re.sub(r'\s+', ' ', value).strip()[:limit]

def parse_metadata(body, final, content_type):
    if content_type.split(';')[0].strip().lower() not in ('text/html', 'application/xhtml+xml'):
        raise ValueError(localized_message('theSourceIsNotAnHtmlPage'))
    charset = re.search(r'charset=["\s]*([\w-]+)', content_type, re.I)
    if not charset: charset = re.search(br'charset=["\s]*([\w-]+)', body[:4096], re.I)
    encoding = charset[1] if charset else 'utf-8'
    if isinstance(encoding, bytes): encoding = encoding.decode('ascii')
    try: text = body.decode(encoding, errors='replace')
    except LookupError: text = body.decode('utf-8', errors='replace')
    parser = Metadata(); parser.feed(text)
    meta = parser.meta
    image = urljoin(final, meta.get('og:image') or meta.get('twitter:image') or '')
    return {'title': clean(meta.get('og:title') or meta.get('twitter:title') or ''.join(parser.title), 200),
            'description': clean(meta.get('og:description') or meta.get('twitter:description') or meta.get('description', ''), 500),
            'site': clean(meta.get('og:site_name') or urlsplit(final).hostname or '', 100)}, image

async def thumbnail(url):
    raw, _, mime = await fetch_public(url, max_bytes=4*1024*1024, accept='image/png,image/jpeg,image/webp,image/gif')
    if mime.split(';')[0].lower() not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'): return ''
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 16_000_000: return ''
            image.thumbnail((480, 320))
            output = io.BytesIO(); image.convert('RGB').save(output, format='JPEG', quality=80)
    return 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode('ascii')

def create_note_links_router(ready, rate, now):
    router = Router(); cache = OrderedDict(); lock = asyncio.Semaphore(3)
    @router.post('/api/note-link-preview')
    async def preview(data: LinkInput, request: Request):
        session = ready(request); rate(request, 'note-link-preview', 90, 60)
        key = (session['user_id'], data.url)
        async with lock:
            if key in cache and cache[key][0] > now():
                cache.move_to_end(key); return cache[key][1]
            result = {'url': data.url, 'title': '', 'description': '', 'site': '', 'image': '', 'available': False}
            try:
                web_url(data.url)
                async with asyncio.timeout(20):
                    body, final, content_type = await fetch_public(data.url, max_bytes=1024*1024, accept='text/html,application/xhtml+xml')
                    metadata, image = parse_metadata(body, final, content_type)
                    result.update(metadata); result['available'] = True
                    if image and image != final:
                        try: result['image'] = await thumbnail(image)
                        except (ValueError, OSError, httpx.HTTPError, TimeoutError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning): pass
            except (ValueError, OSError, httpx.HTTPError, TimeoutError): pass
            cache[key] = (now() + (600 if result['available'] else 60), result)
            while len(cache) > 128: cache.popitem(last=False)
            return result
    return router
