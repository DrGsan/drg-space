"""Atomic subpage/shortcut creation and recoverable owned-child deletion."""
from app.i18n import message as localized_message
import copy
import json
import secrets
from typing import Annotated
from pydantic import BaseModel,ConfigDict,Field
from pycrdt import XmlElement,XmlFragment
from app.http import Request
from app.page_links import page_links,prune_stored_links


def reconcile_links(c,page,blocks,session,stamp,fail):
    """Only a rich owned-child shortcut can trash a subtree. Ordinary references
    and Markdown projections never acquire destructive side effects. A matching
    deletion batch makes rich-editor undo recover exactly that operation.
    """
    if blocks is None:return
    from app.notion_sync.access import info
    linked=info(c,page)
    # Source links are references, never local trash commands. Removing one in
    # an experimental editor must not delete a retained synchronized subtree.
    if linked and linked['status']!='detached':return
    from app.modules.social import note_access
    from app.modules.notion import require_role
    previous=page_links(json.loads(page['blocks']) if page['blocks'] else [])
    current=page_links(blocks)
    for identity in previous^current:
        child=c.execute('SELECT * FROM notes WHERE id=? AND parent_id=?',(identity,page['id'])).fetchone()
        if not child or child['space_id']!=page['space_id']:continue
        prefix='page-link:'+page['id']+':'+identity+':'
        removing=identity in previous
        if removing and child['deleted']:continue
        if not removing and (not child['deleted'] or not (child['trash_batch'] or '').startswith(prefix)):continue
        rows=c.execute('WITH RECURSIVE tree(id) AS (SELECT id FROM notes WHERE id=? UNION SELECT n.id FROM notes n JOIN tree t ON n.parent_id=t.id) SELECT n.* FROM notes n JOIN tree t ON t.id=n.id',(identity,)).fetchall()
        affected=[row for row in rows if not row['deleted']] if removing else [row for row in rows if row['trash_batch']==child['trash_batch']]
        for row in affected:
            note_access(c,row['id'],session['user_id'],fail,True)
            require_role(c,row,session['user_id'],fail,session=session,now=lambda:stamp)
        batch=prefix+secrets.token_urlsafe(12) if removing else None
        for row in affected:
            c.execute('UPDATE notes SET deleted=?,trash_batch=?,version=version+1 WHERE id=?',(stamp if removing else None,batch,row['id']))
            if removing:c.execute('UPDATE shares SET revoked=1 WHERE note_id=?',(row['id'],))
        c.note_access_index=None;c.realtime_dirty=True


class SubpageInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(ge=1)
    title:str=Field(default='Без названия',min_length=1,max_length=200)
    body:str=Field(default='',max_length=200000)
    blocks:list[dict]|None=None
    parent_blocks:list[dict]|None=None
    replace_block_id:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    repair_ids:list[Annotated[str,Field(min_length=1,max_length=100)]]|None=Field(default=None,max_length=2000)


def register_subpages(router,db,ready,now,fail,owned,serialize,publication_update):
    from app.modules.notes import validate_blocks
    from app.modules.notion import require_role
    from app.collaboration import load_doc,project,MAX_STATE
    from app.share_editing import replace_content
    from app.note_history import snapshot

    @router.post('/api/notes/{nid}/subpages')
    async def subpage(nid:str,data:SubpageInput,request:Request):
        session=ready(request);event=None;events=[];created=None
        with db() as c:
            c.execute('BEGIN IMMEDIATE');parent=owned(c,nid,session['user_id'])
            require_role(c,parent,session['user_id'],fail,session=session,now=now)
            if parent['version']!=data.version:fail(409,localized_message('pageHasBeenChangedUpdateItBeforeCreating'))
            from app.notion_sync.access import info
            linked=info(c,parent)
            if linked and linked['status']!='detached':
                # Older clients repair local child shortcuts on open. Such
                # navigation must never become an outbound Notion edit.
                if data.repair_ids is not None:return {'parent':serialize(parent,c,session['user_id'],session),'page':None,'added':0}
                fail(422,localized_message('notionUnsupportedWrite'))
            if c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(nid,)).fetchone():fail(422,localized_message('databaseRecordsAreAddedThroughTheNoteBase'))
            blocks=json.loads(parent['blocks']) if parent['blocks'] is not None else copy.deepcopy(data.parent_blocks)
            if blocks is None:fail(409,localized_message('firstOpenThePageInTheBlockEditor'))
            validate_blocks(blocks,fail);validate_blocks(data.blocks,fail)
            # A client conversion is accepted only for the exact legacy version;
            # existing rich blocks and the canonical shared document always win.
            from app.modules.note_files import validate_file_links
            validate_file_links(c,nid,parent['body'],blocks,session['user_id'],fail)
            for identity in page_links(blocks)-page_links(json.loads(parent['blocks'] or '[]')):
                linked=owned(c,identity,session['user_id'])
                if linked['space_id']!=parent['space_id']:fail(422,localized_message('theLinkMustPointToTheSameWorkspace'))
            if data.repair_ids is not None:
                dangling={identity for identity in page_links(blocks) if not c.execute('SELECT 1 FROM notes WHERE id=? AND deleted IS NULL',(identity,)).fetchone()}
                if dangling:
                    events=prune_stored_links(c,dangling,now(),fail,{nid})
                    parent=owned(c,nid,session['user_id']);blocks=json.loads(parent['blocks'])
            stamp=now();links=page_links(blocks);add=[]
            if data.repair_ids is not None:
                for identity in dict.fromkeys(data.repair_ids):
                    row=c.execute('SELECT * FROM notes WHERE id=? AND parent_id=? AND deleted IS NULL',(identity,nid)).fetchone()
                    if row and identity not in links:
                        owned(c,identity,session['user_id']);add.append(row)
            else:
                if not data.title.strip():fail(422,localized_message('enterTheNameOfTheSubpage'))
                identity=secrets.token_urlsafe(12)
                validate_file_links(c,identity,data.body,data.blocks,session['user_id'],fail)
                for linked_id in page_links(data.blocks):
                    linked=owned(c,linked_id,session['user_id'])
                    if linked['space_id']!=parent['space_id']:fail(422,localized_message('theLinkMustPointToTheSameWorkspace'))
                position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM notes WHERE parent_id=? AND deleted IS NULL',(nid,)).fetchone()[0]
                c.execute('INSERT INTO notes(id,user_id,title,body,created,updated,blocks,parent_id,space_id,position) VALUES(?,?,?,?,?,?,?,?,?,?)',(identity,session['user_id'],data.title.strip(),data.body,stamp,stamp,json.dumps(data.blocks) if data.blocks is not None else None,nid,parent['space_id'],position))
                c.note_access_index=None
                created=owned(c,identity,session['user_id']);add=[created]
            if add:
                replacements=[]
                def append_shortcuts(items):
                    for child in add:
                        shortcut={'id':data.replace_block_id or 'subpage_'+child['id'],'type':'pageLink','props':{'pageId':child['id'],'title':child['title']},'children':[]}
                        found=False
                        if data.replace_block_id:
                            def replace(branch):
                                nonlocal found
                                for index,block in enumerate(branch):
                                    if block.get('id')==data.replace_block_id:
                                        shortcut['children']=block.get('children',[]);branch[index]=shortcut;found=True;return
                                    replace(block.get('children',[]))
                            replace(items)
                            if not found:fail(409,localized_message('theBlockHasBeenChangedRecreateTheSubpage'))
                        else:items.append(shortcut)
                        replacements.append(shortcut)
                append_shortcuts(blocks);validate_blocks(blocks,fail)
                doc=load_doc(None);saved=None
                from app.federation.notes import collaborative
                if collaborative(c,parent):
                    saved=c.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(nid,)).fetchone()
                    doc=load_doc(saved['state'] if saved else None);before=doc.get_state()
                    if saved:
                        if project(doc,fail)[2]!=json.loads(parent['blocks'] or '[]'):fail(409,localized_message('theSharedPageHasBeenChangedOpenIt'))
                        root=list(doc.get('prosemirror',type=XmlFragment).children)[0]
                        with doc.transaction():
                            for shortcut in replacements:
                                content=XmlElement('pageLink',shortcut['props'],[])
                                if data.replace_block_id:
                                    def replace_xml(group):
                                        for container in group.children:
                                            children=list(container.children)
                                            if dict(container.attributes).get('id')==data.replace_block_id:
                                                del container.children[0];container.children.insert(0,content);return True
                                            if len(children)>1 and replace_xml(children[1]):return True
                                        return False
                                    if not replace_xml(root):fail(409,localized_message('theBlockHasBeenChangedRecreateTheSubpage'))
                                else:root.children.append(XmlElement('blockContainer',{'id':shortcut['id']},[content]))
                        title,body,blocks=project(doc,fail)
                    else:title,body,blocks=replace_content(doc,parent['title'],blocks,fail)
                    state=doc.get_update()
                    if len(state)>MAX_STATE:fail(413,localized_message('theSharedDocumentIsTooLarge'))
                    epoch=saved['epoch'] if saved else secrets.token_urlsafe(16)
                    c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET state=excluded.state,updated=excluded.updated',(nid,state,epoch,stamp))
                    event=(nid,state,doc.get_update(before),epoch,parent['version']+1,not bool(saved))
                else:
                    # Reuse the rich-to-Markdown projection without changing the
                    # stored private block representation or block identities.
                    title,body,_=replace_content(doc,parent['title'],blocks,fail)
                snapshot(c,parent,stamp)
                c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',(title,body,json.dumps(blocks,ensure_ascii=False),stamp,nid))
            result={'parent':serialize(owned(c,nid,session['user_id']),c,session['user_id'],session),'page':serialize(created,c,session['user_id'],session) if created else None,'added':len(add)}
        if event:events.append(event)
        if publication_update:
            for event in events:await publication_update(*event)
        return result
