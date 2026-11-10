"""Optional real-container persistence check: python tests/docker_social_smoke.py.
Uses disposable containers/volumes; never reads the project's .env.
"""
import base64,json,os,secrets,subprocess,tempfile,time
from pathlib import Path
import httpx,pyotp
from pycrdt import Doc,Map,XmlFragment,XmlElement,XmlText
from websockets.sync.client import connect

def main():
    # Compose must only use disposable runtime.env credentials. An exported
    # DATABASE_URL, FILE_STORAGE or AWS key from the developer's shell must not
    # redirect this check to a live database or bucket.
    inherited={'PATH','HOME','USER','LOGNAME','TMPDIR','LANG','LC_ALL','LC_CTYPE',
               'DOCKER_CONFIG','XDG_RUNTIME_DIR','SYSTEMROOT','GUNICORN_WORKERS','GUNICORN_THREADS'}
    env={k:v for k,v in os.environ.items() if k in inherited}
    docker=['docker','--host=unix:///var/run/docker.sock'];suffix=secrets.token_hex(5)
    name='drg-social-smoke-'+suffix;volume=name+'-data';url='http://127.0.0.1:8010';password='Container-owner-password-123';backup_password='Container-backup-password-123'
    image=os.getenv('DRG_DOCKER_TEST_IMAGE','drg-site-image')
    postgres=os.getenv('DRG_DOCKER_TEST_POSTGRES')=='true';compose=None
    redactions=[]
    def command(*args):
        result=subprocess.run(docker+list(args),env=env,capture_output=True,text=True)
        if result.returncode:
            message=result.stderr
            if compose:
                diagnostics=subprocess.run(docker+compose+['logs','--tail','30','postgres'],env=env,capture_output=True,text=True)
                message+='\n'+diagnostics.stdout
            for value in redactions:message=message.replace(value,'[redacted]')
            raise RuntimeError('Docker command failed: '+message)
        return result.stdout.strip()
    def healthy():
        ready_since=None
        for _ in range(80):
            try:
                # /health bypasses the storage gate. A protected API returning
                # 401 without cookies also proves the gate is available; wait
                # for a stable interval while sibling workers initialize.
                if httpx.get(url+'/api/me',trust_env=False,timeout=2).status_code==401:
                    if ready_since is None:ready_since=time.monotonic()
                    if time.monotonic()-ready_since>=1:return
                else:ready_since=None
            except httpx.HTTPError:ready_since=None
            time.sleep(.25)
        details=command(*compose,'logs','--tail','30') if compose else command('logs','--tail','30',name)
        for value in redactions:details=details.replace(value,'[redacted]')
        raise AssertionError('Container did not become ready: '+details)
    def call(c,path,method='GET',data=None,**kwargs):
        if method!='GET':c.headers['x-csrf-token']=c.cookies.get('drg_csrf','')
        deadline=time.monotonic()+15
        while True:
            response=c.request(method,'/api'+path,**({'json':data} if data is not None else {}),**kwargs)
            # Startup can briefly reacquire the global gate in another worker.
            # Only replay reads rejected by that gate, never mutations or an
            # arbitrary failing response. Persistent unavailability still fails.
            if method!='GET' or response.status_code!=503 or response.headers.get('retry-after')!='5' or time.monotonic()>=deadline:break
            time.sleep(.25)
        assert response.is_success,f'{path}: HTTP {response.status_code}'
        return response
    def enroll(c,pw):
        secret=call(c,'/2fa/enroll','POST',{'password':pw}).json()['secret']
        codes=call(c,'/2fa/confirm','POST',{'code':pyotp.TOTP(secret).now()}).json()['recovery_codes']
        call(c,'/2fa/activate','POST',{'saved_recovery_codes':True});return codes
    def synced(c,nid,initialize=False):
        cookie='; '.join(f'{k}={v}' for k,v in c.cookies.items())
        with connect(url.replace('http','ws')+'/api/live/notes/'+nid,origin=url,additional_headers={'Cookie':cookie}) as ws:
            ws.send(json.dumps({'type':'auth','csrf':c.cookies.get('drg_csrf'),'client_id':123}))
            event=json.loads(ws.recv(timeout=5))
            if initialize:
                assert event['type']=='init'
                text=XmlText('Перезапуск сохраняет совместный документ')
                block=XmlElement('blockContainer',{'id':'smoke-paragraph'},[XmlElement('paragraph',{},[text])])
                doc=Doc({'prosemirror':XmlFragment([XmlElement('blockGroup',{},[block])]),'meta':Map({'title':'Планы семьи'})})
                ws.send(json.dumps({'type':'init','version':event['page']['version'],'update':base64.b64encode(doc.get_update()).decode()}))
                event=json.loads(ws.recv(timeout=5))
            assert event['type']=='sync';return event['epoch']
    def chat_preserved(c,sid,original,reply):
        items=call(c,f'/chat/{sid}/messages').json()['items']
        assert len(items)==2 and [item['id'] for item in items]==[original['id'],reply['id']]
        assert items[0]['body']==original['body'] and items[0]['reply'] is None
        assert items[1]['body']==reply['body']
        assert items[1]['reply']=={'id':original['id'],'sender':'friend','body':original['body'][:240],'unavailable':False}
        room=next(item for item in call(c,'/chat/rooms').json()['items'] if item['id']==sid)
        assert room['favorite'] is True and room['pinned'] is True
        assert room['last_message']['id']==reply['id'] and room['last_message']['reply']==items[1]['reply']
    with tempfile.TemporaryDirectory(prefix='drg-docker-smoke-') as directory:
        try:
            file=Path(directory)/'runtime.env';setup=secrets.token_urlsafe(24)
            file.write_text('APP_SECRET='+secrets.token_urlsafe(48)+'\nSETUP_TOKEN='+setup+'\nDATA_ENCRYPTION_KEY='+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()+'\nPUBLIC_URL='+url+'\n');file.chmod(0o600)
            if postgres:
                with file.open('a') as config:
                    config.write('POSTGRES_PASSWORD='+secrets.token_urlsafe(32)+'\nPOSTGRES_ADMIN_PASSWORD='+secrets.token_urlsafe(32)+'\n')
                redactions.extend(line.split('=',1)[1] for line in file.read_text().splitlines())
                override=Path(directory)/'override.yaml'
                override.write_text(f'services:\n  app:\n    image: {image}\n    container_name: {name}\n    ports: !override\n      - "127.0.0.1:8010:8000"\n')
                compose=['compose','--project-name',name,'--project-directory',str(Path.cwd()),'--env-file',str(file),
                         '-f','compose.yaml','-f','compose.postgres.yaml','-f',str(override)]
                command(*compose,'up','--detach','--no-build')
                name=command(*compose,'ps','--quiet','app')
                database_container=command(*compose,'ps','--quiet','postgres')
                flags=command('exec',database_container,'psql','-X','-U','postgres','-d','drg','-Atc',
                              "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname='drg'")
                assert flags=='f|f|f|f',repr(flags)
                configured=json.loads(command('inspect',name))[0]['Config']['Env']
                assert not any(v.startswith(('POSTGRES_ADMIN_PASSWORD=','POSTGRES_PASSWORD=')) for v in configured)
            else:
                command('volume','create',volume)
                command('run','--detach','--name',name,'--env-file',str(file),'--publish','127.0.0.1:8010:8000','--volume',volume+':/data',image)
            healthy()
            with httpx.Client(base_url=url,trust_env=False,headers={'Origin':url},timeout=30) as owner,httpx.Client(base_url=url,trust_env=False,headers={'Origin':url},timeout=30) as friend:
                call(owner,'/setup','POST',{'login':'owner','password':password,'token':setup});codes=enroll(owner,password)
                invite=call(owner,'/invites','POST',{'login':'friend'}).json()['url'].rsplit('/',1)[1]
                call(friend,'/register','POST',{'login':'friend','password':password,'invite':invite});enroll(friend,password)
                sid=call(owner,'/social/families','POST',{'name':'Контейнерная семья'}).json()['id']
                request=call(owner,f'/social/families/{sid}/invites','POST',{'login':'friend'}).json()['id'];call(friend,f'/social/requests/{request}/accept','POST')
                nid=call(owner,'/notes','POST',{'title':'Планы семьи','space_id':sid}).json()['id'];epoch=synced(owner,nid,True)
                call(owner,'/modules','PUT',{'enabled':{'chat':True}});call(friend,'/modules','PUT',{'enabled':{'chat':True}})
                original_message=call(friend,f'/chat/{sid}/messages','POST',{'body':'Переписка остаётся после перезапуска','client_id':'container-smoke-message'}).json()
                reply_message=call(owner,f'/chat/{sid}/messages','POST',{'body':'Ответ сохраняет связь с исходным сообщением','client_id':'container-smoke-reply','reply_to':original_message['id']}).json()
                assert call(owner,f'/chat/{sid}/preferences','PUT',{'favorite':True,'pinned':True}).json()=={'favorite':True,'pinned':True}
                chat_preserved(owner,sid,original_message,reply_message)
                assert all(call(owner,'/modules').json()['enabled'].values())
                call(owner,'/modules','PUT',{'enabled':{'clock':True}})
                clocks=call(owner,'/dashboard/layout').json()
                clocks['instances'] += [{'id':'moscow_clock','module_id':'clock','config':{'timezone':'Europe/Moscow','label':'MSK','show_seconds':False}},{'id':'vlad_clock','module_id':'clock','config':{'mode':'digital','timezone':'Asia/Vladivostok','label':'VL','date_format':'yyyy-mm-dd'}}]
                clocks=call(owner,'/dashboard/layout','PUT',clocks).json()
                database=call(owner,'/databases','POST',{'title':'Проекты семьи','space_id':sid}).json()['id']
                record=call(owner,f'/databases/{database}/records','POST',{'title':'Контейнерный проект','properties':{'status':'В работе','date':{'start':'2026-10-11','end':'2026-10-14'}}}).json()['id']
                friend_id=call(friend,'/me').json()['id']
                call(owner,f'/notes/{record}/permissions','PUT',{'user_id':friend_id,'role':'commenter'})
                call(friend,f'/notes/{record}/comments','POST',{'body':'@owner Проверим перенос базы'})
                command('restart',name);healthy()
                assert call(friend,f'/databases/{database}').json()['records'][0]['properties']['status']=='В работе'
                assert call(friend,f'/notes/{record}').json()['role']=='commenter'
                assert call(owner,f'/notes/{record}/comments').json()[0]['body']=='@owner Проверим перенос базы'
                assert call(owner,'/dashboard/layout').json()==clocks
                assert synced(friend,nid)==epoch
                assert 'совместный документ' in call(friend,'/notes/'+nid).json()['body']
                chat_preserved(owner,sid,original_message,reply_message)
                private=call(owner,'/notes','POST',{'title':'Личная переносимая заметка','body':'Только мой текст'}).json()['id']
                private_database=call(owner,'/databases','POST',{'title':'Личная переносимая база'}).json()['id']
                call(owner,f'/databases/{private_database}/records','POST',{'title':'Личная запись','properties':{'status':'Готово'}})
                call(owner,'/tasks','POST',{'title':'Моя переносимая задача','day':'2026-10-11'})
                personal=call(owner,'/account/data/export','POST',{'password':password,'archive_password':backup_password}).content
                personal_headers={'x-account-password-b64':base64.b64encode(password.encode()).decode(),'x-archive-password-b64':base64.b64encode(backup_password.encode()).decode(),'x-import-confirmation':'IMPORT','x-import-settings':'false'}
                preview=call(friend,'/account/data/preview','POST',content=personal,headers=personal_headers).json()
                assert preview['summary']['notes']==6 and preview['summary']['databases']==2 and preview['summary']['shared_notes']==3
                original_friend=call(friend,'/me').json()
                call(friend,'/account/data/import','POST',content=personal,headers=personal_headers)
                assert call(friend,'/me').json()['role']==original_friend['role']=='member'
                assert call(friend,'/me').json()['two_factor']==original_friend['two_factor']
                copied=next(n for n in call(friend,'/notes').json() if n['title']=='Личная переносимая заметка')
                assert copied['id']!=private and copied['space_id'] is None
                assert friend.get('/api/notes/'+private).status_code==404
                command('restart',name);healthy()
                assert call(friend,'/notes/'+copied['id']).json()['body']=='Только мой текст'
                published=call(owner,f'/notes/{private}/shares','POST',{'mode':'public','permission':'edit','duration':13,'unit':'minutes'}).json()['token']
                public=call(owner,'/shared/'+published).json()
                call(owner,'/shared/'+published,'PUT',{'title':public['title'],'body':'Разрешённая правка через ссылку','blocks':[{'id':'link-smoke-block','type':'paragraph','content':'Разрешённая правка через ссылку','children':[]}],'version':public['version']},headers={'x-share-edit-token':public['edit_token']})
                backup=call(owner,'/admin/backups/export','POST',{'admin_password':password,'backup_password':backup_password}).content
                call(owner,f'/chat/{sid}/messages','POST',{'body':'После снимка','client_id':'later-container-message'})
                assert call(owner,f'/chat/{sid}/preferences','PUT',{'favorite':False,'pinned':False}).json()=={'favorite':False,'pinned':False}
                headers={'x-admin-password-b64':base64.b64encode(password.encode()).decode(),'x-backup-password-b64':base64.b64encode(backup_password.encode()).decode(),'x-restore-confirmation':'RESTORE'}
                call(owner,'/admin/backups/restore','POST',content=backup,headers=headers)
                assert friend.get('/api/notes/'+nid).status_code==401
                call(owner,'/login','POST',{'login':'owner','password':password});call(owner,'/2fa/verify','POST',{'code':codes[0]})
                assert call(owner,'/dashboard/layout').json()==clocks
                assert call(owner,'/shared/'+published).json()['permission']=='edit'
                assert call(owner,'/shared/'+published).json()['body']=='Разрешённая правка через ссылку'
                assert synced(owner,nid)==epoch
                chat_preserved(owner,sid,original_message,reply_message)
                assert call(owner,f'/databases/{database}').json()['records'][0]['note_id']==record
                assert call(owner,f'/notes/{record}/comments').json()[0]['body']=='@owner Проверим перенос базы'
                print('Docker: isolated personal export/preview/import creates private copies with fresh IDs and preserves member role/2FA across restart; clocks, databases, roles, comments, shared CRDT, family access, chat replies and per-user favorite/pin preferences survive restart and encrypted site restore.')
        finally:
            # Collect logs before disposable containers are removed, including
            # failures in API/assertion checks after successful startup.
            artifact_dir=os.getenv('DRG_TEST_ARTIFACT_DIR')
            if artifact_dir:
                try:
                    log_args=compose+['logs','--no-color','--tail','100'] if compose else ['logs','--tail','100',name]
                    result=subprocess.run(docker+log_args,env=env,capture_output=True,text=True,timeout=15)
                    logs=result.stdout+'\n'+result.stderr
                    for value in redactions:logs=logs.replace(value,'[redacted]')
                    target=Path(artifact_dir);target.mkdir(parents=True,exist_ok=True)
                    (target/'docker-smoke.log').write_text(logs)
                except (OSError,subprocess.TimeoutExpired) as error:
                    print('Could not retain disposable container logs: '+type(error).__name__)
            if compose:subprocess.run(docker+compose+['down','--volumes'],env=env,capture_output=True)
            else:
                subprocess.run(docker+['rm','--force',name],env=env,capture_output=True)
                subprocess.run(docker+['volume','rm','--force',volume],env=env,capture_output=True)

if __name__=='__main__':main()
