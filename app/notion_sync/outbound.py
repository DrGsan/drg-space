"""Experimental, conditional leaf edits; unsupported changes stay local.

The block PATCH API has no documented compare-and-set. It is deliberately not
used for read/modify/write. A unique old_str condition on the page markdown must
still match when Notion applies the update. No full-document replacements,
page/file deletion, blind retries or asynchronous writes are permitted.
"""
import copy
import json
import re
from urllib.parse import quote

from app.notion_sync.client import NotionError,page_id
from app.notion_sync.snapshot import PageSnapshot,read_subtree
from app.notion_sync.state import dump
from app.notion_sync.blocks import convert,file_url


DEFAULTS={'backgroundColor':'default','textColor':'default','textAlignment':'left','isToggleable':False,'checked':False}


def normalized(block):
    result=copy.deepcopy(block)
    props=result.get('props') or {}
    # The shared paragraph schema also serves bookmark/URL cards. Ordinary
    # text paragraphs receive its empty fields from the web editor as well.
    defaults=DEFAULTS|{'title':'','description':'','url':'','linkDisplay':'auto'} if block.get('type')=='paragraph' else DEFAULTS
    result['props']={key:value for key,value in props.items() if key not in defaults or value!=defaults[key]}
    if block.get('type')=='paragraph':
        content=result.get('content') or []
        result['content']=[item for item in content if not (isinstance(item,dict) and item.get('type')=='text' and item.get('text')=='')] if isinstance(content,list) else content
    result['children']=[normalized(child) for child in result.get('children',[])]
    if block.get('type')=='columnList' and result['children']:
        total=sum(child.get('props',{}).get('width',1) for child in result['children'])
        if total>0:
            for child in result['children']:child.setdefault('props',{})['width']=round(child.get('props',{}).get('width',1)/total,8)
    # BlockNote omits inline content on structural nodes; the converter emits
    # an empty array. These are the same document, not a structural edit.
    if block.get('type') in ('columnList','column','pageLink','divider') and not result.get('content'):
        result.pop('content',None)
    return result


def inline(items):
    if isinstance(items,str):items=[{'type':'text','text':items,'styles':{}}]
    output=[]
    for item in items or []:
        kind=item.get('type')
        if kind=='link':
            href=item.get('href','')
            if not href.startswith(('https://','http://','mailto:')):raise NotionError('notionUnsupportedWrite',422)
            output.append('['+inline(item.get('content'))+']('+quote(href,safe=':/?=#%&+@,;!$*-._~')+')');continue
        if kind!='text':raise NotionError('notionUnsupportedWrite',422)
        styles=item.get('styles') or {}
        if set(styles)-{'bold','italic','strike','underline','code'}:raise NotionError('notionUnsupportedWrite',422)
        value=item.get('text','')
        if not isinstance(value,str):raise NotionError('notionUnsupportedWrite',422)
        if styles.get('code'):
            fence='`'*(max([len(x) for x in re.findall(r'`+',value)]+[0])+1)
            value=fence+(' ' if value.startswith('`') else '')+value+(' ' if value.endswith('`') else '')+fence
        else:value=re.sub(r'([\\`*_~\[\]<>])',r'\\\1',value)
        for key,marker in (('bold','**'),('italic','*'),('strike','~~')):
            if styles.get(key):value=marker+value+marker
        if styles.get('underline'):value='<u>'+value+'</u>'
        output.append(value)
    return ''.join(output)


def markdown(block):
    kind=block['type'];props=block.get('props') or {};content=inline(block.get('content'))
    if block.get('children'):raise NotionError('notionUnsupportedWrite',422)
    supported={'heading':{'level'},'checkListItem':{'checked'},'paragraph':set(),'bulletListItem':set(),'numberedListItem':set(),'quote':set()}
    if kind not in supported or set(normalized(block).get('props',{}))-supported[kind]:raise NotionError('notionUnsupportedWrite',422)
    if props.get('textColor','default')!='default' or props.get('backgroundColor','default')!='default' or props.get('textAlignment','left')!='left':raise NotionError('notionUnsupportedWrite',422)
    if kind=='paragraph':
        if normalized(block).get('props'):raise NotionError('notionUnsupportedWrite',422)
        return content or '<p />'
    if not content:raise NotionError('notionUnsupportedWrite',422)
    if kind=='heading' and not props.get('isToggleable'):
        level=props.get('level',1)
        if type(level) is not int or not 1<=level<=3:raise NotionError('notionUnsupportedWrite',422)
        return '#'*level+' '+content
    if kind=='bulletListItem':return '- '+content
    if kind=='numberedListItem':return '1. '+content
    if kind=='checkListItem':return '- ['+('x' if props.get('checked') else ' ')+'] '+content
    if kind=='quote':return '> '+content.replace('\n','\n> ')
    # Files, structure, database values and code/embedded XML need separate
    # reversible protocols; the experiment must not erase them to accept edits.
    raise NotionError('notionUnsupportedWrite',422)


def effective_blocks(before,after,keep_one=False):
    """BlockNote keeps a new trailing empty paragraph as an insertion cursor.

    It is local editor state, not a request to create a remote empty block.
    Preserve actual source empty blocks and every non-empty user paragraph.
    """
    known={item.get('id') for item in before};original={item.get('id'):item for item in before};result=copy.deepcopy(after)
    for item in result:
        old=original.get(item.get('id'))
        if old and item.get('type') in ('columnList','column'):
            item['children']=effective_blocks(old.get('children',[]),item.get('children',[]),item.get('type')=='column')
    if result and (not keep_one or len(result)>1) and result[-1].get('id') not in known:
        item=normalized(result[-1])
        if item.get('type')=='paragraph' and not item.get('props') and not item.get('children') and not inline(item.get('content')):result.pop()
    return result


def column_markdown(block):
    """Serialize editable text layouts, never opaque/shared/media blocks."""
    kind=block['type'];children=block.get('children') or []
    if kind=='columnList':
        if normalized(block).get('props') or len(children)<2 or any(child.get('type')!='column' for child in children):raise NotionError('notionUnsupportedWrite',422)
        widths=[child.get('props',{}).get('width',1) for child in children];total=sum(widths)
        if total<=0:raise NotionError('notionUnsupportedWrite',422)
        columns=[]
        for child,width in zip(children,widths):
            if set(child.get('props',{}))-{'width'} or not child.get('children'):raise NotionError('notionUnsupportedWrite',422)
            body='\n\n'.join(column_markdown(item) for item in child['children'])
            columns.append('<column ratio="'+format(100*width/total,'.10g')+'">\n'+body+'\n</column>')
        return '<columns>\n'+'\n'.join(columns)+'\n</columns>'
    if kind=='column':raise NotionError('notionUnsupportedWrite',422)
    return markdown(block)


def column_plan(before,after):
    """One conditional replacement of the changed text/layout region.

    Unchanged surrounding pages, shared blocks and files are excluded. Reusing
    opaque content in a reconstructed container would risk changing its source
    identity, so such structural changes remain unsupported.
    """
    left=before.get('blocks') or [];right=effective_blocks(left,after.get('blocks') or [])
    start=0
    while start<min(len(left),len(right)) and normalized(left[start])==normalized(right[start]):start+=1
    end_left=len(left);end_right=len(right)
    while end_left>start and end_right>start and normalized(left[end_left-1])==normalized(right[end_right-1]):end_left-=1;end_right-=1
    old=left[start:end_left];new=right[start:end_right]
    # Appending a layout needs an existing text/layout anchor for the condition.
    if not old:
        if start:old=[left[start-1]];new=[right[start-1]]+new
        elif end_left<len(left):old=[left[end_left]];new=new+[right[end_right]]
        else:raise NotionError('notionUnsupportedWrite',422)
    if not any(b.get('type')=='columnList' for b in old+new):raise NotionError('notionUnsupportedWrite',422)
    def count(items):return sum(1+count(b.get('children',[])) for b in items)
    if count(old)>50 or count(new)>200:raise NotionError('notionUnsupportedWrite',422)
    for block in old:column_markdown(block)
    output='\n\n'.join(column_markdown(block) for block in new)
    return [{'block_ids':[b['id'] for b in old],'new_str':output}]


def plan(before,after):
    """Validate the entire change before the first content mutation."""
    for key in ('title','icon','cover','parent_id'):
        if before.get(key)!=after.get(key):raise NotionError('notionUnsupportedWrite',422)
    changes=[]
    def walk(left,right,allow_insert=False):
        if allow_insert:right=effective_blocks(left,right)
        old_ids=[item.get('id') for item in left];new_ids=[item.get('id') for item in right]
        if len(set(old_ids))!=len(old_ids) or len(set(new_ids))!=len(new_ids):raise NotionError('notionUnsupportedWrite',422)
        remaining=set(new_ids);run=[]
        for item in left:
            if item['id'] not in remaining:
                if not allow_insert or item.get('type')!='paragraph' or item.get('children') or normalized(item).get('props'):raise NotionError('notionUnsupportedWrite',422)
                markdown(item);run.append(item['id'])
            elif run:
                changes.append({'block_ids':run,'new_str':'','delete':True});run=[]
        if run:changes.append({'block_ids':run,'new_str':'','delete':True})
        left=[item for item in left if item['id'] in remaining];kept_ids=[item['id'] for item in left]
        if [identity for identity in new_ids if identity in old_ids]!=kept_ids:raise NotionError('notionUnsupportedWrite',422)
        inserted={};position=0;existing=[]
        for item in right:
            if position<len(left) and item.get('id')==left[position].get('id'):existing.append(item);position+=1
            else:
                if not allow_insert or item.get('type')!='paragraph' or item.get('children'):raise NotionError('notionUnsupportedWrite',422)
                # New paragraphs may carry only ordinary supported text props.
                props=normalized(item).get('props',{})
                if props:raise NotionError('notionUnsupportedWrite',422)
                inserted.setdefault(position,[]).append(markdown(item))
        by_id={}
        for old,new in zip(left,existing):
            if old.get('id')!=new.get('id') or old.get('type')!=new.get('type'):raise NotionError('notionUnsupportedWrite',422)
            a=normalized(old);b=normalized(new)
            old_children=a.pop('children');new_children=b.pop('children')
            if a!=b:
                if old_children or new_children:raise NotionError('notionUnsupportedWrite',422)
                # Only content and the todo checkmark may change. Unrecognized
                # editor properties are rejected rather than silently dropped.
                old_props=dict(a.get('props',{}));new_props=dict(b.get('props',{}))
                if old['type']=='checkListItem':old_props.pop('checked',None);new_props.pop('checked',None)
                if old_props!=new_props:raise NotionError('notionUnsupportedWrite',422)
                change={'block_id':old['id'],'new_str':markdown(new)};changes.append(change);by_id[old['id']]=change
            # A column is an ordinary ordered block collection. Insertion and
            # deletion remain restricted to plain paragraphs, never shared
            # blocks, child pages, databases or attachments.
            walk(old_children,new_children,old['type']=='column')
        for position,paragraphs in inserted.items():
            if not left:raise NotionError('notionUnsupportedWrite',422)
            index=position-1 if position else 0;anchor=left[index];target=existing[index]
            if anchor.get('type')!='paragraph' or anchor.get('children'):raise NotionError('notionUnsupportedWrite',422)
            change=by_id.get(anchor['id'])
            if change is None:
                change={'block_id':anchor['id'],'new_str':markdown(target)};changes.append(change);by_id[anchor['id']]=change
            content='\n\n'.join(paragraphs)
            change['new_str']=change['new_str']+'\n\n'+content if position else content+'\n\n'+change['new_str']
    try:walk(before.get('blocks') or [],after.get('blocks') or [],True)
    except NotionError as error:
        if error.code!='notionUnsupportedWrite':raise
        changes=column_plan(before,after)
    if sum(len(change.get('block_ids',[])) or 1 for change in changes)>50:raise NotionError('notionUnsupportedWrite',422)
    return changes


def current_document(note):
    return {key:json.loads(note[key]) if key=='blocks' and note[key] else note[key] for key in ('title','blocks','icon','cover','parent_id')}


def source_document(c,root,note,document):
    """Exclude only proven server-generated navigation to retained children.

    Older clients persisted these shortcuts when opening a linked copy. They
    aren't new source content. Keep them locally; ordinary page links, foreign
    children, labels with extra content and structural edits remain protected.
    """
    candidates=[block for block in document.get('blocks') or [] if block.get('type')=='pageLink' and block.get('id')=='subpage_'+str(block.get('props',{}).get('pageId','')) and not block.get('children') and not block.get('content') and not set(block.get('props',{}))-{'pageId','title'}]
    if not candidates:return document
    children={row['id'] for row in c.execute('SELECT n.id FROM notes n JOIN notion_pages p ON p.note_id=n.id WHERE p.root_id=? AND n.parent_id=? AND n.user_id=? AND n.deleted IS NULL AND n.space_id IS NOT DISTINCT FROM ?',(root['id'],note['id'],root['user_id'],note['space_id']))}
    omitted={block['id'] for block in candidates if block['props']['pageId'] in children}
    if not omitted:return document
    return {**document,'blocks':[block for block in document['blocks'] if block.get('id') not in omitted]}


def verified_document(service,payload,fresh,target,mapping,nid):
    """Accept either real page shape: with or without the editor cursor.

    Both editors can materialize a final empty paragraph. Compare the complete
    submitted document as well as its single-cursor-free form, then retain the
    actual remote shape/mapping. Never trim arbitrary source empty blocks.
    """
    from app.notion_sync.assets import identity
    source=PageSnapshot(**payload['source'])
    def icon(raw):
        value=raw.get('icon') or {}
        return value.get('emoji') if value.get('type')=='emoji' else identity(value)
    if fresh.title!=target.get('title') or fresh.parent!=source.parent or icon(fresh.raw)!=icon(source.raw) or identity(fresh.raw.get('cover'))!=identity(source.raw.get('cover')):return None
    resolve=lambda value:service.public_url+'/page/'+mapping[page_id(value)] if page_id(value) in mapping else None
    converted,_,_=convert(fresh.blocks,resolve,source_id=fresh.id);links={}
    def media(items):
        for block in items:
            kind=block.get('type');saved=payload.get('assets',{}).get('block:'+block.get('id',''))
            if saved and kind in ('image','video','audio','file','pdf'):links[file_url(block[kind])]=service.public_url+'/api/notes/'+nid+'/files/'+saved['id']
            media(block.get('children',[]))
    media(fresh.blocks)
    from app.notion_images import replace_images
    converted=replace_images(converted,links)
    def values(items):return [{key:values(value) if key=='children' else value for key,value in normalized(block).items() if key!='id'} for block in items]
    effective=copy.deepcopy(target);effective['blocks']=effective_blocks((payload.get('baseline') or {}).get('blocks') or [],target.get('blocks') or [])
    for candidate in (effective,target):
        if values(converted)==values(candidate.get('blocks') or []):return candidate,converted
    return None


def block_mapping(local,remote):
    result={}
    def walk(left,right):
        for a,b in zip(left,right):result[a['id']]=b['id'];walk(a.get('children',[]),b.get('children',[]))
    walk(local,remote);return result


def recover_completed(service,uid):
    """Acknowledge a proven successful journal, never choose divergent versions.

    No network write or re-send occurs. The already-read post-write snapshot
    must match the unchanged submitted intent, with every operation confirmed.
    Later source edits remain ordinary incoming updates; the next actual write
    still performs its fresh authorization and source-version checks.
    """
    if not service.config.write_configured:return 0
    from app.notion_sync.service import local_hash,remote_hash
    recovered=0;mappings={};rescans={};stamp=int(service.clock())
    with service.db() as c:
        c.execute('BEGIN IMMEDIATE')
        rows=c.execute("SELECT p.id AS link_id,p.root_id,p.notion_id,p.payload,r.connection_id,r.payload AS root_payload,n.* FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id JOIN users u ON u.id=r.user_id JOIN notes n ON n.id=p.note_id JOIN notion_connections read_grant ON read_grant.id=r.connection_id WHERE r.user_id=? AND u.role='owner' AND r.status='conflict' AND r.mode='duplex' AND p.status='conflict' AND read_grant.status='active' AND n.deleted IS NULL AND n.user_id=r.user_id AND p.user_id=r.user_id LIMIT 100",(uid,)).fetchall()
        for note in rows:
            payload=json.loads(note['payload']);journal=payload.get('outbound') or {};remote=(payload.get('conflict') or {}).get('remote');root_payload=json.loads(note['root_payload'])
            if journal.get('state')!='prepared' or not journal.get('operations') or journal.get('index')!=len(journal['operations']) or not remote or local_hash(note)!=journal.get('local_hash'):continue
            if note['root_id'] not in mappings:mappings[note['root_id']]={r['notion_id']:r['note_id'] for r in c.execute('SELECT notion_id,note_id FROM notion_pages WHERE root_id=?',(note['root_id'],))}
            fresh=PageSnapshot(**remote);matched=verified_document(service,payload,fresh,journal['target'],mappings[note['root_id']],note['id'])
            if not matched:continue
            target,converted=matched;since=max(0,(payload.get('conflict') or {}).get('created',stamp)-60);rescans[note['connection_id']]=min(rescans.get(note['connection_id'],since),since);payload.pop('outbound',None);payload.pop('conflict',None)
            payload.update(source=fresh.value(),remote_hash=remote_hash(fresh),local_hash=local_hash(note),baseline=target,block_map=block_mapping(target.get('blocks') or [],converted))
            c.execute("UPDATE notion_pages SET status='active',payload=?,remote_stamp=?,note_version=?,updated=? WHERE id=?",(dump(payload),fresh.raw.get('last_edited_time',''),note['version'],stamp,note['link_id']))
            c.execute("DELETE FROM notion_jobs WHERE root_id=? AND entity_id=? AND direction='push' AND error='notionConflictUnresolved' AND lease_owner=''",(note['root_id'],note['notion_id']))
            remaining=c.execute("SELECT 1 FROM notion_pages WHERE root_id=? AND status='conflict' LIMIT 1",(note['root_id'],)).fetchone()
            if not remaining:
                root_payload.pop('has_conflict',None);root_payload.pop('error',None)
                c.execute("UPDATE notion_roots SET status='active',payload=?,updated=?,version=version+1 WHERE id=? AND status='conflict' AND mode='duplex'",(dump(root_payload),stamp,note['root_id']))
            recovered+=1
        for connection,since in rescans.items():
            row=c.execute('SELECT payload FROM notion_connections WHERE id=?',(connection,)).fetchone();value=json.loads(row[0])
            value['watermark']=min(value.get('watermark',since),since);value.pop('scan',None)
            c.execute('UPDATE notion_connections SET payload=?,next_scan=0 WHERE id=?',(dump(value),connection))
    return recovered


def guard(service,root_id,connection_id):
    if not service.config.write_configured:raise NotionError('notionWriteForbidden',403)
    with service.db() as c:
        row=c.execute("SELECT 1 FROM notion_roots r JOIN users u ON u.id=r.user_id JOIN notion_connections n ON n.id=r.connection_id AND n.user_id=r.user_id WHERE r.id=? AND r.connection_id=? AND r.mode='duplex' AND r.status='active' AND u.role='owner' AND n.status='active' AND n.kind='read'",(root_id,connection_id)).fetchone()
        if not row:raise NotionError('notionWriteForbidden',403)


def prepare_operations(read,payload,changes,document,cache):
    operations=[]
    for change in changes:
        fragments=[]
        for identity in change.get('block_ids') or [change['block_id']]:
            try:remote_id=page_id(payload.get('block_map',{}).get(identity,identity))
            except ValueError:raise NotionError('notionUnsupportedWrite',422) from None
            if remote_id not in cache:cache[remote_id]=read.request('GET','/v1/pages/'+remote_id+'/markdown?markdown_version=v2')
            block=cache[remote_id]
            fragment=block.get('markdown','').strip('\n')
            if not fragment or block.get('truncated') or block.get('unknown_block_ids'):raise NotionError('notionUnsupportedWrite',422)
            fragments.append(fragment)
        old='\n\n'.join(fragments)
        if document.count(old)!=1:raise NotionError('notionUnsupportedWrite',422)
        if change.get('delete'):
            # Consume one separator with a whole consecutive paragraph run.
            # This keeps neighbours separate without leaving phantom empty
            # paragraphs or invalidating the next deletion in the same edit.
            start=document.index(old);end=start+len(old)
            if (start and not document[:start].endswith('\n\n')) or (end<len(document) and not document[end:].startswith('\n\n')):raise NotionError('notionUnsupportedWrite',422)
            if start:old='\n\n'+old
            elif end<len(document):old+='\n\n'
        operations.append({'old_str':old,'new_str':change['new_str']})
    return operations


def compiled_operations(read,payload,target,changes=None):
    """One preflight for normal writes and explicit recovery previews."""
    if changes is None:changes=plan(payload.get('baseline') or {},target)
    if not changes:return []
    source=PageSnapshot(**payload['source'])
    page_md=read.request('GET','/v1/pages/'+source.id+'/markdown?markdown_version=v2')
    if page_md.get('truncated') or page_md.get('unknown_block_ids') or not isinstance(page_md.get('markdown'),str):raise NotionError('notionUnsupportedWrite',422)
    cache={}
    try:operations=prepare_operations(read,payload,changes,page_md['markdown'],cache)
    except NotionError as error:
        if error.code!='notionUnsupportedWrite':raise
        operations=prepare_operations(read,payload,column_plan(payload['baseline'],target),page_md['markdown'],cache)
    for index,operation in enumerate(operations):
        for other_index,other in enumerate(operations):
            if index!=other_index and (operation['old_str'] in other['old_str'] or operation['old_str'] in other['new_str']):raise NotionError('notionUnsupportedWrite',422)
    return operations

def push(service,root,job):
    from app.notion_sync.service import local_hash,remote_hash
    with service.db() as c:
        link=c.execute('SELECT * FROM notion_pages WHERE root_id=? AND notion_id=?',(root['id'],job['entity_id'])).fetchone()
        if not link:raise NotionError('notionLinkUnavailable',409)
        link=dict(link);payload=json.loads(link['payload']);note=c.execute('SELECT * FROM notes WHERE id=?',(link['note_id'],)).fetchone()
        if not note or note['deleted'] is not None:raise NotionError('notionLinkUnavailable',409)
        target=source_document(c,root,note,current_document(note));version=note['version'];desired_hash=local_hash(note)
        payload['baseline']=source_document(c,root,note,payload.get('baseline') or {})
        mapping={row['notion_id']:row['note_id'] for row in c.execute('SELECT notion_id,note_id FROM notion_pages WHERE root_id=?',(root['id'],)).fetchall()}
    if desired_hash==payload.get('local_hash'):return
    changes=plan(payload.get('baseline') or {},target)
    effective=copy.deepcopy(target);effective['blocks']=effective_blocks((payload.get('baseline') or {}).get('blocks') or [],target.get('blocks') or [])
    connection=root['connection_id']
    guard(service,root['id'],connection)
    if not changes:
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE');current=c.execute('SELECT version FROM notes WHERE id=?',(link['note_id'],)).fetchone()
            if not current or current['version']!=version:raise NotionError('notionLocalChanged',409,2)
            payload.update(local_hash=desired_hash,baseline=effective)
            c.execute('UPDATE notion_pages SET payload=?,note_version=?,updated=? WHERE id=?',(dump(payload),version,int(service.clock()),link['id']))
        return
    read=service.connections.client(root['connection_id'],request_check=lambda:service.check_job(root,job));write=None
    try:
        source=PageSnapshot(**payload['source'])
        raw=None;parent=source.parent
        if source.id!=root['notion_id']:
            from app.notion_sync.hierarchy import selected_parent,metadata
            raw=metadata(read,'page',source.id)
            parent=selected_parent(read,raw,set(mapping),root['notion_id'])
            if parent is None:raise NotionError('notionSourceUnavailable',404)
        fresh=next(iter(read_subtree(read,source.id,known_ids=set(mapping),parent_id=parent,root_raw=raw).values()))
        if payload.get('outbound',{}).get('state')=='sending':
            service.conflict(root,link,fresh);raise NotionError('notionWriteUncertain',409)
        if remote_hash(fresh)!=payload['remote_hash']:
            service.conflict(root,link,fresh);return
        operations=compiled_operations(read,payload,target,changes)
        journal={'state':'prepared','target':target,'version':version,'local_hash':desired_hash,'operations':operations}
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM notes WHERE id=?',(link['note_id'],)).fetchone()
            if not row or row['version']!=version:raise NotionError('notionLocalChanged',409,2)
            payload['outbound']=journal;c.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(payload),link['id']))
        write=service.connections.client(connection,mutation_check=lambda:guard(service,root['id'],connection),request_check=lambda:service.check_job(root,job))
        for index,operation in enumerate(operations):
            guard(service,root['id'],connection)
            with service.db() as c:
                c.execute('BEGIN IMMEDIATE');journal.update(state='sending',index=index);payload['outbound']=journal
                c.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(payload),link['id']))
            try:response=write.request('PATCH','/v1/pages/'+source.id+'/markdown',{'type':'update_content','markdown_version':'v2','allow_async':False,'update_content':{'content_updates':[operation]}})
            except NotionError as error:
                if error.status==429 or error.code in ('notionWriteForbidden','notionConnectionPaused'):
                    journal['state']='prepared';payload['outbound']=journal
                    with service.db() as c:c.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(payload),link['id']))
                    raise
                if error.code=='notionAccessDenied' and index==0:
                    journal['state']='prepared';payload['outbound']=journal
                    with service.db() as c:c.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(payload),link['id']))
                    raise NotionError('notionWriteAccessRequired',403) from None
                if error.code=='notionAuthorizationExpired':service.connections.pause(connection,'reauthorize')
                service.conflict(root,link,fresh)
                raise NotionError('notionWriteUncertain',409) from None
            if response.get('warnings') or response.get('truncated') or response.get('unknown_block_ids'):
                service.conflict(root,link,fresh);raise NotionError('notionConflictUnresolved',409)
            journal.update(state='prepared',index=index+1);payload['outbound']=journal
            with service.db() as c:c.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(payload),link['id']))
        fresh=next(iter(read_subtree(read,source.id,known_ids=set(mapping),parent_id=source.parent).values()))
        # The returned document must be verified against the submitted local
        # intent before declaring it synchronized. Canonical block IDs can change
        # after a markdown replacement, so compare values, never accept only 200.
        matched=verified_document(service,payload,fresh,target,mapping,link['note_id'])
        if not matched:
            service.conflict(root,link,fresh);raise NotionError('notionConflictUnresolved',409)
        effective,converted=matched
        changed=False
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE');current=c.execute('SELECT * FROM notes WHERE id=?',(link['note_id'],)).fetchone()
            if not current or current['version']!=version:changed=True
            else:
                block_map=block_mapping(effective.get('blocks') or [],converted)
                payload.pop('outbound',None)
                payload.update(source=fresh.value(),remote_hash=remote_hash(fresh),local_hash=desired_hash,baseline=effective,block_map=block_map)
                c.execute('UPDATE notion_pages SET payload=?,remote_stamp=?,note_version=?,updated=? WHERE id=?',(dump(payload),fresh.raw.get('last_edited_time',''),version,int(service.clock()),link['id']))
        if changed:service.conflict(root,link,fresh);return
    finally:
        read.close()
        if write:write.close()
