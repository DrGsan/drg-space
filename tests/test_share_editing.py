import base64
import json
import pytest
from pycrdt import XmlFragment
from tests.test_security import owner, member, client, reset, note, share
from tests.test_social import family, shared, initialize, hello, next_event, content
from app import main as m
from app.collaboration import load_doc, project


def draft(n, text='Updated through link'):
    return {'title': 'Changed by link', 'body': text, 'blocks': [{'id': 'share-block', 'type': 'paragraph', 'content': [{'type': 'text', 'text': text, 'styles': {'bold': True}}], 'children': []}], 'version': n['version']}


def put(c, token, page=None, **kw):
    page = page or c.get('/api/shared/'+token).json()
    h = {'x-share-edit-token': page.get('edit_token', '')}
    return c.put('/api/shared/'+token, json=draft(page, **kw), headers=h)


def test_default_links_are_read_only_and_can_be_changed_only_by_publisher():
    c, _, _ = owner(); g, _ = member(c); nid = note(c)
    token = share(c, nid, 'public'); anonymous = client()
    page = anonymous.get('/api/shared/'+token).json()
    assert page['permission']=='view' and 'edit_token' not in page
    assert put(anonymous, token, page).status_code == 403
    assert g.patch('/api/shares/'+token, json={'permission':'edit'}).status_code == 404
    assert c.patch('/api/shares/'+token, json={'permission':'admin'}).status_code == 422
    assert c.patch('/api/shares/'+token, json={'permission':'edit'}).status_code == 200
    page = anonymous.get('/api/shared/'+token).json()
    assert page['permission']=='edit' and page['edit_token']
    assert put(anonymous, token, page).status_code == 200
    assert c.get('/api/notes/'+nid).json()['title']=='Changed by link'
    assert c.patch('/api/shares/'+token, json={'permission':'view'}).status_code == 200
    assert put(anonymous, token, page).status_code == 403
    assert c.get('/api/notes/'+nid+'/revisions').json()


@pytest.mark.parametrize('mode', ['public','password','authenticated','users'])
def test_editing_requires_same_access_conditions_as_reading(mode):
    c, _, _ = owner(); g, _ = member(c); outsider, _ = member(c,'outsider'); nid = note(c)
    kwargs = {'permission':'edit'}
    if mode=='password': kwargs['password']='page-secret-password'
    if mode=='users': kwargs['allowed']=['friend']
    token = share(c,nid,mode,**kwargs); anonymous = client()
    reader = anonymous if mode in ('public','password') else g
    if mode=='password':
        assert put(reader,token,{'version':1}).status_code==423
        assert reader.post('/api/shared/'+token+'/unlock',json={'password':'page-secret-password'}).status_code==200
    page = reader.get('/api/shared/'+token).json()
    assert put(reader,token,page).status_code==200
    assert c.get('/api/notes/'+nid).json()['body']=='Updated through link'
    if mode in ('authenticated','users'):
        assert put(anonymous,token,page).status_code==401
    if mode=='users': assert put(outsider,token,page).status_code==403
    if mode=='password': assert put(client(),token,page).status_code==423
    if mode in ('authenticated','users'):
        del reader.headers['x-csrf-token']
        assert put(reader,token,page).status_code==403


def test_expiry_revocation_grant_scope_csrf_and_versions_checked_for_every_write():
    c, _, _ = owner(); a = client(); nid=note(c)
    token=share(c,nid,'public',permission='edit',duration=13,unit='minutes')
    other=share(c,note(c),'public',permission='edit')
    page=a.get('/api/shared/'+token).json(); different=a.get('/api/shared/'+other).json()
    assert a.put('/api/shared/'+token,json=draft(page)).status_code==403
    assert put(a,token,{**page,'edit_token':different['edit_token']}).status_code==403
    assert a.put('/api/shared/'+token,json=draft(page),headers={'x-share-edit-token':page['edit_token'],'origin':'https://attacker.example'}).status_code==403
    assert put(a,token,page).status_code==200
    assert put(a,token,page).status_code==409
    fresh=a.get('/api/shared/'+token).json()
    with m.db() as db: db.execute('UPDATE shares SET expires=? WHERE token=?',(m.now()-1,token))
    assert put(a,token,fresh).status_code==404
    fresh=a.get('/api/shared/'+other).json(); c.delete('/api/shares/'+other)
    assert put(a,other,fresh).status_code==404
    assert c.get('/api/notes/'+nid).json()['body']=='Updated through link'


def test_extra_fields_and_new_page_links_cannot_escape_the_shared_page():
    c, _, _ = owner(); nid=note(c); token=share(c,nid,'public',permission='edit'); a=client(); page=a.get('/api/shared/'+token).json()
    h={'x-share-edit-token':page['edit_token']}; data=draft(page)
    for key in ('user_id','parent_id','space_id','permission'):
        assert a.put('/api/shared/'+token,json={**data,key:'anything'},headers=h).status_code==422
    data['blocks']=[{'type':'pageLink','props':{'pageId':note(c)}}]
    assert a.put('/api/shared/'+token,json=data,headers=h).status_code==422
    data['blocks']=[{'type':'paragraph','content':[{'type':'link','href':'javascript:alert(1)'}]}]
    assert a.put('/api/shared/'+token,json=data,headers=h).status_code==422
    assert c.get('/api/notes/'+nid).json()['title']=='Private title'


def test_shared_link_edits_update_canonical_crdt_and_online_members_without_resetting_epoch():
    c, _, _ = owner(); sid=family(c); nid=shared(c,sid)['id']; doc,epoch=initialize(c,nid)
    token=share(c,nid,'public',permission='edit'); anonymous=client(); page=anonymous.get('/api/shared/'+token).json()
    with c.websocket_connect('/api/live/notes/'+nid) as ws:
        hello(ws,c,777); assert next_event(ws,'sync')['epoch']==epoch
        r=put(anonymous,token,page,text='Новая общая мысль 🕐');assert r.status_code==200,r.text
        update=next_event(ws,'update');assert update['epoch']==epoch
        doc.apply_update(base64.b64decode(update['update']))
        title,body,blocks=project(doc,m.fail)
        assert title=='Changed by link' and 'Новая общая мысль 🕐' in body and blocks[0]['content'][0]['styles']['bold']
    with m.db() as db:
        saved=db.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(nid,)).fetchone()
        assert saved['epoch']==epoch
        assert project(load_doc(saved['state']),m.fail)==(title,body,blocks)
    assert c.get('/api/notes/'+nid).json()['blocks']==blocks


def test_link_can_initialize_shared_document_and_preserve_tables_nested_blocks_and_marks():
    c, _, _ = owner(); sid=family(c); nid=shared(c,sid)['id']; token=share(c,nid,'public',permission='edit'); a=client(); page=a.get('/api/shared/'+token).json()
    data=draft(page)
    data['blocks']=[{'id':'heading','type':'heading','props':{'level':2},'content':[{'type':'link','href':'https://example.com','content':[{'type':'text','text':'Ссылка','styles':{'italic':True}}]}],'children':[{'id':'check','type':'checkListItem','props':{'checked':True},'content':'Готово','children':[]}]},
                    {'id':'table','type':'table','content':{'type':'tableContent','headerRows':1,'rows':[{'cells':[[{'type':'text','text':'Имя','styles':{}}],[{'type':'text','text':'Значение','styles':{}}]]},{'cells':[[{'type':'text','text':'UTC','styles':{}}],[{'type':'text','text':'+3','styles':{}}]]}]},'children':[]},
                    {'id':'code','type':'codeBlock','props':{'language':'python'},'content':'print("clock")','children':[]}]
    r=a.put('/api/shared/'+token,json=data,headers={'x-share-edit-token':page['edit_token']});assert r.status_code==200,r.text
    result=r.json();assert result['blocks'][0]['children'][0]['props']['checked']
    assert result['blocks'][0]['content'][0]['type']=='link'
    assert result['blocks'][1]['content']['headerRows']==1 and 'UTC' in result['body']
    assert result['blocks'][2]['content']=='print("clock")'


def test_inline_equations_survive_shared_edits_canonical_projection_and_history():
    c,_,_=owner();sid=family(c);nid=shared(c,sid)['id'];doc,epoch=initialize(c,nid)
    token=share(c,nid,'public',permission='edit');guest=client();page=guest.get('/api/shared/'+token).json()
    data=draft(page);data['blocks'][0]['content']=[{'type':'text','text':'Value: ','styles':{'code':True}},{'type':'equation','props':{'latex':r'\frac{a}{b}'}}]
    response=guest.put('/api/shared/'+token,json=data,headers={'x-share-edit-token':page['edit_token']});assert response.status_code==200,response.text
    saved=c.get('/api/notes/'+nid).json();assert r'$\frac{a}{b}$' in saved['body']
    assert saved['blocks'][0]['content'][1]=={'type':'equation','props':{'latex':r'\frac{a}{b}'}}
    with m.db() as db:
        canonical=db.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(nid,)).fetchone()
        assert canonical['epoch']==epoch and project(load_doc(canonical['state']),m.fail)[2]==saved['blocks']
    page=guest.get('/api/shared/'+token).json();data['version']=page['version'];data['blocks'][0]['content'][1]['props']['latex']='x'*4097
    response=guest.put('/api/shared/'+token,json=data,headers={'x-share-edit-token':page['edit_token']});assert response.status_code==422,response.text
    assert c.get('/api/notes/'+nid).json()['blocks']==saved['blocks']


def test_spoilers_survive_public_edits_shared_projection_and_history_without_schema_changes():
    c,_,_=owner();sid=family(c);nid=shared(c,sid)['id'];doc,epoch=initialize(c,nid)
    token=share(c,nid,'public',permission='edit');guest=client();page=guest.get('/api/shared/'+token).json()
    data=draft(page);data['blocks'][0]['content']=[{'type':'text','text':'Открыто. ','styles':{}},{'type':'link','href':'https://example.com','content':[{'type':'text','text':'тайна 👀','styles':{'spoiler':True,'bold':True,'code':True}}]}]
    with c.websocket_connect('/api/live/notes/'+nid) as ws:
        hello(ws,c,998);assert next_event(ws,'sync')['epoch']==epoch
        response=guest.put('/api/shared/'+token,json=data,headers={'x-share-edit-token':page['edit_token']});assert response.status_code==200,response.text
        event=next_event(ws,'update');assert event['epoch']==epoch;doc.apply_update(base64.b64decode(event['update']))
        title,body,blocks=project(doc,m.fail)
        assert '||**`тайна 👀`**||' in body
        assert blocks[0]['content'][1]['content'][0]['styles']=={'spoiler':True,'bold':True,'code':True}
    saved=c.get('/api/notes/'+nid).json();assert saved['blocks']==blocks
    assert guest.get('/api/shared/'+token).json()['blocks']==blocks
    with m.db() as db:
        canonical=db.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(nid,)).fetchone()
        assert canonical['epoch']==epoch and project(load_doc(canonical['state']),m.fail)==(title,body,blocks)
    # Another edit creates a history snapshot retaining the hidden mark.
    page=guest.get('/api/shared/'+token).json();data.update(version=page['version'],title='After spoiler')
    assert guest.put('/api/shared/'+token,json=data,headers={'x-share-edit-token':page['edit_token']}).status_code==200
    revisions=c.get('/api/notes/'+nid+'/history').json()
    assert any('"spoiler": true' in json.dumps(c.get('/api/notes/'+nid+'/history/'+r['id']).json()) for r in revisions)
