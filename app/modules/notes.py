"""Page hierarchy, structured block documents, trash and revisions.

Independent router: receives persistence/auth services explicitly, never imports
main.py. Markdown remains a portable projection; blocks are the rich document.
"""
from app.i18n import message as localized_message
import json, re, secrets
from urllib.parse import urlparse
from app.retention import purge_trash
from app.http import Router, Request,Query
from app.modules.social import note_access, space_access
from app.note_access import access_index,participants,profile,visible_rows
from app.modules.notion import page_role, require_role, can_manage
from pydantic import BaseModel, Field, field_validator

ALLOWED_BLOCKS={'paragraph','heading','bulletListItem','numberedListItem','checkListItem','toggleListItem','quote','codeBlock','table','image','video','audio','file','divider','pageLink','callout','columnList','column'}
COVERS={'','blue','sage','sand','rose','night'}

class PageInput(BaseModel):
    title: str = Field(min_length=1,max_length=200)
    body: str = Field(default='',max_length=200000)
    blocks: list[dict] | None = None
    parent_id: str | None = None
    space_id: str | None = None
    version: int | None = Field(default=None,ge=1)
    @field_validator('title')
    @classmethod
    def title_not_blank(cls,v):
        if not v.strip(): raise ValueError(localized_message('theNameCannotBeEmpty'))
        return v.strip()

class NativeDocument(PageInput):
    version: int = Field(ge=1)

class PageMeta(BaseModel):
    parent_id: str | None = None
    icon: str | None = Field(default=None,max_length=100)
    cover: str | None = None
    favorite: bool | None = None
    full_width: bool | None = None
    position: int | None = Field(default=None,ge=0,le=1000000)
    version: int | None = Field(default=None,ge=1)

class PageMove(BaseModel):
    parent_id: str | None = None
    before_id: str | None = None
    version: int = Field(ge=1)


def validate_blocks(blocks,fail):
    if blocks is None:return
    count=0;ids=set()
    def walk(items,depth=0):
        nonlocal count
        if not isinstance(items,list) or depth>30:fail(422,localized_message('invalidBlockNesting'))
        for b in items:
            count+=1
            if count>2000 or not isinstance(b,dict) or b.get('type') not in ALLOWED_BLOCKS:fail(422,localized_message('unknownBlockTypeOrTooManyBlocks'))
            bid=b.get('id')
            if bid is not None:
                if not isinstance(bid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',bid) or bid in ids:fail(422,localized_message('invalidBlockIdentifier'))
                ids.add(bid)
            if 'props' in b and not isinstance(b['props'],dict):fail(422,localized_message('invalidBlockProperties'))
            if b['type']=='paragraph':
                for key,maximum in (('title',500),('description',5000)):
                    if key in b.get('props',{}) and (not isinstance(b['props'][key],str) or len(b['props'][key])>maximum):fail(422,localized_message('invalidBlockProperties'))
            if b['type']=='column':
                width=b.get('props',{}).get('width',1)
                if type(width) not in (int,float) or not 0<width<=100:fail(422,localized_message('invalidBlockProperties'))
            if 'linkDisplay' in b.get('props',{}):
                mode=b['props']['linkDisplay']
                if not isinstance(mode,str) or mode not in ('auto','url','bookmark','mention'):fail(422,localized_message('invalidLinkFormat'))
                if mode=='auto':b['props'].pop('linkDisplay')
            if 'content' in b and not isinstance(b['content'],(str,list,dict,type(None))):fail(422,localized_message('invalidBlockContent'))
            walk(b.get('children',[]),depth+1)
    def urls(obj,depth=0):
        if depth>50:fail(422,localized_message('invalidContentNesting'))
        if isinstance(obj,dict):
            if obj.get('type')=='equation':
                props=obj.get('props',{})
                if not isinstance(props,dict) or set(props)-{'latex'} or not isinstance(props.get('latex'),str) or not 1<=len(props['latex'])<=4096:fail(422,localized_message('latexFormulaFrom1To4096Characters'))
            for k,v in obj.items():
                if k in ('url','href') and v:
                    if not isinstance(v,str):fail(422,localized_message('invalidLink2'))
                    parsed=urlparse(v)
                    if not (parsed.scheme in ('http','https','mailto') or (v.startswith('#') and len(v)<150)):fail(422,localized_message('linksOnlyAllowHttpHttpsAndMailto'))
                urls(v,depth+1)
        elif isinstance(obj,list):
            for v in obj:urls(v,depth+1)
    walk(blocks);urls(blocks)
    encoded=json.dumps(blocks,ensure_ascii=False)
    if len(encoded.encode())>500000:fail(422,localized_message('pageIsTooLarge'))


def create_notes_router(db,ready,now,fail,data_dir=None,publication_update=None,router=None):
    router=router or Router()
    def owned(c,nid,uid,include_deleted=False):
        return note_access(c,nid,uid,fail,include_deleted)
    def serialize(n,c=None,uid=None,session=None,catalog=False):
        from app.notion_sync import presentation
        if c is not None:n=presentation.page(c,n,uid)
        d=dict(n);d.pop('user_id',None);d.pop('trash_batch',None)
        from app.federation.notes import collaborative
        d['federation_shared']=bool(c is not None and not n['space_id'] and collaborative(c,n))
        if c is not None:
            d.update(presentation.assets(c,n,uid))
            if n['space_id']:
                d['owner']=profile(c,n['user_id'],now())
                d['participants']=[profile(c,identity,now()) for identity in sorted(participants(c,n))]
                d['external']=uid not in access_index(c).members.get(n['space_id'],set())
        if catalog:d.pop('body',None);d.pop('blocks',None)
        else:
            d['blocks']=json.loads(d['blocks']) if d['blocks'] is not None else None
            if c is not None:d['blocks']=presentation.blocks(c,d['blocks'],uid)
        if c is not None:
            from app.notion_sync.access import serialized
            sync=serialized(c,n)
            if sync:d['notion_sync']=sync
            from app.note_catalog import page_metadata
            d.update(page_metadata(c,n,uid,d.get('navigation_aliases',[])))
            d['role']=page_role(c,n,uid);d['manage']=can_manage(c,n,uid)
            from app.note_locks import lock_status
            if session:d.update(lock_status(c,n,session,now))
        return d
    def descendants(c,nid,uid):
        owned(c,nid,uid,True)
        return [r['id'] for r in c.execute('WITH RECURSIVE tree(id) AS (SELECT id FROM notes WHERE id=? UNION SELECT n.id FROM notes n JOIN tree t ON n.parent_id=t.id) SELECT id FROM tree',(nid,))]
    def parent_ok(c,pid,nid,uid,scope=None):
        if pid is None:return
        parent=owned(c,pid,uid)
        if parent['space_id']!=scope:fail(422,localized_message('subpagesMustBeInTheSameWorkspace'))
        if nid and pid in descendants(c,nid,uid):fail(422,localized_message('youCannotPlaceAPageInsideItselfOr'))
    def linked_pages_ok(c,blocks,uid,scope=None):
        for b in blocks or []:
            if b.get('type')=='pageLink':
                target=owned(c,b.get('props',{}).get('pageId',''),uid)
                if target['space_id']!=scope:fail(422,localized_message('linkToSubpageMustPointToTheSame'))
            linked_pages_ok(c,b.get('children',[]),uid,scope)
    def snapshot(c,n):
        from app.note_history import snapshot as checkpoint
        checkpoint(c,n,now())
    def version_ok(n,v):
        if v is not None and n['version']!=v:fail(409,localized_message('thePageHasBeenChangedInADifferent'))

    @router.get('/api/notes')
    def list_pages(request:Request,trash:bool=False,catalog:bool=False,catalog_revision:str|None=Query(default=None,max_length=64)):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge_trash(c,now())
            columns='id,user_id,space_id,title,created,updated,parent_id,icon,cover,favorite,locked,full_width,deleted,trash_batch,position,version' if catalog else '*'
            rows=visible_rows(c,s['user_id'],columns,include_deleted=trash)
            from app.notion_sync.access import preload,pending
            preload(c,rows)
            from app.notion_sync.presentation import hidden
            rows=[row for row in rows if not pending(c,row) and not hidden(c,row['id'],s['user_id'])]
            if trash:
                from app.note_trash import ordering
                orders=ordering(c)
                rows.sort(key=lambda n:(-(n['deleted'] or 0),-(orders.get(n['id'],{}).get('order',0) if orders.get(n['id'],{}).get('deleted')==n['deleted'] else 0),n['id']))
            rows=[r for r in rows if bool(r['deleted'])==trash]
            from app.note_catalog import preload as preload_metadata,conditional
            preload_metadata(c,rows,s['user_id'],s,now())
            result=[serialize(r,c,s['user_id'],s,catalog=catalog) for r in rows]
            if not trash:result.sort(key=lambda n:(n['position'],-n['updated']))
            return conditional(result,s,catalog_revision) if catalog and catalog_revision is not None else result

    @router.post('/api/notes')
    def create_page(data:PageInput,request:Request):
        s=ready(request);validate_blocks(data.blocks,fail);nid=secrets.token_urlsafe(12)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');scope=data.space_id
            if data.parent_id:
                parent=owned(c,data.parent_id,s['user_id']);require_role(c,parent,s['user_id'],fail,session=s,now=now)
                if scope is not None and parent['space_id']!=scope:fail(422,localized_message('subpagesMustBeInTheSameWorkspace'))
                scope=parent['space_id']
            if scope and not data.parent_id:space_access(c,scope,s['user_id'],fail)
            parent_ok(c,data.parent_id,None,s['user_id'],scope);linked_pages_ok(c,data.blocks,s['user_id'],scope)
            from app.modules.note_files import validate_file_links
            validate_file_links(c,nid,data.body,data.blocks,s['user_id'],fail)
            position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM notes WHERE parent_id IS ? AND space_id IS ? AND (space_id IS NOT NULL OR user_id=?) AND deleted IS NULL',(data.parent_id,scope,s['user_id'])).fetchone()[0]
            c.execute('INSERT INTO notes(id,user_id,title,body,created,updated,blocks,parent_id,space_id,position) VALUES(?,?,?,?,?,?,?,?,?,?)',(nid,s['user_id'],data.title,data.body,now(),now(),json.dumps(data.blocks) if data.blocks is not None else None,data.parent_id,scope,position))
            if data.parent_id and c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(data.parent_id,)).fetchone():
                c.execute('INSERT INTO database_records(note_id,database_id,properties) VALUES(?,?,?)',(nid,data.parent_id,'{}'))
            return serialize(owned(c,nid,s['user_id']),c,s['user_id'],s)

    @router.get('/api/notes/search')
    def search(request:Request,q:str=Query(default='',max_length=200),space_id:str|None=None):
        from app.modules.note_search import search_pages
        s=ready(request)
        with db() as c:
            index=access_index(c)
            rows=[row for row in visible_rows(c,s['user_id'],'id,title,body,blocks,icon,parent_id,updated') if (space_id is None or (index.pages[row['id']]['space_id'] or 'private')==space_id or (space_id=='personal' and index.personal(index.pages[row['id']],s['user_id'])))]
            from app.notion_sync.access import preload,pending
            preload(c,rows);rows=[row for row in rows if not pending(c,row)]
            from app.notion_sync import presentation
            rows=[presentation.page(c,row,s['user_id']) for row in rows if not presentation.hidden(c,row['id'],s['user_id'])]
        results=search_pages(rows,q)
        return {'items':results[:100],'total':len(results)}

    @router.get('/api/notes/{nid}')
    def get_page(nid:str,request:Request):
        s=ready(request)
        with db() as c:return serialize(owned(c,nid,s['user_id']),c,s['user_id'],s)

    @router.put('/api/notes/{nid}')
    def save_page(nid:str,data:PageInput,request:Request):
        s=ready(request);validate_blocks(data.blocks,fail)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=owned(c,nid,s['user_id']);require_role(c,n,s['user_id'],fail,session=s,now=now);version_ok(n,data.version)
            from app.note_subpages import reconcile_links
            reconcile_links(c,n,data.blocks,s,now(),fail)
            linked_pages_ok(c,data.blocks,s['user_id'],n['space_id'])
            if c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(nid,)).fetchone():fail(422,localized_message('settingsAndDatabaseEntriesAreModifiedThroughThe'))
            from app.federation.notes import collaborative
            if collaborative(c,n):fail(409,localized_message('sharedPagesAreEditedThroughASharedEditor'))
            snapshot(c,n)
            # A Markdown-only edit explicitly invalidates the previous rich projection.
            c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',(data.title,data.body,json.dumps(data.blocks) if data.blocks is not None else None,now(),nid))
            return serialize(owned(c,nid,s['user_id']),c,s['user_id'],s)

    @router.put('/api/notes/{nid}/document')
    async def save_native_document(nid: str, data: NativeDocument, request: Request):
        # Native editors submit a versioned document. Reuse publication edits'
        # CRDT conversion and broadcast so existing web peers keep their epoch.
        s=ready(request);validate_blocks(data.blocks,fail);event=None
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=owned(c,nid,s['user_id'])
            require_role(c,n,s['user_id'],fail,session=s,now=now);version_ok(n,data.version)
            if c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(nid,)).fetchone():
                fail(422,localized_message('settingsAndDatabaseEntriesAreModifiedThroughThe'))
            from app.note_subpages import reconcile_links
            from app.modules.note_files import validate_file_links
            reconcile_links(c,n,data.blocks,s,now(),fail)
            linked_pages_ok(c,data.blocks,s['user_id'],n['space_id'])
            validate_file_links(c,nid,data.body,data.blocks,s['user_id'],fail)
            title,body,blocks=data.title,data.body,data.blocks
            from app.federation.notes import collaborative
            shared_document=collaborative(c,n)
            if shared_document and blocks is None:
                fail(422,localized_message('aBlockDocumentIsRequiredForAShared'))
            if blocks is not None:
                from app.collaboration import load_doc
                from app.share_editing import replace_content
                saved=c.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(nid,)).fetchone() if shared_document else None
                doc=load_doc(saved['state'] if saved else None);before=doc.get_state()
                try:projected_title,body,projected_blocks=replace_content(doc,title,blocks,fail)
                except (ValueError,TypeError,KeyError,AttributeError,OverflowError):fail(422,localized_message('invalidPageContent'))
                if shared_document:
                    title,blocks=projected_title,projected_blocks
                    state=doc.get_update();epoch=saved['epoch'] if saved else secrets.token_urlsafe(16)
                    c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET state=excluded.state,updated=excluded.updated',(nid,state,epoch,now()))
                    event=(nid,state,doc.get_update(before),epoch,n['version']+1,not bool(saved))
            snapshot(c,n)
            c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',(title,body,json.dumps(blocks,ensure_ascii=False) if blocks is not None else None,now(),nid))
            result=serialize(owned(c,nid,s['user_id']),c,s['user_id'],s)
        if event and publication_update:await publication_update(*event)
        return result

    @router.post('/api/notes/{nid}/move')
    def move_page(nid:str,data:PageMove,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=owned(c,nid,s['user_id'])
            from app.notion_sync.access import can_reorder
            from app.notion_sync import presentation
            original=nid;shown=presentation.page(c,n,s['user_id'])
            reorder=owned(c,presentation.storage_id(c,nid,s['user_id']),s['user_id'])
            local_reorder=data.parent_id==reorder['parent_id'] and can_reorder(c,n,s['user_id'])
            if local_reorder:
                from app.note_locks import require_unlocked
                require_unlocked(c,reorder,s,now,fail,content=False)
                version_ok(shown,data.version);n=reorder;nid=n['id']
            else:
                require_role(c,n,s['user_id'],fail,session=s,now=now)
                version_ok(n,data.version)
            parent_ok(c,data.parent_id,nid,s['user_id'],n['space_id'])
            if data.parent_id and not local_reorder:require_role(c,owned(c,data.parent_id,s['user_id']),s['user_id'],fail,session=s,now=now)
            from app.note_access import pin_before_move
            pin_before_move(c,n,data.parent_id)
            siblings=c.execute('SELECT * FROM notes WHERE parent_id IS ? AND space_id IS ? AND (space_id IS NOT NULL OR user_id=?) AND deleted IS NULL AND id<>? ORDER BY position,updated DESC,id',(data.parent_id,n['space_id'],s['user_id'],nid)).fetchall()
            ids=[row['id'] for row in siblings]
            before=presentation.storage_id(c,data.before_id,s['user_id']) if data.before_id else None
            if before is not None and before not in ids:fail(409,localized_message('theOrderOfPagesHasChangedRefreshThe'))
            ids.insert(ids.index(before) if before else len(ids),nid)
            # Normalize tied legacy positions in the same transaction. Ordering
            # siblings does not edit their contents or their access settings.
            for position,identity in enumerate(ids):c.execute('UPDATE notes SET position=? WHERE id=?',(position,identity))
            c.execute('UPDATE notes SET parent_id=?,updated=?,version=version+1 WHERE id=?',(data.parent_id,now(),nid))
            presentation.clear(c)
            return serialize(owned(c,original,s['user_id']),c,s['user_id'],s)

    @router.patch('/api/notes/{nid}')
    def metadata(nid:str,data:PageMeta,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=owned(c,nid,s['user_id'])
            if data.model_fields_set - {'favorite','version'} or 'favorite' not in data.model_fields_set:
                require_role(c,n,s['user_id'],fail,session=s,now=now);version_ok(n,data.version)
            fields={k:v for k,v in data.model_dump().items() if k in data.model_fields_set and k!='version'}
            if 'favorite' in fields:
                if fields['favorite'] is None:fail(422,localized_message('theValueCannotBeEmpty'))
                from app.notion_sync.presentation import page
                shown=page(c,n,s['user_id']);favorite=fields.pop('favorite')
                for target in [shown['id'],*shown.get('navigation_aliases',[])]:
                    if favorite:c.execute('INSERT INTO note_favorites VALUES(?,?,?) ON CONFLICT(note_id,user_id) DO NOTHING',(target,s['user_id'],now()))
                    else:c.execute('DELETE FROM note_favorites WHERE note_id=? AND user_id=?',(target,s['user_id']))
            if 'parent_id' in fields:
                parent_ok(c,fields['parent_id'],nid,s['user_id'],n['space_id'])
                if fields['parent_id']:require_role(c,owned(c,fields['parent_id'],s['user_id']),s['user_id'],fail,session=s,now=now)
                record=c.execute('SELECT database_id FROM database_records WHERE note_id=?',(nid,)).fetchone()
                if record and fields['parent_id']!=record[0]:fail(422,localized_message('entriesRemainInTheirDatabase'))
                if not record and fields['parent_id'] and c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(fields['parent_id'],)).fetchone():fail(422,localized_message('addAnEntryThroughTheDatabase'))
                from app.note_access import pin_before_move
                pin_before_move(c,n,fields['parent_id'])
            from app.modules.note_assets import asset_id,image_reference
            if 'cover' in fields and fields['cover'] not in COVERS and not asset_id(fields['cover']):fail(422,localized_message('unknownCoverImage'))
            for kind in ('icon','cover'):
                if kind in fields:image_reference(c,nid,fields[kind],kind,fail)
            for key in ('icon','favorite','full_width','position'):
                if key in fields and fields[key] is None:fail(422,localized_message('theValueCannotBeEmpty'))
            if fields:
                c.execute('UPDATE notes SET '+','.join(k+'=?' for k in fields)+',updated=?,version=version+1 WHERE id=?',(*fields.values(),now(),nid))
            return serialize(owned(c,nid,s['user_id']),c,s['user_id'],s)

    @router.post('/api/notes/{nid}/duplicate')
    def duplicate(nid:str,request:Request):
        s=ready(request)
        created_images=[]
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE');n=owned(c,nid,s['user_id']);require_role(c,n,s['user_id'],fail,session=s,now=now);nodes=descendants(c,nid,s['user_id']);mapping={old:secrets.token_urlsafe(12) for old in nodes}
                for child in nodes:require_role(c,owned(c,child,s['user_id'],True),s['user_id'],fail,session=s,now=now)
                if n['parent_id']:require_role(c,owned(c,n['parent_id'],s['user_id']),s['user_id'],fail,session=s,now=now)
                def remap_blocks(items):
                    for b in items:
                        b['id']=secrets.token_hex(12)
                        if b.get('type')=='pageLink':
                            pid=b.get('props',{}).get('pageId');b['props']['pageId']=mapping.get(pid,pid)
                        remap_blocks(b.get('children',[]))
                for old in nodes:
                    src=c.execute('SELECT * FROM notes WHERE id=?',(old,)).fetchone()
                    if src['deleted']:continue
                    blocks=json.loads(src['blocks']) if src['blocks'] else None
                    if blocks:remap_blocks(blocks)
                    c.execute('INSERT INTO notes(id,user_id,title,body,created,updated,blocks,parent_id,icon,cover,full_width,space_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(mapping[old],s['user_id'],src['title']+(' (копия)' if old==nid else ''),src['body'],now(),now(),json.dumps(blocks) if blocks is not None else None,mapping.get(src['parent_id'],src['parent_id']),src['icon'],src['cover'],src['full_width'],src['space_id']))
                    from app.note_access import copy_access
                    copy_access(c,src,mapping[old])
                from app.data_crypto import media_store
                store=media_store(db,data_dir) if data_dir is not None else None
                for old in nodes:
                    for decoration in c.execute('SELECT a.*,m.mime,m.size,m.width,m.height,m.frames FROM note_assets a JOIN media m ON m.id=a.id WHERE a.note_id=?',(old,)).fetchall():
                        if not c.execute('SELECT 1 FROM notes WHERE id=?',(mapping[old],)).fetchone():continue
                        identity=secrets.token_hex(16)
                        if store is None:raise RuntimeError('Page image storage is not configured.')
                        store.write(identity,store.read(decoration['id']));created_images.append(identity)
                        c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)',(identity,s['user_id'],decoration['mime'],decoration['size'],decoration['width'],decoration['height'],decoration['frames'],now()))
                        c.execute('INSERT INTO note_assets VALUES(?,?,?,?,?)',(identity,mapping[old],decoration['kind'],decoration['payload'],now()))
                        source=c.execute('SELECT '+decoration['kind']+' FROM notes WHERE id=?',(old,)).fetchone()[0]
                        if source=='image:'+decoration['id']:c.execute('UPDATE notes SET '+decoration['kind']+'=? WHERE id=?',('image:'+identity,mapping[old]))
                for old in nodes:
                    d=c.execute('SELECT * FROM note_databases WHERE note_id=?',(old,)).fetchone()
                    if d and c.execute('SELECT 1 FROM notes WHERE id=?',(mapping[old],)).fetchone():
                        properties=json.loads(d['properties'])
                        for prop in properties:
                            if prop['type']=='relation':
                                target=prop['database_id'];prop['database_id']=mapping.get(target,target)
                                # External inverse relations still describe the original database.
                                if target not in mapping:prop['two_way']=False;prop.pop('inverse_id',None)
                        c.execute('INSERT INTO note_databases(note_id,properties,views) VALUES(?,?,?)',(mapping[old],json.dumps(properties),d['views']))
                for old in nodes:
                    r=c.execute('SELECT * FROM database_records WHERE note_id=?',(old,)).fetchone()
                    if r and c.execute('SELECT 1 FROM notes WHERE id=?',(mapping[old],)).fetchone():
                        db_props=json.loads(c.execute('SELECT properties FROM note_databases WHERE note_id=?',(r['database_id'],)).fetchone()[0]);types={p['id']:p['type'] for p in db_props};values=json.loads(r['properties'])
                        for key,value in values.items():
                            if types.get(key) in ('relation','dependency') and isinstance(value,list):values[key]=[mapping.get(x,x) for x in value]
                        c.execute('INSERT INTO database_records(note_id,database_id,properties) VALUES(?,?,?)',(mapping[old],mapping.get(r['database_id'],r['database_id']),json.dumps(values)))
                return serialize(owned(c,mapping[nid],s['user_id']),c,s['user_id'],s)

        except BaseException:
            if created_images:
                from app.data_crypto import media_store
                store=media_store(db,data_dir)
                for identity in created_images:store.delete(identity)
            raise

    @router.delete('/api/notes/{nid}')
    async def trash_page(nid:str,request:Request):
        s=ready(request);batch=secrets.token_urlsafe(12);events=[]
        with db() as c:
            c.execute('BEGIN IMMEDIATE');owned(c,nid,s['user_id']);ids=descendants(c,nid,s['user_id'])
            for child in ids:require_role(c,owned(c,child,s['user_id'],True),s['user_id'],fail,session=s,now=now)
            from app.note_trash import record
            stamp=now();record(c,[child for child in ids if c.execute('SELECT deleted FROM notes WHERE id=?',(child,)).fetchone()[0] is None],stamp)
            for child in ids:
                c.execute('UPDATE notes SET deleted=?,trash_batch=?,version=version+1 WHERE id=? AND deleted IS NULL',(stamp,batch,child))
                c.execute('UPDATE shares SET revoked=1 WHERE note_id=?',(child,))
            from app.page_links import prune_stored_links
            # Deletion authorizes removal of navigation references to that page;
            # ordinary text links and every unrelated document block are retained.
            events=prune_stored_links(c,set(ids),stamp,fail)
        if publication_update:
            for event in events:await publication_update(*event)
        return {'ok':True}

    @router.post('/api/notes/{nid}/restore')
    def restore(nid:str,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge_trash(c,now());n=owned(c,nid,s['user_id'],True);require_role(c,n,s['user_id'],fail,session=s,now=now)
            if not n['deleted']:fail(409,localized_message('thePageHasAlreadyBeenRestored'))
            batch=n['trash_batch'];ids=descendants(c,nid,s['user_id'])
            for child in ids:require_role(c,owned(c,child,s['user_id'],True),s['user_id'],fail,session=s,now=now)
            from app.note_trash import forget
            forget(c,[child for child in ids if c.execute('SELECT trash_batch FROM notes WHERE id=?',(child,)).fetchone()[0]==batch])
            for child in ids:
                c.execute('UPDATE notes SET deleted=NULL,trash_batch=NULL,version=version+1 WHERE id=? AND trash_batch=?',(child,batch))
            # Restore an independently deleted child at root if its parent remains trashed.
            if n['parent_id']:
                parent=c.execute('SELECT deleted FROM notes WHERE id=?',(n['parent_id'],)).fetchone()
                if not parent or parent['deleted']:
                    if c.execute('SELECT 1 FROM database_records WHERE note_id=?',(nid,)).fetchone():fail(409,localized_message('firstRestoreTheDatabaseForThisEntry'))
                    from app.note_access import pin_before_move
                    pin_before_move(c,n,None)
                    c.execute('UPDATE notes SET parent_id=NULL WHERE id=?',(nid,))
            return serialize(owned(c,nid,s['user_id']),c,s['user_id'],s)

    @router.delete('/api/notes/{nid}/permanent')
    def permanent(nid:str,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge_trash(c,now());n=owned(c,nid,s['user_id'],True);require_role(c,n,s['user_id'],fail,session=s,now=now)
            if not n['deleted']:fail(409,localized_message('firstMoveThePageToTheTrash'))
            children=descendants(c,nid,s['user_id'])
            for child in children:require_role(c,owned(c,child,s['user_id'],True),s['user_id'],fail,session=s,now=now)
            from app.modules.note_assets import purge_page_assets
            purge_page_assets(c,children,now())
            from app.note_access import remove_page_settings
            remove_page_settings(c,children)
            for child in reversed(children):c.execute('DELETE FROM notes WHERE id=?',(child,))
        return {'ok':True}

    @router.get('/api/notes/{nid}/history')
    def history(nid:str,request:Request,before:str|None=Query(default=None,max_length=100),limit:int=Query(default=100,ge=1,le=200),trash:bool=False):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=owned(c,nid,s['user_id'],trash)
            if page_role(c,n,s['user_id'])!='editor':fail(403,localized_message('theHistoryIsAvailableToPageEditors'))
            from app.note_history import purge_history
            purge_history(c,now(),nid)
            params=[nid];cursor=''
            if before:
                row=c.execute('SELECT rowid AS ordinal,created FROM note_revisions WHERE id=? AND note_id=?',(before,nid)).fetchone()
                if not row:fail(409,localized_message('theVersionListHasChangedRefreshTheHistory'))
                cursor=' AND (created<? OR (created=? AND rowid<?))';params.extend((row['created'],row['created'],row['ordinal']))
            params.append(limit)
            return [dict(r) for r in c.execute('SELECT id,title,created FROM note_revisions WHERE note_id=?'+cursor+' ORDER BY created DESC,rowid DESC LIMIT ?',tuple(params))]

    @router.get('/api/notes/{nid}/history/{rid}')
    def revision(nid:str,rid:str,request:Request,trash:bool=False):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=owned(c,nid,s['user_id'],trash)
            if page_role(c,n,s['user_id'])!='editor':fail(403,localized_message('theHistoryIsAvailableToPageEditors'))
            from app.note_history import purge_history
            purge_history(c,now(),nid)
            row=c.execute('SELECT * FROM note_revisions WHERE id=? AND note_id=?',(rid,nid)).fetchone()
            if not row:fail(404,localized_message('versionNotFoundOrOlderThan90Days'))
            result=dict(row);result['blocks']=json.loads(row['blocks']) if row['blocks'] is not None else None
            return result|{'current_version':n['version']}

    @router.post('/api/notes/{nid}/history/{rid}/restore')
    def restore_revision(nid:str,rid:str,request:Request,version:int|None=Query(default=None,ge=1)):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=owned(c,nid,s['user_id']);require_role(c,n,s['user_id'],fail,session=s,now=now)
            from app.note_history import purge_history
            purge_history(c,now(),nid)
            version_ok(n,version)
            r=c.execute('SELECT * FROM note_revisions WHERE id=? AND note_id=?',(rid,nid)).fetchone()
            if not r:fail(404,localized_message('versionNotFound'))
            from app.note_subpages import reconcile_links
            reconcile_links(c,n,json.loads(r['blocks']) if r['blocks'] is not None else None,s,now(),fail)
            c.execute('DELETE FROM collaborative_docs WHERE note_id=?',(nid,))
            snapshot(c,n)
            changed=c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=? AND version=?',(r['title'],r['body'],r['blocks'],now(),nid,n['version']))
            if changed.rowcount!=1:fail(409,localized_message('thePageHasBeenChangedRefreshTheHistory'))
            return serialize(owned(c,nid,s['user_id']),c,s['user_id'],s)

    from app.note_subpages import register_subpages
    register_subpages(router,db,ready,now,fail,owned,serialize,publication_update)
    return router,owned
