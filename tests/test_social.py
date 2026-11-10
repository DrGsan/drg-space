import base64,json,secrets
import pytest
from pycrdt import Doc,XmlFragment,XmlElement,XmlText,Map
from app.http import WebSocketDisconnect
from tests.test_security import owner,member,client,reset,note,share
from tests.test_admin import backup,headers,proof,uid
from app import main as m
from app.collaboration import load_doc,project


def family(c):
    r=c.post('/api/social/families',json={'name':'Наша семья'});assert r.status_code==200,r.text;return r.json()['id']
def join(c,g,sid,name='friend'):
    r=c.post(f'/api/social/families/{sid}/invites',json={'login':name});assert r.status_code==200,r.text
    q=r.json()['id'];r=g.post('/api/social/requests/'+q+'/accept');assert r.status_code==200,r.text

def shared(c,sid,parent=None):
    r=c.post('/api/notes',json={'title':'Вместе','body':'Общие планы','space_id':sid,'parent_id':parent});assert r.status_code==200,r.text;return r.json()
def enabled(c,value=True):assert c.put('/api/modules',json={'enabled':{'chat':value}}).status_code==200

def seed(title='Вместе',text='Общие планы'):
    t=XmlText(text);p=XmlElement('paragraph',{'textAlignment':'left'},[t]);b=XmlElement('blockContainer',{'id':'paragraph-one'},[p]);g=XmlElement('blockGroup',{},[b])
    return Doc({'prosemirror':XmlFragment([g]),'meta':Map({'title':title})})
def content(d):return list(list(list(list(d.get('prosemirror',type=XmlFragment).children)[0].children)[0].children)[0].children)[0]
def hello(ws,c,cid=1):ws.send_json({'type':'auth','csrf':c.headers['x-csrf-token'],'client_id':cid})
def next_event(ws,kind):
    for _ in range(20):
        event=ws.receive_json()
        if event['type']==kind:return event
    raise AssertionError('Expected '+kind)
def initialize(c,nid):
    with c.websocket_connect('/api/live/notes/'+nid) as ws:
        hello(ws,c);init=next_event(ws,'init');d=seed();ws.send_json({'type':'init','version':init['page']['version'],'update':base64.b64encode(d.get_update()).decode()})
        sync=next_event(ws,'sync');return load_doc(base64.b64decode(sync['state'])),sync['epoch']


def test_family_requires_consent_and_owner_controls_members():
    c,_,_=owner();g,_=member(c);o,_=member(c,'outsider');sid=family(c);n=shared(c,sid)
    assert g.get('/api/notes/'+n['id']).status_code==404
    assert o.post(f'/api/social/families/{sid}/invites',json={'login':'friend'}).status_code==404
    q=c.post(f'/api/social/families/{sid}/invites',json={'login':'friend'}).json()['id']
    assert c.post('/api/social/requests/'+q+'/accept').status_code==404
    assert o.post('/api/social/requests/'+q+'/accept').status_code==404
    assert g.post('/api/social/requests/'+q+'/accept').status_code==200
    assert g.get('/api/notes/'+n['id']).status_code==200
    assert g.patch('/api/social/families/'+sid,json={'name':'Hijack'}).status_code==403
    assert g.delete('/api/social/spaces/'+sid).status_code==403
    assert g.delete(f'/api/social/families/{sid}/members/{c.get("/api/me").json()["id"]}').status_code==403
    assert c.delete(f'/api/social/families/{sid}/members/{c.get("/api/me").json()["id"]}').status_code==409
    assert c.delete(f'/api/social/families/{sid}/members/{g.get("/api/me").json()["id"]}').status_code==200
    assert g.get('/api/notes/'+n['id']).status_code==404
    assert c.get('/api/notes/'+n['id']).status_code==200


def test_friendship_mutual_consent_uniqueness_decline_and_delete():
    c,_,_=owner();g,_=member(c)
    assert c.post('/api/social/friends',json={'login':'owner'}).status_code==422
    q=c.post('/api/social/friends',json={'login':'friend'}).json()['id']
    assert c.post('/api/social/friends',json={'login':'friend'}).status_code==409
    assert g.post('/api/social/friends',json={'login':'owner'}).status_code==409
    assert not g.get('/api/social').json()['spaces']
    assert g.delete('/api/social/requests/'+q).status_code==200
    q=g.post('/api/social/friends',json={'login':'owner'}).json()['id']
    sid=c.post('/api/social/requests/'+q+'/accept').json()['space_id'];n=shared(c,sid);token=share(c,n['id'],'public')
    assert len(g.get('/api/social').json()['spaces'][0]['members'])==2
    assert c.post('/api/social/requests/'+q+'/accept').status_code==404
    assert g.get('/api/notes/'+n['id']).status_code==200
    deletion=g.delete('/api/social/spaces/'+sid);assert deletion.status_code==200
    assert c.get('/api/social').json()['spaces'][0]['deletion']
    with m.db() as db:
        from app.workspace_lifecycle import purge
        purge(db,deletion.json()['deletion']['delete_at'])
    assert c.get('/api/social').json()['spaces']==[]
    assert client().get('/api/shared/'+token).status_code==404


def test_shared_trees_allow_different_creators_without_cross_scope_links():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);root=shared(c,sid);child=shared(g,sid,root['id']);private=note(c);sid2=family(c)
    assert c.get('/api/notes/'+child['id']).status_code==200
    assert g.post('/api/notes',json={'title':'Bad','parent_id':root['id'],'space_id':sid2}).status_code==422
    assert c.patch('/api/notes/'+private,json={'parent_id':root['id']}).status_code==422
    assert c.patch('/api/notes/'+root['id'],json={'parent_id':child['id']}).status_code==422
    assert c.post('/api/notes',json={'title':'Leak','space_id':sid,'blocks':[{'type':'pageLink','props':{'pageId':private}}]}).status_code==422
    assert c.get('/api/notes/search?q=общие&space_id='+sid).json()['total']==2
    assert g.get('/api/notes/search?q=private').json()['total']==0
    assert g.post('/api/notes/'+root['id']+'/shares',json={'mode':'public'}).status_code==403
    duplicate=c.post('/api/notes/'+root['id']+'/duplicate').json();assert duplicate['space_id']==sid
    assert len(g.get('/api/notes').json())==4
    assert c.delete('/api/notes/'+root['id']).status_code==200
    assert g.get('/api/notes/'+child['id']).status_code==404
    assert g.post('/api/notes/'+root['id']+'/restore').status_code==200
    assert c.get('/api/notes/'+child['id']).status_code==200


def test_chat_optional_history_dedupe_acl_and_disconnect():
    c,_,_=owner();g,_=member(c);o,_=member(c,'outsider');sid=family(c);join(c,g,sid)
    enabled(c,False)
    assert c.get(f'/api/chat/{sid}/messages').status_code==403
    enabled(c);enabled(g);enabled(o)
    data={'body':'Привет!','client_id':'idempotent-123'}
    assert o.post(f'/api/chat/{sid}/messages',json=data).status_code==404
    with c.websocket_connect('/api/live/chat/'+sid) as ws:
        hello(ws,c);assert next_event(ws,'ready')['type']=='ready'
        r=g.post(f'/api/chat/{sid}/messages',json=data);assert r.status_code==200,r.text
        assert next_event(ws,'message')['message']['body']=='Привет!'
        assert g.post(f'/api/chat/{sid}/messages',json=data).json()['id']==r.json()['id']
        # A real network may deliver the retry broadcast before access is revoked.
        assert next_event(ws,'message')['message']['id']==r.json()['id']
        assert len(c.get(f'/api/chat/{sid}/messages').json()['items'])==1
        assert c.get(f'/api/chat/{sid}/messages?after={r.json()["id"]}').json()['items']==[]
        enabled(c,False)
        ws.send_json({'type':'ping'})
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
    assert c.get(f'/api/chat/{sid}/messages').status_code==403
    enabled(c);assert len(c.get(f'/api/chat/{sid}/messages').json()['items'])==1


def test_ws_origin_csrf_auth_and_revoked_membership():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);n=shared(c,sid);path='/api/live/notes/'+n['id']
    for headers_ in ({'origin':'https://evil.example'},{'origin':''}):
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(path,headers=headers_) as ws:pass
    with c.websocket_connect(path) as ws:
        ws.send_json({'type':'auth','csrf':'wrong','client_id':1})
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client().websocket_connect(path):pass
    with g.websocket_connect(path) as ws:
        hello(ws,g);next_event(ws,'init')
        c.delete(f'/api/social/families/{sid}/members/{g.get("/api/me").json()["id"]}')
        ws.send_json({'type':'ping'})
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
    assert c.get('/api/notes/'+n['id']).json()['body']=='Общие планы'


def test_crdt_merges_simultaneous_same_paragraph_and_persists_on_reconnect():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);n=shared(c,sid);canonical,epoch=initialize(c,n['id'])
    a=load_doc(canonical.get_update());b=load_doc(canonical.get_update());state_a=a.get_state();state_b=b.get_state()
    content(a).insert(0,'Аня: ');content(b).insert(0,'Борис: ')
    with c.websocket_connect('/api/live/notes/'+n['id']) as ws:
        hello(ws,c);next_event(ws,'sync')
        for i,d,state in [(1,a,state_a),(2,b,state_b)]:
            ws.send_json({'type':'update','epoch':epoch,'seq':i,'update':base64.b64encode(d.get_update(state)).decode()});assert next_event(ws,'ack')['seq']==i
    saved=g.get('/api/notes/'+n['id']).json();assert all(t in saved['body'] for t in ('Аня','Борис','Общие планы'))
    assert c.put('/api/notes/'+n['id'],json={'title':'Overwrite','body':'Lost'}).status_code==409
    with g.websocket_connect('/api/live/notes/'+n['id']) as ws:
        hello(ws,g);synced=next_event(ws,'sync');restored=load_doc(base64.b64decode(synced['state']))
        assert project(restored,m.fail)[1]==saved['body'];assert synced['epoch']==epoch
    token=share(c,n['id'],'public');assert client().get('/api/shared/'+token).json()['body']==saved['body']
    assert g.get('/api/notes/search?q=Аня').json()['total']==1


def test_shared_data_backup_restore_and_admin_user_deletion():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);root=shared(c,sid);child=shared(g,sid,root['id']);doc,epoch=initialize(c,root['id'])
    q=g.post('/api/social/friends',json={'login':'owner'}).json()['id'];friend_space=c.post('/api/social/requests/'+q+'/accept').json()['space_id'];shared(g,friend_space)
    enabled(c);c.post(f'/api/chat/{sid}/messages',json={'body':'Сохранить разговор','client_id':'backup-test-123'})
    body=backup(c);user=uid(c)
    assert c.post('/api/admin/users/delete',json=proof(user_id=user,confirmation='friend')).status_code==200
    from tests.deletion_helpers import finish_account
    finish_account(user)
    assert not any(s['kind']=='friends' for s in c.get('/api/social').json()['spaces'])
    assert c.get('/api/notes/'+child['id']).status_code==200
    r=c.post('/api/admin/backups/restore',content=body,headers=headers());assert r.status_code==200,r.text
    with m.db() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert db.execute('SELECT COUNT(*) FROM friendships').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM space_members WHERE space_id=?',(sid,)).fetchone()[0]==2
        row=db.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(root['id'],)).fetchone();assert row['epoch']==epoch
        assert project(load_doc(row['state']),m.fail)[1]=='Общие планы'
        assert db.execute('SELECT body FROM messages').fetchone()[0]=='Сохранить разговор'
        assert db.execute('SELECT parent_id FROM notes WHERE id=?',(child['id'],)).fetchone()[0]==root['id']
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0


def test_document_validation_rollback_stale_epoch_and_session_revocation():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);n=shared(c,sid);d,epoch=initialize(c,n['id'])
    before=c.get('/api/notes/'+n['id']).json()
    with c.websocket_connect('/api/live/notes/'+n['id']) as ws:
        hello(ws,c);next_event(ws,'sync')
        # A poisoned XML update must not replace either persisted representation.
        bad=seed();container=list(list(bad.get('prosemirror',type=XmlFragment).children)[0].children)[0];container.attributes['id']='not a safe id'
        ws.send_json({'type':'update','epoch':epoch,'seq':1,'update':base64.b64encode(bad.get_update()).decode()});assert next_event(ws,'error')['status']==422
        assert c.get('/api/notes/'+n['id']).json()['body']==before['body']
        ws.send_json({'type':'update','epoch':epoch,'seq':2,'update':'not base64'});assert next_event(ws,'error')['status']==422
        # An ordinary logout immediately invalidates an already-open channel.
        assert c.post('/api/logout').status_code==200
        ws.send_json({'type':'ping'})
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
    with m.db() as conn:
        row=conn.execute('SELECT state FROM collaborative_docs WHERE note_id=?',(n['id'],)).fetchone()
        assert project(load_doc(row[0]),m.fail)[1]==before['body']


def test_chat_widgets_validate_membership_and_disabled_server_routes():
    c,_,_=owner();g,_=member(c);sid=family(c)
    enabled(c,False)
    config=c.get('/api/dashboard/layout').json();config['instances'].append({'id':'my_chat','module_id':'chat','config':{'space_id':sid}})
    assert c.put('/api/dashboard/layout',json=config).status_code==200
    config=g.get('/api/dashboard/layout').json();config['instances'].append({'id':'my_chat','module_id':'chat','config':{'space_id':sid}})
    assert g.put('/api/dashboard/layout',json=config).status_code==404
    assert c.get('/api/chat/'+sid+'/messages').status_code==403
    enabled(c);config=c.get('/api/dashboard/layout').json();config['instances'].append({'id':'all_chat','module_id':'chat','config':{'space_id':None}})
    assert c.put('/api/dashboard/layout',json=config).status_code==200
    assert len([i for i in c.get('/api/dashboard/layout').json()['instances'] if i['module_id']=='chat'])==3


def test_half_open_connection_can_be_replaced_only_by_the_same_user():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);n=shared(c,sid);initialize(c,n['id'])
    with c.websocket_connect('/api/live/notes/'+n['id']) as old:
        hello(old,c,77);next_event(old,'sync')
        with g.websocket_connect('/api/live/notes/'+n['id']) as attacker:
            hello(attacker,g,77)
            with pytest.raises(WebSocketDisconnect):attacker.receive_json()
        with c.websocket_connect('/api/live/notes/'+n['id']) as new:
            hello(new,c,77);next_event(new,'sync')
            with pytest.raises(WebSocketDisconnect):old.receive_json()
            new.send_json({'type':'ping'});assert next_event(new,'pong')['type']=='pong'


def test_restoring_shared_history_invalidates_the_old_document_generation():
    c,_,_=owner();sid=family(c);n=shared(c,sid);d,epoch=initialize(c,n['id']);old=d.get_state();content(d).insert(0,'New ')
    with c.websocket_connect('/api/live/notes/'+n['id']) as ws:
        hello(ws,c);next_event(ws,'sync')
        ws.send_json({'type':'update','epoch':epoch,'seq':1,'update':base64.b64encode(d.get_update(old)).decode()});next_event(ws,'ack')
        rid=c.get('/api/notes/'+n['id']+'/history').json()[0]['id']
        assert c.post(f'/api/notes/{n["id"]}/history/{rid}/restore').status_code==200
        ws.send_json({'type':'update','epoch':epoch,'seq':2,'update':base64.b64encode(d.get_update()).decode()})
        assert next_event(ws,'error')['status']==409
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
    assert c.get('/api/notes/'+n['id']).json()['body']=='Общие планы'


def test_membership_removal_cleans_widgets_so_every_backup_remains_restorable():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid)
    layout=g.get('/api/dashboard/layout').json();layout['instances'].append({'id':'family_chat','module_id':'chat','config':{'space_id':sid}})
    assert g.put('/api/dashboard/layout',json=layout).status_code==200
    assert c.delete(f'/api/social/families/{sid}/members/{g.get("/api/me").json()["id"]}').status_code==200
    assert g.get('/api/dashboard/layout').json()['instances'][-1]['config']['space_id'] is None
    saved=backup(c);assert c.post('/api/admin/backups/restore',content=saved,headers=headers()).status_code==200
