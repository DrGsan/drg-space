"""Versioned native document writes reuse ACL, history and the shared CRDT."""
import base64
import json
import pytest
from tests.test_security import owner, member, client, reset
from tests.test_social import family, shared, initialize, hello, next_event
from app import main as m
from app.collaboration import load_doc, project


def payload(page, text='Native 🧩'):
    return {'title':'Native title','body':'untrusted projection', 'version':page['version'],
        'blocks':[{'id':'native-block','type':'paragraph','props':{'textAlignment':'left'},
                   'content':[{'type':'link','href':'https://example.com','content':[{'type':'text','text':text,'styles':{'bold':True,'spoiler':True}}]}], 'children':[]}]}


def test_private_document_preserves_blocks_projects_markdown_and_rejects_stale_version():
    c,_,_=owner();page=c.post('/api/notes',json={'title':'Before','body':'Legacy'}).json()
    data=payload(page);data['blocks'][0]['props']['nativeFutureField']={'nested':[1,True,None]}
    url='/api/notes/'+page['id']+'/document'
    response=c.put(url,json=data);assert response.status_code==200,response.text
    saved=response.json();assert saved['blocks']==data['blocks']
    assert saved['body']!='untrusted projection' and 'Native 🧩' in saved['body']
    assert c.put(url,json=data).status_code==409
    assert c.get('/api/notes/'+page['id']).json()['blocks']==saved['blocks']
    assert c.get('/api/notes/'+page['id']+'/history').json()
    assert c.put(url,json={k:v for k,v in data.items() if k!='version'}).status_code==422


def test_shared_document_notifies_web_peer_without_changing_epoch():
    c,_,_=owner();nid=shared(c,family(c))['id'];doc,epoch=initialize(c,nid)
    page=c.get('/api/notes/'+nid).json()
    with c.websocket_connect('/api/live/notes/'+nid) as ws:
        hello(ws,c,812);assert next_event(ws,'sync')['epoch']==epoch
        result=c.put('/api/notes/'+nid+'/document',json=payload(page))
        assert result.status_code==200,result.text
        update=next_event(ws,'update');assert update['epoch']==epoch
        doc.apply_update(base64.b64decode(update['update']))
        assert project(doc,m.fail)==(result.json()['title'],result.json()['body'],result.json()['blocks'])
    with m.db() as db:
        canonical=db.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(nid,)).fetchone()
        assert canonical['epoch']==epoch
        assert project(load_doc(canonical['state']),m.fail)[2]==result.json()['blocks']


def test_native_document_requires_access_csrf_unlocked_page_and_valid_assets():
    c,_,_=owner();g,_=member(c);page=c.post('/api/notes',json={'title':'Private'}).json();url='/api/notes/'+page['id']+'/document';data=payload(page)
    assert client().put(url,json=data).status_code==401
    assert g.put(url,json=data).status_code==404
    assert c.put(url,json=data,headers={'x-csrf-token':'wrong'}).status_code==403
    assert c.put(url,json=data,headers={'origin':'https://attacker.test'}).status_code==403
    bad=payload(page);bad['blocks'][0]={'type':'image','props':{'url':'javascript:bad'}}
    assert c.put(url,json=bad).status_code==422
    assert c.post('/api/notes/'+page['id']+'/lock',json={'mode':'lock'}).status_code==200
    assert c.put(url,json=data).status_code==423
    assert c.get('/api/notes/'+page['id']).json()['title']=='Private'


def test_legacy_markdown_is_not_converted_or_destroyed():
    c,_,_=owner();page=c.post('/api/notes',json={'title':'Legacy','body':'**Styled**\n\n- Item'}).json()
    data={'title':'Title changed','body':page['body'],'blocks':None,'version':page['version']}
    response=c.put('/api/notes/'+page['id']+'/document',json=data)
    assert response.status_code==200,response.text
    assert response.json()['body']==page['body'] and response.json()['blocks'] is None
