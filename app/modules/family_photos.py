"""Optional private API bridge. Telegram content is owned by family-photo."""
from app.i18n import message as localized_message
import json
import os
import re
import threading
import time
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from app.http import Router, Request, StreamingResponse,Response
from app.notifications import actor
from .family_photo_events import FamilyPhotoEvents,read_map
from .family_photo_messaging import FamilyMessaging
from .family_photo_reactions import FamilyReactionSync

SPEC = {'title':localized_message('familyPhotos'),'kind':'module','default_enabled':False,
        'description':localized_message('familyPhotoArchiveAndConversationsFromConnectedTelegram')}


class Grant(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool
    groups: list[str] = Field(default_factory=list, max_length=100)
    write_groups:list[str]=Field(default_factory=list,max_length=100)
    admin_password: str | None = Field(default=None, min_length=1, max_length=256)


class GroupSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    telegram_id: str = Field(pattern=r'^-[1-9]\d{0,19}$')


class GroupAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation: str | None = Field(default=None, max_length=40)


class CopyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class ExportRequest(CopyRequest):
    groups: list[str] = Field(min_length=1,max_length=100)
    videos: StrictBool = False


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(default="", max_length=3500)
    attachments:list[str]=Field(default_factory=list,max_length=10)
    client_id: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    reply_to: int | None = Field(default=None, ge=1, le=2**53-1)


class ReadReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message_id: StrictInt = Field(ge=1, le=2**53-1)


class FamilyPhotos(FamilyMessaging,FamilyPhotoEvents,FamilyReactionSync):
    def filter_connection(self,session,connection):
        if not isinstance(connection,dict):return
        target=connection.get('migrated_group_id')
        if target and not self.allowed(session,target):connection.pop('migrated_group_id',None)
        if session['role']!='owner':connection.pop('migrated_to',None)

    def __init__(self, db, ready, owner, fail, check_password, protection, *, admin_access, coordinator=None):
        self.db, self.ready, self.owner, self.fail = db, ready, owner, fail
        self.check_password, self.protection = check_password, protection
        self.admin_access = admin_access
        self.url = os.getenv('FAMILY_PHOTO_URL','').rstrip('/')
        self.token = os.getenv('FAMILY_PHOTO_TOKEN','')
        self.lock = threading.Lock()
        self.permission_sync_lock = threading.Lock()
        self.permission_confirmed = None
        self._http_lock = threading.Lock()
        self._http_enabled = False
        self._http_client = None
        self.cached = None
        self.checked_at = 0
        self.ever_available = False
        self.reader_id = None
        self.coordinator = coordinator
        self.router = self.routes()

    def configured(self):
        parsed = urlparse(self.url)
        return bool(parsed.scheme in {'http','https'} and parsed.hostname and not parsed.username
                    and not parsed.password and not parsed.query and not parsed.fragment and len(self.token)>=32)

    def client(self, timeout=5):
        # Internal traffic must not inherit the host's external proxy settings.
        return httpx.Client(base_url=self.url+'/', headers={'Authorization':'Bearer '+self.token},
                            timeout=timeout, follow_redirects=False, trust_env=False,
                            limits=httpx.Limits(max_connections=8,max_keepalive_connections=4,keepalive_expiry=30))

    def start_http(self):
        # Lifecycle begins after fork. Streams use independent clients so a
        # long download cannot occupy the control API's bounded connection pool.
        with self._http_lock:self._http_enabled=True

    def close_http(self):
        with self._http_lock:
            client,self._http_client=self._http_client,None
            self._http_enabled=False
        if client is not None:client.close()

    def control_request(self,method,path,*,data=None,timeout=5):
        with self._http_lock:
            if self._http_enabled:
                if self._http_client is None:self._http_client=self.client()
                client=self._http_client
            else:client=None
        if client is not None:return client.request(method,path,json=data,timeout=timeout)
        with self.client(timeout=timeout) as temporary:
            return temporary.request(method,path,json=data,timeout=timeout)

    def status(self):
        if not self.configured():return {'configured':False,'available':False}
        with self.lock:
            if self.cached is not None and time.monotonic()-self.checked_at<10:return dict(self.cached)
            result = {'configured':True,'available':False}
            try:
                response=self.control_request('GET','v1/status',timeout=2)
                data=response.json()
                if response.status_code==200 and data.get('service')=='family-photo' and data.get('api_version')==1:
                    self.reader_id=data.get('reader_id') if type(data.get('reader_id')) is int else None
                    result |= {'available':True,'worker_online':data.get('worker_online',False),
                               'worker':data.get('worker'),'can_send':bool(data.get('can_send')),
                               'group_management':data.get('group_management')==1,
                               'group_copies':data.get('group_copies')==1,'photo_exports':data.get('photo_exports')==1,
                               'export_notifications':data.get('export_notifications')==1}
                    result['bot_messaging']=data.get('bot_messaging')==1
                    result['connection_status']=data.get('connection_status')==1
                    result['bot_reactions']=data.get('bot_reactions')==1
                    result['gallery_versions']=data.get('gallery_versions')==1
                    result['gallery_index']=data.get('gallery_index')==1
                    version=data.get('service_version')
                    if isinstance(version,str) and re.fullmatch(r'\d+\.\d+\.\d+',version):
                        result['service_version']=version
                    self.ever_available=True
            except (httpx.HTTPError,ValueError):pass
            self.cached,self.checked_at=result,time.monotonic()
            return dict(result)

    @staticmethod
    def access(session):
        value=json.loads(session['settings']).get('family_photo_access',{})
        return value if isinstance(value,dict) else {}

    def allowed(self, session, gid=None):
        if session['role']=='owner':return True
        grant=self.access(session)
        return grant.get('enabled') is True and (gid is None or gid in grant.get('groups',[]))

    def module(self, session, settings):
        if not self.configured() or not self.allowed(session):return None
        status=self.status()
        if not status['available'] and not self.ever_available and 'family_photos' not in settings.get('modules',{}):return None
        return {'id':'family_photos',**SPEC,'available':status['available']}

    def validate_switch(self, session, enabled):
        if not enabled:return
        if not self.allowed(session):self.fail(403,localized_message('theAdministratorHasNotGrantedAccessToFamily'))
        if not self.configured():self.fail(403,localized_message('theFamilyPhotoServiceIsNotConfigured'))
        was_enabled=json.loads(session['settings']).get('modules',{}).get('family_photos') is True
        if not was_enabled and not self.status()['available']:self.fail(503,localized_message('theFamilyPhotoServiceIsUnavailable'))

    def gate(self, request, gid=None, chat=False,source=True):
        s=self.ready(request)
        if not self.configured():self.fail(404,localized_message('theFamilyPhotoServiceIsNotConfigured'))
        if not self.allowed(s,gid):self.fail(403,localized_message('noAccessToThisFamilyGroup'))
        settings=json.loads(s['settings'])
        if settings.get('modules',{}).get('family_photos') is not True:
            self.fail(403,localized_message('enableFamilyPhotosInTheSettings'))
        if chat:
            from app.modules.preferences import module_enabled
            if not module_enabled(settings,'chat'):self.fail(403,localized_message('theMessagesSectionIsDisabled'))
        if gid is not None and not re.fullmatch(r'(?:-\d{1,20}|export:[a-f0-9]{8,24})',gid):self.fail(404,localized_message('groupNotFound'))
        if source and gid is not None and self.status().get('group_management'):
            if not any(group['id']==gid for group in self.fetch('v1/groups')['items']):
                self.fail(404,localized_message('theGroupIsDisabledOrAwaitingDeletion'))
        return s

    def management(self, request):
        self.owner(request)
        if not self.status().get('group_management'):
            self.fail(409,localized_message('updateFamilyPhotoToManageGroups'))

    def cleanup_purged(self, purged):
        if not purged:return
        from .family_photo_events import state,save_state
        ids=set(purged);rooms={'telegram:'+gid for gid in ids}
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            for row in c.execute('SELECT id,settings FROM users').fetchall():
                settings=json.loads(row['settings']);before=json.dumps(settings)
                access=settings.get('family_photo_access',{})
                if isinstance(access,dict) and isinstance(access.get('groups'),list):
                    access['groups']=[gid for gid in access['groups'] if gid not in ids]
                    access['write_groups']=[gid for gid in access.get('write_groups',[]) if gid not in ids]
                    if not access['groups']:
                        settings.pop('family_photo_access',None)
                        settings.get('modules',{}).pop('family_photos',None)
                for key in ('family_photo_reads','chat_notification_mutes'):
                    values=settings.get(key)
                    if isinstance(values,dict):
                        for gid in ids|rooms:values.pop(gid,None)
                        if not values:settings.pop(key,None)
                dashboard=settings.get('dashboard')
                if isinstance(dashboard,dict) and isinstance(dashboard.get('instances'),list):
                    dashboard['instances']=[item for item in dashboard['instances'] if not (item.get('module_id')=='chat' and item.get('config',{}).get('space_id') in rooms)]
                if json.dumps(settings)!=before:c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),row['id']))
            current=state(c)
            for values in current.values():
                if isinstance(values,dict):
                    for gid in ids:values.pop(gid,None)
            save_state(c,current)
            for gid in ids:
                c.execute('DELETE FROM app_settings WHERE key LIKE ?',('message-reactions:telegram:'+gid+':%',))
            for row in c.execute("SELECT id,payload FROM notifications WHERE kind='family_message'").fetchall():
                if json.loads(row['payload']).get('family_group_id') in ids:c.execute('DELETE FROM notifications WHERE id=?',(row['id'],))
        self.fetch('v1/admin/purged/ack','POST',{'purged':purged})

    def audit_group(self, request, action, gid):
        monitor=getattr(self.protection,'monitor',None)
        if monitor:monitor.record(request,'admin_action',action='family_group_'+action,outcome='success',details={'group_id':gid})

    def fetch(self, path, method='GET', data=None):
        try:
            response=self.control_request(method,path,data=data)
            if response.status_code>=400:
                if response.status_code in {401,403}:self.fail(503,localized_message('failedToConnectToTheFamilyPhotoService'))
                reasons={'already_connected':'Группа уже подключена.', 'cleanup_pending':'Дождитесь завершения очистки группы на сайте.',
                    'refresh_discovery':'Обновите список доступных групп Telegram.', 'cancellation_expired':'Срок отмены удаления истёк.',
                    'deletion_pending':'Сначала отмените удаление группы.', 'no_photos':'Нет загруженных фотографий для выгрузки.',
                    'queue_full':'Очередь подготовки заполнена. Дождитесь завершения текущих заданий.',
                    'exports_full':'Достигнут лимит готовых архивов. Удалите ненужную выгрузку.',
                    'request_conflict':'Этот запрос уже использован для другой выгрузки.',
                    'copy_of_copy':'Нельзя создавать копию существующей копии группы.'}
                try:reason=reasons.get(response.json().get('detail'))
                except (ValueError,AttributeError,TypeError):reason=None
                self.fail(response.status_code if response.status_code<500 else 503,
                    reason or ('Данные недоступны.' if response.status_code==404 else 'Не удалось выполнить запрос к семейному архиву.'))
            return response.json()
        except (httpx.HTTPError,ValueError):self.fail(503,localized_message('theFamilyPhotoServiceIsTemporarilyUnavailable'))

    def routes(self):
        router=Router()

        @router.get('/api/family-photos/status')
        def status(request:Request):
            self.ready(request)
            return self.status()

        @router.get('/api/family-photos/unread')
        def unread(request:Request):
            s=self.gate(request,chat=True)
            return {'unread':sum(self.family_counts(s).values())}

        @router.get('/api/family-photos/groups')
        def list_groups(request:Request):
            s=self.gate(request)
            data=self.fetch('v1/groups')
            data['items']=[g for g in data['items'] if self.allowed(s,g['id'])]
            # Older archive versions remain readable during a rolling update.
            counts=self.family_counts(s,data['items']) if all('latest_message_id' in g for g in data['items']) else {}
            for group in data['items']:
                self.filter_connection(s,group.get('connection'))
                if group.get('can_send') and not self.write_allowed(s,group['id']):group['bot_reason']='not_allowed'
                group['can_send']=bool(self.status().get('bot_messaging') and group.get('can_send') and self.write_allowed(s,group['id']))
                group['unread']=counts.get(group['id'],0)
                from app.chat_notifications import mute_until
                group['mute_until']=mute_until(json.loads(s['settings']),'telegram:'+group['id'],int(time.time()))
                if group.get('has_avatar'):
                    from urllib.parse import urlencode
                    group['avatar_url']=f'/api/family-photos/groups/{group["id"]}/avatar?'+urlencode({'v':group['avatar_version']})
            data['status']=self.status()
            return data

        @router.get('/api/family-photos/groups/{gid}/sync')
        def sync_progress(gid:str,request:Request):
            s=self.gate(request,gid)
            data=self.fetch(f'v1/groups/{gid}/sync')
            self.filter_connection(s,data.get('connection'))
            if s['role']!='owner':data.pop('cleanup',None)
            return data

        @router.post('/api/family-photos/groups/{gid}/read')
        def mark_read(gid:str,data:ReadReceipt,request:Request):
            s=self.gate(request,gid,chat=True)
            self.fetch('v1/groups/'+gid+'/messages/'+str(data.message_id))
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                row=c.execute('SELECT role,settings FROM users WHERE id=?',(s['user_id'],)).fetchone()
                if not row:self.fail(401,localized_message('sessionUnavailable'))
                from .family_photo_events import has_access
                settings=json.loads(row['settings'])
                if not has_access(settings,row['role'],gid):self.fail(403,localized_message('accessToTheFamilyConversationIsClosed'))
                reads=read_map(settings)
                reads[gid]=max(reads.get(gid,0),data.message_id)
                settings['family_photo_reads']=reads
                c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
                for notice in c.execute("SELECT id,payload FROM notifications WHERE user_id=? AND kind='family_message' AND read=0",(s['user_id'],)).fetchall():
                    payload=json.loads(notice['payload'])
                    if payload.get('family_group_id')==gid and payload.get('message_id',0)<=data.message_id:
                        c.execute('UPDATE notifications SET read=1 WHERE id=?',(notice['id'],))
            return {'ok':True}

        def pagination(request,window=False):
            args={}
            for key in ('before','limit')+(('until',) if window else ()):
                value=request.args.get(key)
                if value is not None:
                    if not value.isdigit() or len(value)>16:self.fail(422,localized_message('invalidPage'))
                    args[key]=value
                    if key=='until' and int(value)>253402300799:self.fail(422,localized_message('invalidPage'))
            from urllib.parse import urlencode
            return '?'+urlencode(args) if args else ''

        def rewrite(data,gid,session=None):
            def file(item):
                location='site-uploads' if item.get('site_upload') else 'media'
                item['url']=f'/api/family-photos/groups/{gid}/{location}/{item["id"]}'
                if item.get('kind')=='sticker' and item.get('mime')=='application/x-tgsticker':item['animation_url']=item['url']+'?animation=1'
                if item.get('has_thumbnail'):item['thumbnail_url']=item['url']+'?thumbnail=1'
                if item.get('has_audio_preview'):item['playback_url']=item['url']+'?audio=1'
            from urllib.parse import urlencode
            def person(person):
                if person.get('has_avatar') and re.fullmatch(r'-?\d{1,20}',str(person.get('id',''))):
                    person['avatar_url']=f'/api/family-photos/groups/{gid}/people/{person["id"]}/avatar?'+urlencode({'v':person.get('avatar_version','')})
            def emoji(entry):
                if entry.get('state')=='ready' and re.fullmatch(r'\d{1,20}',str(entry.get('document_id',''))):
                    entry['url']=f'/api/family-photos/groups/{gid}/emoji/{entry["document_id"]}'
                    if entry.get('mime')=='application/x-tgsticker':entry['animation_url']=entry['url']+'?animation=1'
                    if entry.get('has_thumbnail'):entry['thumbnail_url']=entry['url']+'?thumbnail=1'
                    if entry.get('has_audio_preview'):entry['playback_url']=entry['url']+'?audio=1'
            from contextlib import nullcontext
            with (self.db() if session else nullcontext()) as reaction_db:
                for item in data.get('items',[]):
                    if 'media' in item:
                        for attachment in item['media']:file(attachment)
                        if item.get('sender_has_avatar') and re.fullmatch(r'-?\d{1,20}',str(item.get('sender_id',''))):
                            item['sender_avatar_url']=f'/api/family-photos/groups/{gid}/people/{item["sender_id"]}/avatar?'+urlencode({'v':item.get('sender_avatar_version','')})
                        for entry in item.get('custom_emojis',[]):emoji(entry)
                        for reaction in item.get('reactions',[]):
                            reaction['source']='telegram'
                            for reactor in reaction.get('people',[]):person(reactor)
                            if reaction.get('custom_emoji'):
                                entry=reaction['custom_emoji'];entry['document_id']=reaction['document_id'];emoji(entry)
                        if session:
                            site_actor=item.get('site_actor_id')
                            if isinstance(site_actor,str) and re.fullmatch('[a-f0-9]{32}',site_actor):
                                item['site_profile']=actor(reaction_db,site_actor,int(time.time()))
                            from app.reactions import payload
                            item['site_reactions']=payload(reaction_db,'telegram:'+gid,item['id'],int(time.time()))
                    else:file(item)
            return data

        @router.get('/api/family-photos/groups/{gid}/media')
        def gallery(gid:str,request:Request):
            self.gate(request,gid)
            return rewrite(self.fetch(f'v1/groups/{gid}/media'+pagination(request,True)),gid)

        @router.get('/api/family-photos/groups/{gid}/media-index')
        def gallery_index(gid:str,request:Request):
            # The archive validates source state before reading its index cache.
            # Keep account/module grants here without fetching the full catalog.
            self.gate(request,gid,source=False)
            from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
            from urllib.parse import urlencode
            zone=request.args.get('timezone','UTC')
            try:
                if len(zone)>64:raise ValueError()
                ZoneInfo(zone)
            except (ValueError,ZoneInfoNotFoundError):self.fail(422,localized_message('invalidPage'))
            return self.fetch(f'v1/groups/{gid}/media-index?'+urlencode({'timezone':zone}))

        @router.get('/api/family-photos/groups/{gid}/media-version')
        def gallery_version(gid:str,request:Request):
            self.gate(request,gid)
            through=request.args.get('through','0')
            if not through.isdigit() or len(through)>16 or int(through)>2**53-1:
                self.fail(422,localized_message('invalidPage'))
            until=request.args.get('until')
            if until is not None and (not until.isdigit() or len(until)>12 or int(until)>253402300799):
                self.fail(422,localized_message('invalidPage'))
            return self.fetch(f'v1/groups/{gid}/media-version?through='+through+('&until='+until if until else ''))

        @router.get('/api/family-photos/groups/{gid}/messages')
        def history(gid:str,request:Request):
            session=self.gate(request,gid,chat=True)
            return rewrite(self.fetch(f'v1/groups/{gid}/messages'+pagination(request)),gid,session)

        @router.get('/api/family-photos/groups/{gid}/messages/{mid}')
        def message(gid:str,mid:str,request:Request):
            session=self.gate(request,gid,chat=True)
            if not mid.isdigit() or len(mid)>16:self.fail(404,localized_message('messageNotFound'))
            data=self.fetch(f'v1/groups/{gid}/messages/{mid}')
            rewrite({'items':[data]},gid,session)
            return data

        from app.reactions import Reaction
        @router.post('/api/family-photos/groups/{gid}/messages/{mid}/reactions')
        def react(gid:str,mid:str,data:Reaction,request:Request):
            session=self.gate(request,gid,chat=True)
            if not mid.isdigit() or len(mid)>16 or not 0<int(mid)<=2**53-1:self.fail(404,localized_message('messageNotFound'))
            group=next((g for g in self.fetch('v1/groups').get('items',[]) if g.get('id')==gid),None)
            if not group or group.get('kind')=='export' or group.get('can_send') is False or not self.status().get('can_send'):
                self.fail(403,localized_message('thisGroupIsOnlyForReading'))
            if not self.write_allowed(session,gid):self.fail(403,localized_message('theOwnerHasNotAllowedSendingToThis'))
            self.protection.rate(request,'family-photo-reaction',60,subject=session['user_id'])
            self.fetch(f'v1/groups/{gid}/messages/{mid}')
            from .family_photo_events import has_access
            from app.modules.preferences import module_enabled
            from app.reactions import toggle,read
            mirror=self.status().get('bot_reactions')
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                current=c.execute('SELECT role,settings FROM users WHERE id=?',(session['user_id'],)).fetchone()
                settings=json.loads(current['settings']) if current else {}
                if not current or not has_access(settings,current['role'],gid) or not module_enabled(settings,'chat'):self.fail(403,localized_message('accessToTheConversationIsClosed'))
                if current['role']!='owner' and gid not in settings.get('family_photo_access',{}).get('write_groups',[]):self.fail(403,localized_message('sendingIsNoLongerAllowed'))
                revision=max(time.time_ns()//1000,read(c,'telegram:'+gid,int(mid)).get('revision',0)+1) if mirror else None
                return {'reactions':toggle(c,'telegram:'+gid,int(mid),session['user_id'],data.emoji,int(time.time()),self.fail,revision=revision),'bot_reaction_sync':{'state':'queued'} if mirror else None}

        @router.post('/api/family-photos/groups/{gid}/messages')
        def send(gid:str,data:Message,request:Request):
            result=self.send_site_message(gid,data,request)
            result['site_actor_id']=result.get('actor_id')
            return rewrite({'items':[result]},gid,self.gate(request,gid,chat=True))['items'][0]

        @router.get('/api/family-photos/groups/{gid}/outbox/{oid}')
        def delivery(gid:str,oid:str,request:Request):
            s=self.gate(request,gid,chat=True)
            if not re.fullmatch(r'[a-f0-9]{32}',oid):self.fail(404,localized_message('messageNotFound'))
            data=self.fetch(f'v1/groups/{gid}/outbox/{oid}')
            if data.get('actor_id')!=s['user_id']:self.fail(404,localized_message('messageNotFound'))
            return data

        @router.get('/api/family-photos/groups/{gid}/outbox')
        def pending(gid:str,request:Request):
            s=self.gate(request,gid,chat=True)
            from urllib.parse import urlencode
            return self.fetch(f'v1/groups/{gid}/outbox?'+urlencode({'actor_id':s['user_id']}))

        @router.get('/api/family-photos/groups/{gid}/avatar')
        def avatar(gid:str,request:Request):
            self.gate(request,gid)
            from urllib.parse import urlencode
            version=request.args.get('v','')
            if len(version)>80:self.fail(404,localized_message('avatarNotFound'))
            client=self.client(timeout=30)
            try:
                response=client.send(client.build_request('GET',f'v1/groups/{gid}/avatar?'+urlencode({'v':version})),stream=True)
            except httpx.HTTPError:
                client.close();self.fail(503,localized_message('theFamilyPhotoServiceIsTemporarilyUnavailable'))
            if response.status_code!=200:
                code=response.status_code;response.close();client.close();self.fail(404 if code==404 else 503,localized_message('avatarIsUnavailable'))
            def content():
                try:
                    for chunk in response.iter_bytes(256*1024):
                        self.gate(request,gid);yield chunk
                finally:response.close();client.close()
            return StreamingResponse(content(),media_type='image/jpeg',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

        def detail_asset(gid,path,request,animation=False):
            self.gate(request,gid)
            if animation:return self.fetch(path)
            try:
                with self.client(timeout=15) as client:
                    with client.stream('GET',path) as response:
                        if response.status_code!=200:self.fail(404 if response.status_code==404 else 503,localized_message('imageIsNotAvailable'))
                        mime=response.headers.get('content-type','').split(';')[0]
                        if mime not in {'image/jpeg','image/webp','image/png','video/webm','application/x-tgsticker'}:self.fail(503,localized_message('invalidImage'))
                        body=bytearray()
                        for chunk in response.iter_bytes(65536):
                            body.extend(chunk)
                            if len(body)>8*1024*1024:self.fail(503,localized_message('imageIsTooLarge'))
                self.gate(request,gid)
                return Response(bytes(body),media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
            except httpx.HTTPError:self.fail(503,localized_message('familyArchiveIsNotAccessible'))

        @router.get('/api/family-photos/groups/{gid}/people/{sid}/avatar')
        def person_avatar(gid:str,sid:str,request:Request):
            if not re.fullmatch(r'-?\d{1,20}',sid):self.fail(404,localized_message('authorNotFound'))
            from urllib.parse import urlencode
            version=request.args.get('v','')
            if len(version)>80:self.fail(404,localized_message('avatarNotFound'))
            return detail_asset(gid,f'v1/groups/{gid}/people/{sid}/avatar?'+urlencode({'v':version}),request)

        @router.get('/api/family-photos/groups/{gid}/emoji/{eid}')
        def emoji_asset(gid:str,eid:str,request:Request):
            if not re.fullmatch(r'\d{1,20}',eid):self.fail(404,localized_message('emojiNotFound'))
            animation=request.args.get('animation')=='1';query='?animation=1' if animation else '?thumbnail=1' if request.args.get('thumbnail')=='1' else ''
            return detail_asset(gid,f'v1/groups/{gid}/emoji/{eid}'+query,request,animation)

        @router.get('/api/family-photos/groups/{gid}/media/{fid}')
        def download(gid:str,fid:str,request:Request):
            self.gate(request,gid)
            if not re.fullmatch(r'[a-f0-9]{32}',fid):self.fail(404,localized_message('fileNotFound'))
            query='?thumbnail=1' if request.args.get('thumbnail')=='1' else ''
            if request.args.get('animation')=='1':query='?animation=1'
            if request.args.get('audio')=='1':query='?audio=1'
            if request.args.get('download')=='1':query+=('&' if query else '?')+'download=1'
            client=self.client(timeout=30)
            try:
                headers={}
                if value:=request.headers.get('Range'):headers['Range']=value
                response=client.send(client.build_request('GET',f'v1/groups/{gid}/media/{fid}'+query,headers=headers),stream=True)
            except httpx.HTTPError:
                client.close();self.fail(503,localized_message('theFamilyPhotoServiceIsTemporarilyUnavailable'))
            if response.status_code not in {200,206,416}:
                code=response.status_code;response.close();client.close()
                self.fail(404 if code==404 else 503,localized_message('fileUnavailable'))
            headers={k:v for k,v in response.headers.items() if k.lower() in {
                'content-length','content-range','accept-ranges','content-disposition'}}
            headers|={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                      'Content-Security-Policy':"sandbox; default-src 'none'"}
            def content():
                try:
                    for chunk in response.iter_bytes(256*1024):
                        self.gate(request,gid)
                        yield chunk
                finally:response.close();client.close()
            return StreamingResponse(content(),status_code=response.status_code,headers=headers,
                media_type=response.headers.get('content-type','application/octet-stream'))

        def export_session(request,write=False):
            self.management(request)
            return self.admin_access.proof(request,None) if write else self.admin_access.require(request)

        def export_groups(gids):
            active={group['id'] for group in self.fetch('v1/groups')['items']}
            if any(gid not in active for gid in gids):self.fail(404,localized_message('theGroupIsDisabledOrAwaitingDeletion'))

        def job_gate(request,jid):
            session=export_session(request)
            if not re.fullmatch(r'[a-f0-9]{32}',jid):self.fail(404,localized_message('archiveNotFound'))
            data=self.fetch(f'v1/exports/{jid}?actor_id='+session['user_id'])
            export_groups(data['groups'])
            return session,data

        @router.get('/api/family-photos/admin/exports')
        @router.get('/api/family-photos/exports')
        def exports(request:Request):
            session=export_session(request)
            data=self.fetch('v1/exports?actor_id='+session['user_id'])
            for item in data['items']:item['download_url']=f"/api/family-photos/admin/exports/{item['id']}/download"
            return data

        @router.post('/api/family-photos/admin/exports')
        @router.post('/api/family-photos/exports')
        def prepare_export(data:ExportRequest,request:Request):
            session=export_session(request,True)
            if not self.status().get('photo_exports'):self.fail(409,localized_message('updateFamilyPhotoToPrepareArchives'))
            if len(set(data.groups))!=len(data.groups):self.fail(422,localized_message('groupsShouldNotBeDuplicated'))
            export_groups(data.groups)
            self.protection.rate(request,'family-photo-export',10,subject=session['user_id'])
            return self.fetch('v1/exports?actor_id='+session['user_id'],'POST',data.model_dump())

        @router.post('/api/family-photos/admin/exports/{jid}/cancel')
        @router.post('/api/family-photos/exports/{jid}/cancel')
        def cancel_export(jid:str,request:Request):
            session=export_session(request,True)
            if not re.fullmatch(r'[a-f0-9]{32}',jid):self.fail(404,localized_message('archiveNotFound'))
            return self.fetch(f'v1/exports/{jid}/cancel?actor_id='+session['user_id'],'POST',{})

        @router.get('/api/family-photos/admin/exports/{jid}/download')
        @router.get('/api/family-photos/exports/{jid}/download')
        def download_export(jid:str,request:Request):
            session,job=job_gate(request,jid)
            if job['state']!='ready' or job['expires']<=time.time():self.fail(410,localized_message('theDownloadDeadlineHasExpiredOrTheArchive'))
            client=self.client(timeout=60)
            try:
                headers={key:request.headers[key] for key in ('Range',) if request.headers.get(key)}
                response=client.send(client.build_request('GET',f'v1/exports/{jid}/download?actor_id='+session['user_id'],headers=headers),stream=True)
            except httpx.HTTPError:
                client.close();self.fail(503,localized_message('archiveIsTemporarilyUnavailable'))
            if response.status_code not in {200,206,416}:
                code=response.status_code;response.close();client.close();self.fail(code if code in {404,410} else 503,localized_message('archiveUnavailable'))
            headers={k:v for k,v in response.headers.items() if k.lower() in {'content-length','content-range','accept-ranges','content-disposition','etag'}}
            headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
            def content():
                checked=0
                try:
                    for chunk in response.iter_bytes(256*1024):
                        if job['expires']<=time.time():return
                        if time.monotonic()-checked>=1:
                            export_session(request)
                            export_groups(job['groups'])
                            checked=time.monotonic()
                        yield chunk
                finally:response.close();client.close()
            return StreamingResponse(content(),status_code=response.status_code,headers=headers,media_type='application/zip')

        @router.post('/api/family-photos/admin/groups/{gid}/copy')
        @router.post('/api/family-photos/groups/{gid}/copy')
        def copy_group(gid:str,data:CopyRequest,request:Request):
            export_session(request,True)
            if not re.fullmatch(r'(?:-\d{1,20}|export:[a-f0-9]{8,24})',gid):self.fail(404,localized_message('groupNotFound'))
            catalog=self.fetch('v1/admin/groups')['items']
            group=next((g for g in catalog if g['id']==gid),None)
            if not group:self.fail(404,localized_message('groupNotFound'))
            if group.get('copy_source') or group.get('copy_created') is not None:self.fail(409,localized_message('itIsNotPossibleToCreateACopy'))
            if not self.status().get('group_copies'):self.fail(409,localized_message('updateFamilyPhotoToCreateCopies'))
            result=self.fetch(f'v1/admin/groups/{gid}/copy','POST',data.model_dump())
            self.audit_group(request,'copied',gid)
            return result

        @router.get('/api/family-photos/admin/storage')
        def storage(request:Request):
            self.owner(request)
            if not self.configured():return {'configured':False,'available':False}
            data=self.fetch('v1/storage')
            fields=('objects_bytes','database_bytes','total_bytes','media_bytes','thumbnail_bytes',
                    'message_bytes','avatar_bytes','other_bytes','objects','checked_at')
            if not isinstance(data,dict) or any(type(data.get(key)) is not int or data[key]<0 for key in fields):
                self.fail(503,localized_message('familyPhotoStatisticsAreTemporarilyUnavailable'))
            return {'configured':True,'available':True,**{key:data[key] for key in fields}}

        @router.get('/api/family-photos/admin')
        def admin(request:Request):
            session=self.owner(request)
            if not self.configured():self.fail(404,localized_message('theFamilyPhotoServiceIsNotConfigured'))
            status=self.status()
            managed=self.fetch('v1/admin/groups') if status.get('group_management') else None
            if managed:self.cleanup_purged(managed.get('purged',{}))
            items=managed['items'] if managed is not None else self.fetch('v1/groups')['items'] if status['available'] else []
            if status.get('photo_exports'):
                jobs=self.fetch('v1/exports?actor_id='+session['user_id'])['items']
                stamp=int(time.time())
                for group in items:
                    own=[job for job in jobs if job.get('groups')==[group['id']]]
                    ready=[job for job in own if job.get('state')=='ready' and job.get('expires',0)>stamp]
                    preparing=[job for job in own if job.get('state') in {'queued','running'}]
                    if ready:
                        job=max(ready,key=lambda j:j.get('ready_at',j.get('created',0)))
                        group['export_ready']={'id':job['id'],'expires':job['expires'],'download_url':f"/api/family-photos/admin/exports/{job['id']}/download"}
                    if preparing:group['export_preparing']={'id':preparing[0]['id']}
            with self.db() as c:
                users=[]
                for row in c.execute('SELECT id,login,role,settings FROM users ORDER BY login'):
                    value=json.loads(row['settings']).get('family_photo_access',{})
                    enabled=isinstance(value,dict) and value.get('enabled') is True
                    users.append({'id':row['id'],'login':row['login'],'role':row['role'],
                        'profile':actor(c,row['id'],int(time.time())),
                        'enabled':enabled,'groups':value.get('groups',[]) if enabled else [],'write_groups':value.get('write_groups',[]) if enabled else []})
            return {'users':users,'groups':items,'status':status,'server_time':managed['server_time'] if managed else int(time.time())}

        @router.get('/api/family-photos/admin/discovery')
        def discovery(request:Request):
            self.management(request)
            return self.fetch('v1/admin/discovery')

        @router.post('/api/family-photos/admin/discovery')
        def refresh_discovery(request:Request):
            self.management(request)
            self.admin_access.proof(request,None)
            return self.fetch('v1/admin/discovery','POST',{})

        @router.post('/api/family-photos/admin/groups')
        def add_source(data:GroupSource,request:Request):
            self.management(request)
            self.admin_access.proof(request,None)
            result=self.fetch('v1/admin/groups','POST',{'telegram_id':data.telegram_id})
            self.audit_group(request,'added',data.telegram_id)
            return result

        @router.post('/api/family-photos/admin/groups/{gid}/{action}')
        def source_action(gid:str,action:str,data:GroupAction,request:Request):
            self.management(request)
            self.admin_access.proof(request,None)
            if not re.fullmatch(r'(?:-\d{1,20}|export:[a-f0-9]{8,24})',gid):self.fail(404,localized_message('groupNotFound'))
            if action not in {'enable','disable','delete','cancel'}:self.fail(404,localized_message('actionNotFound'))
            if action=='delete' and data.confirmation!=gid:self.fail(422,localized_message('toDeleteEnterTheGroupId'))
            result=self.fetch(f'v1/admin/groups/{gid}/{action}','POST',{})
            self.audit_group(request,action,gid)
            return result

        @router.put('/api/family-photos/admin/users/{uid}')
        def grant(uid:str,data:Grant,request:Request):
            self.owner(request)
            self.admin_access.proof(request,data.admin_password)
            if not re.fullmatch(r'[a-f0-9]{32}',uid):self.fail(404,localized_message('accountNotFound'))
            if not self.configured():self.fail(404,localized_message('theFamilyPhotoServiceIsNotConfigured'))
            # Revocation remains possible during service outages.
            if data.enabled:
                catalog=self.fetch('v1/admin/groups' if self.status().get('group_management') else 'v1/groups')['items']
                known={group['id'] for group in catalog}
                if not data.groups or set(data.groups)-known:self.fail(422,localized_message('selectAvailableGroups'))
                if set(data.write_groups)-set(data.groups):self.fail(422,localized_message('sendingIsOnlyAvailableInGroupsThatAre'))
                writable={group['id'] for group in catalog if group.get('kind')=='supergroup' and not group.get('copy_created')}
                if set(data.write_groups)-writable:self.fail(422,localized_message('copiesAndOfflineArchivesAreOnlyForRead'))
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                row=c.execute('SELECT settings,role FROM users WHERE id=?',(uid,)).fetchone()
                if not row:self.fail(404,localized_message('accountNotFound'))
                if row['role']=='owner':self.fail(422,localized_message('administratorsHaveAccessToAllGroups'))
                settings=json.loads(row['settings'])
                groups=sorted(set(data.groups)) if data.enabled else []
                if data.enabled:
                    settings['family_photo_access']={'enabled':True,'groups':groups,'write_groups':sorted(set(data.write_groups))}
                    settings.setdefault('modules',{})['family_photos']=True
                    previous=read_map(settings)
                    settings['family_photo_reads']={group['id']:previous.get(group['id'],group.get('latest_message_id',group.get('cursor',0))) for group in catalog if group['id'] in groups}
                else:
                    settings.pop('family_photo_access',None)
                    settings.get('modules',{}).pop('family_photos',None)
                    settings.pop('family_photo_reads',None)
                c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
            if self.status().get('bot_messaging'):self.sync_send_permissions()
            return {'ok':True,'enabled':data.enabled,'groups':groups,'write_groups':sorted(set(data.write_groups)) if data.enabled else []}

        self.messaging_routes(router,rewrite)
        return router
