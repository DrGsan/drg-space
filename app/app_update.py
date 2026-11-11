"""Public stable release hint, cached across workers; never installs updates."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import time

import httpx
from app.process_locks import try_file_lock

API='https://api.github.com/repos/DrGsan/drg-space/releases/latest'
RELEASES='https://github.com/DrGsan/drg-space/releases/tag/'
SUCCESS_TTL=6*3600
FAILURE_TTL=15*60
VERSION=re.compile(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?')

def version(value):
    match=VERSION.fullmatch(value) if isinstance(value,str) and len(value)<=120 else None
    if not match:return None
    if match[4] and any(p.isdigit() and len(p)>1 and p.startswith('0') for p in match[4].split('.')):return None
    return tuple(int(match[i]) for i in (1,2,3)),bool(match[4])

def release(value,stamp):
    if not isinstance(value,dict) or value.get('draft') is not False or value.get('prerelease') is not False:return None
    tag=value.get('tag_name');parsed=version(tag)
    if not parsed or parsed[1] or not re.fullmatch(r'v?\d+\.\d+\.\d+',tag):return None
    if value.get('html_url')!=RELEASES+tag:return None
    try:published=datetime.fromisoformat(value['published_at'].replace('Z','+00:00'))
    except (KeyError,AttributeError,ValueError,TypeError):return None
    if published.tzinfo is None or published.timestamp()>stamp:return None
    return {k:value[k] for k in ('tag_name','html_url','published_at','draft','prerelease')}

def fetch_latest():
    with httpx.Client(timeout=httpx.Timeout(3,connect=1.5),follow_redirects=False,trust_env=False) as client:
        with client.stream('GET',API,headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'DrG-Space-update-check'}) as response:
            response.raise_for_status();body=bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body)>256*1024:raise ValueError('Release response too large')
    return json.loads(body)

def check(root,installed,*,stamp=None):
    """Unknown, stale, rate-limited or invalid metadata produces no badge."""
    stamp=int(time.time()) if stamp is None else stamp
    path=Path(root)/'.github-release-cache.json'
    unknown={'available':False}
    def read():
        try:
            with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as source:body=source.read(8193)
            value=json.loads(body) if len(body)<=8192 else None
            if not isinstance(value,dict) or type(value.get('checked')) is not int or type(value.get('until')) is not int:return None
            if not value['checked']<=stamp<value['until']<=value['checked']+SUCCESS_TTL:return None
            return value
        except (OSError,ValueError,TypeError):return None
    def write(value):
        temporary=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as output:json.dump(value,output)
            os.replace(temporary,path)
        finally:temporary.unlink(missing_ok=True)
    try:
        value=read()
        if value is None:
            lease=try_file_lock(root,'github-release-check.lock')
            if lease is None:return unknown
            with lease:
                value=read()
                if value is None:
                    # Persist the retry fence before I/O, also covering worker crashes.
                    value={'checked':stamp,'until':stamp+FAILURE_TTL,'release':None};write(value)
                    try:latest=release(fetch_latest(),stamp)
                    except (httpx.HTTPError,OSError,ValueError,TypeError):latest=None
                    if latest:
                        value={'checked':stamp,'until':stamp+SUCCESS_TTL,'release':latest};write(value)
        latest=release(value.get('release'),stamp);current=version(installed)
        if not latest or not current:return unknown
        candidate=version(latest['tag_name'])[0]
        if candidate<current[0] or candidate==current[0] and not current[1]:return unknown
        return {'available':True,'version':latest['tag_name'].removeprefix('v'),'url':latest['html_url']}
    except (OSError,ValueError,TypeError):return unknown
