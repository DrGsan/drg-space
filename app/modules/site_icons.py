"""Public-origin favicons, discovered without opening bookmark paths or tokens."""
from app.i18n import message as localized_message
import asyncio
import io
from collections import OrderedDict
from html.parser import HTMLParser
from urllib.parse import urlsplit,urljoin
from PIL import Image,ImageOps
from app.http import Router,Request
from app.http import Response
from app.safe_fetch import fetch_public
from app.modules.widget_models import owned_widget

class Icons(HTMLParser):
    def __init__(self):super().__init__();self.icons=[]
    def handle_starttag(self,tag,attrs):
        data=dict(attrs)
        if tag=='link' and any(x in data.get('rel','').lower().split() for x in ('icon','apple-touch-icon')) and data.get('href') and len(self.icons)<10:self.icons.append(data['href'])

def png(body):
    with Image.open(io.BytesIO(body)) as image:
        if image.format not in ('ICO','PNG','JPEG','WEBP','GIF') or image.width*image.height>1000000:raise ValueError('Unsupported favicon.')
        image.seek(0);image.load();image=ImageOps.fit(image.convert('RGBA'),(64,64));output=io.BytesIO();image.save(output,format='PNG');return output.getvalue()

def create_site_icons_router(db,ready,rate,now,fail):
    r=Router();cache=OrderedDict();lock=asyncio.Semaphore(3)
    @r.get('/api/widgets/{wid}/site-icon/{index}')
    async def icon(wid:str,index:int,request:Request):
        s=ready(request);rate(request,'site-icons',120,subject=s['user_id'])
        with db() as c:instance=owned_widget(c,s['user_id'],wid,'bookmarks',fail)
        links=instance['config'].get('links',[])
        if not 0<=index<len(links):fail(404,localized_message('labelNotFound'))
        target=urlsplit(links[index]['url']);origin=target.scheme+'://'+target.netloc+'/'
        if origin in cache and cache[origin][0]>now():
            data=cache[origin][1]
            if data:return Response(data,media_type='image/png')
            fail(404,localized_message('websiteIconIsUnavailable'))
        data=None
        async with lock:
            urls=[]
            try:
                body,final,kind=await fetch_public(origin)
                if 'html' in kind:
                    parser=Icons();parser.feed(body.decode('utf-8',errors='replace'));urls=[urljoin(final,x) for x in parser.icons[:3]]
            except Exception:pass
            urls.append(urljoin(origin,'favicon.ico'))
            for url in urls:
                try:body,_,_=await fetch_public(url);data=await asyncio.to_thread(png,body);break
                except Exception:continue
            cache[origin]=(now()+(86400 if data else 600),data)
            while len(cache)>256:cache.popitem(last=False)
        if not data:fail(404,localized_message('websiteIconIsUnavailable'))
        return Response(data,media_type='image/png')
    return r
