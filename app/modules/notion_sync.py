"""Native/web clients share the same account-scoped integration protocol."""
from typing import Literal
from pydantic import BaseModel,Field,ConfigDict
from app.http import Router,Request,Response,JSONResponse
from app.notion_sync.client import NotionError
from app.notion_sync.events import receive,MAX_BODY
from app.storage import blocking
from app.i18n import message

class OAuthStart(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['read','write']='read'
    platform:Literal['web','ios']='web'

class Preview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    connection_id:str=Field(min_length=1,max_length=80)
    source:str=Field(min_length=1,max_length=2000)

class Pages(BaseModel):
    model_config=ConfigDict(extra='forbid')
    connection_id:str=Field(min_length=1,max_length=80)
    query:str=Field(default='',max_length=200)
    cursor:str=Field(default='',max_length=2000)

class Tree(BaseModel):
    model_config=ConfigDict(extra='forbid')
    connection_id:str=Field(min_length=1,max_length=80)
    refresh:bool=Field(default=False,strict=True)

class Confirmation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    preview_hash:str=Field(min_length=64,max_length=64)

class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['pause','resume','disconnect','cancel','retry_assets']
    version:int=Field(ge=1)

class CopyPage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    note_id:str=Field(min_length=16,max_length=16)

class Experimental(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=Field(strict=True)
    version:int=Field(ge=1)
    connection_id:str|None=Field(default=None,max_length=80)

class Resolve(BaseModel):
    model_config=ConfigDict(extra='forbid')
    note_id:str=Field(min_length=16,max_length=16)
    review_hash:str=Field(min_length=64,max_length=64)
    version:int=Field(ge=1)

class Recovery(Resolve):
    direction:Literal['pull','push']
    note_version:int=Field(ge=1)
    local_hash:str=Field(min_length=64,max_length=64)
    keep_copy:bool=Field(default=True,strict=True)


def create_notion_sync_router(service,ready,rate,fail,administrator=None):
    r=Router()
    account_ready=ready
    def ready(request):
        session=account_ready(request)
        if session['role']!='owner':fail(403,message('onlyTheSiteOwnerCanAccessThis'))
        return session
    def run(function,*args):
        try:return function(*args)
        except NotionError as error:fail(error.status,message(error.code,**error.params),headers={'Retry-After':str(error.delay)} if error.delay else None)
        except ValueError as error:
            if str(error) not in ('notionNotConfigured','notionInvalidPage'):raise
            fail(422,message(str(error)))

    @r.get('/api/account/notion-sync')
    def status(request:Request):return service.status(ready(request)['user_id'])

    @r.post('/api/account/notion-sync/oauth')
    def oauth(data:OAuthStart,request:Request):
        s=ready(request);rate(request,'notion-oauth',12,subject=s['user_id'])
        return {'url':run(service.connections.start,s,data.kind,data.platform)}

    @r.get('/api/notion-sync/oauth/callback')
    def callback(request:Request):
        platform=run(service.connections.complete,request.args.get('state',''),request.args.get('code',''))
        target='drg-family://notion-sync?result=connected' if platform=='ios' else '/?notion=connected'
        return Response('',status_code=302,headers={'Location':target,'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'"})

    @r.post('/api/account/notion-sync/preview')
    def preview(data:Preview,request:Request):
        s=ready(request);rate(request,'notion-sync-preview',12,subject=s['user_id'])
        return run(service.preview,s['user_id'],data.connection_id,data.source)

    @r.post('/api/account/notion-sync/pages')
    async def pages(data:Pages,request:Request):
        s=ready(request);rate(request,'notion-page-picker',60,subject=s['user_id'])
        return await blocking(run,service.picker.list,s['user_id'],data.connection_id,data.query,data.cursor)

    @r.post('/api/account/notion-sync/tree')
    async def tree(data:Tree,request:Request):
        s=ready(request);rate(request,'notion-page-picker',60,subject=s['user_id'])
        return await blocking(run,service.picker.tree,s['user_id'],data.connection_id,data.refresh)

    @r.get('/api/account/notion-sync/roots/{rid}')
    def root(rid:str,request:Request):
        s=ready(request)
        def get():
            with service.db() as c:return service.describe(c,service.owned(c,rid,s['user_id']))
        return run(get)

    @r.post('/api/account/notion-sync/roots/{rid}/confirm')
    def confirm(rid:str,data:Confirmation,request:Request):return run(service.confirm,ready(request)['user_id'],rid,data.preview_hash)

    @r.post('/api/account/notion-sync/roots/{rid}/action')
    def action(rid:str,data:Action,request:Request):
        s=ready(request)
        if data.action=='retry_assets':rate(request,'notion-asset-retry',12,subject=s['user_id'])
        return run(service.action,s['user_id'],rid,data.action,data.version)

    @r.post('/api/account/notion-sync/copy')
    async def copy(data:CopyPage,request:Request):
        from app.notion_sync.copy import independent
        s=ready(request);rate(request,'notion-independent-copy',12,subject=s['user_id'])
        return await blocking(run,independent,service,s['user_id'],data.note_id)

    @r.post('/api/account/notion-sync/roots/{rid}/experimental')
    def experimental(rid:str,data:Experimental,request:Request):
        return run(service.experimental,ready(request)['user_id'],rid,data.version,data.enabled,data.connection_id)

    @r.get('/api/account/notion-sync/roots/{rid}/conflict')
    async def conflict(rid:str,request:Request,refresh:bool=True):return await blocking(run,service.review_conflict,ready(request)['user_id'],rid,refresh)

    @r.post('/api/account/notion-sync/roots/{rid}/resolve')
    async def resolve(rid:str,data:Resolve,request:Request):
        return await blocking(run,service.accept_source,ready(request)['user_id'],rid,data.note_id,data.review_hash,data.version)

    @r.get('/api/account/notion-sync/roots/{rid}/recovery')
    async def recovery(rid:str,request:Request,note_id:str|None=None):
        from app.notion_sync.recovery import review
        s=ready(request);rate(request,'notion-recovery-review',30,subject=s['user_id'])
        return await blocking(run,review,service,s['user_id'],rid,note_id)

    @r.post('/api/account/notion-sync/roots/{rid}/recovery')
    async def recover(rid:str,data:Recovery,request:Request):
        from app.notion_sync.recovery import resolve
        s=ready(request);rate(request,'notion-recovery',12,subject=s['user_id'])
        return await blocking(run,resolve,service,s['user_id'],rid,data.model_dump())

    @r.post('/api/notion-sync/webhooks/{kind}')
    async def webhook(kind:str,request:Request):
        if not service.config.configured:fail(404,'notionNotConfigured')
        if not request.headers.get('x-notion-signature'):rate(request,'notion-webhook-setup',12,subject=kind)
        body=bytearray()
        async for part in request.stream():
            if len(body)+len(part)>MAX_BODY:fail(413,'notionInvalidRequest')
            body.extend(part)
        return await blocking(run,receive,service,kind,bytes(body),request.headers.get('x-notion-signature',''))

    @r.get('/api/admin/notion-sync/webhooks/{kind}')
    def webhook_setup(kind:str,request:Request):
        import json
        ready(request)
        if administrator is None:fail(403,message('notionWriteForbidden'))
        administrator(request)
        if kind!='read':fail(404,message('notionInvalidRequest'))
        with service.db() as c:row=c.execute('SELECT value FROM app_settings WHERE key=?',('notion-webhook-verification:'+kind,)).fetchone()
        pending=json.loads(row[0]) if row else None
        if pending and service.clock()-pending['created']>86400:pending=None
        return JSONResponse({'pending':pending},headers={'Cache-Control':'no-store'})

    @r.delete('/api/admin/notion-sync/webhooks/{kind}')
    def webhook_reset(kind:str,request:Request):
        ready(request)
        if administrator is None:fail(403,message('notionWriteForbidden'))
        administrator(request)
        if kind!='read':fail(404,message('notionInvalidRequest'))
        with service.db() as c:c.execute('DELETE FROM app_settings WHERE key=?',('notion-webhook-verification:'+kind,))
        return {'ok':True}
    return r
