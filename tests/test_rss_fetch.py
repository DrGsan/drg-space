import asyncio
import socket
from contextlib import asynccontextmanager
import httpx
import pytest
from defusedxml.common import DefusedXmlException
from app import safe_fetch as f
from app.modules import rss


def test_rss_atom_titles_dates_relative_links_and_unsafe_xml():
    body=b'<rss><channel><title>News</title><item><title>&lt;b&gt;Headline&lt;/b&gt;</title><link>/story</link><pubDate>Sun, 11 Oct 2026 10:00:00 GMT</pubDate></item><item><title>Unsafe</title><link>javascript:alert(1)</link></item><item><title>Missing link</title></item></channel></rss>'
    items=rss.parse_feed(body,'https://example.com/feed');assert len(items)==1
    assert items[0]['title']=='Headline';assert items[0]['url']=='https://example.com/story';assert items[0]['published']==1791712800
    atom=b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title><entry><title>Entry</title><link href="https://example.com/atom"/><updated>2026-10-11T10:00:00Z</updated></entry></feed>'
    assert rss.parse_feed(atom,'https://example.com/feed','Own')[0]['source']=='Own'
    with pytest.raises(DefusedXmlException):rss.parse_feed(b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss>&x;</rss>','https://example.com')
    with pytest.raises(ValueError):rss.parse_feed(b'<html/>','https://example.com')


def test_html_feed_discovery(monkeypatch):
    calls=[]
    async def fetch(url):
        calls.append(url)
        if len(calls)==1:return b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>',url,'text/html'
        return b'<rss><channel><item><title>Found</title><link>/story</link></item></channel></rss>',url,'application/rss+xml'
    monkeypatch.setattr(rss,'fetch_public',fetch)
    assert asyncio.run(rss.load_feed('https://example.com',''))[0]['title']=='Found'
    assert calls==['https://example.com','https://example.com/feed.xml']


@pytest.mark.parametrize('url',['https://iphones.ru/feed','https://www.iphones.ru/feed','https://www.iphones.ru/feed/','https://www.iphones.ru:443/feed'])
def test_iphones_feed_request_bypasses_publisher_cache_without_changing_configured_url(monkeypatch,url):
    monkeypatch.setattr(rss.time,'time',lambda:1791882000)
    requested=[]
    async def fetch(address):
        requested.append(address)
        stamp='Tue, 13 Oct 2026 07:41:19 GMT' if 'nocache=1' in address else 'Mon, 12 Oct 2026 14:30:51 GMT'
        return f'<rss><channel><item><title>Fresh news</title><link>https://www.iphones.ru/iNotes/today</link><pubDate>{stamp}</pubDate></item></channel></rss>'.encode(),address,'application/rss+xml'
    monkeypatch.setattr(rss,'fetch_public',fetch)
    item=asyncio.run(rss.load_feed(url,'iPhones'))[0]
    assert requested==['https://www.iphones.ru/feed?nocache=1&_drg_refresh=1791882000']
    assert item['published']==1791877279 and item['url']=='https://www.iphones.ru/iNotes/today'
    assert item['source']=='iPhones'
    monkeypatch.setattr(rss.time,'time',lambda:1791882600)
    assert rss.feed_request_url(url).endswith('_drg_refresh=1791882600')


def test_iphones_html_discovery_also_uses_fresh_feed(monkeypatch):
    calls=[]
    async def fetch(address):
        calls.append(address)
        if len(calls)==1:
            return b'<html><link rel="alternate" type="application/rss+xml" href="https://www.iphones.ru/feed"></html>',address,'text/html'
        assert 'nocache=1' in address
        return b'<rss><channel><item><title>Fresh</title><link>/iNotes/today</link></item></channel></rss>',address,'application/rss+xml'
    monkeypatch.setattr(rss,'fetch_public',fetch)
    assert asyncio.run(rss.load_feed('https://www.iphones.ru/',''))[0]['url']=='https://www.iphones.ru/iNotes/today'


@pytest.mark.parametrize('url',[
    'https://other.example/feed','https://iphones.ru.evil.example/feed','https://www.iphones.ru/category/news/feed',
    'https://www.iphones.ru/feed?signature=keep%2Bexact&token=abc','https://www.iphones.ru/feed?nocache=1',
])
def test_other_feeds_and_existing_queries_are_not_rewritten(url):
    assert rss.feed_request_url(url)==url


def test_feed_request_rewrite_never_normalizes_away_invalid_credentials_or_ports():
    for url in ('https://user:secret@iphones.ru/feed','https://iphones.ru:8443/feed'):
        with pytest.raises(ValueError):rss.feed_request_url(url)


@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','169.254.169.254','192.168.1.1','::1','fc00::1','224.0.0.1','ff02::1','0.0.0.0','::ffff:127.0.0.1'])
def test_private_and_special_dns_addresses_are_rejected(address):
    async def run():
        loop=asyncio.get_running_loop()
        async def resolve(*args,**kwargs):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443))]
        loop.getaddrinfo=resolve
        with pytest.raises(ValueError):await f.public_addresses('public-looking.example',443)
    asyncio.run(run())


def fake_client(monkeypatch, replies, calls):
    class Client:
        def __init__(self,**kwargs):assert kwargs['trust_env'] and not kwargs['follow_redirects']
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        @asynccontextmanager
        async def stream(self,method,url,**kwargs):
            calls.append((url,kwargs));response=replies.pop(0)
            yield response
    monkeypatch.setattr(f.httpx,'AsyncClient',Client)


def test_fetch_pins_public_dns_and_rechecks_redirect(monkeypatch):
    resolved=[];calls=[]
    async def dns(host,port):
        resolved.append(host)
        if host=='127.0.0.1':raise ValueError('private')
        return ['93.184.215.14']
    monkeypatch.setattr(f,'public_addresses',dns)
    fake_client(monkeypatch,[httpx.Response(302,headers={'location':'http://127.0.0.1/private'})],calls)
    with pytest.raises(ValueError):asyncio.run(f.fetch_public('https://example.com/feed?x=1'))
    assert resolved==['example.com','127.0.0.1']
    assert calls[0][0]=='https://93.184.215.14/feed?x=1'
    assert calls[0][1]['headers']['Host']=='example.com'
    assert calls[0][1]['extensions']['sni_hostname']=='example.com'


def test_conditional_public_resource_retains_validators_and_accepts_304(monkeypatch):
    async def dns(*args):return ['93.184.215.14']
    monkeypatch.setattr(f,'public_addresses',dns);calls=[]
    fake_client(monkeypatch,[httpx.Response(304,headers={'ETag':'"v2"','Last-Modified':'Sun, 08 Nov 2026 19:01:30 GMT'})],calls)
    resource=asyncio.run(f.fetch_public_resource('https://example.com/calendar.ics',accept='text/calendar',etag='"v1"',last_modified='Sat, 07 Nov 2026 19:01:30 GMT'))
    assert resource.status==304 and resource.body==b'' and resource.etag=='"v2"'
    headers=calls[0][1]['headers'];assert headers['If-None-Match']=='"v1"' and headers['If-Modified-Since'].startswith('Sat,') and headers['Accept']=='text/calendar'


def test_public_resource_does_not_forward_validators_across_redirects(monkeypatch):
    async def dns(*args):return ['93.184.215.14']
    monkeypatch.setattr(f,'public_addresses',dns);calls=[]
    fake_client(monkeypatch,[httpx.Response(302,headers={'location':'https://other.example/calendar.ics'}),httpx.Response(304)],calls)
    asyncio.run(f.fetch_public_resource('https://example.com/calendar.ics',etag='"private-resource-validator"'))
    assert 'If-None-Match' in calls[0][1]['headers'] and 'If-None-Match' not in calls[1][1]['headers']


class RawStream(httpx.AsyncByteStream):
    def __init__(self,body):self.body=body
    async def __aiter__(self):yield self.body


def test_bounded_response_and_no_unpinned_fallback(monkeypatch):
    async def dns(*args):return ['93.184.215.14']
    monkeypatch.setattr(f,'public_addresses',dns);calls=[]
    response=httpx.Response(200,stream=RawStream(b'x'*(f.MAX_BYTES+1)),request=httpx.Request('GET','https://93.184.215.14/feed'))
    fake_client(monkeypatch,[response],calls)
    with pytest.raises(ValueError):asyncio.run(f.fetch_public('https://example.com/feed'))
    assert len(calls)==1


def test_proxy_tls_uses_original_hostname_and_preserves_verification():
    import ssl
    calls=[]
    class Stream:
        async def start_tls(self,**kwargs):calls.append(kwargs);return self
    async def run():
        context=ssl.create_default_context()
        stream=f.HostnameStream(Stream(),'93.184.215.14','example.com')
        await stream.start_tls(context,'proxy.example',8)
        await stream.start_tls(context,'93.184.215.14',8)
    asyncio.run(run())
    assert [call['server_hostname'] for call in calls]==['proxy.example','example.com']
    assert all(call['ssl_context'].check_hostname and call['ssl_context'].verify_mode==ssl.CERT_REQUIRED for call in calls)


def test_proxy_selection_preserves_no_proxy_for_original_hostname(monkeypatch):
    monkeypatch.setattr(f,'get_environment_proxies',lambda:{'all://':'http://proxy.example:8080','all://*.example.com':None})
    async def run():
        transport=f.pinned_transport('https://feed.example.com/rss','93.184.215.14','feed.example.com')
        assert not isinstance(transport._pool,__import__('httpcore').AsyncHTTPProxy)
        await transport.aclose()
        transport=f.pinned_transport('https://other.example/rss','93.184.215.14','other.example')
        assert isinstance(transport._pool,__import__('httpcore').AsyncHTTPProxy)
        await transport.aclose()
    asyncio.run(run())


def test_compressed_bombs_are_rejected_without_decoding(monkeypatch):
    async def dns(*args):return ['93.184.215.14']
    monkeypatch.setattr(f,'public_addresses',dns);calls=[]
    response=httpx.Response(200,headers={'content-encoding':'gzip'},stream=RawStream(b'not decoded'),request=httpx.Request('GET','https://93.184.215.14/feed'))
    fake_client(monkeypatch,[response],calls)
    with pytest.raises(ValueError):asyncio.run(f.fetch_public('https://example.com/feed'))
    assert calls[0][1]['headers']['Accept-Encoding']=='identity'
