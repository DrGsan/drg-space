import json
import pytest
from tests.test_security import owner,member,client,note,share
from app import main as m

@pytest.fixture(autouse=True)
def reset_pages():
    with m.db() as c:
        for table in ['share_sessions','shares','note_revisions','notes','tasks','invites','sessions','recovery_codes','users','throttle','auth_failures','security_bans']:c.execute('DELETE FROM '+table)

def page(c,title='Page',parent=None,blocks=None):
    r=c.post('/api/notes',json={'title':title,'body':'Legacy Markdown','parent_id':parent,'blocks':blocks});assert r.status_code==200,r.text;return r.json()

def test_page_tree_cycle_and_foreign_parent():
    c,_,_=owner();g,_=member(c)
    root=page(c);child=page(c,'Child',root['id']);grandchild=page(c,'Grandchild',child['id'])
    assert c.patch('/api/notes/'+root['id'],json={'parent_id':grandchild['id']}).status_code==422
    assert c.patch('/api/notes/'+root['id'],json={'parent_id':root['id']}).status_code==422
    foreign=page(g)
    assert c.patch('/api/notes/'+root['id'],json={'parent_id':foreign['id']}).status_code==404
    assert g.get('/api/notes/'+root['id']).status_code==404
    assert c.patch('/api/notes/'+child['id'],json={'parent_id':None}).json()['parent_id'] is None

def test_rich_document_and_conflict_prevent_lost_updates():
    c,_,_=owner();blocks=[{'id':'abc','type':'heading','props':{'level':2},'content':[{'type':'text','text':'Heading','styles':{'bold':True}}],'children':[]}]
    p=page(c,blocks=blocks)
    r=c.put('/api/notes/'+p['id'],json={'title':'Changed','body':'## Heading','blocks':blocks,'version':p['version']});assert r.status_code==200;assert r.json()['blocks']==blocks
    r=c.put('/api/notes/'+p['id'],json={'title':'Lost update','version':p['version']});assert r.status_code==409
    assert c.get('/api/notes/'+p['id']).json()['title']=='Changed'
    r=c.patch('/api/notes/'+p['id'],json={'icon':'💡','version':p['version']});assert r.status_code==409

def test_block_validation_prevents_bad_links_and_invalid_trees():
    c,_,_=owner()
    for blocks in [[{'type':'unknown'}],[{'type':'image','props':{'url':'javascript:alert(1)'}}],[{'id':'same','type':'paragraph'},{'id':'same','type':'paragraph'}],[{'type':'paragraph','content':[{'type':'link','href':'data:text/html,evil','content':[]}]}]]:
        assert c.post('/api/notes',json={'title':'Bad','blocks':blocks}).status_code==422
    blocks=[{'type':'paragraph','children':[]}];n=blocks[0]
    for i in range(35):n['children']=[{'type':'paragraph'}];n=n['children'][0]
    assert c.post('/api/notes',json={'title':'Too deep','blocks':blocks}).status_code==422

def test_rich_publication_does_not_publish_child_content():
    c,_,_=owner();root=page(c);child=page(c,'Secret child',root['id'])
    blocks=[{'type':'pageLink','props':{'pageId':child['id'],'title':'Child'}},{'type':'toggleListItem','content':'Toggle','children':[{'type':'paragraph','content':'Nested text'}]}]
    assert c.put('/api/notes/'+root['id'],json={'title':'Root','body':'Toggle','blocks':blocks}).status_code==200
    token=share(c,root['id'],'public');r=client().get('/api/shared/'+token)
    assert r.status_code==200;assert r.json()['blocks']==blocks
    assert 'Legacy Markdown' not in r.text
    assert client().get('/api/notes/'+child['id']).status_code==401

def test_trash_restores_only_the_same_deletion_batch_and_links_stay_revoked():
    c,_,_=owner();root=page(c);child=page(c,'Child',root['id']);old_deleted=page(c,'Already trashed',root['id'])
    token=share(c,root['id'],'public');child_token=share(c,child['id'],'public')
    c.delete('/api/notes/'+old_deleted['id']);assert c.delete('/api/notes/'+root['id']).status_code==200
    assert c.get('/api/notes').json()==[];assert len(c.get('/api/notes?trash=true').json())==3
    assert client().get('/api/shared/'+token).status_code==404
    assert client().get('/api/shared/'+child_token).status_code==404
    assert c.post('/api/notes/'+root['id']+'/restore').status_code==200
    assert len(c.get('/api/notes').json())==2;assert len(c.get('/api/notes?trash=true').json())==1
    assert client().get('/api/shared/'+token).status_code==404
    # Restore an independently deleted child after deleting its parent; child goes to root.
    c.delete('/api/notes/'+root['id']);r=c.post('/api/notes/'+old_deleted['id']+'/restore');assert r.json()['parent_id'] is None

def test_permanent_delete_and_foreign_trash_access():
    c,_,_=owner();g,_=member(c);root=page(c);child=page(c,'Child',root['id'])
    assert c.delete('/api/notes/'+root['id']+'/permanent').status_code==409
    c.delete('/api/notes/'+root['id'])
    assert g.post('/api/notes/'+root['id']+'/restore').status_code==404
    assert g.delete('/api/notes/'+root['id']+'/permanent').status_code==404
    assert c.delete('/api/notes/'+root['id']+'/permanent').status_code==200
    assert c.get('/api/notes?trash=true').json()==[]

def test_duplicate_tree_remaps_links_without_copying_publication_access():
    c,_,_=owner();root=page(c);child=page(c,'Child',root['id']);share(c,root['id'],'public')
    blocks=[{'id':'link','type':'pageLink','props':{'pageId':child['id'],'title':'Child'}}]
    c.put('/api/notes/'+root['id'],json={'title':'Root','body':'Child','blocks':blocks})
    r=c.post('/api/notes/'+root['id']+'/duplicate');assert r.status_code==200;duplicate=r.json()
    notes=c.get('/api/notes').json();new_child=next(n for n in notes if n['parent_id']==duplicate['id'])
    assert duplicate['blocks'][0]['props']['pageId']==new_child['id'];assert duplicate['blocks'][0]['id']!='link'
    assert c.get('/api/notes/'+duplicate['id']+'/shares').json()==[]

def test_history_restore_is_private_and_preserves_previous_state():
    c,_,_=owner();g,_=member(c);p=page(c)
    c.put('/api/notes/'+p['id'],json={'title':'New','body':'New body'})
    history=c.get('/api/notes/'+p['id']+'/history').json();assert len(history)==1
    rid=history[0]['id'];assert g.post('/api/notes/'+p['id']+'/history/'+rid+'/restore').status_code==404
    r=c.post('/api/notes/'+p['id']+'/history/'+rid+'/restore');assert r.json()['body']=='Legacy Markdown'
    assert len(c.get('/api/notes/'+p['id']+'/history').json())==2

def test_dashboard_settings_independent_from_appearance_and_other_users():
    c,_,_=owner();g,_=member(c)
    layout=c.get('/api/dashboard/layout').json()
    layout['instances'].reverse();layout['instances'][1]['hidden']=True
    r=c.put('/api/dashboard/layout',json=layout);assert r.status_code==200
    saved=r.json()
    assert c.put('/api/appearance',json={'theme':'rose','mode':'dark'}).status_code==200
    assert c.get('/api/dashboard/layout').json()==saved
    assert all(not w['hidden'] for w in g.get('/api/dashboard/layout').json()['instances'])
    assert c.put('/api/dashboard/layout',json=layout).status_code==409
    saved['instances'].append(saved['instances'][0].copy())
    assert c.put('/api/dashboard/layout',json=saved).status_code==422
