from app.i18n import message as localized_message
import asyncio
import html
import re
import time
from collections import OrderedDict
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
import httpx
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from app.http import Router, Request
from app.safe_fetch import fetch_public
from app.modules.widget_models import owned_widget, web_url

def plain(value):
    return html.unescape(re.sub(r'<[^>]*>','',value or ''))[:500]
def local(tag):return tag.split('}')[-1]
def child(element,name):return next((item for item in element if local(item.tag)==name),None)
def text(element,name):
    value=child(element,name)
    return ''.join(value.itertext()) if value is not None else ''
def parse_feed(body,base,label=''):
    root=ET.fromstring(body,forbid_dtd=True,forbid_entities=True,forbid_external=True)
    if local(root.tag) not in ('rss','feed','RDF'):raise ValueError(localized_message('theSourceIsNotRssOrAtom'))
    channel=child(root,'channel')
    source=label or plain(text(channel if channel is not None else root,'title')) or base
    result=[]
    for node in root.iter():
        if local(node.tag) not in ('item','entry'):continue
        link=text(node,'link')
        if local(node.tag)=='entry':
            link=next((item.get('href','') for item in node if local(item.tag)=='link' and item.get('rel','alternate')=='alternate'),'')
        if not link.strip():continue
        link=urljoin(base,link.strip())
        try:web_url(link)
        except ValueError:continue
        title=plain(text(node,'title'))
        if not title:continue
        stamp=None
        raw=text(node,'pubDate') or text(node,'published') or text(node,'updated') or text(node,'date')
        if raw:
            try:stamp=int(parsedate_to_datetime(raw).timestamp())
            except (ValueError,TypeError,OverflowError):
                try:stamp=int(datetime.fromisoformat(raw.replace('Z','+00:00')).timestamp())
                except (ValueError,OverflowError):pass
        result.append({'title':title,'url':link,'source':source,'published':stamp})
        if len(result)>=100:break
    return result

class Discover(HTMLParser):
    def __init__(self):super().__init__();self.feeds=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='link' and 'alternate' in attrs.get('rel','').lower().split() and attrs.get('type','').lower() in ('application/rss+xml','application/atom+xml') and attrs.get('href'):
            self.feeds.append(attrs['href'])

def feed_request_url(url):
    """Bypass the stale publisher cache on the public iPhones RSS endpoint.

    Keep configured URLs intact and never alter other feeds or signed queries.
    The canonical host avoids a redirect that can discard the cache parameter;
    every resulting destination still goes through fetch_public's DNS checks.
    """
    web_url(url)
    parsed=urlsplit(url)
    if (parsed.scheme=='https' and parsed.hostname in ('iphones.ru','www.iphones.ru')
            and parsed.port in (None,443) and parsed.path in ('/feed','/feed/') and not parsed.query):
        return urlunsplit(('https','www.iphones.ru','/feed','nocache=1&_drg_refresh='+str(int(time.time())),''))
    return url

async def load_feed(url,label):
    body,final,content_type=await fetch_public(feed_request_url(url))
    if 'text/html' in content_type.lower() or body.lstrip()[:100].lower().startswith((b'<!doctype html',b'<html')):
        parser=Discover();parser.feed(body.decode('utf-8',errors='replace'))
        if not parser.feeds:raise ValueError(localized_message('noFeedFoundOnThePageSpecifyThe'))
        body,final,_=await fetch_public(feed_request_url(urljoin(final,parser.feeds[0])))
    return parse_feed(body,final,label)

def create_rss_router(db,ready,rate,now,fail):
    router=Router();cache=OrderedDict();lock=asyncio.Semaphore(3)
    async def source(uid,feed,refresh=False):
        key=(uid,feed['url'],feed.get('label',''))
        if not refresh and key in cache and cache[key][0]>now():
            cache.move_to_end(key);return cache[key][1]
        async with lock:
            if not refresh and key in cache and cache[key][0]>now():return cache[key][1]
            try:
                items=await load_feed(feed['url'],feed.get('label',''))
                data={'items':items,'error':None};ttl=600
            except (ValueError,ET.ParseError,DefusedXmlException,httpx.HTTPError,TimeoutError):
                previous=cache.get(key)
                data={'items':previous[1]['items'] if previous else [],'error':'Не удалось обновить источник. Проверьте адрес RSS/Atom и доступность сайта.'};ttl=60
            cache[key]=(now()+ttl,data)
            while len(cache)>128:cache.popitem(last=False)
            return data
    @router.get('/api/widgets/{widget_id}/rss')
    async def rss(widget_id:str,request:Request,refresh:bool=False):
        s=ready(request);rate(request,'rss',30,60)
        with db() as c:instance=owned_widget(c,s['user_id'],widget_id,'rss',fail)
        config=instance['config'];feeds=config.get('feeds',[])
        results=await asyncio.gather(*(source(s['user_id'],feed,refresh) for feed in feeds))
        items=[];seen=set();errors=[]
        for feed,data in zip(feeds,results):
            if data['error']:errors.append({'source':feed.get('label') or feed['url'],'message':data['error']})
            for item in data['items']:
                if item['url'] not in seen:seen.add(item['url']);items.append(item)
        items.sort(key=lambda item:item['published'] or 0,reverse=True)
        return {'items':items[:config.get('limit',10)],'errors':errors,'updated':now()}
    return router
