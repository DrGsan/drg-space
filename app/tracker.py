"""Project tracker domain and authorization shared by HTTP and federation.

Completed issues have no TTL. Explicit branch deletion has seven-day recovery,
separate from project removal. History and counters are committed
in the same transaction as the corresponding change.
"""
import json
import re
import secrets
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal
from app.http import HTTPError
from app.i18n import message as localized_message
from app.tracker_issue_lifecycle import visible

IDENTITY = r'^[A-Za-z0-9_-]{16}$'
USER = r'^[a-f0-9]{32}$'
STATES = ('todo', 'work', 'review', 'done', 'cancelled')
ROLES = {'viewer': 1, 'editor': 2, 'manager': 3, 'owner': 4}


def dump(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
def fail(code, key): raise HTTPError(code, localized_message(key))


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Project(Strict):
    name: str = Field(min_length=1, max_length=100)
    key: str = Field(pattern=r'^[A-Z][A-Z0-9]{1,9}$')
    description: str = Field(default='', max_length=5000)
    space_id: str | None = Field(default=None, pattern=IDENTITY)
    avatar: str = Field(default='',max_length=1400000)

    @field_validator('avatar')
    @classmethod
    def valid_avatar(cls,value):
        from app.tracker_avatar import normalize
        return normalize(value)

    @field_validator('name')
    @classmethod
    def nonempty(cls, value):
        if not value.strip(): raise ValueError(localized_message('trackerNameRequired'))
        return value.strip()


class Issue(Strict):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default='', max_length=100000)
    status: Literal['todo', 'work', 'review', 'done', 'cancelled'] = 'todo'
    kind: Literal['task', 'bug', 'improvement', 'epic'] = 'task'
    parent_id: str | None = Field(default=None, pattern=IDENTITY)
    group_id: str | None = Field(default=None,pattern=IDENTITY)
    assignee_id: str = Field(default='', max_length=32)
    assignee_peer: str = Field(default='', max_length=32)
    priority: int = Field(default=0, ge=0, le=3)
    due: str = Field(default='', max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=20)
    url: str = Field(default='', max_length=2000)
    merge_request_url: str = Field(default='',max_length=2000)
    time: str = Field(default='', max_length=5)
    flagged: bool = False
    cancellation_reason: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def valid_cancellation(self):
        if self.status!='cancelled' and self.cancellation_reason:raise ValueError(localized_message('trackerInvalidIssue'))
        return self

    @field_validator('title')
    @classmethod
    def nonempty(cls, value):
        if not value.strip(): raise ValueError(localized_message('trackerNameRequired'))
        return value.strip()

    @field_validator('due')
    @classmethod
    def valid_date(cls, value):
        if value:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value): raise ValueError(localized_message('trackerInvalidDate'))
            date.fromisoformat(value)
        return value

    @field_validator('assignee_id', 'assignee_peer')
    @classmethod
    def valid_actor(cls, value):
        if value and not re.fullmatch(USER, value): raise ValueError(localized_message('trackerInvalidMember'))
        return value

    @field_validator('tags')
    @classmethod
    def clean_tags(cls, values):
        result = []
        for value in values:
            value = value.strip().lstrip('#')
            if not value or len(value) > 40 or any(ord(c) < 32 for c in value):
                raise ValueError(localized_message('trackerInvalidTags'))
            if value.casefold() not in {item.casefold() for item in result}: result.append(value)
        return result

    @field_validator('url','merge_request_url')
    @classmethod
    def valid_url(cls, value):
        from app.modules.tasks import TaskPayload
        return TaskPayload.safe_url(value)

    @field_validator('time')
    @classmethod
    def valid_time(cls, value):
        if value and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError(localized_message('trackerInvalidDate'))
        return value


class IssueUpdate(Strict):
    version: int = Field(ge=1)
    title: str | None = None
    description: str | None = None
    status: Literal['todo', 'work', 'review', 'done', 'cancelled'] | None = None
    kind: Literal['task', 'bug', 'improvement', 'epic'] | None = None
    parent_id: str | None = None
    group_id: str | None = None
    assignee_id: str | None = None
    assignee_peer: str | None = None
    priority: int | None = None
    due: str | None = None
    tags: list[str] | None = None
    url: str | None = None
    merge_request_url: str | None = None
    time: str | None = None
    flagged: bool | None = None
    cancellation_reason: str | None = Field(default=None, max_length=2000)
    position: int | None = Field(default=None, ge=0, le=2**53-1)


class Comment(Strict):
    body: str = Field(min_length=1, max_length=10000)

    @field_validator('body')
    @classmethod
    def nonempty(cls, value):
        if not value.strip(): raise ValueError(localized_message('trackerNameRequired'))
        return value.strip()


class Member(Strict):
    actor_id: str = Field(pattern=USER)
    peer_id: str = Field(default='', max_length=32)
    role: Literal['viewer', 'editor', 'manager'] = 'editor'

    @field_validator('peer_id')
    @classmethod
    def valid_peer(cls, value):
        if value and not re.fullmatch(USER, value): raise ValueError(localized_message('trackerInvalidMember'))
        return value


class Group(Strict):
    name: str = Field(min_length=1,max_length=100)
    description: str = Field(default='',max_length=100000)

    @field_validator('name')
    @classmethod
    def nonempty(cls,value):
        if not value.strip():raise ValueError(localized_message('trackerNameRequired'))
        return value.strip()


class GroupUpdate(Group):
    version: int = Field(ge=1)


def group_row(c,gid,pid=None):
    row=c.execute('SELECT * FROM tracker_groups WHERE id=?',(gid,)).fetchone()
    if not row or pid is not None and row['project_id']!=pid:fail(404,'trackerGroupNotFound')
    return dict(row)


def serialize_group(c,row,detail=False):
    payload=json.loads(row['payload'])
    if not detail:payload.pop('description',None)
    result={k:row[k] for k in ('id','project_id','position','created','updated','version')} | payload
    result['counts']={s:0 for s in STATES}
    for r in c.execute('SELECT i.status,COUNT(*) n FROM tracker_issues i WHERE i.project_id=? AND i.group_id=? AND '+visible('i')+' GROUP BY i.status',(row['project_id'],row['id'])):result['counts'][r['status']]=r['n']
    if detail:
        result['history']=[]
        for item in c.execute('SELECT * FROM tracker_history WHERE group_id=? ORDER BY created DESC,id DESC LIMIT 50',(row['id'],)):
            event=dict(item);value=json.loads(item['payload'])
            # Keep the full audit encrypted in storage; don't return repeated
            # descriptions or the original conversion snapshot over federation.
            event['payload']={k:{f:v for f,v in part.items() if f=='name'} for k,part in value.items() if k in ('before','after') and isinstance(part,dict)}
            result['history'].append(event)
    return result


def lock_owner(c,uid):
    # Keys are encrypted, so their owner-scoped uniqueness is checked while
    # holding this row lock. SQLite already has BEGIN IMMEDIATE at the boundary.
    if c.backend.kind=='postgres':c.raw.execute('SELECT id FROM users WHERE id=%s FOR UPDATE',(uid,))


def lock_project(c,pid):
    # All tree/group changes serialize on this row on both engines.
    c.execute('UPDATE tracker_projects SET version=version WHERE id=?',(pid,))


def create_group(c,pid,data,uid,stamp,peer_id=''):
    access(c,pid,uid,peer_id,required='manager');lock_project(c,pid)
    access(c,pid,uid,peer_id,required='manager')
    if c.execute('SELECT COUNT(*) FROM tracker_groups WHERE project_id=?',(pid,)).fetchone()[0]>=200:fail(422,'trackerGroupLimit')
    gid=secrets.token_urlsafe(12)
    position=c.execute('SELECT COALESCE(MAX(position),0)+1024 FROM tracker_groups WHERE project_id=?',(pid,)).fetchone()[0]
    c.execute('INSERT INTO tracker_groups(id,project_id,payload,position,created,updated) VALUES(?,?,?,?,?,?)',(gid,pid,dump(data.model_dump()),position,stamp,stamp))
    c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?',(stamp,pid))
    group_history(c,pid,gid,uid,peer_id,'group_created',{},stamp)
    return serialize_group(c,group_row(c,gid),True)


def update_group(c,gid,data,uid,stamp,peer_id=''):
    row=group_row(c,gid);access(c,row['project_id'],uid,peer_id,required='manager');lock_project(c,row['project_id'])
    access(c,row['project_id'],uid,peer_id,required='manager');row=group_row(c,gid)
    if row['version']!=data.version:fail(409,'trackerConflict')
    payload=data.model_dump(exclude={'version'})
    result=c.execute('UPDATE tracker_groups SET payload=?,updated=?,version=version+1 WHERE id=? AND version=?',(dump(payload),stamp,gid,data.version))
    if result.rowcount!=1:fail(409,'trackerConflict')
    group_history(c,row['project_id'],gid,uid,peer_id,'group_updated',{'before':json.loads(row['payload']),'after':payload},stamp)
    c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?',(stamp,row['project_id']))
    return serialize_group(c,group_row(c,gid),True)


def group_history(c,pid,gid,uid,peer,event,payload,stamp):
    c.execute('INSERT INTO tracker_history(id,project_id,group_id,actor_id,peer_id,event,payload,created) VALUES(?,?,?,?,?,?,?,?)',(secrets.token_urlsafe(12),pid,gid,uid,peer,event,dump(payload),stamp))


def project_row(c, pid):
    row = c.execute('SELECT * FROM tracker_projects WHERE id=?', (pid,)).fetchone()
    if not row: fail(404, 'trackerProjectNotFound')
    return dict(row)


def role(c, project, uid, peer_id='', *, pending=False, include_deleted=False):
    from app.account_lifecycle import pending as deleting_account
    from app.workspace_lifecycle import pending as deleting_space
    if deleting_account(c, project['owner_id']): return None
    if project['space_id'] and deleting_space(c, project['space_id']): return None
    if not peer_id and deleting_account(c, uid): return None
    from app.tracker_lifecycle import pending as deleting_project
    if deleting_project(c, project['id']) and not (include_deleted and not peer_id and project['owner_id'] == uid): return None
    if not peer_id and project['owner_id'] == uid: return 'owner'
    if not peer_id and project['space_id'] and not c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?', (project['space_id'], uid)).fetchone(): return None
    if peer_id and not c.execute("SELECT 1 FROM federation_contacts f JOIN federation_peers p ON p.id=f.peer_id WHERE f.user_id=? AND f.peer_id=? AND f.actor_id=? AND f.status='accepted' AND p.active=1", (project['owner_id'], peer_id, uid)).fetchone(): return None
    membership = c.execute('SELECT role,state FROM tracker_members WHERE project_id=? AND actor_id=? AND peer_id=?',
                           (project['id'], uid, peer_id)).fetchone()
    if membership:
        if membership['state'] == 'active' or pending and membership['state'] == 'pending': return membership['role']
        return None
    if not peer_id and project['space_id']:
        if c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?', (project['space_id'], uid)).fetchone():
            from app.workspace_management import is_manager
            return 'manager' if is_manager(c, project['space_id'], uid) else 'editor'
    return None


def access(c, pid, uid, peer_id='', *, required='viewer'):
    project = project_row(c, pid)
    actual = role(c, project, uid, peer_id)
    if actual is None: fail(404, 'trackerProjectNotFound')
    if ROLES[actual] < ROLES[required]: fail(403, 'trackerReadOnly')
    return project


def offboard_projects(c, sid, uid, new_owner, actor_id, stamp):
    """Keep common projects in their workspace when a member leaves/is removed."""
    for project in c.execute('SELECT id FROM tracker_projects WHERE space_id=? AND owner_id=?', (sid, uid)).fetchall():
        pid=project['id']
        c.execute('UPDATE tracker_projects SET owner_id=?,updated=?,version=version+1 WHERE id=?', (new_owner, stamp, pid))
        # Federation contacts belong to the former owner; never inherit grants
        # merely because the new owner happens to know the same remote actor.
        c.execute("UPDATE tracker_members SET state='revoked',revision=revision+1 WHERE project_id=? AND (peer_id!='' OR actor_id=?)", (pid, uid))
        c.execute('UPDATE files SET user_id=? WHERE id IN (SELECT id FROM tracker_attachments WHERE issue_id IN (SELECT id FROM tracker_issues WHERE project_id=?)) OR id IN (SELECT id FROM tracker_issues WHERE project_id=?)', (new_owner, pid, pid))
        c.quota_recount.update((uid, new_owner))
        c.quota_transfer_owners=getattr(c,'quota_transfer_owners',set()) | {new_owner}
        history(c,pid,None,actor_id,'','ownership_transferred',{'actor_id':new_owner,'reason':'workspace_offboarding'},stamp)


def person(c, uid, peer_id='', owner_id=None, stamp=0):
    if not uid: return None
    if not peer_id:
        from app.notifications import actor
        return actor(c, uid, stamp) | {'actor_id': uid, 'peer_id': ''}
    row = c.execute('SELECT profile FROM federation_profiles WHERE user_id=? AND peer_id=? AND actor_id=?',
                    (owner_id, peer_id, uid)).fetchone()
    profile = json.loads(row[0]) if row else {}
    return {'id': uid, 'actor_id': uid, 'peer_id': peer_id, 'login': profile.get('login', ''),
            'display_name': profile.get('display_name', ''), 'avatar_url': None}


def members(c, project, stamp):
    result = {(project['owner_id'], ''): {'actor_id': project['owner_id'], 'peer_id': '', 'role': 'owner', 'state': 'active', 'revision': 0}}
    if project['space_id']:
        for row in c.execute('SELECT user_id FROM space_members WHERE space_id=?', (project['space_id'],)):
            if (row[0], '') not in result:
                result[(row[0], '')] = {'actor_id': row[0], 'peer_id': '', 'role': role(c, project, row[0]), 'state': 'active', 'revision': 0}
    for row in c.execute('SELECT actor_id,peer_id,role,state,revision FROM tracker_members WHERE project_id=?', (project['id'],)):
        if row['actor_id'] == project['owner_id'] and not row['peer_id']: continue
        result[(row['actor_id'], row['peer_id'])] = dict(row)
    return [item | {'person': person(c, item['actor_id'], item['peer_id'], project['owner_id'], stamp)}
            for item in result.values() if item['state'] != 'revoked']


def serialize_project(c, row, uid, stamp, peer_id='', invitation=False, include_members=True, include_deleted=False):
    actual = role(c, row, uid, peer_id, pending=invitation, include_deleted=include_deleted)
    if not actual: fail(404, 'trackerProjectNotFound')
    from app.tracker_avatar import metadata
    payload = metadata(json.loads(row['payload']))
    shared = row['space_id'] is not None or bool(c.execute("SELECT 1 FROM tracker_members WHERE project_id=? AND state!='revoked'", (row['id'],)).fetchone())
    result = {key: row[key] for key in ('id', 'owner_id', 'space_id', 'created', 'updated', 'version')}
    result['viewer']={'actor_id':uid,'peer_id':peer_id}
    result.update(payload, role=actual, manage=ROLES[actual] >= ROLES['manager'], editable=ROLES[actual] >= ROLES['editor'],
                  shared=shared, retain_completed=True, cancellation_supported=True, issue_deletion_supported=True, pending=invitation, can_delete=actual == 'owner' and not peer_id)
    result['avatar_url']='/api/tracker/projects/'+row['id']+'/avatar?v='+payload['avatar_version'] if payload['has_avatar'] and not invitation else None
    if not invitation:
        result['counts'] = {state: 0 for state in STATES}
        for item in c.execute('SELECT i.status,COUNT(*) AS n FROM tracker_issues i WHERE i.project_id=? AND '+visible('i')+' GROUP BY i.status', (row['id'],)):
            result['counts'][item['status']] = item['n']
        if include_members:
            result['members'] = members(c, row, stamp)
            result['groups']=[serialize_group(c,dict(g)) for g in c.execute('SELECT * FROM tracker_groups WHERE project_id=? ORDER BY position,id',(row['id'],))]
    return result


def projects(c, uid, stamp, peer_id='', owner_id=None, invitations=False, include_deleted=False):
    # Avoid scanning other organizations' content. Remote catalogs are bound
    # to an accepted contact and the local project owner, not a server admin.
    if peer_id:
        rows = c.execute('SELECT p.* FROM tracker_projects p JOIN tracker_members m ON m.project_id=p.id WHERE p.owner_id=? AND m.actor_id=? AND m.peer_id=? AND m.state!=? ORDER BY p.updated DESC,p.id',
                         (owner_id, uid, peer_id, 'revoked')).fetchall()
    else:
        rows = c.execute("SELECT p.* FROM tracker_projects p WHERE p.owner_id=? OR p.space_id IN (SELECT space_id FROM space_members WHERE user_id=?) OR p.id IN (SELECT project_id FROM tracker_members WHERE actor_id=? AND peer_id='' AND state!='revoked') ORDER BY p.updated DESC,p.id", (uid, uid, uid)).fetchall()
    result = []
    for row in rows:
        row = dict(row)
        active = role(c, row, uid, peer_id, include_deleted=include_deleted)
        if active: result.append(serialize_project(c, row, uid, stamp, peer_id, include_members=False, include_deleted=include_deleted))
        elif invitations and role(c, row, uid, peer_id, pending=True):
            result.append(serialize_project(c, row, uid, stamp, peer_id, invitation=True))
    return result


def history(c, project_id, issue_id, uid, peer_id, event, payload, stamp):
    c.execute('INSERT INTO tracker_history(id,project_id,issue_id,actor_id,peer_id,event,payload,created) VALUES(?,?,?,?,?,?,?,?)',
              (secrets.token_urlsafe(12), project_id, issue_id, uid, peer_id, event, dump(payload), stamp))


def create_project(c, data, uid, stamp):
    if c.execute('SELECT COUNT(*) FROM tracker_projects WHERE owner_id=?', (uid,)).fetchone()[0] >= 100:
        fail(422, 'trackerProjectLimit')
    if data.space_id:
        from app.modules.social import space_access
        from app.workspace_management import is_manager
        space = space_access(c, data.space_id, uid, lambda code, value: (_ for _ in ()).throw(HTTPError(code, value)))
        if space['kind'] != 'friends' and not is_manager(c, space['id'], uid): fail(403, 'trackerReadOnly')
    lock_owner(c,uid)
    for row in c.execute('SELECT payload FROM tracker_projects WHERE owner_id=?', (uid,)):
        if json.loads(row[0])['key'] == data.key: fail(409, 'trackerDuplicateKey')
    pid = secrets.token_urlsafe(12)
    payload = data.model_dump(exclude={'space_id'})
    c.execute('INSERT INTO tracker_projects(id,owner_id,space_id,payload,created,updated) VALUES(?,?,?,?,?,?)',
              (pid, uid, data.space_id, dump(payload), stamp, stamp))
    history(c, pid, None, uid, '', 'project_created', {}, stamp)
    return serialize_project(c, project_row(c, pid), uid, stamp)


def issue_row(c, iid, *, include_deleted=False):
    row = c.execute('SELECT i.* FROM tracker_issues i WHERE i.id=?'+('' if include_deleted else ' AND '+visible('i')), (iid,)).fetchone()
    if not row: fail(404, 'trackerIssueNotFound')
    return dict(row)


def status_entry_times(c, rows):
    """Board order follows transitions, never ordinary edits or positions.

    Legacy/imported work without a transition uses its real creation date.
    Read all updates, not the activity UI's most recent 50.
    """
    rows={row['id']:row for row in rows}
    result={iid:(row['completed_at'] or row['created']) if row['status']=='done' else row['created'] for iid,row in rows.items()}
    identities=[iid for iid,row in rows.items() if row['status'] in ('work','review','cancelled')]
    for start in range(0,len(identities),200):
        batch=identities[start:start+200]
        for item in c.execute("SELECT issue_id,payload,created FROM tracker_history WHERE event='updated' AND issue_id IN ("+','.join('?' for _ in batch)+')',tuple(batch)):
            change=json.loads(item['payload']).get('status')
            if isinstance(change,dict) and change.get('after')==rows[item['issue_id']]['status'] and change.get('before')!=change.get('after'):
                result[item['issue_id']]=max(result[item['issue_id']],item['created'])
    return result


def serialize_issue(c, row, project, stamp, *, detail=False, entry_times=None, delete_scope=False):
    result = {key: row[key] for key in ('id', 'project_id', 'number', 'parent_id', 'status', 'kind', 'assignee_id',
               'assignee_peer', 'author_id', 'author_peer', 'position', 'created', 'updated', 'completed_at', 'version')}
    payload = json.loads(row['payload'])
    result['group_id']=row['group_id']
    result['status_entered_at']=(entry_times if entry_times is not None else status_entry_times(c,[row]))[row['id']]
    if not detail: payload = {key: value for key, value in payload.items() if key != 'description'}
    result.update(payload, key=json.loads(project['payload'])['key'] + '-' + str(row['number']))
    if row['kind']=='epic':
        total,done,cancelled=c.execute("SELECT COALESCE(SUM(CASE WHEN status!='cancelled' THEN 1 ELSE 0 END),0),COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),0),COALESCE(SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END),0) FROM tracker_issues i WHERE i.parent_id=? AND "+visible('i'),(row['id'],)).fetchone()
        result['progress']={'total':total,'done':done,'cancelled':cancelled}
    if detail:
        if delete_scope:
            from app.tracker_issue_lifecycle import plan
            result['delete_scope'] = plan(c, row, project)
        result['comments'] = [dict(item) | {'person': person(c, item['actor_id'], item['peer_id'], project['owner_id'], stamp)}
                              for item in reversed(c.execute('SELECT * FROM tracker_comments WHERE issue_id=? ORDER BY created DESC,id DESC LIMIT 50', (row['id'],)).fetchall())]
        result['history'] = []
        for item in c.execute('SELECT * FROM tracker_history WHERE issue_id=? ORDER BY created DESC,id DESC LIMIT 50', (row['id'],)):
            event = json.loads(item['payload'])
            if item['event'] == 'updated' and 'description' in event:
                # Full text remains in encrypted history. The activity list
                # only displays that it changed; keep RPC responses bounded.
                event['description'] = {'before': None, 'after': None}
            result['history'].append(dict(item) | {'payload': event, 'person': person(c, item['actor_id'], item['peer_id'], project['owner_id'], stamp)})
        result['attachments'] = [dict(item) for item in c.execute("SELECT f.id,f.name,f.mime,f.size,a.created FROM tracker_attachments a JOIN files f ON f.id=a.id WHERE a.issue_id=? AND f.deleted IS NULL ORDER BY a.created,f.id", (row['id'],))]
    return result


def issue_page(c, pid, uid, stamp, peer_id='', *, after=0, limit=200, group_id=None, include_cancelled=True):
    project = access(c, pid, uid, peer_id)
    clause=' AND '+visible('tracker_issues')+('' if include_cancelled else " AND status!='cancelled'");params=[pid,after]
    if group_id is not None:
        if group_id:group_row(c,group_id,pid);clause+=' AND group_id=?';params.append(group_id)
        else:clause+=' AND group_id IS NULL'
    rows = c.execute('SELECT * FROM tracker_issues WHERE project_id=? AND number>?'+clause+' ORDER BY number LIMIT ?', (*params,limit+1)).fetchall()
    entries=status_entry_times(c,rows[:limit])
    page = [serialize_issue(c, dict(row), project, stamp,entry_times=entries) for row in rows[:limit]]
    counts={s:0 for s in STATES if include_cancelled or s!='cancelled'}
    for r in c.execute('SELECT status,COUNT(*) n FROM tracker_issues WHERE project_id=?'+clause+' GROUP BY status',(pid,*params[2:])):counts[r['status']]=r['n']
    return {'items': page, 'more': len(rows) > limit, 'cursor': page[-1]['number'] if page else after,
            'view_counts':counts,'project': serialize_project(c, project, uid, stamp, peer_id)}


def validate_relations(c, project, value, iid=None, *, include_deleted=False):
    if value.group_id:group_row(c,value.group_id,project['id'])
    if value.assignee_peer and not value.assignee_id: fail(422, 'trackerInvalidMember')
    if value.assignee_id:
        if role(c, project, value.assignee_id, value.assignee_peer) is None: fail(422, 'trackerInvalidMember')
    seen = {iid} if iid else set()
    parent = value.parent_id
    while parent:
        if parent in seen or len(seen) > 20: fail(422, 'trackerInvalidParent')
        seen.add(parent)
        row = c.execute('SELECT i.project_id,i.parent_id,i.group_id FROM tracker_issues i WHERE i.id=?'+('' if include_deleted else ' AND '+visible('i')), (parent,)).fetchone()
        if not row or row['project_id'] != project['id']: fail(422, 'trackerInvalidParent')
        if row['group_id']!=value.group_id:fail(422,'trackerInvalidGroup')
        parent = row['parent_id']


def issue_payload(value):
    payload=value.model_dump(exclude={'status', 'kind', 'parent_id', 'assignee_id', 'assignee_peer','group_id'})
    if not payload['cancellation_reason']:payload.pop('cancellation_reason')
    return payload


def notify_issue(c, project, issue, uid, peer_id, event, stamp):
    from app.notifications import notify
    recipients = {project['owner_id']}
    if issue.get('assignee_id') and not issue.get('assignee_peer'): recipients.add(issue['assignee_id'])
    if not issue.get('author_peer'): recipients.add(issue['author_id'])
    for item in c.execute("SELECT actor_id FROM tracker_members WHERE project_id=? AND peer_id='' AND state='active'", (project['id'],)):
        recipients.add(item[0])
    for recipient in recipients:
        if not peer_id and recipient == uid: continue
        if role(c, project, recipient) is not None:
            notify(c, recipient, 'tracker', {'project_id': project['id'], 'issue_id': issue['id'],
                    'title': json.loads(issue['payload'])['title'], 'event': event, 'actor_id': uid if not peer_id else '',
                    'peer_id': peer_id}, stamp)


def create_issue(c, pid, data, uid, stamp, peer_id='', *, original_id=None, created=None, completed_at=None, imported=False):
    access(c,pid,uid,peer_id,required='editor');lock_project(c,pid)
    project = access(c, pid, uid, peer_id, required='editor')
    if data.parent_id:
        parent=issue_row(c,data.parent_id)
        if data.group_id not in (None,parent['group_id']):fail(422,'trackerInvalidGroup')
        data=data.model_copy(update={'group_id':parent['group_id']})
    if data.status!='cancelled' and data.cancellation_reason:fail(422,'trackerInvalidIssue')
    validate_relations(c, project, data)
    number = project['next_number']
    changed = c.execute('UPDATE tracker_projects SET next_number=next_number+1,version=version+1,updated=? WHERE id=? AND next_number=?', (stamp, pid, number))
    if changed.rowcount != 1: fail(409, 'trackerConflict')
    iid = original_id or secrets.token_urlsafe(12)
    if original_id and c.execute('SELECT 1 FROM tracker_issues WHERE id=?', (original_id,)).fetchone(): fail(409, 'trackerConflict')
    position = c.execute('SELECT COALESCE(MAX(position),0)+1024 FROM tracker_issues WHERE project_id=? AND status=?', (pid, data.status)).fetchone()[0]
    c.execute('INSERT INTO tracker_issues(id,project_id,number,parent_id,status,kind,assignee_id,assignee_peer,author_id,author_peer,payload,position,created,updated,completed_at,group_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (iid, pid, number, data.parent_id, data.status, data.kind, data.assignee_id, data.assignee_peer, uid, peer_id,
               dump(issue_payload(data)), position, stamp if created is None else created, stamp,
               (completed_at if imported or completed_at is not None else stamp) if data.status == 'done' else None,data.group_id))
    history(c, pid, iid, uid, peer_id, 'imported' if imported else 'created', {}, stamp)
    row = issue_row(c, iid)
    if not imported: notify_issue(c, project, row, uid, peer_id, 'created', stamp)
    return serialize_issue(c, row, project, stamp, detail=True, delete_scope=True)


def update_issue(c, iid, data, uid, stamp, peer_id=''):
    row = issue_row(c, iid)
    access(c,row['project_id'],uid,peer_id,required='editor');lock_project(c,row['project_id'])
    row=issue_row(c,iid)
    project = access(c, row['project_id'], uid, peer_id, required='editor')
    if row['version'] != data.version: fail(409, 'trackerConflict')
    old = Issue.model_validate(json.loads(row['payload']) | {key: row[key] for key in ('status','kind','parent_id','group_id','assignee_id','assignee_peer')}).model_dump()
    changes = data.model_dump(exclude_unset=True, exclude={'version', 'position'})
    if changes.get('status',old['status'])!='cancelled' and old['status']=='cancelled' and 'cancellation_reason' not in changes:
        changes['cancellation_reason']=''
    updated = Issue.model_validate(old | changes)
    if updated.status!='cancelled' and updated.cancellation_reason:fail(422,'trackerInvalidIssue')
    if updated.parent_id:
        parent=issue_row(c,updated.parent_id)
        if 'group_id' in changes and updated.group_id!=parent['group_id']:fail(422,'trackerInvalidGroup')
        updated=updated.model_copy(update={'group_id':parent['group_id']})
        if updated.group_id!=row['group_id']:changes['group_id']=updated.group_id
    validation=updated
    if not {'assignee_id','assignee_peer'}.intersection(changes):
        # An existing assignment is historical after revocation. Preserve it
        # while editing other fields, but require active access for a new one.
        validation=updated.model_copy(update={'assignee_id':'','assignee_peer':''})
    validate_relations(c, project, validation, iid)
    difference = {key: {'before': old[key], 'after': updated.model_dump()[key]} for key in changes if old[key] != updated.model_dump()[key]}
    position = row['position']
    if updated.status != row['status']:
        position = c.execute('SELECT COALESCE(MAX(position),0)+1024 FROM tracker_issues WHERE project_id=? AND status=?', (project['id'], updated.status)).fetchone()[0]
    if data.position is not None:
        position = data.position
        if position != row['position']: difference['position'] = {'before': row['position'], 'after': position}
    if not difference: return serialize_issue(c, row, project, stamp, detail=True, delete_scope=True)
    completed = row['completed_at'] if row['status'] == 'done' and updated.status == 'done' else stamp if updated.status == 'done' else None
    result = c.execute('UPDATE tracker_issues SET parent_id=?,status=?,kind=?,assignee_id=?,assignee_peer=?,payload=?,position=?,updated=?,completed_at=?,group_id=?,version=version+1 WHERE id=? AND version=?',
                        (updated.parent_id, updated.status, updated.kind, updated.assignee_id, updated.assignee_peer, dump(issue_payload(updated)), position, stamp, completed,updated.group_id, iid, data.version))
    if result.rowcount != 1: fail(409, 'trackerConflict')
    if row['group_id']!=updated.group_id:
        descendants=c.execute('WITH RECURSIVE tree(id) AS (SELECT id FROM tracker_issues WHERE parent_id=? UNION ALL SELECT i.id FROM tracker_issues i JOIN tree t ON i.parent_id=t.id) SELECT i.id,i.group_id FROM tracker_issues i JOIN tree t ON t.id=i.id',(iid,)).fetchall()
        for child in descendants:
            if child['group_id']==updated.group_id:continue
            c.execute('UPDATE tracker_issues SET group_id=?,updated=?,version=version+1 WHERE id=?',(updated.group_id,stamp,child['id']))
            history(c,project['id'],child['id'],uid,peer_id,'updated',{'group_id':{'before':child['group_id'],'after':updated.group_id}},stamp)
    c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?', (stamp, project['id']))
    history(c, project['id'], iid, uid, peer_id, 'updated', difference, stamp)
    row = issue_row(c, iid)
    notify_issue(c, project, row, uid, peer_id, 'updated', stamp)
    return serialize_issue(c, row, project, stamp, detail=True, delete_scope=True)


def add_comment(c, iid, data, uid, stamp, peer_id=''):
    row = issue_row(c, iid)
    access(c, row['project_id'], uid, peer_id, required='editor');lock_project(c, row['project_id'])
    project = access(c, row['project_id'], uid, peer_id, required='editor')
    cid = secrets.token_urlsafe(12)
    c.execute('INSERT INTO tracker_comments(id,issue_id,actor_id,peer_id,body,created) VALUES(?,?,?,?,?,?)', (cid, iid, uid, peer_id, data.body, stamp))
    c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?', (stamp, project['id']))
    notify_issue(c, project, row, uid, peer_id, 'comment', stamp)
    return {'id': cid, 'body': data.body, 'created': stamp, 'actor_id': uid, 'peer_id': peer_id,
            'person': person(c, uid, peer_id, project['owner_id'], stamp)}


def import_reminders(c, list_id, uid, stamp, *, name, key):
    """One transaction moves the complete list; repeat calls cannot duplicate it.

    Snapshot is retained encrypted for a tested rollback while the release is
    being reviewed. The source rows are deleted only after exact field and tree
    verification. Other lists, settings and completion timers are preserved.
    """
    previous = c.execute('SELECT project_id,owner_id FROM tracker_imports WHERE list_id=?', (list_id,)).fetchone()
    if previous:
        if previous['owner_id'] != uid: fail(404, 'trackerProjectNotFound')
        return serialize_project(c, access(c, previous['project_id'], uid), uid, stamp)
    source = c.execute('SELECT * FROM task_lists WHERE id=? AND owner_id=?', (list_id, uid)).fetchone()
    if not source or source['is_default']: fail(404, 'trackerImportListNotFound')
    rows = [dict(row) for row in c.execute('SELECT t.*,d.list_id,d.parent_id,d.payload,d.completed_at,d.version FROM tasks t JOIN task_details d ON d.task_id=t.id WHERE d.list_id=? ORDER BY t.created,t.id', (list_id,))]
    member_rows = [dict(row) for row in c.execute('SELECT * FROM task_list_members WHERE list_id=?', (list_id,))]
    from app.modules.tasks import _friend
    if any(not _friend(c, uid, row['user_id']) for row in member_rows): fail(409, 'trackerInvalidMember')
    project = create_project(c, Project(name=name, key=key), uid, stamp)
    pid = project['id']
    for member in member_rows:
        c.execute('INSERT INTO tracker_members(project_id,actor_id,role,state,created) VALUES(?,?,?,?,?)', (pid, member['user_id'], 'editor', 'active', stamp))
    pending = list(rows)
    imported = set()
    while pending:
        available = [row for row in pending if not row['parent_id'] or row['parent_id'] in imported]
        if not available: fail(422, 'trackerInvalidParent')
        for row in available:
            payload = json.loads(row['payload'])
            data = Issue(title=row['title'], description=payload.get('notes', ''), status='done' if row['done'] else 'todo',
                         kind='task',
                         parent_id=row['parent_id'], due=row['day'], priority=payload.get('priority', 0),
                         tags=payload.get('tags', []), url=payload.get('url', ''), time=payload.get('time', ''), flagged=payload.get('flagged', False))
            result = create_issue(c, pid, data, uid, stamp, original_id=row['id'], created=row['created'], completed_at=row['completed_at'], imported=True)
            if result['title'] != row['title'] or result['description'] != payload.get('notes', '') or result['parent_id'] != row['parent_id'] or (result['status'] == 'done') != bool(row['done']):
                fail(409, 'trackerImportMismatch')
            imported.add(row['id']); pending.remove(row)
    if len(imported) != len(rows): fail(409, 'trackerImportMismatch')
    snapshot = {'list': dict(source), 'tasks': rows, 'members': member_rows}
    c.execute('INSERT INTO tracker_imports(list_id,project_id,owner_id,payload,created) VALUES(?,?,?,?,?)', (list_id, pid, uid, dump(snapshot), stamp))
    # Child-first deletion keeps old parent references valid until their own row
    # is removed. No unrelated task's parent is allowed into this source list.
    for row in reversed(rows): c.execute('DELETE FROM tasks WHERE id=? AND user_id=?', (row['id'], uid))
    c.execute('DELETE FROM task_lists WHERE id=? AND owner_id=?', (list_id, uid))
    history(c, pid, None, uid, '', 'reminders_imported', {'count': len(rows), 'completed': sum(bool(row['done']) for row in rows)}, stamp)
    return serialize_project(c, project_row(c, pid), uid, stamp)


def validate_archive(c, invalid):
    try:
        from app.tracker_lifecycle import validate_archive as validate_deletions
        validate_deletions(c, invalid)
        from app.tracker_issue_lifecycle import validate_archive as validate_issue_deletions
        validate_issue_deletions(c, invalid)
        if c.execute('SELECT 1 FROM tracker_history h JOIN tracker_groups g ON g.id=h.group_id WHERE h.project_id!=g.project_id LIMIT 1').fetchone():raise ValueError()
        for project in c.execute('SELECT * FROM tracker_projects'):
            project = dict(project)
            Project.model_validate(json.loads(project['payload']) | {'space_id': project['space_id']})
            maximum = c.execute('SELECT COALESCE(MAX(number),0) FROM tracker_issues WHERE project_id=?', (project['id'],)).fetchone()[0]
            if project['next_number'] <= maximum: raise ValueError()
            for group in c.execute('SELECT * FROM tracker_groups WHERE project_id=?',(project['id'],)):
                Group.model_validate(json.loads(group['payload']))
            for row in c.execute('SELECT * FROM tracker_issues WHERE project_id=?', (project['id'],)):
                value = Issue.model_validate(json.loads(row['payload']) | {k: row[k] for k in ('status','kind','parent_id','group_id','assignee_id','assignee_peer')})
                # A former assignee remains historical data after leaving.
                value.assignee_id = ''; value.assignee_peer = ''
                validate_relations(c, project, value, row['id'], include_deleted=True)
                if row['status'] != 'done' and row['completed_at'] is not None: raise ValueError()
    except (ValueError, TypeError, KeyError, HTTPError): invalid()
