"""Portal permission boundary for authenticated Telegram bot operations."""
from app.i18n import message as localized_message
import asyncio
import json
import re
import tempfile
import time
from urllib.parse import urlencode
import httpx
from pydantic import BaseModel,ConfigDict,Field,StrictInt
from app.http import Request,StreamingResponse,Query
from app.storage import blocking


class TelegramEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(max_length=3500)
    revision:StrictInt=Field(ge=0)
    client_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')


class FamilyMessaging:
    def write_allowed(self,session,gid):
        return self.allowed(session,gid) and (session['role']=='owner' or gid in self.access(session).get('write_groups',[]))

    def sync_send_permissions(self):
        with self.permission_sync_lock:
            self._sync_send_permissions()

    def _sync_send_permissions(self):
        from app.modules.preferences import module_enabled
        from app.account_lifecycle import pending
        actors={}
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            revision=time.time_ns()
            for row in c.execute('SELECT id,role,settings FROM users'):
                settings=json.loads(row['settings'])
                if settings.get('modules',{}).get('family_photos') is not True or not module_enabled(settings,'chat') or pending(c,row['id']):continue
                access=settings.get('family_photo_access',{})
                if row['role']=='owner':actors[row['id']]=['*']
                elif isinstance(access,dict) and access.get('enabled') is True:
                    actors[row['id']]=sorted(set(access.get('write_groups',[]))&set(access.get('groups',[])))
        stamp=time.monotonic()
        confirmed=self.permission_confirmed
        # The archive expires grants after 120 seconds. Refresh before that,
        # while sending changed/revoked grants on the very next check.
        if confirmed and confirmed[0]==actors and stamp-confirmed[1]<60:return
        result=self.bot_fetch('v1/site-permissions','PUT',{'revision':revision,'actors':actors})
        # Old archives do not confirm revisions; keep their original cadence.
        # A superseded snapshot must not suppress the next corrective send.
        if isinstance(result,dict) and result.get('revision')==revision:
            self.permission_confirmed=(actors,stamp)
        else:self.permission_confirmed=None

    def bot_fetch(self,path,method='GET',body=None):
        try:
            response=self.control_request(method,path,data=body,timeout=60)
        except (httpx.HTTPError,ValueError):self.fail(503,localized_message('theTelegramServiceIsTemporarilyUnavailable'))
        return self.bot_response(response)

    def bot_response(self,response):
        reasons={'send_not_allowed':'Отправка в эту группу не разрешена.','read_only':'Эта группа доступна только для чтения.',
            'not_author':'Можно изменять только свои сообщения.','edit_expired':'Сообщение можно изменить в течение 24 часов.',
            'edit_conflict':'Сообщение уже изменено. Откройте редактирование заново.','message_busy':'Предыдущее действие ещё выполняется.',
            'attachment_unavailable':'Вложение недоступно или срок черновика истёк. Прикрепите файл заново.',
            'caption_too_long':'Подпись вложения слишком длинная для Telegram. Сократите текст.',
            'photo_too_large':'Фото превышает 10 МБ. Отправьте его как файл.','file_too_large':'Файл превышает 50 МБ.',
            'invalid_media':'Не удалось прочитать фото или видео. Можно отправить его как файл.',
            'attachment_not_allowed':'У бота нет разрешения отправлять такие вложения.','queue_full':'Очередь отправки заполнена.',
            'draft_limit':'Слишком много черновиков вложений. Удалите ненужные файлы.',
            'client_conflict':'Этот запрос уже относится к другому сообщению.','upload_conflict':'Этот запрос уже относится к другому файлу.',
            'message_not_found':'Сообщение недоступно.','reply_unavailable':'Исходное сообщение для ответа удалено.',
            'upload_in_use':'Вложение уже отправляется.','invalid_message':'Введите сообщение или прикрепите файл.'}
        try:data=response.json()
        except ValueError:self.fail(503,localized_message('invalidTelegramServiceResponse'))
        if response.status_code>=400:
            if response.status_code==401:self.fail(503,localized_message('unableToConnectToTheTelegramService'))
            self.fail(min(response.status_code,503),reasons.get(data.get('detail'),'Не удалось выполнить действие в Telegram.'))
        return data

    def write_gate(self,request,gid):
        session=self.gate(request,gid,chat=True)
        if not self.write_allowed(session,gid):self.fail(403,localized_message('theOwnerHasNotAllowedSendingMessagesTo'))
        if not self.status().get('bot_messaging'):self.fail(409,localized_message('updateFamilyPhotoToSendMessages'))
        group=next((group for group in self.fetch('v1/groups')['items'] if group['id']==gid),None)
        if not group or not group.get('can_send'):self.fail(403,localized_message('thisGroupIsOnlyForReading'))
        self.sync_send_permissions()
        return session

    def send_site_message(self,gid,data,request):
        session=self.write_gate(request,gid)
        self.protection.rate(request,'family-photo-message',30,subject=session['user_id'])
        from app.notifications import actor
        with self.db() as c:person=actor(c,session['user_id'],int(time.time()))
        name=person.get('display_name') or person['login']
        name=''.join(ch if ord(ch)>=32 else ' ' for ch in name).strip()[:100] or session['login']
        return self.bot_fetch(f'v1/groups/{gid}/site-sends?'+urlencode({'actor_id':session['user_id']}),'POST',{'author':name,**data.model_dump()})

    def messaging_routes(self,router,rewrite):
        uploads=asyncio.Semaphore(2)
        @router.put('/api/family-photos/groups/{gid}/site-uploads/{fid}')
        async def upload(gid:str,fid:str,request:Request,name:str=Query(max_length=255),mode:str='file'):
            if not re.fullmatch('[a-f0-9]{32}',fid) or mode not in {'preview','file'}:self.fail(422,localized_message('invalidAttachment'))
            session=await blocking(self.write_gate,request,gid)
            from app.chat_recordings import request_payload
            recording=request_payload(request,self.fail)
            if recording and mode!='preview':self.fail(422,localized_message('invalidChatRecording'))
            self.protection.rate(request,'family-photo-upload',30,subject=session['user_id'])
            maximum=50*1024*1024
            if request.headers.get('Content-Length','').isdigit() and int(request.headers['Content-Length'])>maximum:self.fail(413,localized_message('fileExceeds50Mb'))
            async with uploads:
                with tempfile.TemporaryFile() as stream:
                    size=0
                    async for chunk in request.stream():
                        size+=len(chunk)
                        if size>maximum:self.fail(413,localized_message('fileExceeds50Mb'))
                        await blocking(stream.write,chunk)
                    if not size:self.fail(422,localized_message('fileIsEmpty'))
                    await blocking(stream.seek,0)
                    # Recheck revocation after receiving the body.
                    await blocking(self.write_gate,request,gid)
                    def forward():
                        with self.client(timeout=120) as client:
                            return self.bot_response(client.put(f'v1/groups/{gid}/site-uploads/{fid}?'+urlencode({'actor_id':session['user_id'],'name':name,'mode':mode}),
                                content=iter(lambda:stream.read(1024*1024),b''),headers={'Content-Type':'application/octet-stream','Content-Length':str(size),**({'X-DrG-Recording':recording} if recording else {})}))
                    try:item=await blocking(forward)
                    except httpx.HTTPError:self.fail(503,localized_message('unableToUploadTheFileYouCanTry'))
            rewrite({'items':[item]},gid,session)
            return item

        @router.delete('/api/family-photos/groups/{gid}/site-uploads/{fid}')
        def discard(gid:str,fid:str,request:Request):
            session=self.gate(request,gid,chat=True)
            if not re.fullmatch('[a-f0-9]{32}',fid):self.fail(404,localized_message('attachmentIsUnavailable'))
            return self.bot_fetch(f'v1/groups/{gid}/site-uploads/{fid}?'+urlencode({'actor_id':session['user_id']}),'DELETE')

        @router.get('/api/family-photos/groups/{gid}/site-uploads/{fid}')
        def download(gid:str,fid:str,request:Request):
            session=self.gate(request,gid,chat=True)
            if not re.fullmatch('[a-f0-9]{32}',fid):self.fail(404,localized_message('attachmentIsUnavailable'))
            args={'actor_id':session['user_id']}
            if request.args.get('thumbnail')=='1':args['thumbnail']='1'
            if request.args.get('download')=='1':args['download']='1'
            client=self.client(timeout=60)
            try:
                response=client.send(client.build_request('GET',f'v1/groups/{gid}/site-uploads/{fid}?'+urlencode(args),headers={'Range':request.headers['Range']} if request.headers.get('Range') else {}),stream=True)
                if response.status_code>=400 and response.status_code!=416:
                    response.read();self.bot_response(response)
            except BaseException:client.close();raise
            headers={k:v for k,v in response.headers.items() if k.lower() in {'content-type','content-disposition','content-length','content-range','accept-ranges'}}
            headers.update({'Cache-Control':'no-store','Content-Security-Policy':"sandbox; default-src 'none'",'X-Content-Type-Options':'nosniff'})
            def content():
                try:yield from response.iter_bytes(1024*1024)
                finally:response.close();client.close()
            return StreamingResponse(content(),status_code=response.status_code,headers=headers)

        @router.get('/api/family-photos/groups/{gid}/site-sends')
        def pending(gid:str,request:Request):
            session=self.gate(request,gid,chat=True)
            data=self.bot_fetch(f'v1/groups/{gid}/site-sends?'+urlencode({'actor_id':session['user_id']}))
            for item in data['items']:item['site_actor_id']=item['actor_id']
            return rewrite(data,gid,session)

        @router.post('/api/family-photos/groups/{gid}/site-sends/{jid}/cancel')
        def cancel(gid:str,jid:str,request:Request):
            session=self.gate(request,gid,chat=True)
            if not re.fullmatch('[a-f0-9]{32}',jid):self.fail(404,localized_message('theMessageIsNotAvailable'))
            return self.bot_fetch(f'v1/groups/{gid}/site-sends/{jid}/cancel?'+urlencode({'actor_id':session['user_id']}),'POST',{})

        @router.put('/api/family-photos/groups/{gid}/messages/{mid}')
        def edit(gid:str,mid:str,data:TelegramEdit,request:Request):
            session=self.write_gate(request,gid)
            if not mid.isdigit() or not 0<int(mid)<=2**53-1:self.fail(404,localized_message('theMessageIsNotAvailable'))
            self.protection.rate(request,'family-photo-edit',30,subject=session['user_id'])
            return self.bot_fetch(f'v1/groups/{gid}/site-messages/{mid}?'+urlencode({'actor_id':session['user_id']}),'PUT',data.model_dump())
