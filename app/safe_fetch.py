"""Bounded public HTTP fetch, with DNS pinning across direct and proxy requests."""
from app.i18n import message as localized_message
import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit, urljoin
import httpx
from httpx._utils import URLPattern, get_environment_proxies
from app.modules.widget_models import web_url

MAX_BYTES=2*1024*1024

class HostnameStream:
    """Keep CONNECT pinned to an IP, but verify TLS against the original host.

    httpcore 1.0.9 respects sni_hostname for direct connections, but not for
    proxy tunnels. This per-request stream adapter covers both paths without
    disabling certificate verification or changing the session proxy.
    """
    def __init__(self,stream,address,hostname):
        self.stream=stream;self.address=address;self.hostname=hostname
    async def read(self,*args,**kwargs):return await self.stream.read(*args,**kwargs)
    async def write(self,*args,**kwargs):return await self.stream.write(*args,**kwargs)
    async def aclose(self):await self.stream.aclose()
    def get_extra_info(self,*args,**kwargs):return self.stream.get_extra_info(*args,**kwargs)
    async def start_tls(self,ssl_context,server_hostname=None,timeout=None):
        if server_hostname==self.address:server_hostname=self.hostname
        stream=await self.stream.start_tls(ssl_context=ssl_context,server_hostname=server_hostname,timeout=timeout)
        return HostnameStream(stream,self.address,self.hostname)

class HostnameBackend:
    def __init__(self,backend,address,hostname):
        self.backend=backend;self.address=address;self.hostname=hostname
    async def connect_tcp(self,*args,**kwargs):
        return HostnameStream(await self.backend.connect_tcp(*args,**kwargs),self.address,self.hostname)
    async def connect_unix_socket(self,*args,**kwargs):
        return HostnameStream(await self.backend.connect_unix_socket(*args,**kwargs),self.address,self.hostname)
    async def sleep(self,*args,**kwargs):await self.backend.sleep(*args,**kwargs)

def pinned_transport(url,address,hostname):
    # Use the same proxy/NO_PROXY matching as httpx, against the original URL.
    proxies={URLPattern(key):value for key,value in get_environment_proxies().items()}
    proxy=next((proxies[key] for key in sorted(proxies) if key.matches(httpx.URL(url))),None)
    transport=httpx.AsyncHTTPTransport(proxy=proxy,trust_env=True)
    # Narrow compatibility adapter; httpx/httpcore versions are locked.
    transport._pool._network_backend=HostnameBackend(transport._pool._network_backend,address,hostname)
    return transport

async def public_addresses(host,port):
    if host.lower().rstrip('.') in ('localhost','metadata.google.internal') or host.lower().endswith(('.localhost','.local','.internal')):
        raise ValueError(localized_message('localAndServiceAddressesAreNotAllowed'))
    try:
        records=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError(localized_message('websiteAddressNotFound')) from error
    addresses=list(dict.fromkeys(record[4][0] for record in records))
    def public(address):
        ip=ipaddress.ip_address(address)
        return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)
    if not addresses or any(not public(address) for address in addresses):
        raise ValueError(localized_message('onlyPublicWebsiteAddressesAreAllowed'))
    return addresses

@dataclass(frozen=True)
class PublicResource:
    body: bytes
    url: str
    content_type: str
    status: int
    etag: str = ''
    last_modified: str = ''

async def fetch_public_resource(url,max_bytes=MAX_BYTES,accept=None,*,etag='',last_modified=''):
    """Numeric destination is pinned; Host and HTTPS SNI retain the original name.

    trust_env preserves the configured session proxy and TLS CA trust. A proxy
    sees the numeric destination too, so it cannot resolve a rebinding hostname.
    No fallback to an unpinned URL on proxy/network denial.
    """
    async with asyncio.timeout(15):
        for hop in range(4):
            web_url(url)
            parsed=urlsplit(url)
            host=parsed.hostname.encode('idna').decode('ascii')
            port=parsed.port or (443 if parsed.scheme=='https' else 80)
            addresses=await public_addresses(host,port)
            address=addresses[0]
            netloc=f'[{address}]' if ':' in address else address
            if parsed.port: netloc+=f':{port}'
            pinned=urlunsplit((parsed.scheme,netloc,parsed.path or '/',parsed.query,''))
            host_header=f'[{host}]' if ':' in host else host
            if parsed.port:host_header+=f':{port}'
            transport=pinned_transport(url,address,host)
            headers={'Host':host_header,'User-Agent':'DrG Space RSS Reader/1.0','Accept':accept or 'application/rss+xml, application/atom+xml, application/xml, text/xml, text/html','Accept-Encoding':'identity'}
            for name,value in (('If-None-Match',etag),('If-Modified-Since',last_modified)):
                if value and len(value)<=512 and not any(ord(c)<32 for c in value):headers[name]=value
            async with httpx.AsyncClient(timeout=8,transport=transport,trust_env=True,follow_redirects=False) as client, client.stream('GET',pinned,headers=headers,extensions={'sni_hostname':host}) as response:
                if response.status_code in (301,302,303,307,308):
                    if hop==3 or not response.headers.get('location'):raise ValueError(localized_message('tooManyRedirects'))
                    url=urljoin(url,response.headers['location']);etag=last_modified='';continue
                if response.status_code==304:
                    return PublicResource(b'',url,response.headers.get('content-type',''),304,response.headers.get('etag',''),response.headers.get('last-modified',''))
                response.raise_for_status()
                # Raw reads prevent a single compressed chunk from allocating
                # unbounded memory before the size limit can be checked.
                if response.headers.get('content-encoding','identity').strip().lower() not in ('','identity'):
                    raise ValueError(localized_message('theSourceMustSupportAResponseWithoutCompression'))
                if response.headers.get('content-length','').isdigit() and int(response.headers['content-length'])>max_bytes:
                    raise ValueError(localized_message('websiteResponseIsTooLarge'))
                chunks=[];size=0
                async for chunk in response.aiter_raw():
                    size+=len(chunk)
                    if size>max_bytes:raise ValueError(localized_message('websiteResponseIsTooLarge'))
                    chunks.append(chunk)
                return PublicResource(b''.join(chunks),url,response.headers.get('content-type',''),response.status_code,response.headers.get('etag',''),response.headers.get('last-modified',''))
    raise ValueError(localized_message('sourceCouldNotBeLoaded'))

async def fetch_public(url,max_bytes=MAX_BYTES,accept=None):
    resource=await fetch_public_resource(url,max_bytes,accept)
    if not 200<=resource.status<300:raise ValueError(localized_message('sourceCouldNotBeLoaded'))
    return resource.body,resource.url,resource.content_type
