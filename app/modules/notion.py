"""Typed note databases, inherited page roles and contextual discussions."""
from app.i18n import message as localized_message
import json, secrets, re, math
from app.notifications import notify,actor
from datetime import date
from typing import Literal
from app.http import Router, Request
from pydantic import BaseModel, Field, ConfigDict
from app.modules.social import note_access

SCHEMA='''
CREATE TABLE IF NOT EXISTS note_permissions(note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,role TEXT NOT NULL CHECK(role IN ('viewer','commenter','editor')),PRIMARY KEY(note_id,user_id));
CREATE TABLE IF NOT EXISTS note_databases(note_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,properties TEXT NOT NULL,views TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS database_records(note_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,database_id TEXT NOT NULL REFERENCES note_databases(note_id) ON DELETE CASCADE,properties TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS database_record_idx ON database_records(database_id);
CREATE TABLE IF NOT EXISTS note_comments(id TEXT PRIMARY KEY,note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,parent_id TEXT REFERENCES note_comments(id) ON DELETE CASCADE,block_id TEXT,body TEXT NOT NULL,mentions TEXT NOT NULL,created INTEGER NOT NULL,resolved INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS note_comment_idx ON note_comments(note_id,created);
'''
TYPES={'text','number','checkbox','select','multi_select','date','url','formula','relation','rollup','dependency'}
VIEWS={'table','list','board','calendar','gallery','timeline'}
DEFAULT_PROPERTIES=[{'id':'status','name':'Статус','type':'select','options':['Запланировано','В работе','Готово']},{'id':'date','name':'Срок','type':'date'},{'id':'tags','name':'Метки','type':'multi_select','options':['Личное','Работа','Семья']}]
DEFAULT_VIEWS=[{'id':kind,'name':name,'type':kind,'group_by':'status' if kind=='board' else None,'date_by':'date' if kind in ('calendar','timeline') else None,'filter':None,'sort':None,'visible':[]} for kind,name in [('table','Таблица'),('board','Доска'),('calendar','Календарь'),('gallery','Галерея'),('timeline','Временная шкала')]]


def record_preview(c,row):
    """Use a record's first attached image when it has no cover."""
    from app.modules.note_files import page_file
    def unavailable(*_):raise ValueError('Unavailable preview')
    def find(blocks):
        for b in blocks or []:
            if b.get('type')=='image':
                url=b.get('props',{}).get('url','')
                match=re.search(r'/api/notes/[A-Za-z0-9_-]{16}/files/([A-Za-z0-9_-]{16})(?:$|[?#])',url)
                if match:
                    page=c.execute('SELECT * FROM notes WHERE id=?',(row['note_id'],)).fetchone()
                    try:
                        file=page_file(c,page,match[1],unavailable,referenced=True)
                        if file['mime'].startswith('image/'):return '/api/notes/'+row['note_id']+'/files/'+match[1]
                    except ValueError:pass
            nested=find(b.get('children',[]))
            if nested:return nested
        return None
    return find(json.loads(row['blocks']) if row['blocks'] else [])


def can_manage(c,n,uid):
    from app.note_access import access_index
    index=access_index(c)
    # Authorship grants content editing; work-space access is centrally managed.
    if index.work_space(n['space_id']):return index.workspace_manager(n['space_id'],uid)
    return n['user_id']==uid or bool(n['space_id'] and index.workspace_manager(n['space_id'],uid))

def page_role(c,n,uid):
    from app.notion_sync.access import readonly
    if readonly(c,n):return 'viewer'
    from app.note_access import access_index
    return access_index(c).role(n,uid)


def require_role(c,n,uid,fail,comment=False,session=None,now=None):
    if not comment:
        from app.note_locks import require_unlocked
        import time
        require_unlocked(c,n,session,now or (lambda:int(time.time())),fail)
    role=page_role(c,n,uid)
    if role=='viewer' or (not comment and role!='editor'):fail(403,'Недостаточно прав: страница доступна только для '+('просмотра.' if role=='viewer' else 'комментирования.'))


def validate_config(properties,views,fail):
    if not isinstance(properties,list) or len(properties)>30 or not isinstance(views,list) or not 1<=len(views)<=20:fail(422,localized_message('allowsUpTo30PropertiesAnd1To'))
    ids=set();mapping={}
    for p in properties:
        if not isinstance(p,dict) or set(p)-{'id','name','type','options','expression','database_id','inverse_id','multiple','two_way','relation_id','property','aggregate'} or not isinstance(p.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',p['id']) or p['id'] in ids or p['id']=='title':fail(422,localized_message('invalidPropertyIdentifier'))
        if not isinstance(p.get('name'),str) or not p['name'].strip() or len(p['name'])>80 or (not isinstance(p.get('type'),str) or p['type'] not in TYPES):fail(422,localized_message('invalidDatabaseProperty'))
        options=p.get('options',[])
        if not isinstance(options,list) or len(options)>50 or any(not isinstance(o,str) or not o.strip() or len(o)>80 for o in options) or len(set(options))!=len(options):fail(422,localized_message('invalidPropertyOptions'))
        ids.add(p['id']);mapping[p['id']]=p
    from app.database_features import validate_properties
    validate_properties(properties,fail)
    ids=set()
    for v in views:
        if not isinstance(v,dict) or set(v)-{'id','name','type','group_by','date_by','filter','sort','visible','dependency_by','auto_shift','lag_days'} or not isinstance(v.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',v['id']) or v['id'] in ids or (not isinstance(v.get('type'),str) or v['type'] not in VIEWS) or not isinstance(v.get('name'),str) or not v['name'].strip() or len(v['name'])>80:fail(422,localized_message('invalidRepresentation'))
        ids.add(v['id'])
        dependency=v.get('dependency_by')
        if dependency is not None and (not isinstance(dependency,str) or mapping.get(dependency,{}).get('type')!='dependency'):fail(422,localized_message('selectTheAppropriatePropertyForDependencies'))
        if type(v.get('auto_shift',False)) is not bool or type(v.get('lag_days',0)) is not int or not -365<=v.get('lag_days',0)<=365:fail(422,localized_message('invalidDependencyShiftMode'))
        if any(v.get(k) is not None and not isinstance(v.get(k),str) for k in ('group_by','date_by')):fail(422,localized_message('invalidRepresentationProperty'))
        if v.get('group_by') and mapping.get(v['group_by'],{}).get('type')!='select':fail(422,localized_message('aPropertyWithOneOptionIsRequiredFor'))
        if v.get('date_by') and mapping.get(v['date_by'],{}).get('type')!='date':fail(422,localized_message('aDateIsRequiredForTheCalendarAnd'))
        visible=v.get('visible',[])
        if not isinstance(visible,list) or len(visible)>30 or any(not isinstance(x,str) or x not in mapping for x in visible):fail(422,localized_message('unknownColumn'))
        f=v.get('filter')
        if f is not None:
            if not isinstance(f,dict) or set(f)!={'property','operator','value'} or not isinstance(f['property'],str) or f['property'] not in {*mapping,'title'} or f['operator'] not in ('contains','not_contains','equals','not_equals','empty','not_empty') or not isinstance(f['value'],str) or len(f['value'])>200:fail(422,localized_message('invalidFilter'))
        s=v.get('sort')
        if s is not None and (not isinstance(s,dict) or set(s)!={'property','direction'} or not isinstance(s['property'],str) or s['property'] not in {*mapping,'title'} or s['direction'] not in ('asc','desc')):fail(422,localized_message('invalidSorting'))


def validate_values(values,properties,fail):
    if not isinstance(values,dict) or len(json.dumps(values).encode())>50000:fail(422,localized_message('tooManyValues'))
    mapping={p['id']:p for p in properties}
    if set(values)-set(mapping):fail(422,localized_message('unknownPropertyOfTheRecord'))
    for key,v in values.items():
        if v is None:continue
        p=mapping[key];t=p['type']
        valid=True
        if t in ('text','url'):valid=isinstance(v,str) and len(v)<=2000
        if t=='url' and v:valid=valid and bool(re.match(r'^https?://[^\s]+$',v))
        if t=='number':valid=type(v) in (int,float) and math.isfinite(v) and abs(v)<=1e15
        if t=='checkbox':valid=type(v) is bool
        if t=='select':valid=isinstance(v,str) and (not v or v in p.get('options',[]))
        if t=='multi_select':valid=isinstance(v,list) and len(v)<=50 and all(isinstance(x,str) and x in p.get('options',[]) for x in v) and len(set(v))==len(v)
        if t=='date':
            valid=isinstance(v,dict) and not set(v)-{'start','end'} and isinstance(v.get('start'),str)
            if valid:
                try:
                    start=date.fromisoformat(v['start']);end=date.fromisoformat(v.get('end') or v['start']);valid=start<=end and re.fullmatch(r'\d{4}-\d{2}-\d{2}',v['start']) and (not v.get('end') or re.fullmatch(r'\d{4}-\d{2}-\d{2}',v['end']))
                except (ValueError,TypeError):valid=False
        if t in ('relation','dependency'):valid=isinstance(v,list) and len(v)<=100 and all(isinstance(x,str) and re.fullmatch(r'[a-zA-Z0-9_-]{16}',x) for x in v) and len(set(v))==len(v) and (t!='relation' or p.get('multiple',True) or len(v)<=1)
        if t in ('formula','rollup'):valid=False
        if not valid:fail(422,'Некорректное значение свойства «'+p['name']+'».')

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class DatabaseCreate(Strict):
    title:str=Field(min_length=1,max_length=200)
    parent_id:str|None=None
    space_id:str|None=None
class DatabaseTitle(Strict):
    title:str=Field(min_length=1,max_length=200)
    version:int=Field(ge=1)
class Configuration(Strict):
    properties:list[dict]
    views:list[dict]
    version:int=Field(ge=1)
class RecordCreate(Strict):
    title:str=Field(min_length=1,max_length=200)
    properties:dict=Field(default_factory=dict)
class RecordUpdate(Strict):
    properties:dict
    version:int=Field(ge=1)
class FormulaPreview(Strict):
    expression:str=Field(max_length=2000)
    record_id:str|None=Field(default=None,max_length=16)
class Permission(Strict):
    user_id:str
    role:Literal['viewer','commenter','editor']|None
class Comment(Strict):
    body:str=Field(min_length=1,max_length=4000)
    block_id:str|None=Field(default=None,max_length=100)
    parent_id:str|None=None
class Resolve(Strict):resolved:bool


def create_notion_router(db,ready,now,fail,router=None):
    r=router if router is not None else Router()
    def access(c,nid,uid,edit=False,session=None):
        n=note_access(c,nid,uid,fail)
        if edit:require_role(c,n,uid,fail,session=session,now=now)
        return n
    def database(c,nid,uid,edit=False,session=None):
        n=access(c,nid,uid,edit,session);d=c.execute('SELECT * FROM note_databases WHERE note_id=?',(nid,)).fetchone()
        if not d:fail(404,localized_message('databaseNotFound'))
        return n,d
    def operations(c,session):
        from app.note_databases import DatabaseOperations
        return DatabaseOperations(c,session['user_id'],session,now,fail)
    def payload(c,n,d,uid):
        return operations(c,{'user_id':uid}).payload(n,d)
    @r.get('/api/database-catalog')
    def catalog(request:Request):
        session=ready(request)
        with db() as c:return operations(c,session).catalog()
    @r.post('/api/databases/{nid}/formula-preview')
    def preview(nid:str,data:FormulaPreview,request:Request):
        session=ready(request)
        with db() as c:return operations(c,session).preview(nid,data.expression,data.record_id)
    @r.post('/api/databases')
    def create(data:DatabaseCreate,request:Request):
        session=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return operations(c,session).create(data.title,data.parent_id,data.space_id)
    @r.patch('/api/notes/{nid}/database-title')
    def title(nid:str,data:DatabaseTitle,request:Request):
        session=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return operations(c,session).title(nid,data.title,data.version)
    @r.get('/api/databases/{nid}')
    def get(nid:str,request:Request):
        session=ready(request)
        with db() as c:return operations(c,session).read(nid)
    @r.put('/api/databases/{nid}')
    def configure(nid:str,data:Configuration,request:Request):
        session=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return operations(c,session).configure(nid,data.properties,data.views,data.version)
    @r.post('/api/databases/{nid}/records')
    def add(nid:str,data:RecordCreate,request:Request):
        session=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return operations(c,session).add(nid,data.title,data.properties)
    @r.patch('/api/database-records/{rid}')
    def update(rid:str,data:RecordUpdate,request:Request):
        session=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return operations(c,session).update(rid,data.properties,data.version)
    @r.get('/api/notes/{nid}/permissions')
    def permissions(nid:str,request:Request):
        s=ready(request)
        with db() as c:
            from app.modules.note_members import permission_payload
            n=access(c,nid,s['user_id']);return permission_payload(c,n,s['user_id'],now())
    @r.put('/api/notes/{nid}/permissions')
    def permission(nid:str,data:Permission,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=access(c,nid,s['user_id'])
            if not can_manage(c,n,s['user_id']):fail(403,localized_message('thePageOwnerChangesPermissionsInAProject'))
            from app.note_access import participants,save_rule,access_index
            if not n['space_id'] or data.user_id not in participants(c,n):fail(422,localized_message('selectAPageParticipant'))
            if (data.user_id==n['user_id'] and not access_index(c).work_space(n['space_id'])) or can_manage(c,n,data.user_id):fail(422,localized_message('theAuthorRetainsEditingOwnersAndAdministratorsRetain'))
            index=access_index(c)
            previous_role=index.role(n,data.user_id)
            _,policy=index.scope(n)
            if data.user_id in index.rules.get(nid,{}) or data.user_id not in index.members.get(n['space_id'],set()) or (policy and policy['mode']=='restricted'):save_rule(c,nid,data.user_id,data.role)
            if data.role is None:c.execute('DELETE FROM note_permissions WHERE note_id=? AND user_id=?',(nid,data.user_id))
            else:
                c.execute('INSERT INTO note_permissions VALUES(?,?,?) ON CONFLICT(note_id,user_id) DO UPDATE SET role=excluded.role',(nid,data.user_id,data.role))
            if n['space_id']:
                from app.workspace_lifecycle import audit
                audit(c,n['space_id'],s['user_id'],'permission_changed',{'note_id':nid,'user_id':data.user_id,'before_role':previous_role,'after_role':data.role},now())
        return {'ok':True}
    @r.get('/api/notes/{nid}/comments')
    def comments(nid:str,request:Request):
        s=ready(request)
        with db() as c:
            access(c,nid,s['user_id']);items=[]
            for row in c.execute('SELECT a.*,u.login FROM note_comments a JOIN users u ON u.id=a.user_id WHERE note_id=? ORDER BY created,a.rowid LIMIT 1000',(nid,)):
                item=dict(row);item['mentions']=json.loads(item['mentions']);item['profile']=actor(c,row['user_id'],now());items.append(item)
            return items
    @r.post('/api/notes/{nid}/comments')
    def comment(nid:str,data:Comment,request:Request):
        s=ready(request);uid=s['user_id']
        if not data.body.strip():fail(422,localized_message('enterAComment'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=access(c,nid,uid);require_role(c,n,uid,fail,True)
            if c.execute('SELECT COUNT(*) FROM note_comments WHERE note_id=?',(nid,)).fetchone()[0]>=1000:fail(422,localized_message('thePageLimitIs1000Comments'))
            if data.parent_id:
                parent=c.execute('SELECT * FROM note_comments WHERE id=? AND note_id=?',(data.parent_id,nid)).fetchone()
                if not parent or parent['parent_id']:fail(422,localized_message('theReplyRefersToTheTopCommentOn'))
                if parent['resolved']:fail(409,localized_message('theDiscussionHasAlreadyBeenEnded'))
            block_id=data.block_id
            if data.parent_id:block_id=parent['block_id']
            if block_id:
                def ids(blocks):return {b.get('id') for b in blocks}|set().union(*(ids(b.get('children',[])) for b in blocks))
                if block_id not in ids(json.loads(n['blocks'] or '[]')):fail(422,localized_message('theBlockWasNotFoundFirstSaveThe'))
            logins=set(re.findall(r'(?<![\w@])@([a-z0-9_.-]{3,40})',data.body.lower()));mentions=[]
            if n['space_id']:
                from app.note_access import participants
                allowed=participants(c,n)
                mentions=[u['id'] for u in c.execute('SELECT id,login FROM users') if u['id'] in allowed and u['login'] in logins]
            cid=secrets.token_urlsafe(12)
            c.execute('INSERT INTO note_comments(id,note_id,user_id,parent_id,block_id,body,mentions,created) VALUES(?,?,?,?,?,?,?,?)',(cid,nid,uid,data.parent_id,block_id,data.body.strip(),json.dumps(mentions),now()))
            for mentioned in mentions:
                if mentioned!=uid:notify(c,mentioned,'mention',{'note_id':nid,'comment_id':cid,'actor_id':uid,'actor':s['login'],'preview':data.body[:180],'title':n['title']},now())
        return {'ok':True}
    @r.patch('/api/notes/{nid}/comments/{cid}')
    def resolve(nid:str,cid:str,data:Resolve,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=access(c,nid,s['user_id']);require_role(c,n,s['user_id'],fail,True);a=c.execute('SELECT * FROM note_comments WHERE id=? AND note_id=?',(cid,nid)).fetchone()
            if not a or a['parent_id']:fail(404,localized_message('noDiscussionFound'))
            if a['user_id']!=s['user_id'] and not can_manage(c,n,s['user_id']):fail(403,localized_message('theDiscussionCanBeEndedByTheAuthor'))
            c.execute('UPDATE note_comments SET resolved=? WHERE id=?',(data.resolved,cid))
        return {'ok':True}
    @r.delete('/api/notes/{nid}/comments/{cid}')
    def delete(nid:str,cid:str,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=access(c,nid,s['user_id']);require_role(c,n,s['user_id'],fail,True);a=c.execute('SELECT * FROM note_comments WHERE id=? AND note_id=?',(cid,nid)).fetchone()
            if not a:fail(404,localized_message('noCommentFound'))
            if a['user_id']!=s['user_id'] and not can_manage(c,n,s['user_id']):fail(403,localized_message('theAuthorOrOwnerCanDeleteTheComment'))
            c.execute('DELETE FROM note_comments WHERE id=?',(cid,))
        return {'ok':True}
    @r.get('/api/note-mentions')
    def mentions(request:Request):
        s=ready(request);result=[]
        with db() as c:
            from app.note_access import visible_ids
            allowed=visible_ids(c,s['user_id'])
            for row in c.execute('SELECT a.id,a.user_id,a.note_id,a.body,a.created,a.mentions,n.title,u.login FROM note_comments a JOIN notes n ON n.id=a.note_id JOIN users u ON u.id=a.user_id WHERE n.deleted IS NULL ORDER BY a.created DESC,a.rowid DESC'):
                if row['note_id'] in allowed and s['user_id'] in json.loads(row['mentions']):item=dict(row);item.pop('mentions');item['profile']=actor(c,row['user_id'],now());result.append(item)
        return result[:100]
    return r
