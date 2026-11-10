"""Column edits through the real encrypted store, queue, ACL and conditional API."""
import copy
import json
import uuid
import xml.etree.ElementTree as ET
import httpx
import pytest
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,imported,ROOT,CHILD,paragraph,rt
from tests.test_notion_sync_outbound import added
from app import main as m
from app.notion_sync.state import dump,enqueue
from app.notion_sync.outbound import plan,normalized,column_markdown
from app.notion_sync.client import NotionError


def columns_fixture():
    c,s,provider,service,stamp=initialized();original=provider.handle;patches=[];counter=0
    def identity():return str(uuid.uuid4())
    def text(value):
        block=paragraph(value);block['id']=identity();block['paragraph']['rich_text'][0]['annotations']={};return block
    def column(values,ratio=.5):
        bid=identity();provider.blocks[bid]=[text(value) for value in values]
        return {'id':bid,'type':'column','column':{'width_ratio':ratio},'has_children':True}
    group=identity();provider.blocks[group]=[column(['Left']),column(['Right'])]
    protected={'id':CHILD,'type':'child_page','child_page':{'title':'Child'},'has_children':True}
    provider.blocks[ROOT]=[{'id':group,'type':'column_list','column_list':{},'has_children':True},protected]
    def md(block):
        kind=block['type']
        if kind=='child_page':return ''
        if kind=='column_list':return '<columns>\n'+'\n'.join(md(b) for b in provider.blocks[block['id']])+'\n</columns>'
        if kind=='column':return '<column ratio="'+str(100*block['column']['width_ratio'])+'">\n'+'\n\n'.join(md(b) for b in provider.blocks[block['id']])+'\n</column>'
        value=''.join(item['text']['content'] for item in block['paragraph']['rich_text'])
        return value or '<p />'
    def find(bid):
        return next((b for values in provider.blocks.values() for b in values if b['id']==bid),None)
    def parse(document):
        # This fixture emulates provider-assigned IDs and preserves child pages,
        # which aren't included in GET page markdown. It never uses our planner.
        def body(element):
            result=[]
            def paragraphs(value):
                if value and value.strip():result.extend(text(part) for part in value.strip('\n').split('\n\n'))
            paragraphs(element.text)
            for node in element:
                if node.tag=='columns':
                    bid=identity();cols=[]
                    for item in node:
                        cid=identity();provider.blocks[cid]=body(item);cols.append({'id':cid,'type':'column','column':{'width_ratio':float(item.attrib.get('ratio','50'))/100},'has_children':True})
                    provider.blocks[bid]=cols;result.append({'id':bid,'type':'column_list','column_list':{},'has_children':True})
                elif node.tag=='p':result.append(text(''))
                else:raise AssertionError(node.tag)
                paragraphs(node.tail)
            return result
        return body(ET.fromstring('<doc>'+document+'</doc>'))
    def respond(request):
        nonlocal counter
        if request.url.path.endswith('/markdown'):
            provider.requests.append((request.method,request.url.path));bid=request.url.path.split('/')[3]
            if request.method=='PATCH':
                value=json.loads(request.content);patches.append(value);op=value['update_content']['content_updates'][0]
                document='\n\n'.join(filter(None,(md(b) for b in provider.blocks[ROOT])))
                if document.count(op['old_str'])!=1:return httpx.Response(400,json={'code':'validation_error'})
                protected_before=copy.deepcopy([b for b in provider.blocks[ROOT] if b['type']=='child_page'])
                updated=document.replace(op['old_str'],op['new_str'],1);provider.blocks[ROOT]=parse(updated)+protected_before;counter+=1
            values=provider.blocks[ROOT] if bid==ROOT else [find(bid)]
            return httpx.Response(200,json={'markdown':'\n\n'.join(filter(None,(md(b) for b in values))),'truncated':False,'unknown_block_ids':[]})
        return original(request)
    service.connections.transport=httpx.MockTransport(respond)
    root,mapping=imported(service,s);state=service.status(s['user_id'])['roots'][0]
    service.experimental(s['user_id'],root,state['version'],True)
    return c,s,provider,service,stamp,root,mapping,patches


def save(c,nid,mutate):
    page=c.get('/api/notes/'+nid).json();blocks=copy.deepcopy(page['blocks']);mutate(blocks)
    result=c.put('/api/notes/'+nid,json={'title':page['title'],'body':'local text','blocks':blocks,'version':page['version']});assert result.status_code==200,result.text
    return result.json()


def settle(service,stamp):
    stamp[0]+=10
    for _ in range(30):
        if not service.work_once():break
    return service


def test_column_container_editor_normalization_never_stops_sync_or_sends_a_write():
    c,s,p,service,stamp,root,mapping,patches=columns_fixture()
    def mutate(blocks):
        def walk(items):
            for item in items:
                if item['type'] in ('columnList','column','pageLink'):item.pop('content',None)
                walk(item.get('children',[]))
        walk(blocks);blocks.append(added('editor-tail',''))
    saved=save(c,mapping[ROOT],mutate);count=len(p.requests);settle(service,stamp)
    assert service.status(s['user_id'])['roots'][0]['status']=='active' and not patches and len(p.requests)==count
    assert c.get('/api/notes/'+saved['id']).json()['version']==saved['version']


def test_new_paragraph_inside_column_is_sent_and_can_be_changed_then_deleted():
    c,s,p,service,stamp,root,mapping,patches=columns_fixture()
    save(c,mapping[ROOT],lambda blocks:blocks[0]['children'][0]['children'].extend([added('new','Inside column'),added('cursor','')]))
    settle(service,stamp)
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    assert patches[0]['update_content']['content_updates']==[{'old_str':'Left','new_str':'Left\n\nInside column'}]
    def change(blocks):blocks[0]['children'][0]['children'][1]['content'][0]['text']='Updated inside'
    save(c,mapping[ROOT],change);settle(service,stamp)
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    save(c,mapping[ROOT],lambda blocks:blocks[0]['children'][0]['children'].pop(1));settle(service,stamp)
    assert len(patches)==3 and service.status(s['user_id'])['roots'][0]['status']=='active'
    assert p.blocks[ROOT][-1]['id']==CHILD


@pytest.mark.parametrize('operation',['resize','add','reorder','unwrap'])
def test_text_column_structure_roundtrip_keeps_external_child_page(operation):
    c,s,p,service,stamp,root,mapping,patches=columns_fixture()
    def mutate(blocks):
        columns=blocks[0]['children']
        if operation=='resize':columns[0]['props']['width']=.7;columns[1]['props']['width']=.3
        elif operation=='add':columns.append({'id':'local-column','type':'column','props':{'width':.5},'children':[added('new-text','Third')]})
        elif operation=='reorder':columns.reverse()
        elif operation=='unwrap':blocks[0:1]=[item for col in columns for item in col['children']]
    saved=save(c,mapping[ROOT],mutate);settle(service,stamp)
    state=service.status(s['user_id'])['roots'][0]
    assert state['status']=='active',state
    assert len(patches)==1 and p.blocks[ROOT][-1]['id']==CHILD
    assert c.get('/api/notes/'+saved['id']).json()['version']==saved['version']
    # The regenerated provider IDs must map to the unchanged local document.
    def change(blocks):
        first=blocks[0] if operation=='unwrap' else blocks[0]['children'][0]['children'][0]
        first['content'][0]['text']='After structure change'
    save(c,mapping[ROOT],change);settle(service,stamp)
    assert len(patches)==2 and service.status(s['user_id'])['roots'][0]['status']=='active'


def test_structural_fallback_refuses_to_recreate_attachments_or_shared_blocks():
    doc={'title':'x','blocks':[{'id':'layout','type':'columnList','children':[{'id':'a','type':'column','props':{'width':1},'children':[{'id':'media','type':'image','props':{'url':'/private'},'children':[]}]},{'id':'b','type':'column','props':{'width':1},'children':[added('p','text')]}]}]}
    target=copy.deepcopy(doc);target['blocks'][0]['children'].reverse()
    with pytest.raises(NotionError,match='notionUnsupportedWrite'):plan(doc,target)
    assert normalized(doc['blocks'][0])==normalized({**doc['blocks'][0],'children':[{**col,'props':{'width':.5}} for col in doc['blocks'][0]['children']]})


@pytest.mark.parametrize('pending_import',[False,True])
def test_pause_resume_preserves_checkbox_and_queued_intent_even_with_partial_import(pending_import):
    c,s,p,service,stamp,root,mapping,patches=columns_fixture()
    save(c,mapping[ROOT],lambda blocks:blocks[0]['children'][0]['children'][0]['content'][0].update(text='Queued'))
    if pending_import:
        with m.db() as db:db.execute("UPDATE notion_pages SET status='importing' WHERE root_id=? AND notion_id=?",(root,CHILD))
    state=service.status(s['user_id'])['roots'][0];paused=service.action(s['user_id'],root,'pause',state['version'])
    assert paused['mode']=='duplex';settle(service,stamp);assert not patches
    resumed=service.action(s['user_id'],root,'resume',paused['version']);assert resumed['mode']=='duplex'
    settle(service,stamp);assert len(patches)==1


def test_read_only_stopped_link_needs_explicit_enable_before_retry():
    c,s,p,service,stamp,root,mapping,patches=columns_fixture()
    saved=save(c,mapping[ROOT],lambda blocks:blocks[0]['children'][0]['children'][0]['content'][0].update(text='Keep this edit'))
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_roots WHERE id=?',(root,)).fetchone()[0]);value['error']='notionUnsupportedWrite'
        db.execute("UPDATE notion_roots SET mode='pull',status='paused',payload=? WHERE id=?",(dump(value),root))
    state=service.status(s['user_id'])['roots'][0];settle(service,stamp);assert not patches
    restored=service.experimental(s['user_id'],root,state['version'],True)
    assert restored['mode']=='duplex' and restored['status']=='active'
    assert c.get('/api/notes/'+saved['id']).json()['version']==saved['version']
    settle(service,stamp);assert len(patches)==1


def test_old_client_child_navigation_stays_local_and_does_not_block_real_column_edits():
    c,s,p,service,stamp,root,mapping,patches=columns_fixture()
    # Source removed/moved a child. Its already published local copy is retained.
    p.blocks[ROOT]=[b for b in p.blocks[ROOT] if b['type']!='child_page']
    with m.db() as db:enqueue(db,root,ROOT,'pull',int(stamp[0]))
    settle(service,stamp);page=c.get('/api/notes/'+mapping[ROOT]).json();assert len(page['blocks'])==1
    count=len(p.requests)
    repaired=c.post('/api/notes/'+page['id']+'/subpages',json={'version':page['version'],'repair_ids':[mapping[CHILD]]})
    assert repaired.status_code==200 and repaired.json()['added']==0 and repaired.json()['parent']['version']==page['version']
    assert c.post('/api/notes/'+page['id']+'/subpages',json={'version':page['version'],'title':'Not supported'}).status_code==422
    settle(service,stamp);assert len(p.requests)==count and not patches
    shortcut={'id':'subpage_'+mapping[CHILD],'type':'pageLink','props':{'pageId':mapping[CHILD],'title':'Child'},'children':[]}
    saved=save(c,page['id'],lambda blocks:blocks.append(shortcut));settle(service,stamp)
    assert not patches and service.status(s['user_id'])['roots'][0]['status']=='active'
    assert c.get('/api/notes/'+mapping[CHILD]).status_code==200
    save(c,page['id'],lambda blocks:blocks[0]['children'][0]['children'][0]['content'][0].update(text='Real edit'))
    settle(service,stamp);assert len(patches)==1 and service.status(s['user_id'])['roots'][0]['status']=='active'
    assert all(b['type']!='child_page' for b in p.blocks[ROOT])
    # Removing the local shortcut is not a command to trash its retained child.
    save(c,page['id'],lambda blocks:blocks.pop());settle(service,stamp)
    assert c.get('/api/notes/'+mapping[CHILD]).status_code==200
    with m.db() as db:assert db.execute('SELECT deleted FROM notes WHERE id=?',(mapping[CHILD],)).fetchone()[0] is None


def test_foreign_or_unlinked_navigation_is_never_silently_excluded_from_writes():
    c,s,p,service,stamp,root,mapping,patches=columns_fixture()
    foreign=c.post('/api/notes',json={'title':'Independent'}).json()['id']
    shortcut={'id':'subpage_'+foreign,'type':'pageLink','props':{'pageId':foreign,'title':'Independent'},'children':[]}
    save(c,mapping[ROOT],lambda blocks:blocks.append(shortcut));count=len(p.requests);settle(service,stamp)
    assert not patches and len(p.requests)==count and service.status(s['user_id'])['roots'][0]['error']=='notionUnsupportedWrite'


@pytest.mark.parametrize('position',['before','after'])
def test_plain_root_paragraphs_next_to_layout_use_the_layout_as_conditional_anchor(position):
    c,s,p,service,stamp,root,mapping,patches=columns_fixture()
    p.blocks[ROOT]=[b for b in p.blocks[ROOT] if b['type']!='child_page']
    with m.db() as db:enqueue(db,root,ROOT,'pull',int(stamp[0]))
    settle(service,stamp)
    def mutate(blocks):
        items=[added('one','First new paragraph'),added('two','Second new paragraph')]
        if position=='before':blocks[0:0]=items
        else:blocks.extend(items)
    saved=save(c,mapping[ROOT],mutate);settle(service,stamp)
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    assert len(patches)==1 and '<columns>' in patches[0]['update_content']['content_updates'][0]['old_str']
    assert c.get('/api/notes/'+saved['id']).json()['version']==saved['version']
