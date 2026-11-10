"""Owned subpage shortcuts, legacy repair, rich undo and access boundaries."""
import base64,json
from tests.test_security import owner,member,client,reset,share
from tests.test_social import family,join,initialize,hello,next_event
from app import main as m
from app.collaboration import load_doc,project


def create(c,title='Parent',parent=None,scope=None,blocks=None):
    r=c.post('/api/notes',json={'title':title,'body':'Original text','parent_id':parent,'space_id':scope,'blocks':blocks});assert r.status_code==200,r.text;return r.json()
def subpage(c,parent,**extra):
    return c.post('/api/notes/'+parent['id']+'/subpages',json={'version':parent['version'],**extra})
def save(c,page,blocks):
    return c.put('/api/notes/'+page['id'],json={'title':page['title'],'body':'Projected','blocks':blocks,'version':page['version']})

def test_atomic_creation_preserves_rich_parent_and_duplicate_stale_request_creates_nothing():
    c,_,_=owner();p=create(c,blocks=[{'id':'keep','type':'paragraph','content':'Preserved text','children':[]}])
    response=subpage(c,p);assert response.status_code==200,response.text
    result=response.json();child=result['page'];parent=result['parent'];assert child['parent_id']==p['id']
    assert parent['blocks'][0]==p['blocks'][0]
    assert parent['blocks'][1]['props']['pageId']==child['id'] and parent['version']==p['version']+1
    assert subpage(c,p).status_code==409
    assert len(c.get('/api/notes').json())==2
    assert client().post('/api/notes/'+p['id']+'/subpages',json={'version':parent['version']}).status_code==401
    assert subpage(c,parent,replace_block_id='missing').status_code==409
    assert len(c.get('/api/notes').json())==2


def test_legacy_orphans_are_repaired_without_duplicate_links_or_replacing_markdown():
    c,_,_=owner();p=create(c);first=create(c,'Child one',p['id']);second=create(c,'Child two',p['id'])
    conversion=[{'id':'text','type':'paragraph','content':'Original text','children':[]}]
    response=subpage(c,p,repair_ids=[first['id'],second['id']],parent_blocks=conversion);assert response.status_code==200,response.text
    repaired=response.json()['parent'];assert repaired['blocks'][0]==conversion[0]
    assert {b['props']['pageId'] for b in repaired['blocks'][1:]}=={first['id'],second['id']}
    response=subpage(c,repaired,repair_ids=[first['id'],second['id']]);assert response.status_code==200
    assert response.json()['added']==0 and response.json()['parent']['version']==repaired['version']
    unrelated=create(c,'Other root');assert subpage(c,repaired,repair_ids=[unrelated['id']]).json()['added']==0


def test_removing_owned_shortcut_trashes_subtree_and_undo_restores_exact_batch():
    c,_,_=owner();p=create(c,blocks=[]);created=subpage(c,p).json();p=created['parent'];child=created['page']
    grandchild=create(c,'Nested',child['id']);independent=create(c,'Already deleted',child['id'])
    assert c.delete('/api/notes/'+independent['id']).status_code==200
    token=share(c,grandchild['id'],'public')
    response=save(c,p,[{'id':'new','type':'paragraph','content':'Still here'}]);assert response.status_code==200,response.text
    removed=response.json()
    for page in (child,grandchild):assert c.get('/api/notes/'+page['id']).status_code==404
    assert client().get('/api/shared/'+token).status_code==404
    assert save(c,removed,p['blocks']).status_code==200
    for page in (child,grandchild):assert c.get('/api/notes/'+page['id']).status_code==200
    assert c.get('/api/notes/'+independent['id']).status_code==404
    assert client().get('/api/shared/'+token).status_code==404


def test_ordinary_reference_removal_and_markdown_projection_cannot_delete_pages():
    c,_,_=owner();target=create(c,'Unrelated');p=create(c,blocks=[{'id':'reference','type':'pageLink','props':{'pageId':target['id'],'title':'Reference'}}])
    assert save(c,p,[]).status_code==200
    assert c.get('/api/notes/'+target['id']).status_code==200
    created=subpage(c,create(c,blocks=[])).json();parent=created['parent'];child=created['page']
    assert save(c,parent,None).status_code==200
    assert c.get('/api/notes/'+child['id']).status_code==200


def test_locked_children_and_untrusted_parent_edits_are_atomic():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid)
    p=create(c,scope=sid,blocks=[]);created=subpage(c,p).json();parent=created['parent'];child=created['page']
    # A viewer cannot create a child or repair rich content.
    uid=g.get('/api/me').json()['id'];assert c.put('/api/notes/'+p['id']+'/permissions',json={'user_id':uid,'role':'viewer'}).status_code==200
    assert subpage(g,parent).status_code==403
    # A locked parent stays locked even for the owner.
    assert c.post('/api/notes/'+p['id']+'/lock',json={'mode':'lock'}).status_code==200
    assert subpage(c,parent).status_code==423
    assert len(c.get('/api/notes').json())==2


def test_shared_append_preserves_epoch_and_concurrent_node_identities_then_undoes_removal():
    from pycrdt import XmlFragment
    c,_,_=owner();sid=family(c);p=create(c,scope=sid);doc,epoch=initialize(c,p['id']);p=c.get('/api/notes/'+p['id']).json()
    original=list(doc.get('prosemirror',type=XmlFragment).children)[0]
    with c.websocket_connect('/api/live/notes/'+p['id']) as ws:
        hello(ws,c,777);assert next_event(ws,'sync')['epoch']==epoch
        response=subpage(c,p);assert response.status_code==200,response.text
        result=response.json();child=result['page'];parent=result['parent']
        event=next_event(ws,'update');assert event['epoch']==epoch
        doc.apply_update(base64.b64decode(event['update']))
        assert project(doc,m.fail)[2]==parent['blocks']
        root=list(doc.get('prosemirror',type=XmlFragment).children)[0]
        assert dict(root.children[0].attributes)==dict(original.children[0].attributes)
        before=doc.get_state();del root.children[len(root.children)-1]
        ws.send_json({'type':'update','seq':1,'epoch':epoch,'update':base64.b64encode(doc.get_update(before)).decode()});next_event(ws,'ack')
        assert c.get('/api/notes/'+child['id']).status_code==404
        # A rich undo is another CRDT update and restores the owned subtree.
        from app.share_editing import replace_content
        before=doc.get_state();replace_content(doc,parent['title'],parent['blocks'],m.fail)
        ws.send_json({'type':'update','seq':2,'epoch':epoch,'update':base64.b64encode(doc.get_update(before)).decode()});next_event(ws,'ack')
        assert c.get('/api/notes/'+child['id']).status_code==200


def test_publication_edit_cannot_delete_an_owned_subpage_through_its_shortcut():
    c,_,_=owner();result=subpage(c,create(c,blocks=[])).json();p=result['parent'];child=result['page']
    token=share(c,p['id'],'public',permission='edit');anonymous=client();public=anonymous.get('/api/shared/'+token).json()
    response=anonymous.put('/api/shared/'+token,json={'title':p['title'],'body':'Changed','blocks':[{'type':'paragraph','content':'Changed'}],'version':p['version']},headers={'x-share-edit-token':public['edit_token']})
    assert response.status_code==422,response.text
    assert c.get('/api/notes/'+child['id']).status_code==200


def test_owned_shortcut_cannot_bypass_locked_descendants_or_restore_manual_trash():
    c,_,_=owner();result=subpage(c,create(c,blocks=[])).json();p=result['parent'];child=result['page']
    assert c.post('/api/notes/'+child['id']+'/lock',json={'mode':'lock'}).status_code==200
    response=save(c,p,[]);assert response.status_code==423,response.text
    assert c.get('/api/notes/'+p['id']).json()['blocks']==p['blocks']
    assert c.get('/api/notes/'+child['id']).status_code==200
    assert c.post('/api/notes/'+child['id']+'/lock',json={'mode':'unlock'}).status_code==200
    assert c.delete('/api/notes/'+child['id']).status_code==200
    assert save(c,p,[]).status_code==409
    current=c.get('/api/notes/'+p['id']).json()
    assert not any(b['type']=='pageLink' for b in current['blocks'])
    cleared=save(c,current,[]);assert cleared.status_code==200
    assert save(c,cleared.json(),p['blocks']).status_code==404
    assert c.get('/api/notes/'+child['id']).status_code==404


def test_trashing_subpage_prunes_links_preserves_nested_text_and_revisions():
    from app.page_links import page_links
    c,_,_=owner();parent=create(c,blocks=[]);result=subpage(c,parent).json();parent=result['parent'];child=result['page']
    other=create(c,'References',blocks=[{'id':'keep','type':'paragraph','content':'Preserve text'},
        {'id':'link','type':'pageLink','props':{'pageId':child['id'],'title':child['title']},'children':[{'id':'nested','type':'paragraph','content':'Keep nested text'}]}])
    assert c.delete('/api/notes/'+child['id']).status_code==200
    updated=c.get('/api/notes/'+parent['id']).json();assert child['id'] not in page_links(updated['blocks'])
    reference=c.get('/api/notes/'+other['id']).json()
    assert not page_links(reference['blocks']) and 'Preserve text' in reference['body'] and 'Keep nested text' in reference['body']
    assert reference['blocks'][0]['id']=='keep' and reference['blocks'][1]['children'][0]['id']=='nested'
    history=c.get('/api/notes/'+other['id']+'/revisions').json()
    assert history  # Ordinary content keeps its normal version history.


def test_shared_subpage_trash_removes_link_without_resetting_epoch_or_other_nodes():
    c,_,_=owner();sid=family(c);parent=create(c,scope=sid);doc,epoch=initialize(c,parent['id']);parent=c.get('/api/notes/'+parent['id']).json()
    result=subpage(c,parent).json();parent=result['parent'];child=result['page']
    with c.websocket_connect('/api/live/notes/'+parent['id']) as ws:
        hello(ws,c,818);event=next_event(ws,'sync');doc=load_doc(base64.b64decode(event['state']))
        before=project(doc,m.fail)[2];kept=before[0]['id']
        assert c.delete('/api/notes/'+child['id']).status_code==200
        event=next_event(ws,'update');assert event['epoch']==epoch
        doc.apply_update(base64.b64decode(event['update']));blocks=project(doc,m.fail)[2]
        assert blocks[0]['id']==kept and all(b['type']!='pageLink' for b in blocks)
        assert c.get('/api/notes/'+parent['id']).json()['blocks']==blocks


def test_repair_prunes_preexisting_deleted_targets_without_duplicate_children():
    from app.page_links import page_links
    c,_,_=owner();parent=create(c,blocks=[]);result=subpage(c,parent).json();parent=result['parent'];child=result['page']
    with m.db() as db:db.execute('UPDATE notes SET deleted=? WHERE id=?',(m.now(),child['id']))
    response=subpage(c,parent,repair_ids=[]);assert response.status_code==200,response.text
    updated=response.json()['parent'];assert not page_links(updated['blocks'])
    again=subpage(c,updated,repair_ids=[]);assert again.status_code==200
    assert again.json()['parent']['version']==updated['version']
