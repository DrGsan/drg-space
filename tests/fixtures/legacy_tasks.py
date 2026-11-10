# Frozen legacy payload compatibility fixture; never imported by the application.
"""Private and friend-shared reminders, retaining the original task routes."""
from app.i18n import message as localized_message
from app.i18n import text as localized_text, current_language, recipient_language
import json
import re
import secrets
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from app.http import Router, Request
from app.retention import device_timezone

TASKS_SCHEMA = '''
CREATE TABLE IF NOT EXISTS task_lists(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,payload TEXT NOT NULL DEFAULT '{}',is_default INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL,updated INTEGER NOT NULL,version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS task_list_owner_idx ON task_lists(owner_id);
CREATE UNIQUE INDEX IF NOT EXISTS task_default_owner_idx ON task_lists(owner_id) WHERE is_default=1;
CREATE TABLE IF NOT EXISTS task_list_members(list_id TEXT NOT NULL REFERENCES task_lists(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,created INTEGER NOT NULL,PRIMARY KEY(list_id,user_id));
CREATE INDEX IF NOT EXISTS task_member_user_idx ON task_list_members(user_id,list_id);
CREATE TABLE IF NOT EXISTS task_details(task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,list_id TEXT NOT NULL REFERENCES task_lists(id) ON DELETE CASCADE,parent_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,payload TEXT NOT NULL DEFAULT '{}',completed_at INTEGER,version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS task_detail_list_idx ON task_details(list_id);
CREATE INDEX IF NOT EXISTS task_detail_parent_idx ON task_details(parent_id);
'''
MAX_TASKS, MAX_LISTS, MAX_DEPTH = 5000, 100, 2
ID = r'^[a-zA-Z0-9_-]{8,80}$'
COLORS = Literal['blue','sage','amber','lilac','rose','slate']
ICONS = Literal['list','check','briefcase','shopping-cart','home','star','calendar','heart','book','gift']

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class ListPayload(Strict):
    name: str = Field(default='Напоминания', min_length=1, max_length=100)
    icon: ICONS = 'list'
    color: COLORS = 'blue'
    retain_completed: StrictBool = False
    @field_validator('name')
    @classmethod
    def nonempty(cls, value):
        if not value.strip(): raise ValueError(localized_message('enterTheListName'))
        return value.strip()

class ListUpdate(Strict):
    name: str | None = Field(default=None, max_length=100)
    icon: ICONS | None = None
    color: COLORS | None = None
    retain_completed: StrictBool | None = None
    version: int | None = Field(default=None, strict=True, ge=1)

class Member(Strict):
    user_id: str = Field(pattern=r'^[a-f0-9]{32}$')

class TaskPayload(Strict):
    notes: str = Field(default='', max_length=5000)
    url: str = Field(default='', max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    flagged: StrictBool = False
    priority: int = Field(default=0, strict=True, ge=0, le=3)
    time: str = ''
    @field_validator('url')
    @classmethod
    def safe_url(cls, value):
        if not value: return ''
        if any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
            raise ValueError(localized_message('aFullHttpOrHttpsAddressIsRequired'))
        parsed = urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(localized_message('aFullHttpOrHttpsAddressIsRequired2'))
        try: parsed.port
        except ValueError: raise ValueError(localized_message('invalidWebsiteAddress')) from None
        return value
    @field_validator('tags')
    @classmethod
    def clean_tags(cls, values):
        result = []
        for value in values:
            value = value.strip().lstrip('#').strip()
            if not value or len(value)>40 or any(ord(c)<32 for c in value):
                raise ValueError(localized_message('theTagMustContain1To40Characters'))
            if value.casefold() not in {item.casefold() for item in result}: result.append(value)
        return result
    @field_validator('time')
    @classmethod
    def valid_time(cls, value):
        if value and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):
            raise ValueError(localized_message('timeMustBeInHhMmFormat'))
        return value

class Task(TaskPayload):
    title: str = Field(min_length=1, max_length=300)
    day: str | None = None
    done: StrictBool = False
    list_id: str | None = Field(default=None, pattern=ID)
    parent_id: str | None = Field(default=None, pattern=ID)
    @field_validator('title')
    @classmethod
    def nonempty(cls, value):
        if not value.strip(): raise ValueError(localized_message('theNameCannotBeEmpty'))
        return value.strip()
    @field_validator('day')
    @classmethod
    def valid_day(cls, value):
        if not value: return ''
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value): raise ValueError(localized_message('theDateMustBeInYyyyMmDd'))
        date.fromisoformat(value)
        return value
    @model_validator(mode='after')
    def time_needs_day(self):
        if self.time and not self.day: raise ValueError(localized_message('aDateIsRequiredForTheTime'))
        return self

class TaskUpdate(Strict):
    title: str | None = Field(default=None, max_length=300)
    day: str | None = None
    done: StrictBool | None = None
    list_id: str | None = Field(default=None, pattern=ID)
    parent_id: str | None = Field(default=None, pattern=ID)
    notes: str | None = Field(default=None, max_length=5000)
    url: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    flagged: StrictBool | None = None
    priority: int | None = Field(default=None, strict=True, ge=0, le=3)
    time: str | None = None
    version: int | None = Field(default=None, strict=True, ge=1)

def _dump(value): return json.dumps(value,ensure_ascii=False)

def _friend(c, owner, uid):
    if owner==uid: return True
    a,b = sorted((owner,uid))
    row=c.execute('SELECT space_id FROM friendships WHERE first_id=? AND second_id=?',(a,b)).fetchone()
    from app.workspace_lifecycle import pending
    return bool(row and not pending(c,row[0]))

def ensure_default_list(c, uid, stamp):
    lid = 'default_'+uid
    existing = c.execute('SELECT * FROM task_lists WHERE id=? AND owner_id=?',(lid,uid)).fetchone()
    if existing:
        return dict(existing)
    # Only seed new data. A saved or renamed list belongs to its owner and
    # keeps its name when another device, member or background job reads it.
    language = recipient_language(_owner_settings(c,uid), current_language.get())
    payload = ListPayload(name=localized_text('reminders',language=language)).model_dump()
    c.execute('INSERT OR IGNORE INTO task_lists(id,owner_id,payload,is_default,created,updated) VALUES(?,?,?,?,?,?)',
              (lid,uid,_dump(payload),1,stamp,stamp))
    return dict(c.execute('SELECT * FROM task_lists WHERE id=? AND owner_id=?',(lid,uid)).fetchone())

def accessible_task_lists(c, uid, include_deleted=False):
    from app.task_list_deletion import pending
    rows = c.execute('SELECT * FROM task_lists WHERE owner_id=? OR id IN (SELECT list_id FROM task_list_members WHERE user_id=?) ORDER BY is_default DESC,created,id',(uid,uid)).fetchall()
    return [dict(row) for row in rows if _friend(c,row['owner_id'],uid) and (not pending(c,row['id']) or (include_deleted and row['owner_id']==uid))]

def task_list_access(c, list_id, uid, fail, owner_only=False, allow_deleted=False):
    row = c.execute('SELECT * FROM task_lists WHERE id=?',(list_id,)).fetchone()
    if not row or (row['owner_id']!=uid and (not _friend(c,row['owner_id'],uid) or not c.execute('SELECT 1 FROM task_list_members WHERE list_id=? AND user_id=?',(list_id,uid)).fetchone())):
        fail(404,localized_message('listNotFound'))
    if owner_only and row['owner_id']!=uid: fail(403,localized_message('listSettingsAreAvailableToTheOwner'))
    from app.task_list_deletion import pending
    if pending(c,list_id) and not (allow_deleted and row['owner_id']==uid):
        fail(409,localized_message('listPendingDeletion'))
    return dict(row)

def ensure_task_details(c, uid, settings, stamp):
    rows = c.execute('SELECT t.* FROM tasks t LEFT JOIN task_details d ON d.task_id=t.id WHERE t.user_id=? AND d.task_id IS NULL',(uid,)).fetchall()
    if not rows: return
    default = ensure_default_list(c,uid,stamp)
    completed = settings.get('_task_completed_at',{})
    if not isinstance(completed,dict): completed = {}
    for row in rows:
        at = completed.get(row['id']) if row['done'] else None
        if type(at) not in (int,float) or not 0<=at<=32503680000: at = None
        c.execute('INSERT OR IGNORE INTO task_details(task_id,list_id,payload,completed_at) VALUES(?,?,?,?)',
                  (row['id'],default['id'],_dump(TaskPayload().model_dump()),int(at) if at is not None else None))

def _owner_settings(c, uid):
    return json.loads(c.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])

def _prepare(c, uid, stamp):
    from app.retention import purge_tasks
    settings = _owner_settings(c,uid)
    if purge_tasks(c,uid,settings,stamp): c.execute('UPDATE users SET settings=? WHERE id=?',(_dump(settings),uid))

def _task_row(c, tid):
    row = c.execute('SELECT t.*,d.list_id,d.parent_id,d.payload,d.completed_at,d.version FROM tasks t JOIN task_details d ON d.task_id=t.id WHERE t.id=?',(tid,)).fetchone()
    return dict(row) if row else None

def serialize_task(row):
    return {key:row[key] for key in ('id','title','day','done','created','list_id','parent_id','completed_at','version')} | TaskPayload.model_validate(json.loads(row['payload'])).model_dump()

def task_snapshot(c, uid, stamp, include_shared=True, include_deleted=False):
    from app.task_list_deletion import purge
    purge(c,stamp)
    ensure_default_list(c,uid,stamp)
    lists = accessible_task_lists(c,uid,include_deleted=include_deleted)
    if not include_shared: lists = [item for item in lists if item['owner_id']==uid]
    for owner in sorted({uid,*[item['owner_id'] for item in lists]}): _prepare(c,owner,stamp)
    result = []
    for item in lists:
        result.extend(serialize_task(dict(row)) for row in c.execute('SELECT t.*,d.list_id,d.parent_id,d.payload,d.completed_at,d.version FROM tasks t JOIN task_details d ON d.task_id=t.id WHERE d.list_id=? ORDER BY t.created,t.id',(item['id'],)))
    return result

def _actor(c, uid, stamp):
    from app.notifications import actor
    item = actor(c,uid,stamp)
    item['display_name'] = _owner_settings(c,uid).get('profile',{}).get('display_name') or item['login']
    return item

def serialize_list(c, row, uid, stamp):
    from app.task_invitations import pending
    from app.task_list_deletion import pending as deletion
    members = [_actor(c,row['owner_id'],stamp) | {'owner':True,'active':True}]
    for member in c.execute('SELECT user_id FROM task_list_members WHERE list_id=? AND user_id!=? ORDER BY created,user_id',(row['id'],row['owner_id'])):
        active = _friend(c,row['owner_id'],member[0])
        if active or row['owner_id']==uid: members.append(_actor(c,member[0],stamp) | {'owner':False,'active':active})
    return {key:row[key] for key in ('id','owner_id','created','updated','version')} | ListPayload.model_validate(json.loads(row['payload'])).model_dump() | {
        'is_default':bool(row['is_default']) and row['owner_id']==uid,'members':members,'owner':row['owner_id']==uid,'can_manage':row['owner_id']==uid and not deletion(c,row['id']),'shared':len(members)>1,'deletion':deletion(c,row['id']),
        'invitations':[item | {'recipient':_actor(c,item['recipient_id'],stamp)} for item in pending(c,row['id'],stamp)] if row['owner_id']==uid else []}

def task_list_snapshot(c, uid, stamp):
    from app.task_list_deletion import purge
    purge(c,stamp)
    ensure_default_list(c,uid,stamp)
    return [serialize_list(c,item,uid,stamp) for item in accessible_task_lists(c,uid,include_deleted=True)]

def _tree(c, list_id):
    return {row['task_id']:row['parent_id'] for row in c.execute('SELECT task_id,parent_id FROM task_details WHERE list_id=?',(list_id,))}

def descendants(tree, tid):
    result, pending = [], [tid]
    while pending:
        current = pending.pop()
        if current in result: raise ValueError(localized_message('nestedReminderStructure'))
        result.append(current)
        pending.extend(key for key,parent in tree.items() if parent==current)
    return result

def _parent_chain(tree, parent, fail):
    chain = []
    while parent is not None:
        if parent not in tree: fail(422,localized_message('theParentReminderMustBeInTheSame'))
        if parent in chain: fail(422,localized_message('youCannotCreateANestedReminderLoop'))
        chain.append(parent); parent = tree[parent]
    return chain

def _check_tree(tree, tid, parent, moving, fail):
    chain = _parent_chain(tree,parent,fail)
    if tid in chain or any(item in moving for item in chain): fail(422,localized_message('youCannotMoveAReminderInsideANested'))
    subtree_depth = 1
    for key in moving:
        depth, ancestor = 1, tree.get(key)
        while ancestor in moving:
            depth += 1
            if depth>MAX_DEPTH: fail(422,localized_message('maximumFiveLevelsOfNestedReminders'))
            ancestor = tree.get(ancestor)
        subtree_depth = max(subtree_depth,depth)
    if len(chain)+subtree_depth>MAX_DEPTH: fail(422,localized_message('maximumFiveLevelsOfNestedReminders'))
    return chain

def _uncheck_ancestors(c, chain):
    for tid in chain:
        if c.execute('SELECT done FROM tasks WHERE id=?',(tid,)).fetchone()[0]:
            c.execute('UPDATE tasks SET done=0 WHERE id=?',(tid,))
            c.execute('UPDATE task_details SET completed_at=NULL,version=version+1 WHERE task_id=?',(tid,))

def _valid_completed_at(value):
    if type(value) is not int or not 0<=value<=32503680000:return False
    try:datetime.fromtimestamp(value,timezone.utc)
    except (ValueError,OverflowError,OSError):return False
    return True

def validate_task_archive(c, fail):
    """Validate decrypted portable metadata and parent/list boundaries."""
    from app.task_list_deletion import validate_archive
    validate_archive(c,fail)
    lists = {row['id']:dict(row) for row in c.execute('SELECT * FROM task_lists')}
    for item in lists.values():
        if not re.fullmatch(ID,item['id']) or item['version']<1 or item['is_default'] not in (0,1): fail()
        ListPayload.model_validate(json.loads(item['payload']))
        if item['is_default'] and item['id']!='default_'+item['owner_id']: fail()
        if c.execute('SELECT COUNT(*) FROM task_list_members WHERE list_id=?',(item['id'],)).fetchone()[0]>49: fail()
        if any(type(item[key]) is not int or not 0<=item[key]<=32503680000 for key in ('created','updated')): fail()
    for row in c.execute('SELECT * FROM tasks'):
        if not re.fullmatch(ID,row['id']) or row['done'] not in (0,1) or type(row['created']) is not int or not 0<=row['created']<=32503680000: fail()
        Task.model_validate({'title':row['title'],'day':row['day'],'done':bool(row['done'])})
    trees = {lid:_tree(c,lid) for lid in lists}
    for row in c.execute('SELECT t.*,d.list_id,d.parent_id,d.payload,d.completed_at,d.version FROM tasks t JOIN task_details d ON d.task_id=t.id'):
        item = dict(row)
        if item['user_id']!=lists[item['list_id']]['owner_id'] or item['done'] not in (0,1) or item['version']<1: fail()
        Task.model_validate({key:item[key] for key in ('title','day','list_id','parent_id')} | {'done':bool(item['done'])} | json.loads(item['payload']))
        at = item['completed_at']
        if at is not None and (type(at) is not int or not 0<=at<=32503680000 or not item['done']): fail()
        if at is not None:
            try:datetime.fromtimestamp(at,timezone.utc)
            except (ValueError,OverflowError,OSError):fail()
        chain = _parent_chain(trees[item['list_id']],item['parent_id'],lambda *args:fail())
        # Archives from previous releases can contain up to five levels.
        if item['id'] in chain or len(chain)+1>5: fail()

def create_tasks_router(db, ready, now, fail):
    router = Router()
    def prepare(c, session, request):
        uid = session['user_id']; settings = _owner_settings(c,uid)
        if request.headers.get('x-time-zone'): settings['_timezone'] = device_timezone(request.headers['x-time-zone'])
        from app.retention import purge_tasks
        purge_tasks(c,uid,settings,now())
        c.execute('UPDATE users SET settings=? WHERE id=?',(_dump(settings),uid))
        return ensure_default_list(c,uid,now())
    def owned_task(c, tid, uid):
        task = c.execute('SELECT user_id FROM tasks WHERE id=?',(tid,)).fetchone()
        if task and task[0]!=uid:
            details=c.execute('SELECT list_id FROM task_details WHERE task_id=?',(tid,)).fetchone()
            task_list_access(c,details[0] if details else 'default_'+task[0],uid,fail)
        if task: _prepare(c,task[0],now())
        row = _task_row(c,tid)
        if not row: fail(404,localized_message('reminderNotFound'))
        task_list_access(c,row['list_id'],uid,fail)
        return row
    def checked(model, values):
        try: return model.model_validate(values)
        except ValueError as error: fail(422,str(error))

    @router.get('/api/task-lists')
    def lists(request: Request):
        session = ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); prepare(c,session,request)
            return task_list_snapshot(c,session['user_id'],now())

    @router.get('/api/task-lists/friends')
    def friends(request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            ids = [row['second_id'] if row['first_id']==uid else row['first_id'] for row in c.execute('SELECT first_id,second_id FROM friendships WHERE first_id=? OR second_id=?',(uid,uid))]
            return sorted([_actor(c,friend,now()) for friend in ids if _friend(c,uid,friend)],key=lambda item:item['display_name'].casefold())

    @router.post('/api/task-lists')
    def create_list(data: ListPayload, request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); prepare(c,session,request)
            if c.execute('SELECT COUNT(*) FROM task_lists WHERE owner_id=?',(uid,)).fetchone()[0]>=MAX_LISTS: fail(422,localized_message('maximum100Lists'))
            lid = secrets.token_urlsafe(12); stamp = now()
            c.execute('INSERT INTO task_lists(id,owner_id,payload,created,updated) VALUES(?,?,?,?,?)',(lid,uid,_dump(data.model_dump()),stamp,stamp))
            return serialize_list(c,task_list_access(c,lid,uid,fail),uid,stamp)

    @router.patch('/api/task-lists/{lid}')
    def update_list(lid: str, data: ListUpdate, request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); row = task_list_access(c,lid,uid,fail,True)
            if data.version is not None and data.version!=row['version']: fail(409,localized_message('theListHasAlreadyBeenModifiedRefreshThe'))
            payload = checked(ListPayload,json.loads(row['payload']) | data.model_dump(exclude_unset=True,exclude={'version'}))
            c.execute('UPDATE task_lists SET payload=?,updated=?,version=version+1 WHERE id=?',(_dump(payload.model_dump()),now(),lid))
            _prepare(c,uid,now())
            return serialize_list(c,task_list_access(c,lid,uid,fail),uid,now())

    @router.delete('/api/task-lists/{lid}')
    def delete_list(lid: str, request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); row = task_list_access(c,lid,uid,fail,True,allow_deleted=True)
            if row['is_default']: fail(409,localized_message('theMainListCannotBeDeleted'))
            from app.task_list_deletion import schedule
            deletion = schedule(c,row,now())
        return {'ok':True,'deletion':deletion}

    @router.post('/api/task-lists/{lid}/restore')
    def restore_list(lid: str, request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = task_list_access(c,lid,uid,fail,True,allow_deleted=True)
            from app.task_list_deletion import pending,restore
            deletion = pending(c,lid)
            if deletion and deletion['delete_at']<=now(): fail(409,localized_message('listRecoveryExpired'))
            restore(c,lid,now())
            return serialize_list(c,task_list_access(c,lid,uid,fail),uid,now())

    @router.post('/api/task-lists/{lid}/members')
    def add_member(lid: str, data: Member, request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); row=task_list_access(c,lid,uid,fail,True)
            if data.user_id==uid or not _friend(c,uid,data.user_id): fail(422,localized_message('youCanAddAnExistingFriend'))
            from app.task_invitations import pending,create
            present = c.execute('SELECT 1 FROM task_list_members WHERE list_id=? AND user_id=?',(lid,data.user_id)).fetchone()
            invitations=pending(c,lid,now())
            if not present and not any(item['recipient_id']==data.user_id for item in invitations):
                if c.execute('SELECT COUNT(*) FROM task_list_members WHERE list_id=?',(lid,)).fetchone()[0]+len(invitations)>=49: fail(422,localized_message('maximum50ParticipantsInTheList'))
                create(c,row,data.user_id,now())
            c.execute('UPDATE task_lists SET updated=?,version=version+1 WHERE id=?',(now(),lid))
            return serialize_list(c,task_list_access(c,lid,uid,fail),uid,now())

    @router.post('/api/task-lists/{lid}/invitations/{iid}/accept')
    def accept_invitation(lid: str, iid: str, request: Request):
        uid=ready(request)['user_id']
        from app.task_invitations import get,key
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); invitation=get(c,lid,iid,now())
            if not invitation or invitation['recipient_id']!=uid: fail(404,localized_message('taskInvitationUnavailable'))
            if c.execute('SELECT COUNT(*) FROM task_list_members WHERE list_id=?',(lid,)).fetchone()[0]>=49: fail(422,localized_message('maximum50ParticipantsInTheList'))
            c.execute('INSERT OR IGNORE INTO task_list_members VALUES(?,?,?)',(lid,uid,now()))
            c.execute('DELETE FROM app_settings WHERE key=?',(key(lid,iid),))
            c.execute('UPDATE task_lists SET updated=?,version=version+1 WHERE id=?',(now(),lid))
            return serialize_list(c,task_list_access(c,lid,uid,fail),uid,now())

    @router.delete('/api/task-lists/{lid}/invitations/{iid}')
    def cancel_invitation(lid: str, iid: str, request: Request):
        uid=ready(request)['user_id']
        from app.task_invitations import get,key
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); invitation=get(c,lid,iid,now())
            if not invitation or uid not in (invitation['sender_id'],invitation['recipient_id']): fail(404,localized_message('taskInvitationUnavailable'))
            c.execute('DELETE FROM app_settings WHERE key=?',(key(lid,iid),))
            c.execute('UPDATE task_lists SET updated=?,version=version+1 WHERE id=?',(now(),lid))
        return {'ok':True}

    @router.delete('/api/task-lists/{lid}/members/{member_id}')
    def remove_member(lid: str, member_id: str, request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); task_list_access(c,lid,uid,fail,True)
            if member_id==uid: fail(422,localized_message('youCannotDeleteTheListOwner'))
            c.execute('DELETE FROM task_list_members WHERE list_id=? AND user_id=?',(lid,member_id))
            c.execute('UPDATE task_lists SET updated=?,version=version+1 WHERE id=?',(now(),lid))
            return serialize_list(c,task_list_access(c,lid,uid,fail),uid,now())

    @router.get('/api/tasks')
    def tasks(request: Request):
        session = ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); prepare(c,session,request)
            return task_snapshot(c,session['user_id'],now())

    @router.post('/api/tasks')
    def create_task(data: Task, request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); default = prepare(c,session,request)
            target = task_list_access(c,data.list_id or default['id'],uid,fail); _prepare(c,target['owner_id'],now())
            if c.execute('SELECT COUNT(*) FROM tasks WHERE user_id=?',(target['owner_id'],)).fetchone()[0]>=MAX_TASKS: fail(422,localized_message('maximum5000RemindersPerAccount'))
            tree = _tree(c,target['id']); tid = secrets.token_urlsafe(12)
            chain = _check_tree(tree,tid,data.parent_id,[tid],fail)
            payload = data.model_dump(include=set(TaskPayload.model_fields))
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',(tid,target['owner_id'],data.title,data.day or '',int(data.done),now()))
            c.execute('INSERT INTO task_details(task_id,list_id,parent_id,payload,completed_at) VALUES(?,?,?,?,?)',(tid,target['id'],data.parent_id,_dump(payload),now() if data.done else None))
            _uncheck_ancestors(c,chain)
            return serialize_task(_task_row(c,tid))

    @router.patch('/api/tasks/{tid}')
    def update_task(tid: str, data: TaskUpdate, request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); prepare(c,session,request); row = owned_task(c,tid,uid)
            if data.version is not None and data.version!=row['version']: fail(409,localized_message('theReminderHasAlreadyBeenModifiedRefreshThe'))
            changes = data.model_dump(exclude_unset=True,exclude={'version'})
            snapshot = serialize_task(row); values = {key:snapshot[key] for key in Task.model_fields}; values['done'] = bool(values['done'])
            if 'list_id' in changes and changes['list_id']!=row['list_id'] and 'parent_id' not in changes: changes['parent_id']=None
            updated = checked(Task,values | changes)
            target = task_list_access(c,updated.list_id or 'default_'+uid,uid,fail); _prepare(c,target['owner_id'],now())
            source_tree = _tree(c,row['list_id']); moving = descendants(source_tree,tid)
            target_tree = source_tree if target['id']==row['list_id'] else _tree(c,target['id']) | {key:source_tree[key] for key in moving}
            chain = _check_tree(target_tree,tid,updated.parent_id,moving,fail) if updated.parent_id!=row['parent_id'] or target['id']!=row['list_id'] else _parent_chain(target_tree,updated.parent_id,fail)
            if target['owner_id']!=row['user_id'] and c.execute('SELECT COUNT(*) FROM tasks WHERE user_id=?',(target['owner_id'],)).fetchone()[0]+len(moving)>MAX_TASKS: fail(422,localized_message('maximum5000RemindersPerAccount'))
            for child in moving:
                if target['id']!=row['list_id']:
                    c.execute('UPDATE tasks SET user_id=? WHERE id=?',(target['owner_id'],child))
                    c.execute('UPDATE task_details SET list_id=?,version=version+1 WHERE task_id=?',(target['id'],child))
            c.execute('UPDATE tasks SET title=?,day=? WHERE id=?',(updated.title,updated.day or '',tid))
            c.execute('UPDATE task_details SET parent_id=?,payload=?,version=version+1 WHERE task_id=?',(updated.parent_id,_dump(updated.model_dump(include=set(TaskPayload.model_fields))),tid))
            if 'done' in changes:
                completed_now=now()
                for child in moving if updated.done else [tid]:
                    at=None
                    if updated.done:
                        previous=c.execute('SELECT t.done,d.completed_at FROM tasks t JOIN task_details d ON d.task_id=t.id WHERE t.id=?',(child,)).fetchone()
                        # Existing completions keep their own history. Legacy
                        # completions without a stamp retain the task-day fallback.
                        if previous['done']:
                            if _valid_completed_at(previous['completed_at']):at=previous['completed_at']
                        else:at=completed_now
                    c.execute('UPDATE tasks SET done=? WHERE id=?',(int(updated.done),child))
                    c.execute('UPDATE task_details SET completed_at=?,version=version+1 WHERE task_id=?',(at,child))
            if not updated.done or updated.parent_id!=row['parent_id'] or target['id']!=row['list_id']: _uncheck_ancestors(c,chain)
            return serialize_task(_task_row(c,tid)) | {'ok':True}

    @router.delete('/api/tasks/{tid}')
    def delete_task(tid: str, request: Request):
        session = ready(request); uid = session['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); prepare(c,session,request); row = owned_task(c,tid,uid)
            for child in reversed(descendants(_tree(c,row['list_id']),tid)): c.execute('DELETE FROM tasks WHERE id=?',(child,))
        return {'ok':True}

    return router
