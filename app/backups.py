"""Encrypted, portable data snapshots. No schema migrations or SQL imports."""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing,contextmanager
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED,BadZipFile
from cryptography.fernet import Fernet,InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from app.modules.media import inspect_image,MAX_SIZE
from PIL import Image
from app.modules.widget_models import DashboardLayout,validate_layout
from app.modules.notes import validate_blocks,COVERS
from app.passwords import clear_enrollment
from app.data_crypto import DataCipher,media_store,StorageError
from app.database import SCHEMA_HASH
from app.storage_reconciliation import MISSING_KEY

MAGIC=b'DRG-BACKUP-01\x00'
from app.archive_stream import ArchiveWriter,open_archive,MAX_ARCHIVE
from app.file_store import file_store,FileStore
MAX_BACKUP=MAX_ARCHIVE
# Account content plus service metadata; active sessions are never restored.
TABLES=('users','note_assets','blob_gc','notifications','user_presence','session_info','user_limits','files','file_shares','file_share_sessions','spaces','space_members','social_requests','friendships','notes','note_permissions','note_databases','database_records','note_comments','collaborative_docs','messages','note_revisions','shares','share_sessions','media','tasks','task_lists','task_list_members','task_details','invites','sessions','refresh_tokens','recovery_codes','throttle','auth_failures','security_bans','app_settings','password_resets','note_favorites','note_unlocks','authenticators','expense_events','expense_entries','expense_shares')
TABLES=TABLES+('session_presence','chat_reads','calls','push_subscriptions','push_deliveries')
TABLES=TABLES+('guest_call_rooms','guest_call_participants','guest_call_links')
TABLES=TABLES+('security_events','security_incidents')
from app.federation.schema import FEDERATION_TABLES
TABLES=TABLES+FEDERATION_TABLES
from app.tracker_schema import TRACKER_TABLES
TABLES=TABLES+TRACKER_TABLES
from app.notion_sync.schema import TABLES as NOTION_SYNC_TABLES
TABLES=TABLES+NOTION_SYNC_TABLES
TABLES=TABLES+('chat_recordings',)
from app.file_sharing import TABLES as FILE_SHARING_TABLES
TABLES=TABLES+FILE_SHARING_TABLES
EMPTY_ON_RESTORE={'notion_oauth_states','notion_jobs','notion_events','notion_staging','notion_budgets','session_presence','blob_gc','user_presence','session_info','file_share_sessions','sessions','refresh_tokens','share_sessions','throttle','auth_failures','security_bans','note_unlocks','password_resets','push_subscriptions','push_deliveries'}
EMPTY_ON_RESTORE.update(('tracker_uploads','tracker_upload_chunks','file_download_grants'))
EMPTY_ON_RESTORE.update(('guest_call_rooms','guest_call_participants','guest_call_links'))
EMPTY_ON_RESTORE.update(('federation_challenges','federation_replay','federation_events','federation_uploads','federation_upload_chunks','federation_revocations'))

class BackupError(ValueError):pass

def schema(c):
    if hasattr(c,'backend'):return SCHEMA_HASH
    rows=c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' AND name!='storage_metadata' ORDER BY type,name").fetchall()
    # A four-state archive is a strict subset of the current tracker schema.
    # All other schema text still participates in the exact fingerprint.
    rows=[tuple(value.replace("status IN ('todo','work','review','done')","status IN ('todo','work','review','done','cancelled')") if isinstance(value,str) and row[1]=='tracker_issues' else value for value in row) for row in rows]
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

def key(password,salt):
    return Scrypt(salt=salt,length=32,n=32768,r=8,p=1).derive(password.encode())

def secure_write(path,body):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as file:file.write(body);file.flush();os.fsync(file.fileno())

def fsync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

class Backups:
    def __init__(self,db,data_dir,cipher,totp_key,now):
        self.db=db;self.root=Path(data_dir);self.cipher=cipher;self.totp_key=totp_key;self.now=now
        self.media=media_store(db,data_dir);self.staging=DataCipher.ephemeral();self.objects=file_store(db,data_dir)
    def temp(self):
        from app.process_locks import leased_temp
        return leased_temp(self.root, '.drg-backup-work-')
    def cleanup_temps(self):
        from app.process_locks import cleanup_temps
        cleanup_temps(self.root, '.drg-backup-work-')

    def stage_write(self,directory,name,body):
        secure_write(Path(directory)/name,self.staging.seal(body,'stage:'+Path(directory).name+':'+name))

    def stage_read(self,directory,name):
        return self.staging.open((Path(directory)/name).read_bytes(),'stage:'+Path(directory).name+':'+name)

    @contextmanager
    def snapshot_database(self,directory,write=False):
        """Plaintext snapshot lives only in memory; staging on disk is AEAD sealed."""
        connection=sqlite3.connect(':memory:');connection.row_factory=sqlite3.Row
        try:
            connection.deserialize(self.stage_read(directory,'site.sqlite3'))
            connection.execute('PRAGMA trusted_schema=OFF')
            if not write:connection.execute('PRAGMA query_only=ON')
            yield connection
            if write:
                connection.commit()
                path=Path(directory)/'site.sqlite3';path.unlink()
                self.stage_write(directory,'site.sqlite3',connection.serialize())
        finally:connection.close()

    def snapshot(self,directory):
        """Call with exclusive storage access; files are private immutable assets."""
        from app.retention import purge_service_records,purge_tasks
        from app.chat_storage import purge
        from app.file_trash import purge_file_trash
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');purge_service_records(c,self.now());purge(c,self.now());purge_file_trash(c,self.now())
            from app.note_history import purge_history
            purge_history(c,self.now())
            for user in c.execute('SELECT id,settings FROM users').fetchall():
                settings=json.loads(user['settings'])
                if purge_tasks(c,user['id'],settings,self.now()):c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),user['id']))
        destination=Path(directory)/'site.sqlite3';media=Path(directory)/'media';media.mkdir(mode=0o700)
        with self.db() as source,closing(sqlite3.connect(':memory:')) as target:
            source.backup(target)
            target.row_factory=sqlite3.Row
            # Invitations and live IP/SDP credentials are ephemeral, never a
            # restored entry point into an old guest room.
            target.execute('PRAGMA secure_delete=ON')
            for table in ('guest_call_links','guest_call_participants','guest_call_rooms'):
                target.execute('DELETE FROM '+table)
            target.execute('DELETE FROM app_settings WHERE key LIKE ?',('livekit:room:%',))
            for table in ('federation_upload_chunks','federation_uploads','federation_challenges','federation_replay','federation_events','federation_revocations'):
                target.execute('DELETE FROM '+table)
            from app.federation.calls import reset_federated_calls_after_restore
            reset_federated_calls_after_restore(target,self.now())
            target.execute("DELETE FROM app_settings WHERE key LIKE ? OR key LIKE ? OR key=?",('passkey:challenge:%','admin-access:%',MISSING_KEY))
            target.execute('DELETE FROM app_settings WHERE key=?', ('security:alert-state',))
            target.execute('DELETE FROM app_settings WHERE key LIKE ?', ('secret-vault:%',))
            target.row_factory=sqlite3.Row
            from app.quota import usage
            for row in target.execute('SELECT user_id FROM user_limits').fetchall():
                target.execute('UPDATE user_limits SET used_bytes=? WHERE user_id=?',(usage(target,row[0]),row[0]))
            target.commit()
            target.row_factory=sqlite3.Row;fingerprint=schema(target);assets=target.execute('SELECT id,size FROM media').fetchall();objects=target.execute("SELECT object_id,size FROM files WHERE kind='file'").fetchall()
            self.stage_write(directory,'site.sqlite3',target.serialize())
        if len(assets)>10000:raise BackupError('Одна копия поддерживает до 10 000 изображений.')
        total=destination.stat().st_size;files=[]
        for asset in assets:
            if not re.fullmatch(r'[a-f0-9]{32}',asset['id']):raise BackupError('Некорректный идентификатор изображения.')
            total+=asset['size']
            if total>MAX_BACKUP:raise BackupError('Данные превышают лимит одной копии: 512 Мб.')
            body=self.media.read(asset['id'])
            if len(body)!=asset['size']:raise BackupError('Некорректный размер изображения.')
            self.stage_write(directory,'media/'+asset['id'],body)
            files.append({'id':asset['id'],'size':len(body),'sha256':hashlib.sha256(body).hexdigest()})
        if total>MAX_BACKUP:raise BackupError('Данные превышают лимит одной копии: 512 Мб.')
        staged=FileStore(directory,self.staging,backend='local',bind=False);stored=[]
        for row in objects:
            writer=staged.writer(row['object_id'])
            try:
                for chunk in self.objects.iter_read(row['object_id']):writer.write(chunk)
                size,digest=writer.finish()
            except BaseException:writer.abort();raise
            if size!=row['size']:raise BackupError('Размер файла не совпадает с метаданными.')
            total+=size
            if total>MAX_BACKUP:raise BackupError('Данные превышают ARCHIVE_MAX_GB.')
            stored.append({'id':row['object_id'],'size':size,'sha256':digest})
        return {'objects':stored,'format':'drg-site','version':4,'created':self.now(),'schema':fingerprint,'totp_key':self.totp_key,'files':files}

    def encrypt(self,directory,metadata,password):
        directory=Path(directory);output=directory/'site.drgbackup'
        archive=ArchiveWriter(output,password,MAGIC,MAX_BACKUP)
        try:
            with ZipFile(archive,'w',compression=ZIP_DEFLATED,compresslevel=3,allowZip64=True) as z:
                z.writestr('manifest.json',json.dumps(metadata))
                z.writestr('site.sqlite3',self.stage_read(directory,'site.sqlite3'))
                for asset in metadata['files']:z.writestr('media/'+asset['id'],self.stage_read(directory,'media/'+asset['id']))
                staged=FileStore(directory,self.staging,backend='local',bind=False)
                for item in metadata.get('objects',[]):
                    with z.open('objects/'+item['id'],'w',force_zip64=True) as member:
                        for chunk in staged.iter_read(item['id']):member.write(chunk)
            archive.finish();return output
        except BaseException:archive.abort();raise

    def prepare(self,body,password,directory):
        """Decrypt/validate in isolation without plaintext disk files or SQL imports."""
        directory=Path(directory);raw=open_archive(body,password,MAGIC,directory,MAX_BACKUP)
        try:
            with ZipFile(raw) as z:
                infos=z.infolist();names=[item.filename for item in infos]
                if len(names)!=len(set(names)) or len(names)>110002 or 'manifest.json' not in names or 'site.sqlite3' not in names:
                    raise BackupError('Неполная или некорректная копия.')
                if sum(item.file_size for item in infos)>MAX_BACKUP or any(item.flag_bits&1 or item.compress_type not in (0,8) for item in infos):
                    raise BackupError('Копия слишком большая или имеет неподдерживаемый формат.')
                if z.getinfo('manifest.json').file_size>4*1024*1024:raise BackupError('Слишком большой манифест копии.')
                metadata=json.loads(z.read('manifest.json'))
                if not isinstance(metadata,dict):raise BackupError('Некорректный манифест копии.')
                if metadata.get('format')!='drg-site' or type(metadata.get('version')) is not int or metadata['version'] not in (1,2,3,4):raise BackupError('Версия копии не поддерживается.')
                files=metadata['files']
                if not isinstance(files,list) or any(not isinstance(item,dict) or not re.fullmatch(r'[a-f0-9]{32}',item.get('id','')) for item in files):
                    raise BackupError('Некорректный список файлов.')
                objects=metadata.get('objects',[])
                if not isinstance(objects,list) or any(not isinstance(item,dict) or not re.fullmatch(r'[a-f0-9]{32}',item.get('id','')) or type(item.get('size')) is not int or item['size']<0 or not re.fullmatch(r'[a-f0-9]{64}',item.get('sha256','')) for item in objects) or len(objects)!=len({i['id'] for i in objects}):raise BackupError('Некорректный список объектов.')
                expected={'manifest.json','site.sqlite3'}|{'media/'+item['id'] for item in files}|{'objects/'+item['id'] for item in objects}
                if set(names)!=expected or len(files)!=len({item['id'] for item in files}):raise BackupError('В копии есть лишние или повторяющиеся файлы.')
                media=directory/'media';media.mkdir(mode=0o700)
                for item in files:
                    if item.get('size',0)>MAX_SIZE or z.getinfo('media/'+item['id']).file_size!=item['size']:
                        raise BackupError('Некорректный размер изображения.')
                    image=z.read('media/'+item['id'])
                    if hashlib.sha256(image).hexdigest()!=item['sha256']:raise BackupError('Контрольная сумма изображения не совпадает.')
                    self.stage_write(directory,'media/'+item['id'],image)
                if z.getinfo('site.sqlite3').file_size>512*1024**2:raise BackupError('Метаданные базы превышают 512 Мб.')
                self.stage_write(directory,'site.sqlite3',z.read('site.sqlite3'))
                staged=FileStore(directory,self.staging,backend='local',bind=False)
                for item in objects:
                    if z.getinfo('objects/'+item['id']).file_size!=item['size']:raise BackupError('Некорректный размер объекта.')
                    writer=staged.writer(item['id'])
                    try:
                        with z.open('objects/'+item['id']) as member:
                            while chunk:=member.read(1024*1024):writer.write(chunk)
                        size,digest=writer.finish()
                    except BaseException:writer.abort();raise
                    if (size,digest)!=(item['size'],item['sha256']):raise BackupError('Контрольная сумма файла не совпадает.')
            self.validate_database(directory,metadata)
            return metadata
        except BackupError:raise
        except (BadZipFile,KeyError,TypeError,ValueError,sqlite3.DatabaseError,InvalidToken,StorageError,OSError,EOFError,Image.DecompressionBombError,Image.DecompressionBombWarning):
            raise BackupError('Файл копии повреждён или несовместим с этой версией проекта.') from None

    def validate_database(self,directory,metadata):
        with self.db() as c:fingerprint=schema(c)
        from app.database import TRACKER_LEGACY_SCHEMA_HASH
        if metadata.get('schema') not in (fingerprint,TRACKER_LEGACY_SCHEMA_HASH):raise BackupError('Схема копии не совпадает с этой версией проекта. Миграции не поддерживаются.')
        snapshot=self.snapshot_database(directory);source=snapshot.__enter__()
        try:
            source.row_factory=sqlite3.Row
            if source.execute('SELECT 1 FROM app_settings WHERE key LIKE ?',('secret-vault:%',)).fetchone():
                raise BackupError('Переносимая копия не должна содержать секретные хранилища.')
            if schema(source)!=fingerprint or source.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or source.execute('PRAGMA foreign_key_check').fetchone():
                raise BackupError('База копии повреждена или имеет другую схему.')
            if source.execute("SELECT COUNT(*) FROM users WHERE role='owner'").fetchone()[0]!=1 or source.execute("SELECT 1 FROM users WHERE role NOT IN ('owner','admin','member')").fetchone():
                raise BackupError('В копии должен быть один владелец и только допустимые роли сайта.')
            if source.execute('SELECT 1 FROM notes a JOIN notes b ON a.parent_id=b.id WHERE a.space_id IS NOT b.space_id OR (a.space_id IS NULL AND a.user_id!=b.user_id)').fetchone():raise BackupError('В копии нарушена принадлежность страниц.')
            from app.passkeys import validate_passkeys
            for user in source.execute('SELECT settings FROM users'):
                try:validate_passkeys(json.loads(user['settings']))
                except (ValueError,KeyError,TypeError):raise BackupError('Некорректные passkey в копии.')
            from app.collaboration import load_doc,project,MAX_STATE
            def invalid_social(*args):raise BackupError('Некорректные общие данные в копии.')
            from app.note_access import validate_archive as validate_page_access
            validate_page_access(source,invalid_social)
            from app.space_profiles import validate_archive as validate_space_profiles
            validate_space_profiles(source,invalid_social)
            from app.workspace_management import validate_archive as validate_management,is_manager
            validate_management(source,invalid_social)
            from app.workspace_lifecycle import validate_archive as validate_lifecycle
            validate_lifecycle(source,invalid_social)
            from app.registration_invites import validate_archive as validate_registration_invites
            validate_registration_invites(source,invalid_social)
            from app.account_lifecycle import validate_archive as validate_accounts
            from app.note_lifecycle import validate_archive as validate_deletions
            validate_accounts(source,invalid_social);validate_deletions(source,invalid_social)
            from app.reactions import validate_archive as validate_reactions
            validate_reactions(source,invalid_social)
            from app.note_trash import validate_archive as validate_trash_order
            validate_trash_order(source,invalid_social)
            if metadata['version']<4 and source.execute('SELECT 1 FROM app_settings WHERE key LIKE ? OR key LIKE ?',('account-deletion:%','note-deletion:%')).fetchone():invalid_social()
            from app.modules.tasks import validate_task_archive
            validate_task_archive(source,invalid_social)
            from app.tracker import validate_archive as validate_tracker
            validate_tracker(source,invalid_social)
            if source.execute('SELECT 1 FROM spaces s WHERE NOT EXISTS (SELECT 1 FROM space_members m WHERE m.space_id=s.id AND m.user_id=s.owner_id)').fetchone():invalid_social()
            for family in source.execute('SELECT * FROM spaces'):
                if not isinstance(family['name'],str) or not 1<=len(family['name'])<=80 or not re.fullmatch(r'[a-zA-Z0-9_-]{16}',family['id']):invalid_social()
                members=source.execute('SELECT user_id FROM space_members WHERE space_id=?',(family['id'],)).fetchall()
                if family['kind']=='friends':
                    friend=source.execute('SELECT * FROM friendships WHERE space_id=?',(family['id'],)).fetchone()
                    if not friend or {m[0] for m in members}!={friend['first_id'],friend['second_id']}:invalid_social()
                elif len(members)>50:invalid_social()
            from app.modules.family_avatars import validate_avatars
            validate_avatars(source,lambda message:invalid_social())
            for relation in source.execute('SELECT f.*,s.kind FROM friendships f JOIN spaces s ON s.id=f.space_id'):
                if relation['kind']!='friends':invalid_social()
            for invitation in source.execute('SELECT * FROM social_requests'):
                if invitation['sender_id']==invitation['recipient_id'] or invitation['expires']<=invitation['created']:invalid_social()
                if invitation['kind']=='family':
                    family=source.execute('SELECT * FROM spaces WHERE id=?',(invitation['space_id'],)).fetchone()
                    if not family or family['kind']!='family' or not is_manager(source,family['id'],invitation['sender_id']):invalid_social()
                elif invitation['space_id'] is not None:invalid_social()
            for receipt in source.execute('SELECT * FROM chat_reads'):
                if type(receipt['message_id']) is not int or not 1<=receipt['message_id']<=2**53-1 or not source.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?',(receipt['space_id'],receipt['user_id'])).fetchone():invalid_social()
            for message in source.execute('SELECT * FROM messages'):
                if not isinstance(message['id'],int) or message['id']<1 or not isinstance(message['body'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',message['client_id']):invalid_social()
            from app.modules.calls import validate_call_archive
            validate_call_archive(source,invalid_social)
            from app.federation.archive import validate_archive as validate_federation_archive
            validate_federation_archive(source,invalid_social)
            from app.chat_storage import validate_archive
            validate_archive(source,invalid_social)
            for row in source.execute('SELECT d.*,n.space_id,n.title,n.body,n.blocks FROM collaborative_docs d JOIN notes n ON n.id=d.note_id'):
                if not isinstance(row['state'],bytes) or len(row['state'])>MAX_STATE or not re.fullmatch(r'[a-zA-Z0-9_-]{22}',row['epoch']):invalid_social()
                try:title,body,blocks=project(load_doc(row['state']),invalid_social)
                except Exception as error:raise BackupError('Повреждён совместный документ в копии.') from error
                # Stored snapshots must exactly describe the canonical document.
                if (title,body,blocks)!=(row['title'],row['body'],json.loads(row['blocks'])):invalid_social()
            from app.modules.notion import validate_config,validate_values
            for d in source.execute('SELECT * FROM note_databases'):
                validate_config(json.loads(d['properties']),json.loads(d['views']),invalid_social)
                for rec in source.execute('SELECT r.*,n.parent_id,n.space_id,p.space_id AS database_scope FROM database_records r JOIN notes n ON n.id=r.note_id JOIN notes p ON p.id=r.database_id WHERE database_id=?',(d['note_id'],)):
                    if rec['parent_id']!=d['note_id'] or rec['space_id']!=rec['database_scope']:invalid_social()
                    validate_values(json.loads(rec['properties']),json.loads(d['properties']),invalid_social)
            from app.personal_data import validate_database_references,portable_links
            from types import SimpleNamespace
            portable=SimpleNamespace(databases=[SimpleNamespace(note_id=d['note_id'],properties=json.loads(d['properties'])) for d in source.execute('SELECT * FROM note_databases')],records=[SimpleNamespace(note_id=r['note_id'],database_id=r['database_id'],properties=json.loads(r['properties'])) for r in source.execute('SELECT * FROM database_records')])
            sanitized_bases=[{'note_id':d.note_id,'properties':d.properties} for d in portable.databases];sanitized_rows=[{'note_id':r.note_id,'database_id':r.database_id,'properties':r.properties} for r in portable.records]
            portable_links(sanitized_bases,sanitized_rows)
            checked=SimpleNamespace(databases=[SimpleNamespace(**d) for d in sanitized_bases],records=[SimpleNamespace(**r) for r in sanitized_rows])
            validate_database_references(checked,invalid_social)
            for d in portable.databases:
                base=source.execute('SELECT user_id,space_id FROM notes WHERE id=?',(d.note_id,)).fetchone()
                for p in d.properties:
                    if p['type']=='relation':
                        other=source.execute('SELECT user_id,space_id FROM notes WHERE id=?',(p['database_id'],)).fetchone()
                        if not other or base['space_id']!=other['space_id'] or (base['space_id'] is None and base['user_id']!=other['user_id']):invalid_social()
            for a in source.execute('SELECT a.*,p.note_id AS parent_note,p.parent_id AS parent_parent FROM note_comments a LEFT JOIN note_comments p ON p.id=a.parent_id'):
                if not 1<=len(a['body'])<=4000 or a['resolved'] not in (0,1) or (a['parent_id'] and (a['parent_note']!=a['note_id'] or a['parent_parent'])):invalid_social()
                mentions=json.loads(a['mentions'])
                if not isinstance(mentions,list) or any(not isinstance(u,str) or not re.fullmatch(r'[a-f0-9]{32}',u) for u in mentions):invalid_social()
            saved_cipher=Fernet(metadata['totp_key'].encode())
            for user in source.execute('SELECT * FROM users'):
                if not re.fullmatch(r'[a-f0-9]{32}',user['id']) or not re.fullmatch(r'[a-z0-9_.-]{3,40}',user['login']):raise BackupError('Некорректный аккаунт в копии.')
                settings=json.loads(user['settings'])
                if not isinstance(settings,dict):raise BackupError('Некорректные настройки аккаунта.')
                from app.people_names import KEY as NAMES_KEY,valid as valid_names
                if NAMES_KEY in settings and not valid_names(settings[NAMES_KEY]):raise BackupError('Некорректные личные имена в копии.')
                if 'profile' in settings:
                    profile=settings['profile']
                    from app.modules.profile import valid_profile
                    if not valid_profile(profile,lambda aid:source.execute('SELECT 1 FROM media WHERE id=? AND user_id=?',(aid,user['id'])).fetchone()):raise BackupError('Некорректный профиль или ссылка на чужой аватар.')
                if user['totp']:
                    secret=saved_cipher.decrypt(user['totp'].encode()).decode()
                    if not re.fullmatch(r'[A-Z2-7]{16,128}',secret):raise BackupError('Некорректные данные 2FA.')
                elif user['role']=='owner' and settings.get('_two_factor_disabled') is not True:raise BackupError('В копии администратор ещё не завершил первичную настройку.')
                if 'dashboard' in settings:
                    layout=DashboardLayout.model_validate(settings['dashboard'])
                    def bad(*args):raise BackupError('Некорректные настройки виджетов в копии.')
                    validate_layout(layout,source,user['id'],bad)
            from app.modules.authenticator import AuthenticatorInput,normalize
            from app.modules.expenses import Event,Entry,validate_event,validate_entry,validate_photos
            for entry in source.execute('SELECT * FROM authenticators'):
                normalize(AuthenticatorInput(label=entry['label'],issuer=entry['issuer'],secret=saved_cipher.decrypt(entry['secret'].encode()).decode(),algorithm=entry['algorithm'],digits=entry['digits'],period=entry['period']))
            for event in source.execute('SELECT * FROM expense_events'):
                body=json.loads(event['payload']);photos=body.pop('photos',[])
                validate_photos(photos,lambda aid:source.execute('SELECT 1 FROM media WHERE id=? AND user_id=?',(aid,event['user_id'])).fetchone())
                validate_event(Event(title=event['title'],**body))
                for entry in source.execute('SELECT * FROM expense_entries WHERE event_id=?',(event['id'],)):
                    validate_entry(Entry(**json.loads(entry['payload']),event_version=event['version']),body['participants'])
            parents={row[0]:row[1] for row in source.execute('SELECT id,parent_id FROM notes')};finished=set()
            for nid in parents:
                chain=set();current=nid
                while current is not None and current not in finished:
                    if current in chain:raise BackupError('В дереве страниц есть цикл.')
                    chain.add(current);current=parents[current]
                finished.update(chain)
            def invalid_document(*args):raise BackupError('Некорректное содержимое страницы в копии.')
            for row in source.execute('SELECT id,blocks,icon,cover FROM notes'):
                if not isinstance(row['icon'],str) or len(row['icon'])>100 or (row['cover'] not in COVERS and not row['cover'].startswith('image:')):raise BackupError('Некорректные свойства страницы.')
                validate_blocks(json.loads(row['blocks']) if row['blocks'] is not None else None,invalid_document)
                for kind in ('icon','cover'):
                    if row[kind].startswith('image:') and not source.execute('SELECT 1 FROM note_assets WHERE id=? AND note_id=? AND kind=?',(row[kind][6:],row['id'],kind)).fetchone():invalid_document()
            self.validate_files(source,metadata)
            from app.file_sharing import validate_archive as validate_file_sharing
            validate_file_sharing(source,invalid_social)
            for row in source.execute('SELECT blocks FROM note_revisions'):
                validate_blocks(json.loads(row['blocks']) if row['blocks'] is not None else None,invalid_document)
            assets={item['id']:item for item in metadata['files']};rows=source.execute('SELECT * FROM media').fetchall()
            if {row['id'] for row in rows}!=set(assets):raise BackupError('Библиотека и файлы не совпадают.')
            for row in rows:
                body=self.stage_read(directory,'media/'+row['id'])
                if len(body)!=row['size'] or inspect_image(body)!=(row['mime'],row['width'],row['height'],row['frames']):raise BackupError('Некорректное изображение в копии.')
        finally:snapshot.__exit__(None,None,None)

    def validate_files(self,source,metadata):
        rows={r['id']:r for r in source.execute('SELECT * FROM files')}
        objects={i['id']:i for i in metadata.get('objects',[])}
        if {r['object_id'] for r in rows.values() if r['kind']=='file'}!=set(objects):raise BackupError('Файлы и содержимое архива не совпадают.')
        from app.modules.files import filename
        def invalid(*args):raise BackupError('Некорректное дерево файлов.')
        for row in rows.values():
            from app.chat_storage import attachment_room
            attachment=attachment_room(row['id'])
            if not re.fullmatch(r'[a-zA-Z0-9_-]{16}',row['id']) and not attachment:invalid()
            if (row['deleted'] is None)!=(row['trash_batch'] is None):invalid()
            if row['deleted'] is not None:
                if attachment or type(row['deleted']) is not int or row['deleted']<0 or not isinstance(row['trash_batch'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16}',row['trash_batch']):invalid()
            try:filename(row['name'])
            except Exception:invalid()
            if row['kind']=='file':
                if row['object_id'] not in objects or row['size']!=objects[row['object_id']]['size']:invalid()
            elif row['object_id'] is not None or row['size']!=0:invalid()
            chain=set();current=row
            while current['parent_id']:
                if current['id'] in chain:invalid()
                chain.add(current['id']);parent=rows.get(current['parent_id'])
                if not parent or parent['kind']!='folder' or parent['user_id']!=row['user_id']:invalid()
                if row['deleted'] is None and parent['deleted'] is not None:invalid()
                current=parent
            if row['deleted'] is not None:
                root=rows.get(row['trash_batch'])
                if not root or root['user_id']!=row['user_id'] or root['deleted']!=row['deleted'] or root['trash_batch']!=root['id'] or root['id'] not in chain|{row['id'],current['id']}:invalid()
        for row in source.execute('SELECT * FROM user_limits'):
            if row['limit_bytes'] is not None and not 0<=row['limit_bytes']<=100*1024**4:invalid()
        for row in source.execute('SELECT payload FROM note_assets'):
            from app.modules.note_assets import validate_attribution
            validate_attribution(json.loads(row['payload']),invalid)

    def restore(self,directory,metadata):
        backend=getattr(self.db,'__self__',None)
        drain=getattr(backend,'drain_pool',lambda:None)
        drain()
        try:return self._restore(directory,metadata)
        finally:drain()

    def _restore(self,directory,metadata):
        """Copy immutable assets before one DB transaction; old content stays intact
        on failure. Orphans left by a crash are inaccessible and can be collected.
        """
        with self.db() as c:
            if c.execute('SELECT 1 FROM app_settings WHERE key LIKE ?',('secret-vault:%',)).fetchone():
                raise BackupError('На сервере есть секретные хранилища, которых нет в резервной копии. Сначала перенесите нужное содержимое в обычные разделы и удалите хранилища. Восстановление не выполнено.')
        directory=Path(directory);root=self.root/'media';root.mkdir(mode=0o700,exist_ok=True)
        existing_media=self.media.ids()
        new_files=[];new_objects={};staged=FileStore(directory,self.staging,backend='local',bind=False)
        try:
            for asset in metadata['files']:
                destination=root/asset['id']
                if asset['id'] in existing_media:
                    if hashlib.sha256(self.media.read(asset['id'])).hexdigest()!=asset['sha256']:
                        raise BackupError('Обнаружен конфликт изображений. Текущие данные не изменены.')
                else:
                    self.media.write(asset['id'],self.stage_read(directory,'media/'+asset['id']));new_files.append(destination)
            import secrets
            from app.file_scopes import tracker_file_ids
            with self.snapshot_database(directory) as source:
                source.row_factory=sqlite3.Row
                tracker_files=tracker_file_ids(source)
                tracker_objects={row['object_id'] for row in source.execute('SELECT id,object_id FROM files') if row['id'] in tracker_files and row['object_id']}
            for item in metadata.get('objects',[]):
                identity=secrets.token_hex(16);new_objects[item['id']]=identity
                destination=self.objects.scoped('tracker-attachments') if item['id'] in tracker_objects else self.objects
                writer=destination.writer(identity)
                try:
                    for chunk in staged.iter_read(item['id']):writer.write(chunk)
                    writer.finish()
                except BaseException:writer.abort();raise
            fsync_dir(root)
            saved_cipher=Fernet(metadata['totp_key'].encode())
            from app.security_monitor import SecurityMonitor
            restored_security=SecurityMonitor(self.db,self.now,None)
            with self.snapshot_database(directory) as source,self.db() as target:
                source.row_factory=sqlite3.Row
                if hasattr(target,'quota_disabled'):target.quota_disabled=True
                target.execute('BEGIN IMMEDIATE');target.execute('PRAGMA defer_foreign_keys=ON')
                revoked_devices=[tuple(row) for row in target.execute('SELECT peer_id,actor_id,device_id,created FROM federation_device_revocations')]
                for table in reversed(TABLES):target.execute('DELETE FROM '+table)
                for table in TABLES:
                    if table in EMPTY_ON_RESTORE:continue
                    columns=[row[1] for row in source.execute('PRAGMA table_info('+table+')')]
                    query='INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')'
                    for row in source.execute('SELECT * FROM '+table):
                        values=dict(row)
                        if table in ('security_events','security_incidents'):
                            # A portable restore may use another APP_SECRET. The
                            # encrypted subject is authoritative for lookup keys.
                            values['subject_key']=restored_security.subject_key(values['subject'])
                        if table=='files' and values['object_id']:values['object_id']=new_objects[values['object_id']]
                        if table=='authenticators':values['secret']=self.cipher.encrypt(saved_cipher.decrypt(values['secret'].encode())).decode()
                        if table=='users':
                            if values['totp']:values['totp']=self.cipher.encrypt(saved_cipher.decrypt(values['totp'].encode())).decode()
                            values['last_counter']=-1
                            settings=json.loads(values['settings']);clear_enrollment(settings)
                            values['settings']=json.dumps(settings)
                        target.execute(query,[values[column] for column in columns])
                from app.notion_sync.state import pause_after_restore
                pause_after_restore(target)
                # An older archive cannot undo a device revocation recorded on
                # this installation after that archive was created.
                for revoked in revoked_devices:
                    target.execute('INSERT INTO federation_device_revocations VALUES(?,?,?,?) ON CONFLICT(peer_id,actor_id,device_id) DO NOTHING',revoked)
                target.execute("DELETE FROM app_settings WHERE key LIKE ? OR key LIKE ? OR key=?",('passkey:challenge:%','admin-access:%',MISSING_KEY))
                # Restores revoke every session; never restore an emergency grant
                # referencing a session that cannot exist in the restored database.
                target.execute('DELETE FROM app_settings WHERE key IN (?,?)', ('security:service-mode','security:alert-state'))
                target.execute('DELETE FROM app_settings WHERE key LIKE ?',('livekit:room:%',))
                from app.federation.archive import reset_after_restore
                reset_after_restore(target,self.now())
                from app.retention import purge_service_records,purge_tasks
                from app.chat_storage import purge
                from app.file_trash import purge_file_trash
                purge_service_records(target,self.now());purge(target,self.now());purge_file_trash(target,self.now())
                from app.note_history import purge_history
                purge_history(target,self.now())
                for user in target.execute('SELECT id,settings FROM users').fetchall():
                    settings=json.loads(user['settings'])
                    if purge_tasks(target,user['id'],settings,self.now()):target.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),user['id']))
                if target.execute('PRAGMA foreign_key_check').fetchone():raise BackupError('Не удалось восстановить связи данных.')
                from app.quota import usage
                for user in target.execute('SELECT id FROM users').fetchall():
                    target.execute('INSERT OR IGNORE INTO user_limits(user_id) VALUES(?)',(user[0],))
                    target.execute('UPDATE user_limits SET used_bytes=? WHERE user_id=?',(usage(target,user[0]),user[0]))
                administrator=target.execute("SELECT login FROM users WHERE role='owner'").fetchone()[0]
            self.collect_orphans()
            return administrator
        except BaseException:
            # Only remove files absent from committed metadata: cancellation or
            # a cleanup failure after commit must not remove restored assets.
            with self.db() as c:kept={row[0] for row in c.execute('SELECT id FROM media')}
            for file in new_files:
                if file.name not in kept:self.media.delete(file.name)
            with self.db() as c:kept_objects={r[0] for r in c.execute('SELECT object_id FROM files WHERE object_id IS NOT NULL')}
            for identity in new_objects.values():
                if identity not in kept_objects:self.objects.delete(identity)
            raise

    def collect_orphans(self):
        from app.storage_reconciliation import reconcile
        return reconcile(self.db,self.objects,self.media,self.now())
