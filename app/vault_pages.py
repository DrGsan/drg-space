"""Page presentation and copies inside the encrypted, PIN-gated catalog."""
from app.i18n import message as localized_message
import copy
import hashlib
import json
import secrets
import httpx
from contextlib import suppress

from pydantic import BaseModel, ConfigDict, Field
from app.http import Request
from app.secret_vault import save, branch, retire_unused_page_assets
from app.modules.note_assets import thumbnail, IMAGE_LIMIT, asset_id, CoverSelection, cover_grant, selected_cover, track_cover
from app.storage import blocking
from app.data_crypto import StorageError
from PIL import Image


class Meta(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)
    icon: str | None = Field(default=None, max_length=100)
    cover: str | None = Field(default=None, max_length=100)
    favorite: bool | None = None
    full_width: bool | None = None


def register(r, service, Version, tokens):
    db, fail, now = service.db, service.fail, service.now

    def owned(value, nid, version=None):
        item=value['notes'].get(nid)
        if not item:fail(404,localized_message('noteNotFound'))
        if version is not None and version!=item['version']:fail(409,localized_message('noteHasBeenChangedOpenItAgain'))
        return item

    def cleanup(uid):
        try:service.cleanup(uid)
        except (OSError,StorageError):fail(503,localized_message('changesSavedAutomaticReplacementOfThePreviousImage'))

    @r.patch('/api/secret-vault/notes/pages/{nid}/meta')
    def meta(nid: str, data: Meta, request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s,value,_=service.authorized(c,request,'notes');item=owned(value,nid,data.version)
            fields=data.model_dump(exclude_unset=True,exclude={'version'})
            if any(v is None for v in fields.values()):fail(422,localized_message('specifyTheSettingValue'))
            for kind in ('icon','cover'):
                if kind not in fields:continue
                aid=asset_id(fields[kind])
                if aid:
                    image=value['files'].get(aid,{})
                    if image.get('note_id')!=nid or (image.get('page_asset') or {}).get('kind')!=kind:fail(422,localized_message('theImageDoesNotBelongToThePage'))
                elif kind=='cover' and fields[kind] not in ('','blue','sage','sand','rose','night'):fail(422,localized_message('unknownCoverImage'))
                elif kind=='icon' and fields[kind].startswith('image:'):fail(422,localized_message('invalidIcon'))
                item[kind]=fields[kind]
            item.update(fields,version=item['version']+1,updated=now())
            retire_unused_page_assets(value)
            save(c,s['user_id'],value)
        cleanup(s['user_id'])
        return item

    @r.post('/api/secret-vault/notes/pages/{nid}/assets/{kind}')
    async def image(nid: str,kind: str,request: Request):
        if kind not in ('icon','cover'):fail(422,localized_message('selectAnIconOrCoverImage'))
        with db() as c:
            s,value,_=service.authorized(c,request,'notes');old=owned(value,nid)
            version=old['version']
        service.protection.rate(request,'vault-image',30,subject=s['user_id'])
        parts=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>IMAGE_LIMIT:fail(413,localized_message('theImageIsNoMoreThan20Mb'))
            parts.append(chunk)
        try:body,width,height=await blocking(thumbnail,b''.join(parts),kind)
        except (ValueError,OSError,EOFError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(422,localized_message('youNeedPngJpegWebpOrAGif'))
        return await blocking(persist_image,nid,kind,request,value,s,version,body,width,height)

    def persist_image(nid,kind,request,value,s,version,body,width,height,info=None):
        identity,aid=secrets.token_hex(16),secrets.token_hex(16)
        store=service.objects(s['user_id'],value)
        writer=None
        try:
            writer=store.writer(identity);writer.write(body);_,digest=writer.finish()
            checksum=hashlib.sha256()
            for chunk in store.iter_read(identity):checksum.update(chunk)
            if checksum.hexdigest()!=digest:fail(503,localized_message('imageVerificationFailed'))
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                s,current,_=service.authorized(c,request,'notes');item=owned(current,nid,version)
                if current['key']!=value['key']:fail(409,localized_message('storageHasChanged'))
                current['files'][aid]={'id':aid,'name':kind+'.webp','kind':'file','note_id':nid,'parent_id':None,'mime':'image/webp','size':len(body),'object_id':identity,'version':1,'created':now(),'updated':now(),
                    'source_asset':{'id':aid,'user_id':s['user_id'],'mime':'image/webp','size':len(body),'width':width,'height':height,'frames':1,'created':now()},
                    'page_asset':{'id':aid,'note_id':nid,'kind':kind,'payload':json.dumps(info or {}),'created':now()}}
                item.update({kind:'image:'+aid},version=version+1,updated=now())
                retire_unused_page_assets(current);save(c,s['user_id'],current)
        except BaseException:
            if writer:
                with suppress(Exception):writer.abort()
            with suppress(Exception):service.discard(store,identity)
            raise
        cleanup(s['user_id'])
        return {'ok':True}

    @r.post('/api/secret-vault/notes/pages/{nid}/select-cover')
    async def select(nid: str,data: CoverSelection,request: Request):
        with db() as c:
            s,value,_=service.authorized(c,request,'notes');old=owned(value,nid);version=old['version']
        service.protection.rate(request,'vault-image',30,subject=s['user_id'])
        grant=cover_grant(tokens,data.token,s['user_id'],fail)
        try:
            source=await selected_cover(grant)
            body,width,height=await blocking(thumbnail,source,'cover')
            result=await blocking(persist_image,nid,'cover',request,value,s,version,body,width,height,{'attribution':grant['attribution'],'position':50})
            await track_cover(grant)
            return result
        except (httpx.HTTPError,TimeoutError,ValueError,OSError,EOFError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(422,localized_message('thisPhotoIsUnavailableOrExceedsTheMaximum'))

    @r.post('/api/secret-vault/notes/pages/{nid}/duplicate')
    def duplicate(nid: str,data: Version,request: Request):
        staged=[];store=None
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                s,value,_=service.authorized(c,request,'notes');owned(value,nid,data.version)
                selected=branch(value['notes'],nid);mapping={old:secrets.token_urlsafe(12) for old in selected}
                files=[copy.deepcopy(item) for item in value['files'].values() if item.get('note_id') in selected]
                mapping.update({item['id']:secrets.token_hex(16) for item in files})
                store=service.objects(s['user_id'],value)
                def rewrite(obj):
                    if isinstance(obj,list):return [rewrite(x) for x in obj]
                    if isinstance(obj,dict):return {k:rewrite(v) for k,v in obj.items()}
                    if isinstance(obj,str):
                        if obj in mapping:return mapping[obj]
                        if obj.startswith('image:') and obj[6:] in mapping:return 'image:'+mapping[obj[6:]]
                        for old,new in mapping.items():obj=obj.replace('/api/secret-vault/notes/content/'+old,'/api/secret-vault/notes/content/'+new)
                    return obj
                for old in selected:
                    item=rewrite(copy.deepcopy(value['notes'][old]));item.update(version=1,created=now(),updated=now(),favorite=False)
                    if old==nid:item['title']=(item['title']+' (копия)')[:200]
                    def block_ids(blocks):
                        for block in blocks or []:
                            block['id']=secrets.token_hex(12);block_ids(block.get('children'))
                    block_ids(item.get('blocks'))
                    for prop in item.get('database',{}).get('properties',[]):
                        if prop.get('type')=='relation' and prop.get('database_id') not in mapping.values():prop['two_way']=False;prop.pop('inverse_id',None)
                    value['notes'][mapping[old]]=item
                for item in files:
                    identity=secrets.token_hex(16);staged.append(identity)
                    writer=store.writer(identity)
                    try:
                        for chunk in store.iter_read(item['object_id']):writer.write(chunk)
                        _,digest=writer.finish()
                    except BaseException:
                        with suppress(Exception):writer.abort()
                        raise
                    checksum=hashlib.sha256()
                    for chunk in store.iter_read(identity):checksum.update(chunk)
                    if checksum.hexdigest()!=digest:fail(503,localized_message('failedToVerifyTheAttachmentCopy'))
                    created=rewrite(item);created.update(object_id=identity,version=1,created=now(),updated=now())
                    value['files'][created['id']]=created
                service.authorized(c,request,'notes');save(c,s['user_id'],value)
                result=value['notes'][mapping[nid]]
        except BaseException:
            if store:
                for identity in staged:
                    with suppress(Exception):service.discard(store,identity)
            raise
        return result
