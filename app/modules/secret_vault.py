"""PIN-gated vault API. Ordinary note/file routes never see these documents."""
from app.i18n import message as localized_message
import hashlib
import json
import mimetypes
import os
import re
import secrets
from contextlib import suppress
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field
from app.http import Router, Request, Response, StreamingResponse, Query
from app.storage import blocking
from app.secret_vault import load, save, new_vault, branch, backup_warning, counts, retire_unused_page_assets, remove_notes
from app.modules.notes import validate_blocks
from app.modules.files import filename, MAX_FILE_SIZE, TEXT_LIMIT, Text as TextEdit, Selection
from app.data_crypto import StorageError


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Pin(Strict):
    pin: str = Field(pattern=r'^(?:[0-9]{4}|[0-9]{6})$')


class Enable(Pin):
    code: str = Field(min_length=1, max_length=100)


class ChangePin(Enable):
    old_pin: str = Field(pattern=r'^(?:[0-9]{4}|[0-9]{6})$')


class Proof(Strict):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=100)


class Code(Strict):
    code: str = Field(min_length=1, max_length=100)


class Unlock(Pin):
    section: Literal['notes', 'files']
    mobile: bool = False


class Page(Strict):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default='', max_length=200000)
    blocks: list[dict] | None = None
    parent_id: str | None = None
    version: int | None = Field(default=None, ge=1)


class Folder(Strict):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = None


class EditFile(Folder):
    version: int = Field(ge=1)


class Transfer(Strict):
    id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)


class Version(Strict):
    version: int = Field(ge=1)


def router(service,tokens):
    r = Router()
    db, ready, fail, now = service.db, service.ready, service.fail, service.now

    @r.get('/api/secret-vault')
    def status(request: Request):
        s = ready(request)
        with db() as c:
            value = load(c, s['user_id'])
            site_counts={'notes':0,'files':0};any_enabled=False
            if s['role']=='owner':
                for row in c.execute('SELECT key FROM app_settings WHERE key LIKE ?',('secret-vault:%',)).fetchall():
                    candidate=load(c,row[0][len('secret-vault:'):])
                    if candidate:
                        any_enabled=True
                        total=counts(candidate)
                        for field in site_counts:site_counts[field]+=total[field]
            personal=counts(value)
        return {'enabled':bool(value), 'encryption_enabled':service.encrypted, 'two_factor':bool(s['totp']),
                'deleting':bool(value and value.get('deleting')), 'blocked_until':value['blocked_until'] if value else 0,
                'has_content':bool(value and (value['notes'] or value['files'])),
                'counts':personal,
                'backup_warning':backup_warning(**personal) if value else '',
                'site_backup_warning':backup_warning(**site_counts) if any_enabled else ''}

    @r.post('/api/secret-vault/enable')
    def enable(data: Enable, request: Request):
        s = ready(request)
        service.protection.rate(request, 'vault-security', 15, subject=s['user_id'])
        if not service.encrypted:
            fail(409, localized_message('theSecureStorageRequiresTheServerSData'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, user, value = service.current(c, request)
            if value:
                fail(409, localized_message('theSecureStorageIsAlreadyEnabled'))
            service.factor(c, user, data.code)
            save(c, s['user_id'], new_vault(service.ph.hash(data.pin), user['totp']))
            service.audit(c, request, 'vault-enabled', s['user_id'])
        return {'ok':True}

    def check_pin(c, request, s, value, pin):
        if value['blocked_until'] > now():
            return 'Хранилище временно заблокировано после неверных PIN. Подождите 15 минут или подтвердите 2FA.'
        if service.check_password(pin, value['pin']):
            value['failures'] = 0
            value['blocked_until'] = 0
            return None
        value['failures'] += 1
        if value['failures'] >= 5:
            value['blocked_until'] = now()+900
            value['failures'] = 0
            value['grants'] = {}
        save(c, s['user_id'], value)
        service.audit(c, request, 'vault-pin-blocked' if value['blocked_until']>now() else 'vault-pin-failed', s['user_id'], 'blocked')
        return 'Неверный PIN.' if value['blocked_until']<=now() else 'Пять неверных PIN. Хранилище заблокировано на 15 минут.'

    @r.post('/api/secret-vault/unlock')
    def unlock(data: Unlock, request: Request):
        s = ready(request)
        service.protection.rate(request, 'vault-unlock', 60, subject=s['user_id'])
        service.expire_grants(s['user_id'])
        error = None
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, user, value = service.current(c, request)
            if not value or value.get('deleting'):
                fail(423, localized_message('storageIsDisabledOrWaitingForDeletionTo'))
            error = check_pin(c, request, s, value, data.pin)
            if not error:
                mobile = data.mobile or bool(re.search(r'Mobile|Android|iPhone|iPad', request.headers.get('user-agent',''), re.I))
                ttl = 300 if mobile else 1200
                token = secrets.token_urlsafe(32)
                value['grants'] = {k:v for k,v in value['grants'].items() if v['expires']>now() and c.execute('SELECT 1 FROM sessions WHERE token=?', (v['session'],)).fetchone()}
                if len(value['grants'])>=100:
                    fail(429, localized_message('tooManyOpenSecureSectionsCloseOtherSessions'))
                expires=now()+ttl
                value['grants'][hashlib.sha256(token.encode()).hexdigest()] = {'session':s['token'], 'section':data.section, 'expires':expires, 'ttl':ttl}
                save(c, s['user_id'], value)
                service.audit(c, request, 'vault-unlocked', s['user_id'])
        if error:
            fail(429 if 'заблокировано' in error else 403, error)
        return {'token':token, 'expires':expires, 'ttl':ttl}

    @r.post('/api/secret-vault/unblock')
    def unblock(data: Code, request: Request):
        s = ready(request)
        service.protection.rate(request, 'vault-security', 15, subject=s['user_id'])
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, user, value = service.current(c, request)
            if not value:
                fail(404, localized_message('storageIsDisabled'))
            service.factor(c, user, data.code)
            value.update(failures=0, blocked_until=0)
            save(c, s['user_id'], value)
            service.audit(c, request, 'vault-unblocked', s['user_id'])
        return {'ok':True}

    @r.put('/api/secret-vault/pin')
    def change_pin(data: ChangePin, request: Request):
        s = ready(request)
        service.protection.rate(request, 'vault-security', 15, subject=s['user_id'])
        error = None
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, user, value = service.current(c, request)
            if not value or value.get('deleting'):
                fail(423, localized_message('storageIsUnavailable'))
            error = check_pin(c, request, s, value, data.old_pin)
            if not error:
                service.factor(c, user, data.code)
                value.update(pin=service.ph.hash(data.pin), grants={})
                save(c, s['user_id'], value)
                service.audit(c, request, 'vault-pin-changed', s['user_id'])
        if error:
            fail(403, error)
        return {'ok':True}

    @r.delete('/api/secret-vault')
    def destroy(data: Proof, request: Request):
        s = ready(request)
        service.protection.rate(request, 'vault-security', 15, subject=s['user_id'])
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, user, value = service.current(c, request)
            if not value:
                fail(404, localized_message('storageIsDisabled'))
            if not service.check_password(data.password, user['password']):
                fail(403, localized_message('incorrectAccountPassword'))
            service.factor(c, user, data.code)
            value['garbage'] = list(set(value.get('garbage', [])+[v['object_id'] for v in value['files'].values() if v.get('object_id')]))
            value.update(notes={}, files={}, grants={}, deleting=True)
            save(c, s['user_id'], value)
            service.audit(c, request, 'vault-deleted', s['user_id'])
        cleanup(s['user_id'])
        return {'ok':True}

    @r.post('/api/secret-vault/{section}/activity')
    def activity(section: Literal['notes','files'], request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, value, grant = service.authorized(c, request, section)
            grant['expires'] = now()+grant['ttl']
            save(c, s['user_id'], value)
            return {'expires':grant['expires'], 'ttl':grant['ttl']}

    @r.post('/api/secret-vault/{section}/lock')
    def lock(section: Literal['notes','files'], request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, value, grant = service.authorized(c, request, section)
            value['grants'].pop(hashlib.sha256(request.headers['x-vault-token'].encode()).hexdigest(), None)
            save(c, s['user_id'], value)
            service.audit(c, request, 'vault-locked', s['user_id'])
        return {'ok':True}

    @r.post('/api/secret-vault/{section}/auto-lock')
    def auto_lock(section: Literal['notes','files'],request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s,user,value=service.current(c,request)
            identity=hashlib.sha256(request.headers.get('x-vault-token','').encode()).hexdigest()
            grant=value['grants'].get(identity) if value else None
            if grant and grant['session']==s['token'] and grant['section']==section:
                value['grants'].pop(identity)
                value['downloads']={k:v for k,v in value.get('downloads',{}).items() if v.get('grant')!=identity}
                save(c,s['user_id'],value)
                service.audit(c,request,'vault-auto-locked',s['user_id'])
        return {'ok':True}

    @r.get('/api/secret-vault/{section}/items')
    def listing(section: Literal['notes','files'], request: Request):
        with db() as c:
            s, value, grant = service.authorized(c, request, section)
            items = value['notes'].values() if section=='notes' else (f for f in value['files'].values() if not f.get('note_id'))
            result = [{k:v for k,v in item.items() if k not in ('object_id','body','blocks','source_asset','database','record')} | ({'kind':'database' if item.get('database') else 'page'} if section=='notes' else {}) for item in items]
            from app.quota import summary
            quota=summary(c,s['user_id']) if section=='files' else None
        return {'items':result, 'expires':grant['expires'], 'ttl':grant['ttl'], 'max_file_bytes':MAX_FILE_SIZE,'quota':quota}

    @r.get('/api/secret-vault/{section}/state')
    def state(section: Literal['notes','files'], request: Request):
        with db() as c:
            s,value,grant=service.authorized(c,request,section)
            return {'expires':grant['expires'],'ttl':grant['ttl']}

    @r.get('/api/secret-vault/notes/pages/{nid}')
    def note(nid: str, request: Request):
        with db() as c:
            s, value, grant = service.authorized(c, request, 'notes')
            if nid not in value['notes']:
                fail(404, localized_message('noteNotFound'))
            decorations=[{'id':item['id'],'kind':item['page_asset']['kind'],'payload':json.loads(item['page_asset']['payload'])}
                         for item in value['files'].values() if item.get('note_id')==nid and item.get('page_asset')]
            return value['notes'][nid] | {'decorations':decorations}

    def page(data, value, nid=None):
        validate_blocks(data.blocks, fail)
        if not data.title.strip():
            fail(422, localized_message('enterTheNoteName'))
        if data.parent_id and data.parent_id not in value['notes']:
            fail(404, localized_message('parentNoteNotFound'))
        if nid and data.parent_id in branch(value['notes'], nid):
            fail(422, localized_message('cannotMoveThePageIntoItselfOrIts'))
        record=value['notes'].get(nid,{}).get('record')
        if record and data.parent_id!=record['database_id']:
            fail(422,localized_message('theNoteRemainsWithinItsOwnDatabase'))
        if not record and data.parent_id and value['notes'][data.parent_id].get('database'):
            fail(422,localized_message('addAnEntryThroughTheDatabase'))
        return data.model_dump(exclude={'version'}) | {'title':data.title.strip()}

    @r.post('/api/secret-vault/notes')
    def create(data: Page, request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, value, grant = service.authorized(c, request, 'notes')
            nid = secrets.token_urlsafe(12)
            item = page(data, value) | {'id':nid, 'created':now(), 'updated':now(), 'version':1, 'icon':'📄', 'cover':''}
            value['notes'][nid] = item
            save(c, s['user_id'], value)
        return item

    @r.put('/api/secret-vault/notes/pages/{nid}')
    def update(nid: str, data: Page, request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, value, grant = service.authorized(c, request, 'notes')
            old = value['notes'].get(nid)
            if not old:
                fail(404, localized_message('noteNotFound'))
            if data.version != old['version']:
                fail(409, localized_message('noteHasBeenModifiedOpenItAgainBefore'))
            from app.page_links import page_links,prune_vault_links
            previous=page_links(old.get('blocks'));current=page_links(data.blocks)
            selected=set()
            if data.blocks is not None:
                for child in previous-current:
                    if value['notes'].get(child,{}).get('parent_id')==nid:selected|=branch(value['notes'],child)
            old.update(page(data, value, nid), updated=now(), version=old['version']+1)
            if selected:
                remove_notes(value,selected,now(),fail)
                service.audit(c,request,'vault-item-deleted',s['user_id'])
            # Undo may restore an old block, but a permanently deleted secret
            # page cannot be recovered. Return canonical blocks to the editor.
            missing=page_links(old.get('blocks'))-value['notes'].keys()
            if missing:prune_vault_links(value,missing,now(),fail)
            retired=retire_unused_page_assets(value)
            # No revisions, CRDT snapshots, comments or search documents.
            save(c, s['user_id'], value)
        if retired or selected:cleanup(s['user_id'])
        return old

    @r.post('/api/secret-vault/files/folders')
    def folder(data: Folder, request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, value, grant = service.authorized(c, request, 'files')
            parent(value, data.parent_id)
            fid = secrets.token_urlsafe(12)
            item = {'id':fid, 'name':filename(data.name), 'parent_id':data.parent_id, 'kind':'folder', 'size':0, 'mime':'', 'version':1, 'created':now(), 'updated':now()}
            value['files'][fid] = item
            save(c, s['user_id'], value)
        return item

    def parent(value, pid):
        if pid and (pid not in value['files'] or value['files'][pid]['kind']!='folder' or value['files'][pid].get('note_id')):
            fail(404, localized_message('folderNotFound'))

    @r.put('/api/secret-vault/files/{fid}')
    def edit_file(fid: str, data: EditFile, request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, value, grant = service.authorized(c, request, 'files')
            item = value['files'].get(fid)
            if not item or item.get('note_id'):
                fail(404, localized_message('fileNotFound'))
            parent(value, data.parent_id)
            if data.parent_id in branch(value['files'], fid):
                fail(422, localized_message('cannotMoveTheFolderIntoItselfOrIts'))
            if item['version'] != data.version:
                fail(409, localized_message('fileHasBeenModifiedRefreshTheSection'))
            item.update(name=filename(data.name), parent_id=data.parent_id, version=item['version']+1, updated=now())
            save(c, s['user_id'], value)
        return {'ok':True}

    def text_item(value,fid):
        item=value['files'].get(fid)
        if not item or item['kind']!='file' or item.get('note_id'):fail(404,localized_message('fileNotFound'))
        if item['size']>TEXT_LIMIT:fail(413,localized_message('theTextEditorSupportsFilesUpTo2'))
        return item

    @r.get('/api/secret-vault/files/{fid}/thumbnail')
    def thumbnail(fid: str,request: Request):
        from app.file_thumbnails import thumbnail as render
        with db() as c:
            s,value,grant=service.authorized(c,request,'files')
            item=value['files'].get(fid)
            if not item or item.get('note_id'):fail(404,localized_message('fileNotFound'))
            limit=32*1024**2
            if item['kind']!='file' or not item['mime'].startswith('image/') or item['size']>limit:fail(415,localized_message('thumbnailIsNotAvailableForThisFile'))
            body=service.objects(s['user_id'],value).read(item['object_id'],limit)
        result=render(body,fail)
        with db() as c:service.authorized(c,request,'files')
        return Response(result,media_type='image/webp',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})

    @r.get('/api/secret-vault/files/{fid}/text')
    def text(fid: str,request: Request):
        with db() as c:
            s,value,grant=service.authorized(c,request,'files')
            item=text_item(value,fid)
            body=service.objects(s['user_id'],value).read(item['object_id'],TEXT_LIMIT+1)
            try:decoded=body.decode('utf-8')
            except UnicodeDecodeError:fail(415,localized_message('theFileIsNotAUtf8Text'))
            if '\0' in decoded:fail(415,localized_message('binaryFileCannotBeOpenedAsText'))
            return {'text':decoded,'name':item['name'],'version':item['version']}

    @r.put('/api/secret-vault/files/{fid}/text')
    def save_text(fid: str,data: TextEdit,request: Request):
        identity=secrets.token_hex(16);store=None
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                s,value,grant=service.authorized(c,request,'files')
                item=text_item(value,fid)
                if item['version']!=data.version:fail(409,localized_message('fileHasBeenModifiedUpdateItBeforeSaving'))
                body=data.text.encode()
                if len(body)>TEXT_LIMIT:fail(413,localized_message('textSizeExceeds2Mb'))
                store=service.objects(s['user_id'],value)
                writer=store.writer(identity)
                try:writer.write(body);writer.finish()
                except BaseException:
                    with suppress(Exception):writer.abort()
                    raise
                service.authorized(c,request,'files')
                value['garbage'].append(item['object_id'])
                item.update(object_id=identity,size=len(body),version=item['version']+1,updated=now())
                save(c,s['user_id'],value)
        except BaseException:
            if store:
                with suppress(Exception):service.discard(store,identity)
            raise
        cleanup(s['user_id'])
        return {'version':item['version']}

    @r.post('/api/secret-vault/files/{fid}/duplicate')
    def duplicate(fid: str,data: Version,request: Request):
        identity=secrets.token_hex(16);store=None;writer=None
        try:
            with db() as c:
                s,value,grant=service.authorized(c,request,'files')
                item=value['files'].get(fid)
                if not item or item['kind']!='file' or item.get('note_id'):fail(404,localized_message('fileNotFound'))
                if item['version']!=data.version:fail(409,localized_message('fileHasBeenModifiedRefreshTheSection'))
                store=service.objects(s['user_id'],value)
            writer=store.writer(identity)
            for chunk in store.iter_read(item['object_id']):writer.write(chunk)
            size,digest=writer.finish()
            checksum=hashlib.sha256()
            for chunk in store.iter_read(identity):checksum.update(chunk)
            if checksum.hexdigest()!=digest:fail(503,localized_message('fileCopyVerificationFailedOriginalFileSaved'))
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                s,current,grant=service.authorized(c,request,'files')
                old=current['files'].get(fid)
                if current['key']!=value['key'] or not old or old['version']!=data.version:fail(409,localized_message('fileHasBeenModifiedRefreshTheSection'))
                stem,extension=os.path.splitext(item['name'])
                if len(extension)>=20:stem,extension=item['name'],''
                suffix=' копия'+extension
                created=dict(item,id=secrets.token_urlsafe(12),object_id=identity,name=stem[:255-len(suffix)]+suffix,created=now(),updated=now(),version=1)
                current['files'][created['id']]=created;save(c,s['user_id'],current)
        except BaseException:
            if writer:
                with suppress(Exception):writer.abort()
            if store:
                with suppress(Exception):service.discard(store,identity)
            raise
        return {k:v for k,v in created.items() if k!='object_id'}

    @r.put('/api/secret-vault/files/{fid}/content')
    async def replace_file(fid: str,request: Request,version: int=Query(ge=1)):
        with db() as c:
            s,value,grant=service.authorized(c,request,'files')
            item=value['files'].get(fid)
            if not item or item['kind']!='file' or item.get('note_id'):fail(404,localized_message('fileNotFound'))
            name,pid=item['name'],item['parent_id']
        return await write_upload('files',request,name,pid,fid,version)

    @r.post('/api/secret-vault/{section}/upload')
    async def upload(section: Literal['notes','files'], request: Request, name: str = Query(max_length=255), parent_id: str|None=None):
        return await write_upload(section,request,name,parent_id)

    async def write_upload(section,request,name,parent_id,replace_id=None,replace_version=None):
        name = filename(name)
        s = ready(request)
        service.protection.rate(request, 'vault-upload', 60, subject=s['user_id'])
        with db() as c:
            s, value, grant = service.authorized(c, request, section)
        limit = 50*1024**2 if section=='notes' else MAX_FILE_SIZE
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>limit:
            fail(413, localized_message('fileExceedsTheMaximumSize2'))
        store = service.objects(s['user_id'], value)
        identity, fid = secrets.token_hex(16), replace_id or secrets.token_urlsafe(12)
        writer = None
        try:
            writer = await blocking(store.writer, identity)
            async for chunk in request.stream():
                if writer.size+len(chunk)>limit:
                    fail(413, localized_message('fileExceedsTheMaximumSize2'))
                await blocking(writer.write, chunk)
            size, _ = await blocking(writer.finish)
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                s, current, grant = service.authorized(c, request, section)
                if current['key'] != value['key']:
                    fail(409, localized_message('storageHasChanged'))
                if section=='notes':
                    if parent_id not in current['notes']:
                        fail(404, localized_message('noteNotFound'))
                else:
                    parent(current, parent_id)
                if replace_id:
                    old=current['files'].get(fid)
                    if not old or old['kind']!='file' or old.get('note_id'):fail(404,localized_message('fileNotFound'))
                    if old['version']!=replace_version:fail(409,localized_message('fileHasBeenModifiedRefreshTheSection'))
                    value_version=old['version']+1
                    current['garbage'].append(old['object_id'])
                else:value_version=1
                item = {'id':fid, 'name':name, 'kind':'file', 'parent_id':None if section=='notes' else parent_id,
                        'note_id':parent_id if section=='notes' else None, 'mime':mimetypes.guess_type(name)[0] or 'application/octet-stream',
                        'size':size, 'object_id':identity, 'version':value_version, 'created':old['created'] if replace_id else now(), 'updated':now()}
                current['files'][fid] = item
                save(c, s['user_id'], current)
        except BaseException:
            if writer:
                with suppress(Exception):
                    await blocking(writer.abort)
            with suppress(Exception):
                await blocking(service.discard, store, identity)
            raise
        if replace_id:await blocking(cleanup,s['user_id'])
        return {k:v for k,v in item.items() if k!='object_id'} | {'url':'/api/secret-vault/'+section+'/content/'+fid}

    @r.get('/api/secret-vault/{section}/content/{fid}')
    def content(section: Literal['notes','files'], fid: str, request: Request):
        return binary_response(section,fid,request)

    @r.post('/api/secret-vault/{section}/prepare-download/{fid}')
    def prepare_download(section: Literal['notes','files'], fid: str, request: Request, response: Response):
        return prepare_ticket(section,fid,request,response)

    @r.post('/api/secret-vault/files/prepare-selection')
    def prepare_selection(data: Selection,request: Request,response: Response):
        if not data.ids:fail(422,localized_message('selectFilesToDownload'))
        return prepare_ticket('files','selection',request,response,data.ids)

    def prepare_ticket(section,fid,request,response,selection=None):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s,value,grant=service.authorized(c,request,section)
            for identity in selection or [fid]:
                item=value['files'].get(identity)
                if not item or bool(item.get('note_id'))!=(section=='notes'):fail(404,localized_message('fileNotFound'))
            ticket=secrets.token_urlsafe(32)
            value['downloads']={key:item for key,item in value.get('downloads',{}).items() if item['expires']>now()}
            if len(value['downloads'])>=100:fail(429,localized_message('tooManyUnfinishedDownloads'))
            value['downloads'][hashlib.sha256(ticket.encode()).hexdigest()]={'fid':fid,'section':section,'session':s['token'],
                'grant':hashlib.sha256(request.headers['x-vault-token'].encode()).hexdigest(),'expires':now()+30,'selection':selection}
            save(c,s['user_id'],value)
        path='/api/secret-vault/'+section+'/download/'+fid
        response.set_cookie('drg_vault_download',ticket,max_age=30,path=path,
                            httponly=True,secure=os.getenv('PUBLIC_URL','').startswith('https://'),samesite='Strict')
        return {'url':path}

    @r.get('/api/secret-vault/{section}/download/{fid}')
    def download(section: Literal['notes','files'], fid: str, request: Request):
        token=request.cookies.get('drg_vault_download','')
        if not token:fail(423,localized_message('firstConfirmTheDownloadInTheSecureSection'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s,user,value=service.current(c,request)
            ticket_key=hashlib.sha256(token.encode()).hexdigest()
            ticket=value.get('downloads',{}).get(ticket_key) if value else None
            if not ticket or ticket['expires']<=now() or ticket['fid']!=fid or ticket['section']!=section or ticket['session']!=s['token']:
                fail(423,localized_message('downloadPermissionHasBeenUsedOrExpiredRepeat'))
            service.authorized(c,request,section,grant_id=ticket['grant'])
            value['downloads'].pop(ticket_key)
            save(c,s['user_id'],value)
        result=binary_response(section,fid,request,grant_id=ticket['grant'],selection=ticket.get('selection'))
        result.delete_cookie('drg_vault_download',path='/api/secret-vault/'+section+'/download/'+fid)
        return result

    def binary_response(section,fid,request,grant_id=None,selection=None):
        def authorized():
            with db() as c:
                s, value, grant = service.authorized(c, request, section,grant_id=grant_id)
                for identity in selection or [fid]:
                    item=value['files'].get(identity)
                    if not item or (section=='notes')!=bool(item.get('note_id')):fail(404,localized_message('fileNotFound'))
                if selection:item={'name':'Секретные файлы','kind':'folder'}
                return s, value, item
        s, value, item = authorized()
        store = service.objects(s['user_id'], value)
        if item['kind']=='folder':
            import zipstream
            selected=set().union(*(branch(value['files'],identity) for identity in selection or [fid]))
            archive=zipstream.ZipStream(compress_type=zipstream.ZIP_DEFLATED)
            names={}
            for identity in selected:
                current=value['files'][identity];key=(current.get('parent_id') if current.get('parent_id') in selected else None,current['name'])
                names[key]=names.get(key,0)+1
            def label(current):
                key=(current.get('parent_id') if current.get('parent_id') in selected else None,current['name'])
                return (current['id']+'_' if names[key]>1 else '')+current['name']
            for identity in selected:
                current=value['files'][identity];parts=[label(current)];parent_id=current.get('parent_id')
                while parent_id in selected:
                    parent=value['files'][parent_id];parts.insert(0,label(parent));parent_id=parent.get('parent_id')
                path='/'.join(parts)
                if current['kind']=='folder':archive.add(b'',path+'/')
                else:
                    def file_chunks(object_id=current['object_id']):
                        for chunk in store.iter_read(object_id):authorized();yield chunk
                    archive.add(file_chunks(),path,size=current['size'])
            def zip_chunks():
                for chunk in archive:authorized();yield chunk
            return StreamingResponse(zip_chunks(),media_type='application/zip',headers={'Cache-Control':'no-store',
                'Content-Disposition':"attachment; filename=download.zip; filename*=UTF-8''"+quote(item['name']+'.zip',safe='')})
        def chunks():
            for chunk in store.iter_read(item['object_id']):
                authorized()
                yield chunk
        return StreamingResponse(chunks(), media_type='application/octet-stream', headers={'Cache-Control':'no-store',
            'Content-Length':str(item['size']), 'X-Vault-Mime':item['mime'], 'Content-Disposition':"attachment; filename=download; filename*=UTF-8''"+quote(item['name'], safe='')})

    @r.delete('/api/secret-vault/{section}/items/{identity}')
    def delete(section: Literal['notes','files'], identity: str, request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            s, value, grant = service.authorized(c, request, section)
            selected = branch(value[section], identity)
            if section=='notes':remove_notes(value,selected,now(),fail)
            else:
                if any(value['files'][fid].get('note_id') for fid in selected):fail(404,localized_message('fileNotFound'))
                for fid in selected:
                    item=value['files'].pop(fid)
                    if item.get('object_id'):value['garbage'].append(item['object_id'])
            save(c, s['user_id'], value)
            service.audit(c, request, 'vault-item-deleted', s['user_id'])
        cleanup(s['user_id'])
        return {'ok':True}

    def cleanup(uid):
        try:service.cleanup(uid)
        except (StorageError,OSError):fail(503,localized_message('accessToRemoteDataIsClosedPhysicalClearing'))

    from app.vault_pages import register as register_pages
    register_pages(r,service,Version,tokens)
    from app.vault_transfers import register
    register(r, service, Transfer)
    from app.vault_databases import register as register_databases
    register_databases(r,service)
    return r
