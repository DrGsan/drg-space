"""Consent-based contacts and owner-managed workspaces, independent of chat."""
from app.i18n import message as localized_message
import secrets, json
from app.notifications import notify,actor
from app.modules.family_avatars import avatar_url,register_avatars,purge_avatars
from app.http import Router, Request
from pydantic import BaseModel, Field, ConfigDict
from app.space_profiles import SpaceTemplate, templates, save as save_template, purge_orphans as purge_profiles
from app import workspace_management as management

SCHEMA='''
CREATE TABLE IF NOT EXISTS spaces(id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('family','friends')),name TEXT NOT NULL,owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,created INTEGER NOT NULL,chat_revision INTEGER NOT NULL DEFAULT 0,chat_ttl INTEGER NOT NULL DEFAULT 0,chat_cleared INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS space_members(space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,created INTEGER NOT NULL,PRIMARY KEY(space_id,user_id));
CREATE INDEX IF NOT EXISTS member_user_idx ON space_members(user_id,space_id);
CREATE TABLE IF NOT EXISTS social_requests(id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('family','friends')),sender_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,recipient_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,space_id TEXT REFERENCES spaces(id) ON DELETE CASCADE,created INTEGER NOT NULL,expires INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS request_unique_idx ON social_requests(kind,sender_id,recipient_id,COALESCE(space_id,''));
CREATE TABLE IF NOT EXISTS friendships(first_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,second_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,space_id TEXT UNIQUE NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,PRIMARY KEY(first_id,second_id),CHECK(first_id<second_id));
CREATE TABLE IF NOT EXISTS collaborative_docs(note_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,state BLOB NOT NULL,epoch TEXT NOT NULL,updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,sender_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,client_id TEXT NOT NULL,body TEXT NOT NULL,created INTEGER NOT NULL,edited INTEGER NOT NULL DEFAULT 0,revision INTEGER NOT NULL DEFAULT 0,expires INTEGER NOT NULL DEFAULT 0,UNIQUE(space_id,sender_id,client_id));
CREATE INDEX IF NOT EXISTS message_room_idx ON messages(space_id,id);
CREATE INDEX IF NOT EXISTS message_revision_idx ON messages(space_id,revision);
'''

class Name(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=80)
class NewSpace(Name):
    template:SpaceTemplate='group'
class Login(BaseModel):
    model_config=ConfigDict(extra='forbid')
    login:str=Field(min_length=3,max_length=40)


def ensure_friendship(c,sender,recipient,stamp,fail):
    """Create one ordinary contact space inside the caller's transaction."""
    a,b=sorted((sender,recipient))
    existing=c.execute('SELECT space_id FROM friendships WHERE first_id=? AND second_id=?',(a,b)).fetchone()
    if existing:return existing['space_id']
    for uid in (a,b):
        if c.execute('SELECT COUNT(*) FROM friendships WHERE first_id=? OR second_id=?',(uid,uid)).fetchone()[0]>=200:fail(422,localized_message('maximum200Friends'))
    sid=secrets.token_urlsafe(12)
    c.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',(sid,'friends','Друзья',sender,stamp))
    for uid in (a,b):c.execute('INSERT INTO space_members VALUES(?,?,?)',(sid,uid,stamp))
    c.execute('INSERT INTO friendships VALUES(?,?,?)',(a,b,sid))
    return sid


def space_access(c,sid,uid,fail,allow_pending=False):
    row=c.execute('SELECT s.* FROM spaces s JOIN space_members m ON s.id=m.space_id WHERE s.id=? AND m.user_id=?',(sid,uid)).fetchone()
    if not row:fail(404,localized_message('theSharedWorkspaceWasNotFound'))
    from app.workspace_lifecycle import pending
    if not allow_pending and pending(c,sid):fail(423,localized_message('theWorkspaceIsAwaitingDeletionTheOwnerCan'))
    return row

def note_access(c,nid,uid,fail,include_deleted=False):
    from app.note_access import can_view
    n=c.execute('SELECT * FROM notes WHERE id=?',(nid,)).fetchone()
    if not n or not can_view(c,n,uid) or (n['deleted'] and not include_deleted):fail(404,localized_message('pageNotFound'))
    return n

def publication_access(c,nid,uid,fail):
    n=note_access(c,nid,uid,fail)
    if n['space_id']:
        s=c.execute('SELECT * FROM spaces WHERE id=?',(n['space_id'],)).fetchone()
        # Workspace managers approve publishing. Either contact can publish their own pages.
        if s['kind']=='family' and not management.is_manager(c,s['id'],uid):fail(403,localized_message('sharedPagesArePublishedByTheOwnerOr'))
        if s['kind']=='friends' and n['user_id']!=uid:fail(403,localized_message('thePageCreatorCanPublishASharedPage'))
    return n


def clean_chat_widgets(c):
    """Membership removal must not leave unrestorable widget configurations."""
    purge_avatars(c)
    purge_profiles(c)
    from app.note_access import purge_orphans
    purge_orphans(c)
    for user in c.execute('SELECT id,settings FROM users').fetchall():
        settings=json.loads(user['settings']);layout=settings.get('dashboard');changed=False
        if not layout:continue
        for item in layout.get('instances',[]):
            sid=item.get('config',{}).get('space_id') if item.get('module_id')=='chat' else None
            if sid and not c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?',(sid,user['id'])).fetchone():
                item['config']['space_id']=None;changed=True
        if changed:
            layout['version']+=1
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),user['id']))


def create_social_router(db,ready,now,fail,rate):
    r=Router()
    def user(c,login):
        row=c.execute('SELECT id,login FROM users WHERE login=?',(login.strip().lower(),)).fetchone()
        if not row:fail(404,localized_message('theUserWithThisLoginWasNotFound'))
        return row
    def family_owner(c,sid,uid):
        row=space_access(c,sid,uid,fail)
        if row['kind']!='family' or not management.is_manager(c,sid,uid):fail(403,localized_message('availableToTheOwnerOrWorkspaceAdministrator'))
        return row
    def invite(c,kind,sender,target,sid=None):
        if sender==target:fail(422,localized_message('youCannotInviteYourself'))
        c.execute('DELETE FROM social_requests WHERE expires<=?',(now(),))
        if c.execute('SELECT COUNT(*) FROM social_requests WHERE sender_id=?',(sender,)).fetchone()[0]>=100:fail(429,localized_message('tooManyPeopleAreWaitingForAnInvitation'))
        if c.execute("SELECT 1 FROM social_requests WHERE kind=? AND sender_id=? AND recipient_id=? AND space_id IS ?",(kind,sender,target,sid)).fetchone():fail(409,localized_message('theInvitationHasAlreadyBeenSent'))
        if kind=='friends':
            a,b=sorted((sender,target))
            if c.execute('SELECT 1 FROM friendships WHERE first_id=? AND second_id=?',(a,b)).fetchone():fail(409,localized_message('youAreAlreadyFriends'))
            if c.execute("SELECT 1 FROM social_requests WHERE kind='friends' AND sender_id=? AND recipient_id=?",(target,sender)).fetchone():fail(409,localized_message('thisUserHasAlreadyInvitedYouAcceptTheir'))
        else:
            if c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?',(sid,target)).fetchone():fail(409,localized_message('userIsAlreadyInTheWorkspace'))
        rid=secrets.token_urlsafe(12)
        c.execute('INSERT INTO social_requests VALUES(?,?,?,?,?,?,?)',(rid,kind,sender,target,sid,now(),now()+7*86400))
        notify(c,target,'invitation',{'request_id':rid,'actor_id':sender,'actor':c.execute('SELECT login FROM users WHERE id=?',(sender,)).fetchone()[0],'space_id':sid,'kind':kind,'space_name':c.execute('SELECT name FROM spaces WHERE id=?',(sid,)).fetchone()[0] if sid else ''},now())
        if sid:
            from app.workspace_lifecycle import audit
            audit(c,sid,sender,'member_invited',{'user_id':target},now())
        return {'id':rid}
    @r.get('/api/social')
    def overview(request:Request):
        s=ready(request);uid=s['user_id']
        with db() as c:
            spaces=[];profiles=templates(c)
            from app.note_access import access_index
            index=access_index(c)
            for row in c.execute('SELECT s.*,u.login AS owner_login FROM spaces s JOIN space_members m ON m.space_id=s.id JOIN users u ON u.id=s.owner_id WHERE m.user_id=? ORDER BY s.created DESC',(uid,)):
                item=dict(row);item['owner']=item.pop('owner_id')==uid
                item['template']=profiles.get(row['id'],'family') if row['kind']=='family' else None
                config=management.get(c,row['id']) if row['kind']=='family' else management.default()
                item.update(manage=management.is_manager(c,row['id'],uid),advanced=config['advanced'],role=management.role(c,row,uid,config),teams=config['teams'],advanced_available=management.advanced_available(c,row['id']) if row['kind']=='family' else False,owner_user_id=row['owner_id'])
                from app.workspace_lifecycle import pending
                item['deletion']=pending(c,row['id'])
                item['avatar_url']=avatar_url(c,row['id']) if row['kind']=='family' else None
                item['members']=sorted([{**actor(c,m['id'],now()),'joined_at':m['joined_at']} for m in c.execute('SELECT u.id,u.login,m.created AS joined_at FROM users u JOIN space_members m ON m.user_id=u.id WHERE m.space_id=?',(row['id'],))],key=lambda member:member['login'])
                for person in item['members']:person['space_role']=management.role(c,row,person['id'],config)
                if item['kind']=='friends':item['name']=next(((m.get('display_name') or m['login']) for m in item['members'] if m['id']!=uid),'Друг')
                item['notes']=sum(n['space_id']==row['id'] and not n['deleted'] and index.visible(n,uid) for n in index.pages.values());spaces.append(item)
            pending=[dict(row)|{'incoming':row['recipient_id']==uid} for row in c.execute('SELECT r.*,a.login AS sender,b.login AS recipient,s.name AS space_name FROM social_requests r JOIN users a ON a.id=r.sender_id JOIN users b ON b.id=r.recipient_id LEFT JOIN spaces s ON s.id=r.space_id WHERE (r.sender_id=? OR r.recipient_id=?) AND r.expires>? ORDER BY r.created DESC',(uid,uid,now()))]
        return {'spaces':spaces,'requests':pending}
    def create_space(data,request,template):
        s=ready(request);rate(request,'social',30,subject=s['user_id']);name=data.name.strip()
        if not name:fail(422,localized_message('enterTheNameOfTheWorkspace'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT COUNT(*) FROM space_members m JOIN spaces s ON s.id=m.space_id WHERE m.user_id=? AND s.kind='family'",(s['user_id'],)).fetchone()[0]>=20:fail(422,localized_message('noMoreThan20Workspaces'))
            sid=secrets.token_urlsafe(12);c.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',(sid,'family',name,s['user_id'],now()));c.execute('INSERT INTO space_members VALUES(?,?,?)',(sid,s['user_id'],now()))
            save_template(c,sid,template)
            if template=='organization':management.save(c,sid,{**management.default(),'advanced':True,'revision':1})
        return {'id':sid}
    @r.post('/api/social/spaces')
    def create(data:NewSpace,request:Request):
        return create_space(data,request,data.template)
    @r.post('/api/social/families')
    def create_family(data:Name,request:Request):
        return create_space(data,request,'family')
    @r.post('/api/social/friends')
    def add_friend(data:Login,request:Request):
        s=ready(request);rate(request,'social',30,subject=s['user_id'])
        with db() as c:
            c.execute('BEGIN IMMEDIATE');target=user(c,data.login);return invite(c,'friends',s['user_id'],target['id'])
    @r.post('/api/social/spaces/{sid}/invites')
    @r.post('/api/social/families/{sid}/invites')
    def add_member(sid:str,data:Login,request:Request):
        s=ready(request);rate(request,'social',30,subject=s['user_id'])
        with db() as c:
            c.execute('BEGIN IMMEDIATE');family_owner(c,sid,s['user_id']);target=user(c,data.login);return invite(c,'family',s['user_id'],target['id'],sid)
    @r.post('/api/social/requests/{rid}/accept')
    def accept(rid:str,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');q=c.execute('SELECT * FROM social_requests WHERE id=? AND recipient_id=? AND expires>?',(rid,s['user_id'],now())).fetchone()
            if not q:fail(404,localized_message('invitationNotFoundOrExpired'))
            if q['kind']=='family':
                sid=q['space_id']
                family_owner(c,sid,q['sender_id'])
                if c.execute('SELECT COUNT(*) FROM space_members WHERE space_id=?',(sid,)).fetchone()[0]>=50:fail(422,localized_message('theWorkspaceHasNoMoreThan50Members'))
                if c.execute("SELECT COUNT(*) FROM space_members m JOIN spaces s ON s.id=m.space_id WHERE m.user_id=? AND s.kind='family'",(s['user_id'],)).fetchone()[0]>=20:fail(422,localized_message('noMoreThan20Workspaces'))
                c.execute('INSERT OR IGNORE INTO space_members VALUES(?,?,?)',(sid,s['user_id'],now()))
                from app.workspace_lifecycle import audit
                audit(c,sid,s['user_id'],'invitation_accepted',{},now())
            else:
                sid=ensure_friendship(c,q['sender_id'],q['recipient_id'],now(),fail)
            c.execute('DELETE FROM social_requests WHERE id=?',(rid,))
        return {'space_id':sid}
    @r.delete('/api/social/requests/{rid}')
    def decline(rid:str,request:Request):
        s=ready(request)
        with db() as c:c.execute('DELETE FROM social_requests WHERE id=? AND (sender_id=? OR recipient_id=?)',(rid,s['user_id'],s['user_id']))
        return {'ok':True}
    @r.patch('/api/social/spaces/{sid}')
    @r.patch('/api/social/families/{sid}')
    def rename(sid:str,data:Name,request:Request):
        s=ready(request)
        if not data.name.strip():fail(422,localized_message('enterTheNameOfTheWorkspace'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');family_owner(c,sid,s['user_id']);c.execute('UPDATE spaces SET name=? WHERE id=?',(data.name.strip(),sid))
            from app.workspace_lifecycle import audit
            audit(c,sid,s['user_id'],'renamed',{},now())
        return {'ok':True}
    @r.delete('/api/social/spaces/{sid}')
    def remove(sid:str,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');space=space_access(c,sid,s['user_id'],fail,allow_pending=True)
            if space['kind']=='family' and space['owner_id']!=s['user_id']:fail(403,localized_message('theWorkspaceOwnerCanDeleteIt'))
            from app.workspace_lifecycle import schedule
            deletion=schedule(c,space,s['user_id'],now())
        return {'ok':True,'deletion':deletion}
    @r.delete('/api/social/spaces/{sid}/members/{uid}')
    @r.delete('/api/social/families/{sid}/members/{uid}')
    def leave(sid:str,uid:str,request:Request,revoke_publications:bool=False):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');space=space_access(c,sid,s['user_id'],fail)
            if space['kind']!='family':fail(404,localized_message('workspaceNotFound'))
            if uid!=s['user_id'] and not management.is_manager(c,sid,s['user_id']):fail(403,localized_message('theWorkspaceAdministratorCanRemoveParticipants'))
            if uid!=s['user_id'] and space['owner_id']!=s['user_id'] and management.role(c,space,uid)!='member':fail(403,localized_message('theWorkspaceOwnerRemovesTheParticipant'))
            if uid==space['owner_id']:fail(409,localized_message('firstTransferOwnershipToAnotherParticipantOrDelete'))
            from app.workspace_lifecycle import audit,revoke_links,preview
            consequences=preview(c,sid,uid)
            from app.tracker import offboard_projects
            offboard_projects(c,sid,uid,space['owner_id'],s['user_id'],now())
            from app.note_access import access_index
            if access_index(c).work_space(sid):
                from app.workspace_lifecycle import transfer_shared_notes
                transfer_shared_notes(c,uid,fail,now(),space_id=sid)
            if revoke_publications:
                if not management.is_manager(c,sid,s['user_id']):fail(403,localized_message('theWorkspaceAdministratorRetractsThePublication'))
                revoke_links(c,sid)
            audit(c,sid,s['user_id'],'member_removed',{'user_id':uid,'publications_revoked':revoke_publications,**consequences},now())
            from app.note_access import revoke_workspace_grants
            revoke_workspace_grants(c,sid,uid)
            c.execute('DELETE FROM chat_reads WHERE space_id=? AND user_id=?',(sid,uid));c.execute('DELETE FROM space_members WHERE space_id=? AND user_id=?',(sid,uid));clean_chat_widgets(c)
            c.execute('DELETE FROM social_requests WHERE space_id=? AND sender_id=?',(sid,uid))
            from app.workspace_lifecycle import purge
            purge(c,now())
        return {'ok':True}
    from app.modules.workspace_lifecycle import register_lifecycle
    register_lifecycle(r,db,ready,now,fail,rate,space_access)
    register_avatars(r,db,ready,space_access,family_owner,rate,fail)
    from app.modules.workspace_management import register_management
    register_management(r,db,ready,now,fail,rate,space_access)
    return r
