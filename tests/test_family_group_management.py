"""Site authority, immutable source IDs, disabled access and durable purge ack."""
import json
import httpx
import pytest
from tests.test_security import owner,member,reset
from tests.test_family_photos import enable,grant,G1,G2
from app import main as m

@pytest.fixture
def managed(monkeypatch):
    bridge=m.family_photos
    for key,value in {'url':'http://archive.internal:8090','token':'service-secret-'*4,'cached':None,'checked_at':0,'ever_available':False}.items():monkeypatch.setattr(bridge,key,value)
    groups={gid:{'id':gid,'title':'Group '+gid,'enabled':True,'telegram_id':gid,'delete_at':None,'latest_message_id':0} for gid in (G1,G2)}
    purged={};calls=[]
    def handle(request):
        path=request.url.path;calls.append((request.method,path))
        if path=='/v1/status':return httpx.Response(200,json={'service':'family-photo','api_version':1,'group_management':1,'worker_online':True})
        if path=='/v1/groups':return httpx.Response(200,json={'items':[g for g in groups.values() if g['enabled'] and not g['delete_at']]})
        if path=='/v1/admin/groups' and request.method=='GET':return httpx.Response(200,json={'items':list(groups.values()),'purged':dict(purged),'server_time':m.now()})
        if path=='/v1/admin/purged/ack':
            for gid,token in json.loads(request.content)['purged'].items():
                if purged.get(gid)==token:purged.pop(gid)
            return httpx.Response(200,json={'ok':True})
        if path.startswith('/v1/admin/'):
            return httpx.Response(200,json={'ok':True,'items':[]})
        if path.endswith('/unread'):return httpx.Response(200,json={'unread':0})
        if path.endswith('/messages'):return httpx.Response(200,json={'items':[],'more':False})
        return httpx.Response(404,json={'detail':'missing'})
    monkeypatch.setattr(bridge,'client',lambda timeout=5:httpx.Client(base_url=bridge.url+'/',transport=httpx.MockTransport(handle),headers={'Authorization':'Bearer '+bridge.token}))
    return bridge,groups,purged,calls


def test_only_owner_can_manage_sources_and_source_ids_cannot_be_changed(managed):
    bridge,groups,purged,calls=managed
    c,_,_=owner();g,_=member(c);uid=g.get('/api/me').json()['id']
    for path,body in [('/discovery',{}),('/groups',{'telegram_id':'-1003'}),('/groups/'+G1+'/disable',{})]:
        assert g.post('/api/family-photos/admin'+path,json=body).status_code==403
    # An additional site administrator also has no access to this owner section.
    with m.db() as db:db.execute("UPDATE users SET role='admin' WHERE id=?",(uid,))
    assert g.get('/api/family-photos/admin').status_code==403
    assert g.post('/api/family-photos/admin/groups/'+G1+'/disable',json={}).status_code==403
    assert c.post('/api/family-photos/admin/groups/'+G1+'/disable',json={'telegram_id':'-1003'}).status_code==422
    assert c.post('/api/family-photos/admin/groups',json={'telegram_id':'-1003','id':G1}).status_code==422
    assert c.post('/api/family-photos/admin/groups/'+G1+'/delete',json={'confirmation':'wrong'}).status_code==422
    assert c.post('/api/family-photos/admin/groups/'+G1+'/delete',json={'confirmation':G1}).status_code==200
    assert c.post('/api/family-photos/admin/groups/'+G1+'/disable',json={}).status_code==200
    assert c.post('/api/family-photos/admin/groups/'+G1+'/other',json={}).status_code==404
    with m.db() as db:
        events=[json.loads(r[0]) for r in db.execute("SELECT details FROM security_events WHERE action='family_group_delete'")]
        assert events and events[0]['group_id']==G1


def test_disabled_group_disappears_and_blocks_direct_api_without_removing_grants(managed):
    bridge,groups,_,_=managed
    c,_,_=owner();g,_=member(c);uid=g.get('/api/me').json()['id']
    assert grant(c,uid,[G1,G2]).status_code==200
    enable(c)
    groups[G1]['enabled']=False
    assert [x['id'] for x in g.get('/api/family-photos/groups').json()['items']]==[G2]
    assert g.get('/api/family-photos/groups/'+G1+'/messages').status_code==404
    assert c.get('/api/family-photos/groups/'+G1+'/messages').status_code==404
    assert {x['id'] for x in c.get('/api/family-photos/admin').json()['groups']}=={G1,G2}
    # Changing another permission preserves the disabled group selection.
    assert grant(c,uid,[G1,G2]).status_code==200
    with m.db() as db:assert json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])['family_photo_access']['groups']==[G1,G2]


def test_purge_cleans_grants_cursors_mutes_widgets_notifications_then_acknowledges(managed):
    bridge,groups,purged,_=managed
    c,_,_=owner();g,_=member(c);uid=g.get('/api/me').json()['id']
    assert grant(c,uid,[G1,G2]).status_code==200
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
        settings.update(family_photo_reads={G1:1,G2:2},chat_notification_mutes={'telegram:'+G1:-1,'telegram:'+G2:-1},dashboard={'instances':[{'id':'first','module_id':'chat','config':{'space_id':'telegram:'+G1}},{'id':'other','module_id':'chat','config':{'space_id':'telegram:'+G2}}]})
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
        from app.modules.family_photo_events import save_state
        save_state(db,{'cursors':{G1:1,G2:2},'baselines':{G1:0,G2:0}})
        db.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',('message-reactions:telegram:'+G1+':1',json.dumps({'version':1,'space':'telegram:'+G1,'message':1,'users':{uid:['👍']}})))
        from app.notifications import notify
        notify(db,uid,'family_message',{'space_id':'telegram:'+G1,'family_group_id':G1,'message_id':1,'preview':'Deleted'},m.now())
    groups.pop(G1);purged[G1]='confirmed-cleanup'
    assert c.get('/api/family-photos/admin').status_code==200
    assert purged=={}
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
        assert settings['family_photo_access']['groups']==[G2]
        assert settings['family_photo_reads']=={G2:2}
        assert settings['chat_notification_mutes']=={'telegram:'+G2:-1}
        assert [item['id'] for item in settings['dashboard']['instances']]==['other']
        assert not db.execute("SELECT 1 FROM notifications WHERE kind='family_message'").fetchone()
        from app.modules.family_photo_events import state
        assert G1 not in state(db)['cursors']
        assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',('message-reactions:telegram:'+G1+':1',)).fetchone()
    # Retry is idempotent after a lost ack response.
    bridge.cleanup_purged({G1:'confirmed-cleanup'})


def test_migration_target_is_filtered_by_independent_view_grants(managed):
    bridge,groups,_,_=managed;admin,_,_=owner();guest,_=member(admin);uid=guest.get('/api/me').json()['id']
    groups[G1]['connection']={'state':'migrated','checked_at':100,'migrated_to':G2,'migrated_group_id':G2}
    grant(admin,uid,[G1]);enable(guest)
    connection=guest.get('/api/family-photos/groups').json()['items'][0]['connection']
    assert 'migrated_group_id' not in connection and 'migrated_to' not in connection
    grant(admin,uid,[G1,G2])
    connection=next(g for g in guest.get('/api/family-photos/groups').json()['items'] if g['id']==G1)['connection']
    assert connection['migrated_group_id']==G2 and 'migrated_to' not in connection
