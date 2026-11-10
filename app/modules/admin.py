"""Administrator account operations and full encrypted backup/restore."""
from app.i18n import message as localized_message
from app.passkeys import clear_passkeys
from app.account_lifecycle import pending as account_deletion
import asyncio
import base64
import hashlib
import json
import re
import secrets
from pathlib import Path
from app.http import Router,Request,Response
from app.http import FileResponse
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from app.http import BackgroundTask
from app.backups import Backups,BackupError,MAX_BACKUP
from app.storage import blocking
from app.archive_stream import receive_archive,maximum_upload
from app.modules.social import clean_chat_widgets
from app.passwords import NewPassword,clear_enrollment
from app.data_crypto import media_store
from app.modules.profile import AVATAR_LIMIT,thumbnail,profile_name,save_avatar,select_preset
from app.avatar_presets import PRESETS,default_profile,artwork
from app.modules.appearance import new_account_appearance
from PIL import Image,UnidentifiedImageError
from typing import Annotated, Literal
from app.site_roles import LABELS, managed_user, current_manager

class AdminProof(BaseModel):
    model_config=ConfigDict(extra='forbid')
    admin_password:str|None=Field(default=None,min_length=1,max_length=256)
class ManagedProfile(AdminProof):
    display_name:str=Field(default='',max_length=80)
    avatar:str|None=Field(default=None,max_length=4*((AVATAR_LIMIT+2)//3))
    avatar_preset:str|None=Field(default=None,max_length=24)
    remove_avatar:StrictBool=False
    add_friend:StrictBool=False
    family_ids:list[Annotated[str,Field(pattern=r'^[a-zA-Z0-9_-]{16}$')]]=Field(default_factory=list,max_length=20)
class CreateUser(ManagedProfile):
    login:str=Field(min_length=3,max_length=40)
    password:NewPassword

class ChangeRole(AdminProof):
    role:Literal['admin','member']
    previous_role:Literal['admin','member']

class ChangeQuota(AdminProof):
    limit_bytes:int|None=Field(default=None,ge=0,le=100*1024**4)
class ChangePassword(AdminProof):
    password:NewPassword
class UserAction(AdminProof):
    user_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    confirmation:str=Field(max_length=40)
class ExportBackup(AdminProof):
    backup_password:str=Field(min_length=12,max_length=256)
class Friends(AdminProof):
    friend_ids:list[Annotated[str,Field(pattern=r'^[a-f0-9]{32}$')]]=Field(max_length=200)
    version:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirm_removals:StrictBool=False

# Useful bytes of account rows; physical SQLite pages/indexes are shared.
OWNED_TABLES={'authenticators':('user_id','id'),'expense_events':('user_id','id'),'note_favorites':('user_id',None),'password_resets':('user_id',None),'users':('id','id'),'notes':('user_id','id'),'tasks':('user_id','id'),'files':('user_id','id'),'notifications':('user_id','id'),'user_limits':('user_id',None),'media':('user_id','id'),'recovery_codes':('user_id',None),'sessions':('user_id',None),'spaces':('owner_id','id'),'space_members':('user_id',None),'messages':('sender_id','id'),'note_comments':('user_id','id'),'note_permissions':('user_id',None)}

def account_bytes(c,uid,login):
    result=0
    for table,(field,_) in OWNED_TABLES.items():
        rows=c.execute('SELECT * FROM '+table+' WHERE '+field+'=?',(uid,))
        for row in rows:
            result+=sum(len(value.encode()) if isinstance(value,str) else len(value) if isinstance(value,bytes) else 8 if value is not None else 0 for value in row)
    for query,args in [
        ('SELECT x.* FROM task_lists x WHERE x.owner_id=?',(uid,)),
        ('SELECT x.* FROM task_details x JOIN tasks t ON t.id=x.task_id WHERE t.user_id=?',(uid,)),
        ('SELECT x.* FROM task_list_members x JOIN task_lists l ON l.id=x.list_id WHERE l.owner_id=?',(uid,)),
        ('SELECT x.* FROM expense_entries x JOIN expense_events e ON e.id=x.event_id WHERE e.user_id=?',(uid,)),
        ('SELECT x.* FROM expense_shares x JOIN expense_events e ON e.id=x.event_id WHERE e.user_id=?',(uid,)),
        ('SELECT r.* FROM note_revisions r JOIN notes n ON n.id=r.note_id WHERE n.user_id=?',(uid,)),
        ('SELECT s.* FROM shares s JOIN notes n ON n.id=s.note_id WHERE n.user_id=?',(uid,)),
        ('SELECT s.* FROM share_sessions s JOIN shares a ON a.token=s.share_token JOIN notes n ON n.id=a.note_id WHERE n.user_id=?',(uid,)),
        ('SELECT r.* FROM refresh_tokens r JOIN sessions s ON s.token=r.session_token WHERE s.user_id=?',(uid,)),
        ('SELECT d.* FROM note_databases d JOIN notes n ON n.id=d.note_id WHERE n.user_id=?',(uid,)),
        ('SELECT d.* FROM database_records d JOIN notes n ON n.id=d.note_id WHERE n.user_id=?',(uid,)),
        ('SELECT d.* FROM collaborative_docs d JOIN notes n ON n.id=d.note_id WHERE n.user_id=?',(uid,)),
        ('SELECT * FROM social_requests WHERE sender_id=? OR recipient_id=?',(uid,uid)),
        ('SELECT * FROM friendships WHERE first_id=? OR second_id=?',(uid,uid)),
        ('SELECT * FROM invites WHERE login=?',(login,))]:
        for row in c.execute(query,args):result+=sum(len(value.encode()) if isinstance(value,str) else len(value) if isinstance(value,bytes) else 8 if value is not None else 0 for value in row)
    return result


def note_content_bytes(c,uid):
    """Logical UTF-8/blob content already included in the site's database."""
    queries=[
        'SELECT title,body,blocks,icon,cover FROM notes WHERE user_id=?',
        'SELECT r.title,r.body,r.blocks FROM note_revisions r JOIN notes n ON n.id=r.note_id WHERE n.user_id=?',
        'SELECT r.properties,r.views FROM note_databases r JOIN notes n ON n.id=r.note_id WHERE n.user_id=?',
        'SELECT r.properties FROM database_records r JOIN notes n ON n.id=r.note_id WHERE n.user_id=?',
        'SELECT r.state FROM collaborative_docs r JOIN notes n ON n.id=r.note_id WHERE n.user_id=?',
        'SELECT r.body,r.mentions FROM note_comments r JOIN notes n ON n.id=r.note_id WHERE n.user_id=?',
    ]
    return sum(len(value.encode('utf-8')) if isinstance(value,str) else len(value) if isinstance(value,bytes) else 0
               for query in queries for row in c.execute(query,(uid,)) for value in row)

def create_admin_router(db,owner,fail,check_password,hash_password,protection,now,tokens,data_dir,cipher,totp_key,gate,admin_access):
    router=Router();backups=Backups(db,data_dir,cipher,totp_key,now);heavy=asyncio.Lock()
    router.backups=backups;avatars=media_store(db,data_dir);avatar_lock=asyncio.Semaphore(2)
    def proof(request,password):
        return admin_access.proof(request,password)
    def target(c,uid):
        row=c.execute('SELECT id,login,role FROM users WHERE id=?',(uid,)).fetchone()
        if not row:fail(404,localized_message('accountNotFound'))
        return row
    def reset_sessions(c,uid):c.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
    def available(request):
        owner(request)
        if heavy.locked() or gate.exclusive_pending:fail(423,localized_message('operationWithDataIsAlreadyInProgressRetry'))
    def secret_header(request,name):
        value=request.headers.get(name,'')
        if len(value)>2048:fail(422,localized_message('incorrectPasswordConfirmation'))
        try:return base64.b64decode(value,validate=True).decode('utf-8')
        except (ValueError,UnicodeError):fail(422,localized_message('incorrectPasswordConfirmation'))

    def account_profile(c,uid,session,*,include_history=False):
        from app.notifications import actor
        result=actor(c,uid,now(),include_history=include_history)
        role=c.execute('SELECT role FROM users WHERE id=?',(uid,)).fetchone()
        if session['role']=='admin' and role and role[0]=='member' and result['avatar_url']:
            result['avatar_url']=result['avatar_url'].replace('/api/users/','/api/admin/users/')
        return result

    @router.get('/api/admin/users/{uid}/avatar')
    def managed_avatar(uid:str,request:Request):
        manager=owner(request)
        with db() as c:
            managed_user(c,uid,manager,now(),fail)
            profile=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]).get('profile',{})
            preset=profile.get('avatar_preset')
            if preset in PRESETS:body=artwork(preset);mime='image/webp'
            else:
                row=c.execute('SELECT id,mime FROM media WHERE id=? AND user_id=?',(profile.get('avatar_id'),uid)).fetchone()
                if not row:fail(404,localized_message('avatarNotSet'))
                body=avatars.read(row['id']);mime=row['mime']
        return Response(body,media_type=mime,headers={'Cache-Control':'private, no-store'})

    @router.get('/api/admin/users')
    def users(request:Request):
        s=owner(request)
        with db() as c:
            result=[]
            from app import workspace_management as management
            from app.space_profiles import templates
            profiles=templates(c);configs=management.configs(c)
            catalog={row['id']:dict(row) for row in c.execute("SELECT id,name,owner_id FROM spaces WHERE kind='family'")}
            connections={}
            for row in c.execute('SELECT space_id,user_id FROM space_members'):
                sid,uid=row['space_id'],row['user_id']
                if sid not in catalog:continue
                space=catalog[sid];config=configs.get(sid,management.default())
                connections.setdefault(uid,[]).append({'id':sid,'name':space['name'],'template':profiles.get(sid,'family'),'role':management.role(c,space,uid,config),'teams':[t['name'] for t in config['teams'].values() if uid in t['members']]})
            for user in sorted(c.execute('SELECT id,login,role,totp,created,settings FROM users').fetchall(),key=lambda user:(user['role']!='owner',user['login'])):
                uid=user['id'];from app.quota import summary
                quota=summary(c,uid);data_bytes=account_bytes(c,uid,user['login'])
                file_bytes=c.execute('SELECT COALESCE(SUM(size),0) FROM files WHERE user_id=?',(uid,)).fetchone()[0]
                media_bytes=c.execute('SELECT COALESCE(SUM(size),0) FROM media WHERE user_id=?',(uid,)).fetchone()[0]
                result.append({'profile':account_profile(c,uid,s,include_history=True),'friend':bool(c.execute('SELECT 1 FROM friendships WHERE (first_id=? AND second_id=?) OR (first_id=? AND second_id=?)',(uid,s['user_id'],s['user_id'],uid)).fetchone()),'families':[row[0] for row in c.execute("SELECT m.space_id FROM space_members m JOIN spaces s ON s.id=m.space_id WHERE m.user_id=? AND s.kind='family'",(uid,))],'workspaces':connections.get(uid,[]),'sessions':c.execute("SELECT COUNT(*) FROM sessions WHERE user_id=? AND stage='full' AND expires>?",(uid,now())).fetchone()[0],'passkeys':len(json.loads(user['settings']).get('_passkeys',[])),'file_count':c.execute("SELECT COUNT(*) FROM files WHERE user_id=? AND kind='file'",(uid,)).fetchone()[0],'quota':quota,'id':uid,'login':user['login'],'role':user['role'],'role_label':LABELS[user['role']],'two_factor':bool(user['totp']),'created':user['created'],'can_manage':s['role']=='owner' or user['role']=='member','current':uid==s['user_id'],'deletion':account_deletion(c,uid),'data_bytes':data_bytes,'note_content_bytes':note_content_bytes(c,uid),'media_bytes':media_bytes,'file_bytes':file_bytes,'total_bytes':data_bytes+media_bytes+file_bytes,'notes':c.execute('SELECT COUNT(*) FROM notes WHERE user_id=?',(uid,)).fetchone()[0],'tasks':c.execute('SELECT COUNT(*) FROM tasks WHERE user_id=?',(uid,)).fetchone()[0],'images':c.execute('SELECT COUNT(*) FROM media WHERE user_id=?',(uid,)).fetchone()[0]})
            families=[dict(row) for row in c.execute("SELECT id,name FROM spaces WHERE kind='family' AND owner_id=? ORDER BY created,id",(s['user_id'],))]
        backend=getattr(db,'__self__',None)
        database_bytes=backend.physical_bytes() if backend else sum(path.stat().st_size for path in Path(data_dir).glob('site.sqlite3*') if path.is_file())
        return {'can_change_roles':s['role']=='owner','users':result,'families':families,'workspaces':[{'id':s['id'],'name':s['name'],'template':profiles.get(s['id'],'family')} for s in catalog.values()],'database_bytes':database_bytes,'media_bytes':sum(item['media_bytes'] for item in result),'file_bytes':sum(item['file_bytes'] for item in result),'note_content_bytes':sum(item['note_content_bytes'] for item in result),'site_total_bytes':database_bytes+sum(item['media_bytes']+item['file_bytes'] for item in result),'backup_limit_bytes':MAX_BACKUP}

    def connect(c,admin,uid,data):
        if len(set(data.family_ids))!=len(data.family_ids):fail(422,localized_message('spaceHasBeenSpecifiedAgain'))
        if uid==admin and (data.add_friend or data.family_ids):fail(422,localized_message('selectAnotherAccountToAddLinks'))
        if data.add_friend:
            a,b=sorted((admin,uid))
            if not c.execute('SELECT 1 FROM friendships WHERE first_id=? AND second_id=?',(a,b)).fetchone():
                for identity in (a,b):
                    if c.execute('SELECT COUNT(*) FROM friendships WHERE first_id=? OR second_id=?',(identity,identity)).fetchone()[0]>=200:fail(422,localized_message('maximum200Friends'))
                sid=secrets.token_urlsafe(12)
                c.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',(sid,'friends','Друзья',admin,now()))
                for identity in (a,b):c.execute('INSERT INTO space_members VALUES(?,?,?)',(sid,identity,now()))
                c.execute('INSERT INTO friendships VALUES(?,?,?)',(a,b,sid))
            c.execute("DELETE FROM social_requests WHERE kind='friends' AND ((sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?))",(admin,uid,uid,admin))
        for sid in data.family_ids:
            if not c.execute("SELECT 1 FROM spaces WHERE id=? AND kind='family' AND owner_id=?",(sid,admin)).fetchone():fail(403,localized_message('selectTheWorkspaceYouManage'))
            if not c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?',(sid,uid)).fetchone():
                if c.execute('SELECT COUNT(*) FROM space_members WHERE space_id=?',(sid,)).fetchone()[0]>=50:fail(422,localized_message('theWorkspaceHasNoMoreThan50Members'))
                if c.execute("SELECT COUNT(*) FROM space_members m JOIN spaces s ON s.id=m.space_id WHERE m.user_id=? AND s.kind='family'",(uid,)).fetchone()[0]>=20:fail(422,localized_message('noMoreThan20Workspaces'))
                c.execute('INSERT INTO space_members VALUES(?,?,?)',(sid,uid,now()))
            c.execute("DELETE FROM social_requests WHERE kind='family' AND space_id=? AND recipient_id=?",(sid,uid))

    def friendships(c,uid):
        return {row['second_id'] if row['first_id']==uid else row['first_id']:row['space_id']
                for row in c.execute('SELECT * FROM friendships WHERE first_id=? OR second_id=?',(uid,uid))}

    def friends_version(current):
        return hashlib.sha256(json.dumps(sorted(current.items())).encode()).hexdigest()

    @router.get('/api/admin/users/{uid}/friends')
    def friends(uid:str,request:Request):
        admin_access.require(request)
        with db() as c:
            target(c,uid);current=friendships(c,uid)
            from app.notifications import actor
            users=[{'id':row['id'],'login':row['login'],'profile':actor(c,row['id'],now())}
                   for row in c.execute('SELECT id,login FROM users WHERE id!=? ORDER BY login',(uid,))]
        return {'users':users,'friend_ids':sorted(current),'version':friends_version(current)}

    @router.put('/api/admin/users/{uid}/friends')
    def change_friends(uid:str,data:Friends,request:Request):
        proof(request,data.admin_password)
        wanted=set(data.friend_ids)
        if len(wanted)!=len(data.friend_ids) or uid in wanted:fail(422,localized_message('selectOtherUsersWithoutDuplicates'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');target(c,uid);current=friendships(c,uid)
            if friends_version(current)!=data.version:fail(409,localized_message('friendListHasChangedOpenTheEditorAgain'))
            for peer in wanted:target(c,peer)
            removed=set(current)-wanted;added=wanted-set(current)
            if removed and not data.confirm_removals:fail(422,localized_message('confirmTheRemovalOfFriendshipAndSharedWorkspace'))
            for peer in removed:
                c.execute('DELETE FROM task_list_members WHERE (user_id=? AND list_id IN (SELECT id FROM task_lists WHERE owner_id=?)) OR (user_id=? AND list_id IN (SELECT id FROM task_lists WHERE owner_id=?))',(uid,peer,peer,uid))
                from app.workspace_lifecycle import destroy
                destroy(c,current[peer],now())
            for peer in sorted(added):
                if c.execute('SELECT COUNT(*) FROM friendships WHERE first_id=? OR second_id=?',(peer,peer)).fetchone()[0]>=200:fail(422,localized_message('theSelectedUserAlreadyHas200Friends'))
                a,b=sorted((uid,peer));sid=secrets.token_urlsafe(12)
                c.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',(sid,'friends','Друзья',uid,now()))
                for identity in (a,b):c.execute('INSERT INTO space_members VALUES(?,?,?)',(sid,identity,now()))
                c.execute('INSERT INTO friendships VALUES(?,?,?)',(a,b,sid))
            for peer in wanted:
                c.execute("DELETE FROM social_requests WHERE kind='friends' AND ((sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?))",(uid,peer,peer,uid))
            if removed:clean_chat_widgets(c)
        return {'friend_ids':sorted(wanted),'added':len(added),'removed':len(removed)}
    async def avatar_body(data):
        if sum([data.avatar is not None,data.avatar_preset is not None,data.remove_avatar])>1:fail(422,localized_message('chooseToReplaceOrDeleteTheAvatar'))
        if data.avatar_preset is not None and data.avatar_preset not in PRESETS:fail(422,localized_message('chooseAnAvatarFromTheAvailableSet'))
        if data.avatar is None:return None
        try:
            raw=base64.b64decode(data.avatar,validate=True)
            if len(raw)>AVATAR_LIMIT:fail(413,localized_message('theAvatarMustBeNoMoreThan10'))
            return await blocking(thumbnail,raw)
        except (ValueError,EOFError,UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(422,localized_message('youNeedPngJpegWebpOrAGif'))
    @router.post('/api/admin/users')
    async def create_user(data:CreateUser,request:Request):
        manager=proof(request,data.admin_password);admin=manager['user_id'];name=profile_name(data.display_name,fail);login=data.login.strip().lower()
        if not re.fullmatch(r'[a-z0-9_.-]{3,40}',login):fail(422,localized_message('username340LatinCharactersNumbersPeriodsHyphensOr'))
        async with avatar_lock:
            body=await avatar_body(data);hashed=await blocking(hash_password,data.password);uid=secrets.token_hex(16);aid=None
            try:
                with db() as c:
                    c.execute('BEGIN IMMEDIATE');current_manager(c,manager,now(),fail)
                    if c.execute('SELECT 1 FROM users WHERE login=?',(login,)).fetchone():fail(409,localized_message('theLoginIsAlreadyInUse'))
                    settings={'profile':{**({} if data.remove_avatar else default_profile()),'display_name':name},'_two_factor_disabled':True,'appearance':new_account_appearance()}
                    if data.avatar_preset:settings['profile']['avatar_preset']=data.avatar_preset
                    c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',(uid,login,hashed,'member',json.dumps(settings),now()))
                    connect(c,admin,uid,data)
                    if body is not None:
                        aid,_=save_avatar(c,avatars,uid,settings,body,now(),fail)
                        c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
                    result={'id':uid,'login':login,'profile':account_profile(c,uid,manager)}
            except BaseException:
                if aid:avatars.delete(aid)
                raise
        return result
    @router.put('/api/admin/users/{uid}/profile')
    async def managed_profile(uid:str,data:ManagedProfile,request:Request):
        manager=proof(request,data.admin_password);admin=manager['user_id'];name=profile_name(data.display_name,fail)
        async with avatar_lock:
            body=await avatar_body(data);aid=None;retired=None
            try:
                with db() as c:
                    c.execute('BEGIN IMMEDIATE');managed_user(c,uid,manager,now(),fail)
                    settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]);settings.setdefault('profile',{})['display_name']=name
                    connect(c,admin,uid,data)
                    if body is not None:aid,retired=save_avatar(c,avatars,uid,settings,body,now(),fail)
                    elif data.remove_avatar or data.avatar_preset is not None:
                        retired=select_preset(c,uid,settings,data.avatar_preset)
                    c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
                    result={'id':uid,'profile':account_profile(c,uid,manager)}
            except BaseException:
                if aid:avatars.delete(aid)
                raise
            if retired:avatars.delete(retired)
        return result

    @router.put('/api/admin/users/{uid}/role')
    def change_role(uid:str,data:ChangeRole,request:Request):
        admin=proof(request,data.admin_password)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if current_manager(c,admin,now(),fail)!='owner':fail(403,localized_message('rolesAreAssignedOnlyByTheWebsiteOwner'))
            user=target(c,uid)
            if user['role']=='owner':fail(409,localized_message('theWebsiteOwnerRoleCannotBeChanged'))
            if user['role']!=data.previous_role:fail(409,localized_message('theRoleHasAlreadyBeenChangedUpdateThe'))
            if data.role==user['role']:return {'role':data.role,'role_label':LABELS[data.role]}
            if account_deletion(c,uid):fail(409,localized_message('firstCancelTheAccountDeletion'))
            if data.role=='admin' and not c.execute('SELECT totp FROM users WHERE id=?',(uid,)).fetchone()[0]:
                fail(409,localized_message('beforeAssigningTheAdministratorRoleTheUserMust'))
            c.execute('UPDATE users SET role=? WHERE id=?',(data.role,uid))
            c.execute('DELETE FROM password_resets WHERE user_id=?',(uid,))
            for row in c.execute('SELECT token FROM sessions WHERE user_id=?',(uid,)).fetchall():
                c.execute('DELETE FROM app_settings WHERE key=?',('admin-access:'+row[0],))
            reset_sessions(c,uid)
            monitor=getattr(protection,'monitor',None)
            if monitor:
                monitor.record(request,'site_role_changed',outcome='success',details={
                    'target_user_id':uid,'previous_role':user['role'],'role':data.role},connection=c)
        return {'role':data.role,'role_label':LABELS[data.role],'sessions_revoked':True}

    @router.put('/api/admin/users/{uid}/quota')
    def quota(uid:str,data:ChangeQuota,request:Request):
        manager=proof(request,data.admin_password)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');managed_user(c,uid,manager,now(),fail)
            from app.quota import usage,summary
            c.execute('INSERT OR IGNORE INTO user_limits(user_id,used_bytes) VALUES(?,?)',(uid,usage(c,uid)))
            c.execute('UPDATE user_limits SET limit_bytes=? WHERE user_id=?',(data.limit_bytes,uid))
            from app.notifications import notify
            notify(c,uid,'quota',{'limit_bytes':data.limit_bytes},now())
            return summary(c,uid)
    @router.put('/api/admin/users/{uid}/password')
    def password(uid:str,data:ChangePassword,request:Request,response:Response):
        s=proof(request,data.admin_password);hashed=hash_password(data.password)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');managed_user(c,uid,s,now(),fail)
            if uid==s['user_id']:tokens.require_trusted(c,s)
            clear_passkeys(c,uid)
            c.execute('UPDATE users SET password=? WHERE id=?',(hashed,uid));reset_sessions(c,uid)
            c.execute('DELETE FROM password_resets WHERE user_id=?',(uid,))
        if uid==s['user_id']:tokens.clear_cookies(response)
        return {'ok':True,'can_manage':True,'current':uid==s['user_id']}

    @router.post('/api/admin/users/{uid}/reset-2fa')
    def reset_2fa(uid:str,data:AdminProof,request:Request,response:Response):
        s=proof(request,data.admin_password)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');managed_user(c,uid,s,now(),fail)
            from app.secret_vault import prevent_factor_reset
            prevent_factor_reset(c,uid,fail)
            if uid==s['user_id']:tokens.require_trusted(c,s)
            clear_passkeys(c,uid)
            user=c.execute('SELECT settings,totp FROM users WHERE id=?',(uid,)).fetchone();settings=json.loads(user['settings']);clear_enrollment(settings);settings.pop('_two_factor_disabled',None)
            if user['totp']:settings['_previous_totp_hash']=hashlib.sha256(cipher.decrypt(user['totp'].encode())).hexdigest()
            c.execute('UPDATE users SET totp=NULL,last_counter=-1,settings=? WHERE id=?',(json.dumps(settings),uid))
            c.execute('DELETE FROM recovery_codes WHERE user_id=?',(uid,));reset_sessions(c,uid)
        if uid==s['user_id']:tokens.clear_cookies(response)
        return {'ok':True,'can_manage':True,'current':uid==s['user_id']}

    @router.post('/api/admin/users/delete')
    async def delete(data:UserAction,request:Request):
        available(request)
        proof(request,data.admin_password)
        async with gate.exclusive():
            admin=proof(request,data.admin_password)
            with db() as c:
                c.execute('BEGIN IMMEDIATE');u=managed_user(c,data.user_id,admin,now(),fail)
                if u['role']=='owner':fail(409,localized_message('theWebsiteOwnerSAccountCannotBeDeleted'))
                if data.confirmation!=u['login']:fail(422,localized_message('toDeleteEnterTheUserSLogin'))
                from app.account_lifecycle import schedule
                deletion=schedule(c,data.user_id,admin['user_id'],now(),fail)
        return {'ok':True,'deletion':deletion}

    @router.post('/api/admin/users/restore')
    def restore_user(data:UserAction,request:Request):
        admin=proof(request,data.admin_password)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');u=managed_user(c,data.user_id,admin,now(),fail)
            if data.confirmation!=u['login']:fail(422,localized_message('enterTheUserSLogin2'))
            from app.account_lifecycle import cancel
            cancel(c,data.user_id,now(),fail)
        return {'ok':True}

    @router.post('/api/admin/backups/export')
    async def export(data:ExportBackup,request:Request):
        available(request)
        proof(request,data.admin_password)
        if heavy.locked():fail(423,localized_message('backupOperationIsAlreadyInProgress'))
        async with heavy:
            temporary=backups.temp()
            try:
                async with gate.exclusive():
                    proof(request,data.admin_password)
                    metadata=await blocking(backups.snapshot,temporary.name)
                file=await blocking(backups.encrypt,temporary.name,metadata,data.backup_password)
                return FileResponse(file,media_type='application/octet-stream',filename='drg-site-'+str(now())+'.drgbackup',background=BackgroundTask(temporary.cleanup))
            except BackupError as error:temporary.cleanup();fail(422,str(error))
            except BaseException:temporary.cleanup();raise

    @router.post('/api/admin/backups/restore')
    async def restore(request:Request,response:Response):
        available(request)
        # The body is raw encrypted data: no backup/passwords in query strings.
        admin_password=secret_header(request,'x-admin-password-b64') if 'x-admin-password-b64' in request.headers else None
        proof(request,admin_password)
        password=secret_header(request,'x-backup-password-b64')
        if not 12<=len(password)<=256:fail(422,localized_message('backupPassword12To256Characters'))
        if request.headers.get('x-restore-confirmation')!='RESTORE':fail(422,localized_message('confirmDataReplacementWithTheWordRestore'))
        maximum=maximum_upload(MAX_BACKUP)
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>maximum:fail(413,localized_message('backupFileIsTooLarge'))
        if heavy.locked():fail(423,localized_message('backupOperationIsAlreadyInProgress'))
        async with heavy:
            with backups.temp() as temporary:
                upload=await receive_archive(request,temporary,MAX_BACKUP,fail)
                try:
                    metadata=await blocking(backups.prepare,upload,password,temporary)
                    async with gate.exclusive():
                        proof(request,admin_password)
                        login=await blocking(backups.restore,temporary,metadata)
                except BackupError as error:fail(422,str(error))
        tokens.clear_cookies(response)
        return {'ok':True,'login':login,'sign_in_again':True}
    return router
