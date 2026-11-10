"""Portable personal content, never a database dump or account credentials."""
import copy
import hashlib
import json
import re
import secrets
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
from typing import Annotated, Literal
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from PIL import Image

from app.backups import BackupError, secure_write
from app.modules.appearance import Appearance, THEMES, WALLPAPERS
from app.modules.media import inspect_image, MAX_SIZE
from app.modules.notes import validate_blocks, COVERS
from app.modules.notion import validate_config, validate_values
from app.modules.preferences import MODULES, ModulePreferences
from app.modules.widget_models import DashboardLayout, validate_layout
from app.modules.tasks import ListPayload, TaskPayload

MAGIC = b'DRG-PERSONAL-01\x00'
from app.archive_stream import ArchiveWriter,open_archive,MAX_ARCHIVE
from app.file_store import file_store,FileStore
from app.chat_storage import attachment_room
MAX_MEDIA = MAX_ARCHIVE
Id = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_-]{16}$')]
AssetId = Annotated[str, Field(pattern=r'^[a-f0-9]{32}$')]
Stamp = Annotated[int, Field(ge=0, le=9223372036854775807)]
Flag = Literal[0, 1]
ASSET_URL = re.compile(r'/api/media/([a-f0-9]{32})(?![a-f0-9])')
PAGE_URL = re.compile(r'/page/([a-zA-Z0-9_-]{16})(?![a-zA-Z0-9_-])')
ASSET_LINK = re.compile(r'''(?:https?://[^/\s<>"']+)?/api/media/([a-f0-9]{32})(?![a-f0-9])''')
NOTE_ASSET_LINK=re.compile(r'''(?:https?://[^/\s<>"']+)?/api/notes/([a-zA-Z0-9_-]{16})/assets/([a-f0-9]{32})(?![a-f0-9])''')
NOTE_FILE_LINK=re.compile(r'''(?:https?://[^/\s<>"']+)?/api/notes/([a-zA-Z0-9_-]{16})/files/([a-zA-Z0-9_-]{16})(?![a-zA-Z0-9_-])''')
PAGE_LINK = re.compile(r'''(?:https?://[^/\s<>"']+)?/page/([a-zA-Z0-9_-]{16})(?![a-zA-Z0-9_-])''')
TASK_ARCHIVE_ORIGIN='https://drg-personal.invalid'


def task_links(value,callback,origin=None):
    """Canonicalize only this site's task links; external URLs stay literal."""
    def replace(match,kind):
        text=match[0]
        if text.startswith('/') and match.start():
            prefix=re.split(r'''[\s<>"']''',value[:match.start()])[-1]
            if '://' in prefix or prefix.startswith('//'):return text
        if not text.startswith('/'):
            if origin is None:return text
            try:parsed=urlsplit(text)
            except ValueError:return text
            if (parsed.scheme,parsed.netloc.lower())!=(urlsplit(origin).scheme,urlsplit(origin).netloc.lower()):return text
        return callback(match,kind)
    for pattern,kind in ((NOTE_ASSET_LINK,'note_asset'),(ASSET_LINK,'asset'),(PAGE_LINK,'page')):
        value=pattern.sub(lambda match:replace(match,kind),value)
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Page(Strict):
    id: Id
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=200000)
    blocks: list[dict] | None
    parent_id: Id | None
    icon: str = Field(max_length=100)
    cover: str
    locked: Flag = 0
    favorite: Flag
    full_width: Flag
    deleted: Stamp | None
    trash_batch: Id | None
    position: int = Field(ge=0, le=1000000)
    created: Stamp
    updated: Stamp
    source_shared: StrictBool = False


class Revision(Strict):
    id: Id
    note_id: Id
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=200000)
    blocks: list[dict] | None
    created: Stamp


class Task(TaskPayload):
    position: int = Field(default=0, strict=True, ge=0, le=9007199254740991)
    model_config = ConfigDict(extra='forbid', strict=True)
    id: Id
    title: str = Field(min_length=1, max_length=300)
    day: str = Field(pattern=r'^(|\d{4}-\d{2}-\d{2})$')
    done: Flag
    created: Stamp
    completed_at: Annotated[int, Field(ge=0,le=32503680000)] | None = None
    list_id: Id | None = None
    parent_id: Id | None = None

    @field_validator('day')
    @classmethod
    def real_date(cls, value):
        if value: date.fromisoformat(value)
        return value


class PortableTaskList(ListPayload):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: Id
    is_default: StrictBool = False
    source_shared: StrictBool = False
    created: Stamp
    updated: Stamp


class Asset(Strict):
    id: AssetId
    mime: str
    size: int = Field(ge=1, le=MAX_SIZE)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    frames: int = Field(ge=1, le=200)
    created: Stamp
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class Database(Strict):
    note_id: Id
    properties: list[dict]
    views: list[dict]


class Record(Strict):
    note_id: Id
    database_id: Id
    properties: dict


class Comment(Strict):
    id: Id
    note_id: Id
    parent_id: Id | None
    block_id: str | None = Field(max_length=100)
    body: str = Field(min_length=1, max_length=4000)
    created: Stamp
    resolved: Flag


from app.retention import purge_tasks,purge_trash
from app.modules.authenticator import AuthenticatorInput,normalize
from app.modules.expenses import Event,Entry,validate_event,validate_entry,validate_photos,purge_expenses,validate_groups
class PortableAuthenticator(AuthenticatorInput):
    id: Id
    created: Stamp
class PortableEntry(Entry):
    id: Id
    created: Stamp
    event_version:int=1
class PortableEvent(Event):
    photos:list[str]=Field(default_factory=list,max_length=50)
    id: Id
    created: Stamp
    ends: Stamp
    purge_at: Stamp
    entries:list[PortableEntry]=Field(default_factory=list,max_length=2000)

class PortableFile(Strict):
    id: Id
    parent_id: Id | None
    kind: Literal['file','folder']
    name: str = Field(min_length=1,max_length=255)
    mime: str = Field(max_length=255)
    size: int = Field(ge=0,le=25*1024**3)
    object_id: AssetId | None
    created: Stamp
    updated: Stamp
    deleted: Stamp | None = None
    trash_batch: Id | None = None
    sha256: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')

class PageAsset(Strict):
    id: AssetId
    note_id: Id
    kind: Literal['icon','cover']
    payload: dict
    created: Stamp

from app.tracker_portability import Project as PortableTrackerProject

class Content(Strict):
    tracker_projects: list[PortableTrackerProject] = Field(default_factory=list,max_length=100)
    task_lists: list[PortableTaskList] = Field(default_factory=list,max_length=1000)
    files: list[PortableFile] = Field(default_factory=list,max_length=100000)
    note_assets: list[PageAsset] = Field(default_factory=list,max_length=40000)
    authenticators:list[PortableAuthenticator]=Field(default_factory=list,max_length=100)
    expenses:list[PortableEvent]=Field(default_factory=list,max_length=100)
    settings: dict
    notes: list[Page] = Field(max_length=20000)
    revisions: list[Revision] = Field(max_length=100000)
    tasks: list[Task] = Field(max_length=20000)
    media: list[Asset] = Field(max_length=50000)
    databases: list[Database] = Field(max_length=20000)
    records: list[Record] = Field(max_length=20000)
    comments: list[Comment] = Field(max_length=20000)


def invalid(*args):
    raise BackupError('Некорректные личные данные или ссылки на объекты вне архива.')


def unique(rows, field='id'):
    result = {getattr(row, field): row for row in rows}
    if len(result) != len(rows):
        invalid()
    return result


class ArchiveReferences:
    """Validate widget references against the archive, never the live database."""
    def __init__(self, assets, authenticators=None, task_lists=None):
        self.assets = assets
        self.authenticators=authenticators or {}
        self.task_lists=task_lists or {}

    def execute(self, query, args):
        class Result:
            def __init__(self, present): self.present = present
            def fetchone(self): return (1,) if self.present else None
        return Result((query.startswith('SELECT 1 FROM media ') and args[0] in self.assets) or (query.startswith('SELECT 1 FROM authenticators ') and args[0] in self.authenticators) or (query.startswith('SELECT 1 FROM task_lists ') and args[0] in self.task_lists))


def validate_settings(settings, assets,authenticators=None,task_lists=None):
    if not isinstance(settings, dict) or set(settings) - {'modules', 'appearance', 'dashboard', 'profile','notifications','chat'}:
        invalid()
    result = copy.deepcopy(settings)
    if 'chat' in result:
        value=result.pop('chat')
        if not isinstance(value,dict) or set(value)-{'favorites','pinned'} or any(not isinstance(ids,list) or len(ids)>256 or any(not isinstance(sid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16}',sid) for sid in ids) or len(set(ids))!=len(ids) for ids in value.values()):invalid()
        # Shared room identifiers are not portable account content. Importing
        # an archive cannot replace live relationship-scoped chat preferences.
    if 'notifications' in result:
        v=result['notifications']
        if not isinstance(v,dict) or set(v)-{'sound','popups'} or any(type(x) is not bool for x in v.values()):invalid()
    if 'profile' in result:
        profile=result['profile']
        from app.modules.profile import valid_profile
        if not valid_profile(profile,lambda aid:aid in assets):invalid()
    if any(a not in assets for a in ASSET_URL.findall(json.dumps(result))): invalid()
    if 'modules' in result:
        m = ModulePreferences(enabled=result['modules'])
        if set(m.enabled) - MODULES.keys(): invalid()
        result['modules'] = m.enabled
    if 'appearance' in result:
        a = Appearance.model_validate(result['appearance'])
        if a.theme not in THEMES or a.mode not in ('light', 'dark', 'schedule', 'system') or a.day_start == a.night_start:
            invalid()
        if a.wallpaper not in WALLPAPERS and a.wallpaper not in assets:invalid()
        result['appearance'] = a.model_dump()
    if 'dashboard' in result:
        layout = DashboardLayout.model_validate(result['dashboard'])
        # Space membership and access rights cannot be imported.
        if any(w.module_id == 'chat' and w.config.get('space_id') for w in layout.instances): invalid()
        validate_layout(layout, ArchiveReferences(assets,authenticators,task_lists), 'archive', invalid)
        result['dashboard'] = layout.model_dump()
    return result


def validate_content(content):
    notes = unique(content.notes)
    page_assets=unique(content.note_assets)
    files=unique(content.files)
    from app.modules.files import filename
    from app.modules.note_assets import asset_id,validate_attribution
    object_ids=set()
    for f in content.files:
        if (f.deleted is None)!=(f.trash_batch is None):invalid()
        try:filename(f.name)
        except Exception:invalid()
        if f.kind=='file':
            if not f.object_id or not f.sha256 or f.object_id in object_ids:invalid()
            object_ids.add(f.object_id)
        elif f.object_id is not None or f.sha256 is not None or f.size!=0:invalid()
        chain=set();current=f
        while current.parent_id:
            if current.id in chain:invalid()
            chain.add(current.id);parent=files.get(current.parent_id)
            if not parent or parent.kind!='folder':invalid()
            if f.deleted is None and parent.deleted is not None:invalid()
            current=parent
        if f.deleted is not None:
            root=files.get(f.trash_batch)
            if not root or root.deleted!=f.deleted or root.trash_batch!=root.id or root.id not in chain|{f.id,current.id}:invalid()
    for a in content.note_assets:
        if a.note_id not in notes:invalid()
        validate_attribution(a.payload,invalid)
    assets = unique(content.media)
    if any(a.id not in assets for a in content.note_assets):invalid()
    tasks=unique(content.tasks);task_lists=unique(content.task_lists);unique(content.revisions)
    from app.tracker_portability import validate as validate_tracker_projects
    validate_tracker_projects(content.tracker_projects,content.files,invalid,set(notes))
    if sum(item.is_default for item in content.task_lists)>1:invalid()
    if task_lists and not any(item.is_default for item in content.task_lists):
        if any(item.get('module_id')=='tasks' and item.get('config',{}).get('view','list')=='list' and item.get('config',{}).get('list_id') is None for item in content.settings.get('dashboard',{}).get('instances',[])):invalid()
    for task in content.tasks:
        if not task.title.strip() or (task.list_id is not None and task.list_id not in task_lists):invalid()
        if task_lists and task.list_id is None:invalid()
        if not task.done and task.completed_at is not None:invalid()
        if task.time and not task.day:invalid()
        chain=set();current=task
        while current.parent_id is not None:
            if current.id in chain:invalid()
            chain.add(current.id);parent=tasks.get(current.parent_id)
            if not parent or parent.list_id!=task.list_id:invalid()
            current=parent
            if len(chain)>=5:invalid()
        def check_task_link(match,kind):
            if kind=='page' and match[1] not in notes:invalid()
            if kind=='asset' and match[1] not in assets:invalid()
            if kind=='note_asset' and (match[1] not in notes or match[2] not in assets):invalid()
            return match[0]
        task_links(task.notes,check_task_link)
        parsed=urlsplit(task.url)
        if parsed.scheme+'://'+parsed.netloc==TASK_ARCHIVE_ORIGIN:
            if not PAGE_URL.fullmatch(parsed.path) and not ASSET_URL.fullmatch(parsed.path):invalid()
            task_links(parsed.path,check_task_link)
    comments = unique(content.comments)
    databases = unique(content.databases, 'note_id')
    unique(content.records, 'note_id')
    if sum(a.size for a in content.media) > MAX_MEDIA: invalid()
    content.settings = validate_settings(content.settings, assets,unique(content.authenticators),task_lists)
    finished = set()
    for n in content.notes:
        current = n.id; chain = set()
        while current is not None and current not in finished:
            if current not in notes or current in chain: invalid()
            chain.add(current); current = notes[current].parent_id
        finished.update(chain)
        if (n.cover not in COVERS and not asset_id(n.cover)) or not n.title.strip(): invalid()
        for kind in ('icon','cover'):
            aid=asset_id(getattr(n,kind))
            if aid and (aid not in page_assets or page_assets[aid].note_id!=n.id or page_assets[aid].kind!=kind):invalid()
    def check_refs(value):
        if isinstance(value, str):
            if any(a not in assets for a in ASSET_URL.findall(value)): invalid()
        elif isinstance(value, list):
            for v in value: check_refs(v)
        elif isinstance(value, dict):
            if value.get('type') == 'pageLink' and value.get('props', {}).get('pageId') not in notes: invalid()
            for v in value.values(): check_refs(v)
    for n in [*content.notes, *content.revisions]:
        validate_blocks(n.blocks, invalid); check_refs(n.body); check_refs(n.blocks)
        if isinstance(n, Revision):
            if n.note_id not in notes: invalid()
    for d in content.databases:
        if d.note_id not in notes: invalid()
        validate_config(d.properties, d.views, invalid)
        check_refs(d.properties); check_refs(d.views)
    for r in content.records:
        if r.note_id not in notes or r.database_id not in databases or notes[r.note_id].parent_id != r.database_id or r.note_id in databases: invalid()
        validate_values(r.properties, databases[r.database_id].properties, invalid)
        check_refs(r.properties)
    validate_database_references(content,invalid)
    counts = {}
    for c in content.comments:
        if c.note_id not in notes: invalid()
        check_refs(c.body)
        if c.parent_id:
            p = comments.get(c.parent_id)
            if not p or p.note_id != c.note_id or p.parent_id or p.id == c.id: invalid()
        counts[c.note_id] = counts.get(c.note_id, 0) + 1
        if counts[c.note_id] > 1000: invalid()
    unique(content.authenticators);unique(content.expenses)
    for entry in content.authenticators:normalize(AuthenticatorInput(**entry.model_dump(exclude={'id','created'})))
    for event in content.expenses:
        validate_event(event)
        validate_groups([g.model_dump() for g in event.groups or []],[p.model_dump() for p in event.participants])
        validate_photos(event.photos,lambda aid:aid in assets)
        if event.ends<event.created or event.ends>event.created+20*3600 or event.purge_at!=event.ends+event.retention_days*86400:invalid()
        unique(event.entries)
        for entry in event.entries:validate_entry(entry,[p.model_dump() for p in event.participants],[g.model_dump() for g in event.groups or []])
    return content


def validate_database_references(content,fail):
    databases={d.note_id:d for d in content.databases};records={r.note_id:r for r in content.records}
    for d in content.databases:
        props={p['id']:p for p in d.properties}
        for p in d.properties:
            if p['type']=='relation':
                target=databases.get(p['database_id'])
                if not target:fail()
                if p.get('inverse_id'):
                    inverse=next((x for x in target.properties if x['id']==p['inverse_id']),None)
                    if not inverse or inverse.get('database_id')!=d.note_id or inverse.get('inverse_id')!=p['id']:fail()
            if p['type']=='rollup':
                relation=props[p['relation_id']];target=databases[relation['database_id']]
                if p['property']!='title' and not any(x['id']==p['property'] for x in target.properties):fail()
        graph={r.note_id:set() for r in content.records if r.database_id==d.note_id}
        for record in content.records:
            if record.database_id!=d.note_id:continue
            for p in d.properties:
                if p['type'] not in ('relation','dependency'):continue
                for rid in record.properties.get(p['id']) or []:
                    if rid not in records or records[rid].database_id!=(p['database_id'] if p['type']=='relation' else d.note_id):fail()
                    if p['type']=='dependency':graph[record.note_id].add(rid)
        done=set();visiting=set()
        def walk(rid):
            if rid in visiting:fail()
            if rid in done:return
            visiting.add(rid)
            for parent in graph[rid]:walk(parent)
            visiting.remove(rid);done.add(rid)
        for rid in graph:walk(rid)


def portable_links(databases,records):
    """Preserve portable data when a linked database/record was already deleted.

    No outside identifier survives as a live relation in a personal copy.
    """
    bases={d['note_id']:d for d in databases};rows={r['note_id']:r for r in records}
    for db in databases:
        broken=set()
        for prop in db['properties']:
            if prop['type']=='relation':
                target=bases.get(prop['database_id'])
                if not target:
                    broken.add(prop['id'])
        # Keep names/IDs for formulas, replacing an unavailable link with a marker.
        for i,prop in enumerate(db['properties']):
            if prop['id'] in broken:db['properties'][i]={'id':prop['id'],'name':prop['name'],'type':'text'}
        props={p['id']:p for p in db['properties']}
        for i,prop in enumerate(db['properties']):
            if prop['type']=='rollup':
                rel=props.get(prop['relation_id']);target=bases.get(rel.get('database_id')) if rel else None
                if not target or (prop['property']!='title' and not any(p['id']==prop['property'] for p in target['properties'])):
                    db['properties'][i]={'id':prop['id'],'name':prop['name'],'type':'formula','expression':'"Связанная база или свойство удалены"'}
            if prop['type']=='relation' and prop.get('inverse_id'):
                target=bases[prop['database_id']]
                inverse=next((p for p in target['properties'] if p['id']==prop['inverse_id']),None)
                if not inverse or inverse.get('database_id')!=db['note_id'] or inverse.get('inverse_id')!=prop['id']:prop['two_way']=False;prop.pop('inverse_id',None)
        for record in records:
            if record['database_id']!=db['note_id']:continue
            for key in broken:
                if key in record['properties']:record['properties'][key]='Связанная база удалена'
            for prop in db['properties']:
                value=record['properties'].get(prop['id'])
                if prop['type'] in ('relation','dependency') and isinstance(value,list):
                    target=prop['database_id'] if prop['type']=='relation' else db['note_id']
                    record['properties'][prop['id']]=[x for x in value if x in rows and rows[x]['database_id']==target]


def summary(content):
    return {'tracker_groups':sum(len(p.groups) for p in content.tracker_projects),'tracker_projects':len(content.tracker_projects),'tracker_issues':sum(len(p.issues) for p in content.tracker_projects),'files':sum(f.kind=='file' for f in content.files),'folders':sum(f.kind=='folder' for f in content.files),'file_bytes':sum(f.size for f in content.files),'page_images':len(content.note_assets),'authenticators':len(content.authenticators),'expenses':len(content.expenses),'notes': len(content.notes), 'shared_notes': sum(n.source_shared for n in content.notes), 'trash': sum(n.deleted is not None for n in content.notes),
            'revisions': len(content.revisions), 'databases': len(content.databases),
            'tasks': len(content.tasks), 'task_lists':len(content.task_lists), 'shared_task_lists':sum(item.source_shared for item in content.task_lists), 'images': len(content.media),
            'media_bytes': sum(a.size for a in content.media), 'comments': len(content.comments),
            'settings': list(content.settings)}


class PersonalData:
    def __init__(self, db, root, now, public_url,cipher=None):
        self.cipher=cipher
        self.db = db; self.root = Path(root); self.now = now; self.public_url = public_url
        from app.data_crypto import DataCipher,media_store
        self.media = media_store(db,root);self.staging = DataCipher.ephemeral();self.objects=file_store(db,root)

    def stage_write(self,directory,name,body):
        secure_write(Path(directory)/name,self.staging.seal(body,'personal:'+Path(directory).name+':'+name))

    def stage_read(self,directory,name):
        return self.staging.open((Path(directory)/name).read_bytes(),'personal:'+Path(directory).name+':'+name)

    def temp(self):
        from app.process_locks import leased_temp
        return leased_temp(self.root, '.drg-personal-work-')

    def snapshot(self, uid, directory):
        from app.file_trash import purge_file_trash
        from app.modules.tasks import accessible_task_lists,task_snapshot
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE');purge_trash(c,self.now());purge_expenses(c,self.now());purge_file_trash(c,self.now(),uid)
            # Explicit allowlists: no users row, security state, foreign authors or SQL file.
            settings = json.loads(c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()[0])
            if purge_tasks(c,uid,settings,self.now()):c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
            task_rows=task_snapshot(c,uid,self.now(),include_deleted=True)
            from app.tracker_portability import snapshot as snapshot_tracker,related_files as tracker_files
            tracker_projects=snapshot_tracker(c,uid,self.now())
            list_rows=accessible_task_lists(c,uid,include_deleted=True)
            list_ids={row['id']:secrets.token_urlsafe(12) for row in list_rows}
            task_lists=[{'id':list_ids[row['id']],**json.loads(row['payload']),
                         'is_default':bool(row['is_default'] and row['owner_id']==uid),
                         'source_shared':row['owner_id']!=uid or bool(c.execute('SELECT 1 FROM task_list_members m WHERE m.list_id=? AND EXISTS (SELECT 1 FROM friendships f WHERE (f.first_id=? AND f.second_id=m.user_id) OR (f.second_id=? AND f.first_id=m.user_id))',(row['id'],uid,uid)).fetchone()),
                         'created':row['created'],'updated':row['updated']} for row in list_rows]
            settings = {k: v for k, v in settings.items() if k in ('appearance', 'modules', 'dashboard', 'profile','notifications')}
            # External archive access and its local switch are not portable account content.
            if 'modules' in settings:settings['modules']={k:v for k,v in settings['modules'].items() if k in MODULES}
            instances=settings.get('dashboard', {}).get('instances', [])
            for widget in instances:
                if widget['module_id']=='tasks' and widget.get('config',{}).get('view','list')!='list':
                    widget['config']={'view':widget['config']['view'],'list_id':None}
            if 'dashboard' in settings:
                # Revoked access cannot become a portable reference or a fallback
                # to an unrelated list; the live dashboard is left untouched.
                instances=[w for w in instances if w['module_id']!='tasks' or not w.get('config',{}).get('list_id') or w['config']['list_id'] in list_ids]
                settings['dashboard']['instances']=instances
            for w in instances:
                if w['module_id'] == 'chat': w['config'] = {'space_id': None}
                if w['module_id']=='tasks' and w.get('config',{}).get('list_id') in list_ids:
                    w['config']['list_id']=list_ids[w['config']['list_id']]
            from app.note_access import visible_ids
            accessible=visible_ids(c,uid)
            scope='n.id IN ('+','.join('?' for _ in accessible)+')' if accessible else '0=1'
            scope_params=tuple(accessible)
            fields = tuple(k for k in Page.model_fields if k != 'source_shared')
            notes = [dict(r) for r in c.execute('SELECT '+','.join('n.'+f for f in fields)+', n.space_id IS NOT NULL AS source_shared FROM notes n WHERE '+scope, scope_params) if r['id'] in accessible]
            for n in notes:
                if n['parent_id'] not in accessible:n['parent_id']=None
                n['source_shared'] = bool(n['source_shared'])
                n['favorite']=int(bool(c.execute('SELECT 1 FROM note_favorites WHERE note_id=? AND user_id=?',(n['id'],uid)).fetchone()))
            authenticators=[]
            totp=c.execute('SELECT totp FROM users WHERE id=?',(uid,)).fetchone()[0]
            if not totp and c.execute('SELECT 1 FROM authenticators WHERE user_id=?',(uid,)).fetchone():raise BackupError('Для переноса ключей аутентификатора сначала включите 2FA аккаунта.')
            for entry in c.execute('SELECT * FROM authenticators WHERE user_id=?',(uid,)):
                if not self.cipher:raise BackupError('Не настроено шифрование секретов.')
                item=dict(entry);item.pop('user_id');item['secret']=self.cipher.decrypt(item['secret'].encode()).decode();authenticators.append(item)
            expenses=[]
            for event in c.execute('SELECT * FROM expense_events WHERE user_id=? AND purge_at>?',(uid,self.now())):
                item={k:event[k] for k in ('id','title','created','ends','purge_at')};item.update(json.loads(event['payload']));item['entries']=[{'id':e['id'],'created':e['created'],**json.loads(e['payload'])} for e in c.execute('SELECT * FROM expense_entries WHERE event_id=?',(event['id'],))];expenses.append(item)
            def related(table, model):
                fields = tuple(model.model_fields)
                return [dict(r) for r in c.execute('SELECT '+','.join('a.'+f for f in fields)+' FROM '+table+' a JOIN notes n ON n.id=a.note_id WHERE '+scope, scope_params) if r['note_id'] in accessible]
            from app.note_history import HISTORY_SECONDS
            revisions = [row for row in related('note_revisions', Revision) if row['created']>self.now()-HISTORY_SECONDS]
            databases = related('note_databases', Database)
            records = [row for row in related('database_records', Record) if row['database_id'] in accessible]
            fields = tuple(Comment.model_fields)
            comments = [dict(r) for r in c.execute('SELECT '+','.join('a.'+f for f in fields)+' FROM note_comments a JOIN notes n ON n.id=a.note_id WHERE '+scope+' AND a.user_id=?', (*scope_params,uid)) if r['note_id'] in accessible]
            comment_ids = {r['id'] for r in comments}
            for r in comments:
                if r['parent_id'] not in comment_ids: r['parent_id'] = None
            tasks=[{key:item[key] for key in Task.model_fields if key in item}|{'list_id':list_ids[item['list_id']]} for item in task_rows]
            file_rows=[dict(r) for r in c.execute('SELECT id,parent_id,kind,name,mime,size,object_id,created,updated,deleted,trash_batch FROM files WHERE user_id=?',(uid,)) if not attachment_room(r['id'])]
            exported_ids={r['id'] for r in file_rows}
            for file in tracker_files(c,tracker_projects):
                if file['id'] not in exported_ids:file_rows.append(file);exported_ids.add(file['id'])
            for n in notes:
                for r in c.execute('SELECT id,parent_id,kind,name,mime,size,object_id,created,updated,deleted,trash_batch FROM files WHERE id=? OR parent_id=?',(n['id'],n['id'])):
                    if r['id'] not in exported_ids:file_rows.append(dict(r));exported_ids.add(r['id'])
            note_assets=related('note_assets',PageAsset)
            for a in note_assets:a['payload']=json.loads(a['payload'])
            media = [dict(r) for r in c.execute('SELECT id,mime,size,width,height,frames,created FROM media WHERE user_id=?', (uid,))]
            owned={a['id'] for a in media}
            for a in note_assets:
                if a['id'] not in owned:
                    media.append(dict(c.execute('SELECT id,mime,size,width,height,frames,created FROM media WHERE id=?',(a['id'],)).fetchone()));owned.add(a['id'])
        assets = {r['id'] for r in media}; note_ids = {r['id'] for r in notes}
        exported_files={r['id'] for r in file_rows}
        def scrub(value):
            if isinstance(value, str):
                value = ASSET_LINK.sub(lambda m: m[0] if m[1] in assets else '#unavailable-image', value)
                value = NOTE_ASSET_LINK.sub(lambda m:m[0] if m[1] in note_ids and m[2] in assets else '#unavailable-image',value)
                value = NOTE_FILE_LINK.sub(lambda m:m[0] if m[1] in note_ids and m[2] in exported_files else '#unavailable-file',value)
                return PAGE_LINK.sub(lambda m: m[0] if m[1] in note_ids else '#unavailable-page', value)
            if isinstance(value, list): return [scrub(v) for v in value]
            if isinstance(value, dict):
                if value.get('type') == 'pageLink' and value.get('props', {}).get('pageId') not in note_ids:
                    return {'type': 'paragraph', 'content': 'Связанная страница не входит в личный архив.'}
                return {k: scrub(v) for k, v in value.items()}
            return value
        for r in [*notes, *revisions]:
            r['blocks'] = scrub(json.loads(r['blocks']) if r['blocks'] is not None else None)
            r['body'] = scrub(r['body'])
        for r in [*databases, *records]:
            for field in ('properties', 'views'):
                if field in r: r[field] = scrub(json.loads(r[field]))
        portable_links(databases,records)
        for r in comments: r['body'] = scrub(r['body'])
        for project in tracker_projects:
            project['description']=scrub(project['description'])
            for group in project['groups']:group['description']=scrub(group['description'])
            for issue in project['issues']:
                issue['description']=scrub(issue['description'])
                for comment in issue['comments']:comment['body']=scrub(comment['body'])
        for task in tasks:
            def scrub_task_link(match,kind):
                if kind=='page':return '/page/'+match[1] if match[1] in note_ids else '#unavailable-page'
                if kind=='asset':return '/api/media/'+match[1] if match[1] in assets else '#unavailable-image'
                return '/api/notes/'+match[1]+'/assets/'+match[2] if match[1] in note_ids and match[2] in assets else '#unavailable-image'
            task['notes']=task_links(task.get('notes',''),scrub_task_link,self.public_url)
            parsed=urlsplit(task.get('url',''))
            if (parsed.scheme,parsed.netloc)==(urlsplit(self.public_url).scheme,urlsplit(self.public_url).netloc) and (PAGE_URL.fullmatch(parsed.path) or ASSET_URL.fullmatch(parsed.path)):
                path=task_links(parsed.path,scrub_task_link)
                archive=urlsplit(TASK_ARCHIVE_ORIGIN)
                task['url']=urlunsplit((archive.scheme,archive.netloc,path,parsed.query,parsed.fragment)) if path.startswith('/') else ''
        for a in media:
            if not self.media.exists(a['id']):
                raise BackupError('Личный файл отсутствует. Экспорт не создан.')
            body = self.media.read(a['id'])
            if len(body)!=a['size']:raise BackupError('Некорректный размер личного файла.')
            a['sha256'] = hashlib.sha256(body).hexdigest()
            self.stage_write(directory,a['id'],body)
        staged=FileStore(directory,self.staging,backend='local',bind=False)
        for f in file_rows:
            if f['kind']=='folder':continue
            writer=staged.writer(f['object_id'])
            try:
                for chunk in self.objects.iter_read(f['object_id']):writer.write(chunk)
                size,digest=writer.finish()
            except BaseException:writer.abort();raise
            if size!=f['size']:invalid()
            f['sha256']=digest
        content = validate_content(Content(tracker_projects=tracker_projects,task_lists=task_lists,files=file_rows,note_assets=note_assets,settings=settings, notes=notes, revisions=revisions, tasks=tasks,
                                          media=media, databases=databases, records=records, comments=comments,authenticators=authenticators,expenses=expenses))
        return content

    def encrypt(self, content, password, directory):
        metadata = json.dumps({'format': 'drg-personal', 'version': 1, 'created': self.now(),
                              'data': content.model_dump()}, ensure_ascii=False).encode()
        if len(metadata) > 40 * 1024 * 1024 or len(metadata)+sum(a.size for a in content.media)+sum(f.size for f in content.files) > MAX_ARCHIVE:
            raise BackupError('Содержимое личного архива превышает лимит: 40 Мб текста / ARCHIVE_MAX_GB всего.')
        path=Path(directory)/'personal.drgpersonal';output=ArchiveWriter(path,password,MAGIC,MAX_ARCHIVE)
        try:
            with ZipFile(output,'w',compression=ZIP_DEFLATED,compresslevel=3,allowZip64=True) as z:
                z.writestr('personal.json',metadata)
                for a in content.media:z.writestr('media/'+a.id,self.stage_read(directory,a.id))
                staged=FileStore(directory,self.staging,backend='local',bind=False)
                for f in content.files:
                    if f.kind!='file':continue
                    with z.open('objects/'+f.object_id,'w',force_zip64=True) as member:
                        for chunk in staged.iter_read(f.object_id):member.write(chunk)
            output.finish();return path
        except BaseException:output.abort();raise

    def prepare(self,body,password,directory):
        raw=open_archive(body,password,MAGIC,directory,MAX_ARCHIVE,legacy_limit=200*1024**2)
        def no_duplicates(pairs):
            result = {}
            for k, v in pairs:
                if k in result: invalid()
                result[k] = v
            return result
        try:
            with ZipFile(raw) as z:
                infos = z.infolist(); names = [i.filename for i in infos]
                if len(names) != len(set(names)) or len(names) > 150001 or 'personal.json' not in names: invalid()
                if sum(i.file_size for i in infos) > MAX_ARCHIVE or any(i.flag_bits & 1 or i.compress_type not in (0, 8) for i in infos): invalid()
                if z.getinfo('personal.json').file_size > 40 * 1024 * 1024: invalid()
                manifest = json.loads(z.read('personal.json'), object_pairs_hook=no_duplicates)
                if not isinstance(manifest, dict) or set(manifest) != {'format', 'version', 'created', 'data'} or manifest['format'] != 'drg-personal' or type(manifest['version']) is not int or manifest['version'] != 1 or type(manifest['created']) is not int: invalid()
                content = validate_content(Content.model_validate(manifest['data']))
                if set(names) != {'personal.json'} | {'media/'+a.id for a in content.media} | {'objects/'+f.object_id for f in content.files if f.kind=='file'}: invalid()
                for a in content.media:
                    if z.getinfo('media/'+a.id).file_size != a.size: invalid()
                    image = z.read('media/'+a.id)
                    if hashlib.sha256(image).hexdigest() != a.sha256 or inspect_image(image) != (a.mime, a.width, a.height, a.frames): invalid()
                    self.stage_write(directory,a.id,image)
                staged=FileStore(directory,self.staging,backend='local',bind=False)
                for f in content.files:
                    if f.kind!='file':continue
                    if z.getinfo('objects/'+f.object_id).file_size!=f.size:invalid()
                    writer=staged.writer(f.object_id)
                    try:
                        with z.open('objects/'+f.object_id) as member:
                            while chunk:=member.read(1024*1024):writer.write(chunk)
                        size,digest=writer.finish()
                    except BaseException:writer.abort();raise
                    if (size,digest)!=(f.size,f.sha256):invalid()
                return content
        except BackupError: raise
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, BadZipFile, OSError, EOFError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise BackupError('Личный архив повреждён или содержит недопустимые данные.') from None

    def restore(self, uid, content, directory, apply_settings):
        from app.file_trash import FILE_TRASH_SECONDS,purge_file_trash
        # An old personal archive does not grant a new seven-day retention period.
        trash_cutoff=self.now()-FILE_TRASH_SECONDS
        expired_files={f.id for f in content.files if f.deleted is not None and f.deleted<=trash_cutoff}
        content.files=[f for f in content.files if f.id not in expired_files]
        for f in content.files:
            if f.parent_id in expired_files:f.parent_id=None
        # Expired event-only photos must not become permanent gallery images.
        alive=[e for e in content.expenses if e.purge_at>self.now()]
        expired_photos={aid for e in content.expenses if e.purge_at<=self.now() for aid in e.photos}
        alive_photos={aid for e in alive for aid in e.photos}
        other_content=json.dumps(content.model_dump(exclude={'media','expenses'}))
        discard={aid for aid in expired_photos-alive_photos if aid not in other_content}
        content.media=[a for a in content.media if a.id not in discard];content.expenses=alive
        # Identifiers supplied by the file are only keys inside these local maps.
        pages = {n.id: secrets.token_urlsafe(12) for n in content.notes}
        file_ids={f.id:pages.get(f.id) or secrets.token_urlsafe(12) for f in content.files}
        tracker_project_ids={p.id:secrets.token_urlsafe(12) for p in content.tracker_projects}
        tracker_issue_ids={i.id:secrets.token_urlsafe(12) for p in content.tracker_projects for i in p.issues}
        tracker_group_ids={g.id:secrets.token_urlsafe(12) for p in content.tracker_projects for g in p.groups}
        for identity in tracker_issue_ids:
            if identity in file_ids:file_ids[identity]=tracker_issue_ids[identity]
        assets = {a.id: secrets.token_hex(16) for a in content.media}
        vault_ids={a.id:secrets.token_urlsafe(12) for a in content.authenticators}
        task_list_ids={item.id:secrets.token_urlsafe(12) for item in content.task_lists}
        task_ids={item.id:secrets.token_urlsafe(12) for item in content.tasks}
        comments = {c.id: secrets.token_urlsafe(12) for c in content.comments}
        batches = {n.trash_batch: secrets.token_urlsafe(12) for n in content.notes if n.trash_batch}
        def remap(value):
            if isinstance(value, str):
                value = ASSET_LINK.sub(lambda m: self.public_url+'/api/media/'+assets[m[1]], value)
                value = NOTE_ASSET_LINK.sub(lambda m:'/api/notes/'+pages[m[1]]+'/assets/'+assets[m[2]] if m[1] in pages and m[2] in assets else '#unavailable-image',value)
                value = NOTE_FILE_LINK.sub(lambda m:self.public_url+'/api/notes/'+pages[m[1]]+'/files/'+file_ids[m[2]] if m[1] in pages and m[2] in file_ids else '#unavailable-file',value)
                return PAGE_LINK.sub(lambda m: '/page/'+pages[m[1]] if m[1] in pages else '#unavailable-page', value)
            if isinstance(value, list): return [remap(v) for v in value]
            if isinstance(value, dict):
                result = {k: remap(v) for k, v in value.items()}
                if result.get('type') == 'pageLink': result['props']['pageId'] = pages[value['props']['pageId']]
                return result
            return value
        def remap_record(record):
            props={p['id']:p for d in content.databases if d.note_id==record.database_id for p in d.properties}
            return {k:[pages[x] for x in v] if props[k]['type'] in ('relation','dependency') and v is not None else remap(v) for k,v in record.properties.items()}
        settings = remap(content.settings)
        if settings.get('profile',{}).get('avatar_id'):settings['profile']['avatar_id']=assets[settings['profile']['avatar_id']]
        wallpaper=settings.get('appearance',{}).get('wallpaper')
        if wallpaper and wallpaper not in WALLPAPERS:settings['appearance']['wallpaper']=assets[wallpaper]
        for w in settings.get('dashboard', {}).get('instances', []):
            if w['module_id']=='authenticator':w['config']['entry_ids']=[vault_ids[eid] for eid in w['config'].get('entry_ids',[])]
            if w['module_id']=='tasks' and w.get('config',{}).get('view','list')=='list':
                selected=w.get('config',{}).get('list_id')
                source=selected or next((item.id for item in content.task_lists if item.is_default),None)
                w['config']={'view':'list','list_id':task_list_ids[source] if source else None}
            elif w['module_id']=='tasks':w['config']={'view':w['config']['view'],'list_id':None}
            if w['module_id'] == 'media':
                for a in w['config']['items']:
                    if a['asset_id']: a['asset_id'] = assets[a['asset_id']]
        new_files = [];new_objects=[]
        object_ids={f.object_id:secrets.token_hex(16) for f in content.files if f.kind=='file'}
        tracker_files={identity for project in content.tracker_projects for issue in project.issues for identity in issue.attachments}
        tracker_files.update(f.id for f in content.files if f.kind=='folder' and f.mime.startswith('application/x-drg-tracker;project='))
        while True:
            descendants={f.id for f in content.files if f.parent_id in tracker_files}
            if descendants<=tracker_files:break
            tracker_files.update(descendants)
        staged=FileStore(directory,self.staging,backend='local',bind=False)
        try:
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE'); c.execute('PRAGMA defer_foreign_keys=ON')
                user = c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()
                if not user: invalid()
                if c.execute('SELECT COUNT(*) FROM tracker_projects WHERE owner_id=?',(uid,)).fetchone()[0]+len(content.tracker_projects)>100:
                    from app.tracker import fail as tracker_fail
                    tracker_fail(422,'trackerProjectLimit')
                from app.modules.tasks import ensure_default_list,MAX_LISTS,MAX_TASKS
                default_list=ensure_default_list(c,uid,self.now())
                if c.execute('SELECT COUNT(*) FROM task_lists WHERE owner_id=?',(uid,)).fetchone()[0]+len(content.task_lists)>MAX_LISTS:
                    raise BackupError('После импорта превышается лимит: 100 списков напоминаний.')
                if c.execute('SELECT COUNT(*) FROM tasks WHERE user_id=?',(uid,)).fetchone()[0]+len(content.tasks)>MAX_TASKS:
                    raise BackupError('После импорта превышается лимит: 5000 напоминаний.')
                if content.authenticators:
                    if not c.execute('SELECT totp FROM users WHERE id=?',(uid,)).fetchone()[0]:raise BackupError('В архиве ключи аутентификатора. Перед импортом включите 2FA аккаунта.')
                    if c.execute('SELECT COUNT(*) FROM authenticators WHERE user_id=?',(uid,)).fetchone()[0]+len(content.authenticators)>100:raise BackupError('После импорта превышается лимит: 100 сервисов 2FA.')
                if c.execute('SELECT COUNT(*) FROM expense_events WHERE user_id=? AND purge_at>?',(uid,self.now())).fetchone()[0]+sum(e.purge_at>self.now() for e in content.expenses)>100:raise BackupError('После импорта превышается лимит: 100 событий.')
                for a in content.media:
                    path = self.root/'media'/assets[a.id]
                    self.media.write(assets[a.id], self.stage_read(directory,a.id)); new_files.append(path)
                    c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)', (assets[a.id], uid, a.mime, a.size, a.width, a.height, a.frames, a.created))
                for n in content.notes:
                    data = n.model_dump(); data.update(id=pages[n.id], parent_id=pages.get(n.parent_id), trash_batch=batches.get(n.trash_batch), icon='image:'+assets[n.icon[6:]] if n.icon.startswith('image:') else n.icon, cover='image:'+assets[n.cover[6:]] if n.cover.startswith('image:') else n.cover, body=remap(n.body), blocks=json.dumps(remap(n.blocks)) if n.blocks is not None else None)
                    data.pop('source_shared')
                    fields = tuple(data)
                    c.execute('INSERT INTO notes('+','.join(fields)+',user_id,space_id) VALUES('+','.join('?' for _ in fields)+',?,NULL)', (*data.values(), uid))
                    if n.favorite:c.execute('INSERT INTO note_favorites VALUES(?,?,?)',(pages[n.id],uid,self.now()))
                for a in content.note_assets:
                    c.execute('INSERT INTO note_assets VALUES(?,?,?,?,?)',(assets[a.id],pages[a.note_id],a.kind,json.dumps(a.payload),a.created))
                for f in content.files:
                    if f.kind=='file':
                        identity=object_ids[f.object_id]
                        destination=self.objects.scoped('tracker-attachments') if f.id in tracker_files else self.objects
                        writer=destination.writer(identity);new_objects.append(identity)
                        try:
                            for chunk in staged.iter_read(f.object_id):writer.write(chunk)
                            writer.finish()
                        except BaseException:writer.abort();raise
                    c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated,deleted,trash_batch) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(file_ids[f.id],uid,file_ids.get(f.parent_id),f.kind,f.name,f.mime,f.size,object_ids.get(f.object_id),f.created,f.updated,f.deleted,file_ids.get(f.trash_batch)))
                purge_file_trash(c,self.now(),uid)
                for a in content.authenticators:
                    if not self.cipher:raise BackupError('Не настроено шифрование секретов.')
                    c.execute('INSERT INTO authenticators VALUES(?,?,?,?,?,?,?,?,?)',(vault_ids[a.id],uid,a.label,a.issuer,self.cipher.encrypt(a.secret.encode()).decode(),a.algorithm,a.digits,a.period,a.created))
                for e in content.expenses:
                    if e.purge_at<=self.now():continue
                    eid=secrets.token_urlsafe(12);c.execute('INSERT INTO expense_events(id,user_id,title,payload,created,ends,purge_at) VALUES(?,?,?,?,?,?,?)',(eid,uid,e.title,json.dumps({'participants':[p.model_dump() for p in e.participants],'retention_days':e.retention_days,'currency':e.currency or 'RUB','groups':[g.model_dump() for g in e.groups or []],'photos':[assets[aid] for aid in e.photos]}),e.created,e.ends,e.purge_at))
                    for entry in e.entries:c.execute('INSERT INTO expense_entries VALUES(?,?,?,?)',(secrets.token_urlsafe(12),eid,json.dumps(entry.model_dump(exclude={'id','created','event_version'})),entry.created))
                for r in content.revisions:
                    from app.note_history import HISTORY_SECONDS
                    if r.created<=self.now()-HISTORY_SECONDS:continue
                    c.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)', (secrets.token_urlsafe(12), pages[r.note_id], r.title, remap(r.body), json.dumps(remap(r.blocks)) if r.blocks is not None else None, r.created))
                for d in content.databases:
                    c.execute('INSERT INTO note_databases(note_id,properties,views) VALUES(?,?,?)', (pages[d.note_id], json.dumps([{**p,**({'database_id':pages[p['database_id']]} if p['type']=='relation' else {})} for p in remap(d.properties)]), json.dumps(remap(d.views))))
                for r in content.records:
                    c.execute('INSERT INTO database_records(note_id,database_id,properties) VALUES(?,?,?)', (pages[r.note_id], pages[r.database_id], json.dumps(remap_record(r))))
                current = json.loads(user[0])
                for item in content.task_lists:
                    c.execute('INSERT INTO task_lists(id,owner_id,payload,is_default,created,updated,version) VALUES(?,?,?,0,?,?,1)',
                              (task_list_ids[item.id],uid,json.dumps(item.model_dump(include=set(ListPayload.model_fields))),item.created,item.updated))
                for t in content.tasks:
                    tid=task_ids[t.id]
                    c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)', (tid, uid, t.title, t.day, t.done, t.created))
                for t in content.tasks:
                    completed=t.completed_at if t.done and t.completed_at is not None else self.now() if t.done else None
                    def remap_task_link(match,kind):
                        if kind=='page':return '/page/'+pages[match[1]]
                        if kind=='asset':return '/api/media/'+assets[match[1]]
                        return '/api/notes/'+pages[match[1]]+'/assets/'+assets[match[2]]
                    payload=t.model_dump(include=set(TaskPayload.model_fields));payload['notes']=task_links(t.notes,remap_task_link)
                    parsed=urlsplit(t.url)
                    if parsed.scheme+'://'+parsed.netloc==TASK_ARCHIVE_ORIGIN:
                        path=task_links(parsed.path,remap_task_link)
                        payload['url']=self.public_url+path+('?' +parsed.query if parsed.query else '')+('#'+parsed.fragment if parsed.fragment else '')
                    try:payload=TaskPayload.model_validate(payload).model_dump()
                    except ValueError:invalid()
                    c.execute('INSERT INTO task_details(task_id,list_id,parent_id,payload,completed_at,version) VALUES(?,?,?,?,?,1)',
                              (task_ids[t.id],task_list_ids.get(t.list_id,default_list['id']),task_ids.get(t.parent_id),json.dumps(payload),completed))
                from app import task_order
                imported_orders = {}
                for t in content.tasks:
                    lid=task_list_ids.get(t.list_id,default_list['id'])
                    if lid not in imported_orders: imported_orders[lid]=task_order.positions(c,lid)
                    imported_orders[lid][task_ids[t.id]]=t.position
                for lid,value in imported_orders.items():task_order.save(c,lid,value)
                for a in content.comments:
                    c.execute('INSERT INTO note_comments VALUES(?,?,?,?,?,?,?,?,?)', (comments[a.id], pages[a.note_id], uid, comments.get(a.parent_id), a.block_id, remap(a.body), '[]', a.created, a.resolved))
                from app.tracker_portability import restore as restore_tracker
                restore_tracker(c,content.tracker_projects,uid,self.now(),tracker_project_ids,tracker_issue_ids,file_ids,remap,tracker_group_ids)
                if apply_settings:
                    if 'dashboard' in settings:
                        settings['dashboard']['version'] = current.get('dashboard', {}).get('version', 1)+1
                    current.update(settings)
                    if 'dashboard' in current:
                        layout = DashboardLayout.model_validate(current['dashboard']); validate_layout(layout, c, uid, invalid)
                        current['dashboard'] = layout.model_dump()
                c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(current), uid))
            return summary(content) | {'settings_applied': apply_settings}
        except BaseException:
            with self.db() as c: kept = {r[0] for r in c.execute('SELECT id FROM media WHERE user_id=?', (uid,))}
            for path in new_files:
                if path.name not in kept: self.media.delete(path.name)
            with self.db() as c:kept_objects={r[0] for r in c.execute('SELECT object_id FROM files WHERE user_id=? AND object_id IS NOT NULL',(uid,))}
            for identity in new_objects:
                if identity not in kept_objects:self.objects.delete(identity)
            raise
