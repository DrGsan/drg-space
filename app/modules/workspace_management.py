"""Workspace team administration and explicit ownership transfer."""
from app.i18n import message as localized_message
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from app.http import Request
from app import workspace_management as management
from app.notifications import actor


class Team(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    members: list[str] = Field(default_factory=list, max_length=50)


class Management(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(strict=True, ge=0)
    advanced: StrictBool
    admins: list[str] = Field(max_length=50)
    teams: dict[str, Team] = Field(max_length=50)


class Transfer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(strict=True, ge=0)
    user_id: str = Field(pattern=r'^[a-f0-9]{32}$')


def payload(c, space, uid, now):
    value = management.get(c, space['id'])
    members = [{**actor(c, r[0], now()), 'space_role': management.role(c, space, r[0], value), 'joined_at': r['created']}
               for r in c.execute('SELECT user_id,created FROM space_members WHERE space_id=?', (space['id'],))]
    return {**value, 'advanced_available': management.advanced_available(c, space['id']), 'owner_id': space['owner_id'], 'role': management.role(c, space, uid, value),
            'manage': management.is_manager(c, space['id'], uid), 'members': members}


def register_management(router, db, ready, now, fail, rate, space_access):
    def space(c, sid, uid, manage=False, allow_pending=False):
        row = space_access(c, sid, uid, fail, allow_pending=allow_pending)
        if row['kind'] != 'family': fail(422, localized_message('commandsAreAvailableInTheManagedWorkspace'))
        if manage and not management.is_manager(c, sid, uid): fail(403, localized_message('availableToTheOwnerAndWorkspaceAdministrators'))
        return row

    @router.get('/api/social/spaces/{sid}/management')
    def read(sid: str, request: Request):
        s = ready(request)
        with db() as c: return payload(c, space(c, sid, s['user_id'], allow_pending=True), s['user_id'], now)

    @router.put('/api/social/spaces/{sid}/management')
    def update(sid: str, data: Management, request: Request):
        s = ready(request); rate(request, 'space-management', 60, 60, subject=s['user_id'])
        value = {'version': 1, **data.model_dump()}
        for team in value['teams'].values(): team['name'] = team['name'].strip()
        try: management.validate(value)
        except ValueError as e: fail(422, str(e))
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); row = space(c, sid, s['user_id'], True); current = management.get(c, sid)
            if data.revision != current['revision']: fail(409, localized_message('settingsHaveBeenChangedRefreshTheWorkspace'))
            if not management.advanced_available(c, sid) and ((data.advanced and not current['advanced']) or value['teams'] != current['teams'] or set(value['admins']) - set(current['admins'])):
                fail(422, localized_message('extendedViewIsOnlyAvailableForProjectsAnd'))
            members = {r[0] for r in c.execute('SELECT user_id FROM space_members WHERE space_id=?', (sid,))}
            if row['owner_id'] in value['admins']: fail(422, localized_message('theOwnerAlreadyHasFullPermissions'))
            if not set(value['admins']) <= members or any(not set(t['members']) <= members for t in value['teams'].values()):
                fail(422, localized_message('selectExistingParticipantsForThisWorkspace'))
            if row['owner_id'] != s['user_id'] and set(value['admins']) != set(current['admins']):
                fail(403, localized_message('administratorsAreAppointedByTheWorkspaceOwner'))
            names = [t['name'].casefold() for t in value['teams'].values()]
            if len(names) != len(set(names)): fail(422, localized_message('teamNamesMustBeUnique'))
            value['revision'] += 1; management.save(c, sid, value)
            for uid in set(current['admins']) - set(value['admins']):
                c.execute('DELETE FROM social_requests WHERE space_id=? AND sender_id=?', (sid, uid))
            management.purge(c)
            from app.workspace_lifecycle import audit,purge
            for uid in set(current['admins']) ^ set(value['admins']):
                audit(c,sid,s['user_id'],'role_changed',{'user_id':uid,'before_role':'admin' if uid in current['admins'] else 'member','after_role':'admin' if uid in value['admins'] else 'member'},now())
            for tid in set(current['teams']) | set(value['teams']):
                before=current['teams'].get(tid);after=value['teams'].get(tid)
                if before!=after:
                    audit(c,sid,s['user_id'],'team_changed',{'team':(after or before)['name'],'before_name':before['name'] if before else None,'after_name':after['name'] if after else None,'added_members':sorted(set((after or {}).get('members',[]))-set((before or {}).get('members',[]))),'removed_members':sorted(set((before or {}).get('members',[]))-set((after or {}).get('members',[])))},now())
            if current['advanced']!=value['advanced']:audit(c,sid,s['user_id'],'management_changed',{'advanced':value['advanced']},now())
            purge(c,now())
            return payload(c, row, s['user_id'], now)

    @router.post('/api/social/spaces/{sid}/ownership')
    def transfer(sid: str, data: Transfer, request: Request):
        s = ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = space(c, sid, s['user_id']); value = management.get(c, sid)
            if row['owner_id'] != s['user_id']: fail(403, localized_message('ownershipIsOnlyTransferredToTheCurrentOwner'))
            if data.revision != value['revision']: fail(409, localized_message('settingsHaveBeenChangedRefreshTheWorkspace'))
            if data.user_id == s['user_id'] or not c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?', (sid, data.user_id)).fetchone():
                fail(422, localized_message('selectAnotherExistingParticipantInTheWorkspace'))
            from app.account_lifecycle import pending
            if pending(c,data.user_id):fail(409,localized_message('theAccountForTheNewOwnerIsPending'))
            value['admins'] = sorted((set(value['admins']) | {s['user_id']}) - {data.user_id})
            value['revision'] += 1
            c.execute('UPDATE spaces SET owner_id=? WHERE id=?', (data.user_id, sid)); management.save(c, sid, value)
            from app.workspace_lifecycle import audit
            audit(c,sid,s['user_id'],'ownership_transferred',{'user_id':data.user_id},now())
        return {'ok': True}
