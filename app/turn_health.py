"""Bounded authenticated TURN allocation probe; cached per Gunicorn worker."""
import base64
import hashlib
import hmac
import os
import secrets
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlsplit,parse_qs

COOKIE=0x2112A442

def attribute(kind,value):return struct.pack('!HH',kind,len(value))+value+b'\0'*((-len(value))%4)
def packet(kind,attrs,key=None):
    body=b''.join(attrs);tid=secrets.token_bytes(12)
    if key:
        header=struct.pack('!HHI',kind,len(body)+24,COOKIE)+tid
        body+=attribute(8,hmac.new(key,header+body,hashlib.sha1).digest())
    return struct.pack('!HHI',kind,len(body),COOKIE)+tid+body

def probe(url,secret,stamp):
    scheme,address=url.split(':',1);parsed=urlsplit('//'+address)
    if scheme not in ('turn','turns') or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Invalid TURN address')
    tls=scheme=='turns';tcp=tls or parse_qs(parsed.query).get('transport',['udp'])[0]=='tcp'
    # This installation uses IPv4 NAT. getaddrinfo also supports IPv6-only TURN.
    addresses=socket.getaddrinfo(parsed.hostname,parsed.port or (5349 if tls else 3478),type=socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
    family,kind,proto,_,target=next((item for item in addresses if item[0]==socket.AF_INET),addresses[0])
    connection=socket.socket(family,kind,proto);connection.settimeout(1.5)
    username=str(stamp+120)+':drg-health-'+secrets.token_hex(4)
    password=base64.b64encode(hmac.new(secret.encode(),username.encode(),hashlib.sha1).digest()).decode()
    try:
        connection.connect(target)
        if tls:connection=ssl.create_default_context().wrap_socket(connection,server_hostname=parsed.hostname)
        def exchange(body,key=None):
            connection.sendall(body)
            if tcp:
                result=b''
                while len(result)<20:
                    chunk=connection.recv(20-len(result))
                    if not chunk:raise ValueError('Incomplete TURN response')
                    result+=chunk
                length=struct.unpack('!H',result[2:4])[0]
                if length>8192:raise ValueError('Oversized TURN response')
                while len(result)<20+length:
                    chunk=connection.recv(20+length-len(result))
                    if not chunk:raise ValueError('Incomplete TURN response')
                    result+=chunk
            else:result=connection.recv(8192)
            if len(result)<20 or result[4:20]!=body[4:20]:raise ValueError('Unexpected TURN response')
            typ,length=struct.unpack('!HH',result[:4]);end=20+length
            if end!=len(result):raise ValueError('Incomplete TURN response')
            offset=20;items={};integrity=False
            while offset+4<=end:
                kind,size=struct.unpack('!HH',result[offset:offset+4]);value=result[offset+4:offset+4+size]
                if len(value)!=size:raise ValueError('Invalid TURN attribute')
                items[kind]=value
                if kind==8 and key:
                    prefix=result[:2]+struct.pack('!H',offset+24-20)+result[4:offset]
                    integrity=hmac.compare_digest(value,hmac.new(key,prefix,hashlib.sha1).digest())
                offset+=4+size+((-size)%4)
            if key and not integrity:raise ValueError('Unauthenticated TURN response')
            return typ,items
        requested=attribute(0x19,b'\x11\0\0\0')
        kind,items=exchange(packet(3,[requested]))
        if kind!=0x113 or 0x14 not in items or 0x15 not in items:raise ValueError('Missing TURN auth challenge')
        realm,nonce=items[0x14],items[0x15]
        key=hashlib.md5(username.encode()+b':'+realm+b':'+password.encode()).digest()
        auth=[attribute(6,username.encode()),attribute(0x14,realm),attribute(0x15,nonce)]
        kind,items=exchange(packet(3,[requested,attribute(0x0d,struct.pack('!I',60)),*auth],key),key)
        if kind!=0x103 or 0x16 not in items:raise ValueError('TURN allocation rejected')
        # Health checks never retain relay allocations until their default TTL.
        exchange(packet(4,[attribute(0x0d,b'\0\0\0\0'),*auth],key),key)
        return True
    finally:connection.close()

_lock=threading.Lock();_cached={'key':None,'until':0,'value':False}
def turn_available():
    urls=tuple(v.strip() for v in os.getenv('CALL_TURN_URLS','').split(',') if v.strip())
    secret=os.getenv('CALL_TURN_SECRET','');key=(urls,hashlib.sha256(secret.encode()).digest())
    if not urls or len(secret)<16:return False
    with _lock:
        if _cached['key']==key and _cached['until']>time.monotonic():return _cached['value']
        available=False
        for url in urls[:4]:
            try:
                if probe(url,secret,int(time.time())):available=True;break
            except (OSError,ValueError,TypeError,struct.error):pass
        _cached.update(key=key,until=time.monotonic()+(20 if available else 10),value=available)
        return available
