"""Fixed-origin REST client. Content writes require an explicit write client.

A search/query uses POST without changing content, so gate paths, not just verbs.
No provider response body or credential is included in exceptions/logs.
"""
import hashlib
import json
import re
import uuid
import copy
import httpx

API_VERSION='2026-03-11'
API_ORIGIN='https://api.notion.com'
class NotionError(Exception):
    def __init__(self,code='notionUnavailable',status=502,delay=0,**params):
        self.code,self.status,self.delay,self.params=code,status,delay,params
        super().__init__(code)

def page_id(value):
    value=str(value).strip().split('?',1)[0].split('#',1)[0].rstrip('/').rsplit('/',1)[-1]
    match=re.search(r'([0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$',value)
    if not match:raise ValueError('notionInvalidPage')
    return str(uuid.UUID(match[1]))

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class NotionClient:
    def __init__(self,token,*,write=False,transport=None,before_request=None):
        self.write=write
        self.before_request=before_request or (lambda mutation:None)
        self.http=httpx.Client(base_url=API_ORIGIN,headers={'Authorization':'Bearer '+token,'Notion-Version':API_VERSION,'Content-Type':'application/json'},timeout=httpx.Timeout(30,connect=10),follow_redirects=False,transport=transport)
    def close(self):self.http.close()
    def request(self,method,path,data=None):
        read=(method=='GET' and re.fullmatch(r'/v1/(pages/[0-9a-f-]+(?:/markdown|/properties/[A-Za-z0-9%_:.-]+)?|blocks/[0-9a-f-]+(?:/children)?|databases/[0-9a-f-]+|data_sources/[0-9a-f-]+|views(?:/[0-9a-f-]+)?|users/me)',path.split('?',1)[0])) or (method=='POST' and (path=='/v1/search' or re.fullmatch(r'/v1/data_sources/[0-9a-f-]+/query',path)))
        writable=(method=='PATCH' and re.fullmatch(r'/v1/(blocks/[0-9a-f-]+(?:/children)?|pages/[0-9a-f-]+(?:/markdown)?)',path)) or (method=='POST' and path in ('/v1/pages','/v1/file_uploads')) or (method=='POST' and re.fullmatch(r'/v1/file_uploads/[0-9a-f-]+/(send|complete)',path))
        if not read and not(self.write and writable):raise NotionError('notionWriteForbidden',403)
        self.before_request(not bool(read))
        try:response=self.http.request(method,path,json=data) if data is not None else self.http.request(method,path)
        except httpx.HTTPError:raise NotionError('notionWriteUncertain' if not read else 'notionUnavailable',503,30) from None
        if response.status_code in (429,529):
            try:delay=max(1,min(86400,int(response.headers.get('Retry-After','60'))))
            except ValueError:delay=60
            raise NotionError('notionRateLimited',response.status_code,delay)
        if response.status_code in (401,403,404):raise NotionError({401:'notionAuthorizationExpired',403:'notionAccessDenied',404:'notionSourceUnavailable'}[response.status_code],response.status_code)
        if response.status_code>=500:raise NotionError('notionWriteUncertain' if not read else 'notionUnavailable',response.status_code,30)
        if not response.is_success:raise NotionError('notionInvalidResponse',response.status_code)
        try:value=response.json()
        except ValueError:raise NotionError('notionInvalidResponse') from None
        if not isinstance(value,dict):raise NotionError('notionInvalidResponse')
        return value
    def all(self,path,method='GET',data=None,maximum=2000):
        result=[];cursor=None;seen=set()
        while True:
            body={**(data or {}),'page_size':100}
            if cursor:body['start_cursor']=cursor
            if method=='GET':
                from urllib.parse import urlencode
                response=self.request(method,path+'?'+urlencode(body))
            else:response=self.request(method,path,body)
            items=response.get('results')
            if not isinstance(items,list):raise NotionError('notionInvalidResponse')
            result.extend(items)
            if len(result)>maximum:raise NotionError('notionPageTooLarge',422)
            if not response.get('has_more'):return result
            cursor=response.get('next_cursor')
            if not cursor or cursor in seen:raise NotionError('notionInvalidResponse')
            seen.add(cursor)
    def page(self,identity):return self.request('GET','/v1/pages/'+page_id(identity))
    def children(self,identity):return self.all('/v1/blocks/'+page_id(identity)+'/children')
    def tree(self,identity):
        count=0;cache={};unavailable=set()
        def walk(bid,depth=0,trail=()):
            nonlocal count
            if depth>30:raise NotionError('notionPageTooLarge',422)
            bid=page_id(bid)
            if bid in trail:raise NotionError('notionInvalidResponse',422)
            if bid in unavailable:raise NotionError('notionSourceUnavailable',404)
            if bid not in cache:
                try:cache[bid]=self.children(bid)
                except NotionError as error:
                    if error.code in ('notionSourceUnavailable','notionAccessDenied'):unavailable.add(bid)
                    raise
            items=copy.deepcopy(cache[bid])
            for block in items:
                count+=1
                if count>2000:raise NotionError('notionPageTooLarge',422)
                if block.get('type')=='synced_block':
                    source=(block.get('synced_block',{}).get('synced_from') or {}).get('block_id')
                    try:
                        block['children']=walk(source or block['id'],depth+1,(*trail,bid)) if source or block.get('has_children') else []
                        block['synced_resolved']=True
                    except NotionError as error:
                        if error.code not in ('notionSourceUnavailable','notionAccessDenied','notionInvalidResponse'):raise
                        block['synced_unavailable']=True
                elif block.get('has_children') and block.get('type') not in ('child_page','child_database'):block['children']=walk(block['id'],depth+1,(*trail,bid))
            return items
        return walk(identity)

def exchange(config,kind,data,transport=None):
    client_id,secret=config.credentials(kind)
    try:
        with httpx.Client(timeout=20,transport=transport,follow_redirects=False) as client:
            response=client.post(API_ORIGIN+'/v1/oauth/token',auth=(client_id,secret),headers={'Notion-Version':API_VERSION},json=data)
    except httpx.HTTPError:raise NotionError('notionAuthorizationFailed',503) from None
    if not response.is_success:raise NotionError('notionAuthorizationFailed',400)
    try:body=response.json()
    except ValueError:raise NotionError('notionAuthorizationFailed',400) from None
    if not isinstance(body,dict) or not isinstance(body.get('access_token'),str):raise NotionError('notionAuthorizationFailed',400)
    return body
