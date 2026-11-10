"""Manage page guests without modifying families, friendships or accounts."""
from app.i18n import message as localized_message
from pydantic import BaseModel,Field,ConfigDict
from typing import Literal
from app.http import Router,Request
from app.modules.social import note_access
from app.modules.notion import can_manage,page_role
from app.note_access import access_index,participants,save_rule
from app.notifications import actor
from app import workspace_management as management

class Invitation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    logins:list[str]=Field(min_length=1,max_length=20)
    role:Literal['viewer','commenter','editor']='editor'
class Audience(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(strict=True,ge=0)
    mode:Literal['inherit','workspace','restricted']
    teams:dict[str,Literal['viewer','commenter','editor']]=Field(default_factory=dict,max_length=50)
    members:dict[str,Literal['viewer','commenter','editor']]=Field(default_factory=dict,max_length=50)

def permission_payload(c,n,uid,stamp):
    members=[]
    index=access_index(c);manage=can_manage(c,n,uid)
    for identity in participants(c,n):
        if not c.execute('SELECT 1 FROM users WHERE id=?',(identity,)).fetchone():continue
        profile=actor(c,identity,stamp)
        override=c.execute('SELECT role FROM note_permissions WHERE note_id=? AND user_id=?',(n['id'],identity)).fetchone()
        members.append({**profile,'role':page_role(c,n,identity),'override':override[0] if override else None,'manager':can_manage(c,n,identity),'protected':identity==n['user_id'] and not index.work_space(n['space_id']),'owner':n['user_id']==identity,'sources':index.sources(n,identity) if manage or identity==uid else []})
    members.sort(key=lambda u:(not u['owner'],u['login']))
    supported=index.kinds.get(n['space_id'])=='family' and uid in index.members.get(n['space_id'],set())
    return {'role':page_role(c,n,uid),'manage':manage,'members':members,'audience_supported':supported and (management.advanced_available(c,n['space_id']) or index.scope(n)[1] is not None),'has_audience':index.scope(n)[1] is not None,'work_space':index.work_space(n['space_id']),'advanced':management.advanced_available(c,n['space_id']) and index.management.get(n['space_id'],management.default())['advanced']}

def create_note_members_router(db,ready,rate,now,fail):
    r=Router()
    def manageable(c,nid,uid):
        n=note_access(c,nid,uid,fail)
        if not can_manage(c,n,uid):fail(403,localized_message('participantsAreManagedByThePageAdministratorIn'))
        if not n['space_id']:fail(422,localized_message('addingParticipantsIsAvailableForSharedPages'))
        return n
    def audience_page(c,nid,uid):
        n=manageable(c,nid,uid);index=access_index(c)
        if index.kinds.get(n['space_id'])!='family' or uid not in index.members.get(n['space_id'],set()):fail(403,localized_message('teamSettingsAreAvailableToThePageAdministrator'))
        return n
    @r.get('/api/notes/{nid}/audience')
    def audience(nid:str,request:Request):
        s=ready(request)
        with db() as c:
            n=audience_page(c,nid,s['user_id']);config=management.get(c,n['space_id'])
            index=access_index(c)
            return {**management.get_audience(c,nid),'manager_ids':sorted({*([n['user_id']] if not index.work_space(n['space_id']) else []),index.spaces[n['space_id']],*config['admins']}),'teams_catalog':[{'id':tid,'name':team['name'],'count':len(team['members'])} for tid,team in config['teams'].items()],
                    'people_catalog':[actor(c,row[0],now()) for row in c.execute('SELECT user_id FROM space_members WHERE space_id=?',(n['space_id'],))]}
    @r.post('/api/notes/{nid}/audience/preview')
    def preview_audience(nid:str,data:Audience,request:Request):
        import copy
        s=ready(request);rate(request,'note-audience-preview',60,60,subject=s['user_id'])
        value={'version':1,**data.model_dump()}
        try:management.validate_audience(value)
        except ValueError as e:fail(422,str(e))
        with db() as c:
            n=audience_page(c,nid,s['user_id']);index=access_index(c);current=management.get_audience(c,nid)
            if current['revision']!=data.revision:fail(409,localized_message('accessSettingsHaveBeenChangedOpenTheMenu'))
            config=management.get(c,n['space_id']);members=index.members.get(n['space_id'],set())
            if not set(value['teams'])<=set(config['teams']) or not set(value['members'])<=members:fail(422,localized_message('selectTheTeamsAndParticipantsForThisWorkspace'))
            if value['mode']=='restricted' and n['user_id'] in members and not index.work_space(n['space_id']):value['members'][n['user_id']]='editor'
            after=copy.deepcopy(index);after.audiences[nid]=value;after.scopes={}
            pages=[p for p in index.pages.values() if not p['deleted'] and any(a['id']==nid for a in index.chain(p))]
            identities=set(members)
            for p in pages:identities.update(index.participants(p));identities.update(after.participants(p))
            changes=[]
            for uid in sorted(identities):
                affected=sum((index.role(p,uid) if index.visible(p,uid) else None)!=(after.role(p,uid) if after.visible(p,uid) else None) for p in pages)
                if affected:changes.append({**actor(c,uid,now()),'before':index.role(n,uid) if index.visible(n,uid) else None,'after':after.role(n,uid) if after.visible(n,uid) else None,'pages':affected})
            return {'changes':changes,'pages':len(pages)}
    @r.put('/api/notes/{nid}/audience')
    def set_audience(nid:str,data:Audience,request:Request):
        s=ready(request);rate(request,'note-audience',60,60,subject=s['user_id'])
        value={'version':1,**data.model_dump()}
        try:management.validate_audience(value)
        except ValueError as e:fail(422,str(e))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=audience_page(c,nid,s['user_id']);current=management.get_audience(c,nid)
            if current['revision']!=data.revision:fail(409,localized_message('accessSettingsHaveBeenChangedOpenTheMenu'))
            config=management.get(c,n['space_id']);members=access_index(c).members.get(n['space_id'],set())
            if not set(value['teams'])<=set(config['teams']) or not set(value['members'])<=members:fail(422,localized_message('selectTheTeamsAndParticipantsForThisWorkspace'))
            if value['mode']=='restricted' and n['user_id'] in members and not access_index(c).work_space(n['space_id']):value['members'][n['user_id']]='editor'
            value['revision']+=1;management.save_audience(c,nid,value)
            from app.workspace_lifecycle import audit
            audit(c,n['space_id'],s['user_id'],'audience_changed',{'note_id':nid,'before_access':{k:current[k] for k in ('mode','teams','members')},'after_access':{k:value[k] for k in ('mode','teams','members')}},now())
        return {'ok':True}
    @r.get('/api/notes/{nid}/member-options')
    def options(nid:str,request:Request):
        s=ready(request)
        with db() as c:
            n=manageable(c,nid,s['user_id']);current=participants(c,n)
            uid=s['user_id']
            choices={u['id'] for u in c.execute('SELECT CASE WHEN first_id=? THEN second_id ELSE first_id END AS id FROM friendships WHERE first_id=? OR second_id=?',(uid,uid,uid))}
            choices.update(access_index(c).members.get(n['space_id'],set()))
            return [actor(c,identity,now()) for identity in sorted(choices-current)]
    @r.post('/api/notes/{nid}/members')
    def invite(nid:str,data:Invitation,request:Request):
        s=ready(request);rate(request,'note-members',40,60,subject=s['user_id'])
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=manageable(c,nid,s['user_id']);targets=[]
            for login in dict.fromkeys(login.strip().lower() for login in data.logins):
                if not login or len(login)>40:fail(422,localized_message('enterTheUserSLogin'))
                user=c.execute('SELECT id FROM users WHERE login=?',(login,)).fetchone()
                if not user:fail(404,'Пользователь @'+login+' не найден.')
                if (user['id']==n['user_id'] and not access_index(c).work_space(n['space_id'])) or can_manage(c,n,user['id']):fail(422,localized_message('theOwnerAlreadyHasFullAccess'))
                targets.append(user['id'])
            current=participants(c,n)
            for uid in targets:
                if uid not in current and uid not in access_index(c).members.get(n['space_id'],set()) and not c.execute('SELECT 1 FROM friendships WHERE first_id=? AND second_id=?',tuple(sorted((s['user_id'],uid)))).fetchone():
                    fail(403,localized_message('youCanOnlyAddFriendsToSharedNotes'))
            from app.workspace_lifecycle import audit
            for uid in targets:
                audit(c,n['space_id'],s['user_id'],'page_members_changed',{'note_id':nid,'user_id':uid,'after_role':data.role},now())
                save_rule(c,nid,uid,data.role)
                c.execute('DELETE FROM note_permissions WHERE note_id=? AND user_id=?',(nid,uid))
        return {'ok':True}
    @r.delete('/api/notes/{nid}/members/{uid}')
    def remove(nid:str,uid:str,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=manageable(c,nid,s['user_id'])
            if (uid==n['user_id'] and not access_index(c).work_space(n['space_id'])) or can_manage(c,n,uid):fail(422,localized_message('thePageOrWorkspaceOwnerCannotBeDeleted'))
            if uid not in participants(c,n):fail(404,localized_message('userNotFound'))
            from app.workspace_lifecycle import audit
            audit(c,n['space_id'],s['user_id'],'page_members_changed',{'note_id':nid,'user_id':uid,'after_role':'none'},now())
            # Remove explicit grants further down the branch as well, so an
            # already shared child cannot silently keep the removed guest in.
            index=access_index(c)
            ids=[page['id'] for page in index.pages.values() if any(a['id']==nid for a in index.chain(page))]
            for identity in ids:
                save_rule(c,identity,uid,'none' if identity==nid else None)
                c.execute('DELETE FROM note_permissions WHERE note_id=? AND user_id=?',(identity,uid))
                audience=management.get_audience(c,identity)
                if uid in audience['members']:
                    audience['members'].pop(uid);audience['revision']+=1;management.save_audience(c,identity,audience)
        return {'ok':True}
    return r
