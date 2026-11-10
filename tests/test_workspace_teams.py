import json
import pytest
from tests.test_security import owner, member, reset
from tests.test_people_groups import workspace, invite
from tests.test_social import shared, hello, next_event
from tests.test_admin import backup, headers
from app import main as m
from app.http import WebSocketDisconnect
from app.workspace_management import PREFIX, AUDIENCE_PREFIX, validate_archive

T1='team000000000001'
T2='team000000000002'


def config(c,sid):
    r=c.get(f'/api/social/spaces/{sid}/management');assert r.status_code==200,r.text
    return {k:v for k,v in r.json().items() if k in ('revision','advanced','admins','teams')}


def update(c,sid,value):
    r=c.put(f'/api/social/spaces/{sid}/management',json=value);assert r.status_code==200,r.text
    return config(c,sid)


def audience(c,nid,teams=None,members=None,mode='restricted'):
    current=c.get(f'/api/notes/{nid}/audience');assert current.status_code==200,current.text
    r=c.put(f'/api/notes/{nid}/audience',json={'revision':current.json()['revision'],'mode':mode,'teams':teams or {},'members':members or {}})
    assert r.status_code==200,r.text


def setup():
    c,_,_=owner();a,_=member(c);b,_=member(c,'second');d,_=member(c,'third')
    sid=workspace(c,'organization');invite(c,a,sid,'friend');invite(c,b,sid,'second');invite(c,d,sid,'third')
    ids=[u.get('/api/me').json()['id'] for u in (c,a,b,d)]
    value=config(c,sid);value['teams']={T1:{'name':'Продажи','members':[ids[1]]},T2:{'name':'Разработка','members':[ids[1],ids[2]]}}
    update(c,sid,value)
    return c,a,b,d,sid,ids


def test_team_union_individual_roles_inheritance_search_and_attachments():
    c,a,b,d,sid,ids=setup();root=shared(c,sid);child=shared(c,sid,root['id'])
    attachment=c.post('/api/notes/'+child['id']+'/files?name=secret.txt',content=b'private attachment').json()
    audience(c,root['id'],{T1:'editor',T2:'commenter'},{ids[3]:'viewer'})
    for account in (a,b,d):assert account.get('/api/notes/'+child['id']).status_code==200
    assert a.get('/api/notes/'+child['id']).json()['role']=='editor'
    assert b.get('/api/notes/'+child['id']).json()['role']=='commenter'
    assert d.get('/api/notes/'+child['id']).json()['role']=='viewer'
    assert a.patch('/api/notes/'+child['id'],json={'icon':'⭐'}).status_code==200
    assert b.patch('/api/notes/'+child['id'],json={'icon':'⭐'}).status_code==403
    assert b.post('/api/notes/'+child['id']+'/comments',json={'body':'Комментарий'}).status_code==200
    assert d.post('/api/notes/'+child['id']+'/comments',json={'body':'Нет доступа'}).status_code==403
    assert d.get('/api/notes/'+child['id']+'/files/'+attachment['id']).content==b'private attachment'
    sources=next(u for u in c.get('/api/notes/'+child['id']+'/permissions').json()['members'] if u['id']==ids[1])['sources']
    assert {s['id'] for s in sources if s['kind']=='team'}=={T1,T2}
    value=config(c,sid);value['teams'][T2]['members']=[ids[1]];update(c,sid,value)
    for url in ('/api/notes/'+child['id'],'/api/notes/'+child['id']+'/files/'+attachment['id'],'/api/notes/'+child['id']+'/permissions'):
        assert b.get(url).status_code==404,url
    assert b.get('/api/notes/search?q=Общие').json()['total']==0
    assert b.get('/api/notes').json()==[]
    assert b.get('/api/social').json()['spaces'][0]['notes']==0
    # Disabling the expanded UI never widens the audience.
    value=config(c,sid);value['advanced']=False;update(c,sid,value)
    assert b.get('/api/notes/'+child['id']).status_code==404
    assert a.get('/api/notes/'+child['id']).status_code==200


def test_team_removal_revokes_open_socket_and_individual_grant_can_restore_access():
    c,a,b,d,sid,ids=setup();page=shared(c,sid)
    audience(c,page['id'],{T1:'editor'})
    with a.websocket_connect('/api/live/notes/'+page['id']) as ws:
        hello(ws,a);next_event(ws,'init')
        value=config(c,sid);value['teams'][T1]['members']=[];update(c,sid,value)
        ws.send_json({'type':'ping'})
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
    assert a.get('/api/notes/'+page['id']).status_code==404
    # Existing workspace colleagues can be invited personally without friendship.
    assert c.post('/api/notes/'+page['id']+'/members',json={'logins':['friend'],'role':'viewer'}).status_code==200
    assert a.get('/api/notes/'+page['id']).json()['role']=='viewer'
    assert c.delete('/api/notes/'+page['id']+'/members/'+ids[1]).status_code==200
    assert a.get('/api/notes/'+page['id']).status_code==404


def test_personal_revocation_does_not_deny_independent_team_grants():
    c,a,b,d,sid,ids=setup();page=shared(c,sid)
    audience(c,page['id'],{T1:'viewer'},{ids[1]:'commenter'})
    assert a.get('/api/notes/'+page['id']).json()['role']=='commenter'
    assert c.put('/api/notes/'+page['id']+'/permissions',json={'user_id':ids[1],'role':'editor'}).status_code==200
    assert a.get('/api/notes/'+page['id']).json()['role']=='editor'
    assert c.delete('/api/notes/'+page['id']+'/members/'+ids[1]).status_code==200
    assert a.get('/api/notes/'+page['id']).json()['role']=='viewer'


def test_administrators_cannot_escalate_to_owner_and_transfer_preserves_data():
    c,a,b,d,sid,ids=setup();page=shared(c,sid);audience(c,page['id'])
    value=config(c,sid)
    assert a.put(f'/api/social/spaces/{sid}/management',json=value).status_code==403
    value['admins']=[ids[1]];update(c,sid,value)
    assert a.get('/api/notes/'+page['id']).json()['manage'] is True
    assert a.patch('/api/social/spaces/'+sid,json={'name':'Наша компания'}).status_code==200
    value=config(a,sid);value['admins'].append(ids[2])
    assert a.put(f'/api/social/spaces/{sid}/management',json=value).status_code==403
    assert a.delete('/api/social/spaces/'+sid).status_code==403
    assert a.delete(f'/api/social/spaces/{sid}/members/{ids[0]}').status_code==403
    assert a.post(f'/api/social/spaces/{sid}/ownership',json={'revision':value['revision'],'user_id':ids[2]}).status_code==403
    assert c.post(f'/api/social/spaces/{sid}/ownership',json={'revision':value['revision'],'user_id':ids[2]}).status_code==200
    for account in (c,a,b):assert account.get('/api/notes/'+page['id']).status_code==200
    assert c.get(f'/api/social/spaces/{sid}/management').json()['role']=='admin'
    assert b.get(f'/api/social/spaces/{sid}/management').json()['role']=='owner'
    assert c.delete('/api/social/spaces/'+sid).status_code==403
    assert b.get('/api/social').json()['spaces'][0]['owner'] is True


def test_cross_workspace_members_teams_and_stale_updates_are_rejected():
    c,a,b,d,sid,ids=setup();outside,_=member(c,'outsider');other=workspace(c,'organization');page=shared(c,sid)
    value=config(c,sid);stale=dict(value);update(c,sid,value)
    assert c.put(f'/api/social/spaces/{sid}/management',json=stale).status_code==409
    value=config(c,sid);value['teams'][T1]['members'].append(outside.get('/api/me').json()['id'])
    assert c.put(f'/api/social/spaces/{sid}/management',json=value).status_code==422
    assert outside.get(f'/api/social/spaces/{sid}/management').status_code==404
    assert c.put('/api/notes/'+page['id']+'/audience',json={'revision':0,'mode':'restricted','teams':{'other00000000001':'viewer'},'members':{}}).status_code==422
    assert a.get('/api/notes/'+page['id']+'/audience').status_code==403
    private=c.post('/api/notes',json={'title':'Личное'}).json()['id']
    assert c.get('/api/notes/'+private+'/audience').status_code==422


def test_deleting_team_does_not_open_closed_pages_and_leaving_cleans_memberships():
    c,a,b,d,sid,ids=setup();page=shared(c,sid);audience(c,page['id'],{T1:'editor'})
    value=config(c,sid);value['admins']=[ids[3]];update(c,sid,value)
    assert d.delete(f'/api/social/spaces/{sid}/members/{ids[3]}').status_code==200
    assert ids[3] not in config(c,sid)['admins']
    value=config(c,sid);del value['teams'][T1];update(c,sid,value)
    assert a.get('/api/notes/'+page['id']).status_code==404
    assert c.get('/api/notes/'+page['id']+'/audience').json()['mode']=='restricted'
    assert c.get('/api/notes/'+page['id']+'/audience').json()['teams']=={}


def test_closed_pages_keep_audience_when_copied_moved_and_restored():
    c,a,b,d,sid,ids=setup();root=shared(c,sid);child=shared(c,sid,root['id']);audience(c,root['id'],{T1:'editor'})
    copy=c.post('/api/notes/'+child['id']+'/duplicate');assert copy.status_code==200,copy.text
    duplicate=copy.json()['id']
    assert c.patch('/api/notes/'+duplicate,json={'parent_id':None}).status_code==200
    assert a.get('/api/notes/'+duplicate).status_code==200
    assert b.get('/api/notes/'+duplicate).status_code==404
    assert c.patch('/api/notes/'+child['id'],json={'parent_id':None}).status_code==200
    assert b.get('/api/notes/'+child['id']).status_code==404
    nested=shared(c,sid,root['id'])
    assert c.delete('/api/notes/'+nested['id']).status_code==200
    assert c.delete('/api/notes/'+root['id']).status_code==200
    assert c.post('/api/notes/'+nested['id']+'/restore').status_code==200
    assert b.get('/api/notes/'+nested['id']).status_code==404
    assert a.get('/api/notes/'+nested['id']).status_code==200


def test_copy_preserves_explicit_open_boundary_inside_closed_branch():
    c,a,b,d,sid,ids=setup();root=shared(c,sid);child=shared(c,sid,root['id'])
    audience(c,root['id'],{T1:'editor'});audience(c,child['id'],mode='workspace')
    assert b.get('/api/notes/'+child['id']).status_code==200
    duplicate=c.post('/api/notes/'+child['id']+'/duplicate').json()['id']
    assert b.get('/api/notes/'+duplicate).status_code==200
    assert c.get('/api/notes/'+duplicate+'/audience').json()['mode']=='workspace'


def test_full_backup_keeps_teams_admins_and_audiences_and_rejects_old_reader():
    c,a,b,d,sid,ids=setup();page=shared(c,sid);audience(c,page['id'],{T1:'editor'})
    value=config(c,sid);value['admins']=[ids[2]];update(c,sid,value)
    from app.backups import Backups
    import base64,hashlib
    service=Backups(m.db,m.DATA,m.cipher,base64.urlsafe_b64encode(hashlib.sha256(m.APP_SECRET.encode()).digest()).decode(),m.now)
    with service.temp() as stage:assert service.snapshot(stage)['version']==4
    saved=backup(c)
    assert c.delete('/api/social/spaces/'+sid).status_code==200
    r=c.post('/api/admin/backups/restore',content=saved,headers=headers());assert r.status_code==200,r.text
    from app.note_access import can_view
    with m.db() as db:
        n=db.execute('SELECT * FROM notes WHERE id=?',(page['id'],)).fetchone()
        assert can_view(db,n,ids[1]) and can_view(db,n,ids[2]) and not can_view(db,n,ids[3])
        validate_archive(db,lambda:pytest.fail('invalid archive'))


@pytest.mark.parametrize('corruption',['foreign_member','foreign_team','orphan_policy','invalid_role','invalid_version'])
def test_archive_validation_rejects_invalid_team_rules(corruption):
    c,a,b,d,sid,ids=setup();page=shared(c,sid);audience(c,page['id'],{T1:'editor'})
    def invalid():raise ValueError('invalid archive')
    with m.db() as db:
        if corruption=='foreign_member':
            value=json.loads(db.execute('SELECT value FROM app_settings WHERE key=?',(PREFIX+sid,)).fetchone()[0]);value['teams'][T1]['members'].append('f'*32)
            db.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(value),PREFIX+sid))
        else:
            value=json.loads(db.execute('SELECT value FROM app_settings WHERE key=?',(AUDIENCE_PREFIX+page['id'],)).fetchone()[0])
            if corruption=='foreign_team':value['teams']={'other00000000001':'viewer'}
            if corruption=='invalid_role':value['members'][ids[1]]='owner'
            if corruption=='invalid_version':value['version']=True
            if corruption=='orphan_policy':db.execute('UPDATE app_settings SET key=? WHERE key=?',(AUDIENCE_PREFIX+'missing000000001',AUDIENCE_PREFIX+page['id']))
            else:db.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(value),AUDIENCE_PREFIX+page['id']))
        with pytest.raises(ValueError,match='invalid archive'):validate_archive(db,invalid)


def test_portable_restore_to_raw_sqlite_preserves_team_access(tmp_path):
    from contextlib import contextmanager
    import sqlite3
    from cryptography.fernet import Fernet
    from app.backups import Backups
    from app.schema import SCHEMA
    from tests.test_admin import BACKUP_PASSWORD
    c,a,b,d,sid,ids=setup();page=shared(c,sid);audience(c,page['id'],{T1:'editor'})
    saved=backup(c);destination=tmp_path/'site.sqlite3'
    with sqlite3.connect(destination) as target:target.executescript(SCHEMA)
    @contextmanager
    def raw_db():
        connection=sqlite3.connect(destination);connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:yield connection;connection.commit()
        except BaseException:connection.rollback();raise
        finally:connection.close()
    key=Fernet.generate_key();service=Backups(raw_db,tmp_path,Fernet(key),key.decode(),m.now)
    with service.temp() as stage:
        manifest=service.prepare(saved,BACKUP_PASSWORD,stage)
        assert manifest['version']==4
        assert service.restore(stage,manifest)=='owner'
    from app.note_access import can_view
    with raw_db() as restored:
        n=restored.execute('SELECT * FROM notes WHERE id=?',(page['id'],)).fetchone()
        assert can_view(restored,n,ids[1]) and not can_view(restored,n,ids[2])
        validate_archive(restored,lambda:pytest.fail('invalid restored permissions'))


@pytest.mark.parametrize('template',['family','group'])
def test_simple_spaces_cannot_enable_advanced_mode_or_create_teams(template):
    c,_,_=owner();sid=workspace(c,template)
    state=c.get(f'/api/social/spaces/{sid}/management').json()
    assert state['advanced_available'] is False
    value=config(c,sid);value['advanced']=True
    assert c.put(f'/api/social/spaces/{sid}/management',json=value).status_code==422
    value['advanced']=False;value['teams']={T1:{'name':'dev','members':[]}}
    assert c.put(f'/api/social/spaces/{sid}/management',json=value).status_code==422
    # Existing rules from 1.9.0 stay intact, including closed note audiences.
    from app.workspace_management import save,default
    with m.db() as db:save(db,sid,{**default(),'advanced':True,'teams':{T1:{'name':'Legacy','members':[]}}})
    page=shared(c,sid);audience(c,page['id'])
    assert c.get(f'/api/notes/{page['id']}/permissions').json()['advanced'] is False
    assert c.get(f'/api/notes/{page['id']}/audience').json()['mode']=='restricted'


@pytest.mark.parametrize('template',['project','organization'])
def test_work_spaces_allow_advanced_mode(template):
    c,_,_=owner();sid=workspace(c,template)
    assert c.get(f'/api/social/spaces/{sid}/management').json()['advanced_available'] is True
    value=config(c,sid);value['advanced']=True;value['teams']={T1:{'name':'dev','members':[]}}
    update(c,sid,value)


def test_workspace_tables_receive_team_membership_roles_and_join_dates():
    c,a,b,d,sid,ids=setup()
    overview=next(s for s in a.get('/api/social').json()['spaces'] if s['id']==sid)
    assert overview['teams'][T1]['members']==[ids[1]]
    assert overview['advanced_available'] is True
    assert len(overview['members'])==4
    assert all(type(u['joined_at']) is int and u['joined_at']>0 and u['space_role'] in {'owner','admin','member'} for u in overview['members'])
    details=a.get(f'/api/social/spaces/{sid}/management').json()
    assert {u['id']:u['joined_at'] for u in details['members']}=={u['id']:u['joined_at'] for u in overview['members']}


def test_member_creator_cannot_manage_work_space_access_and_admin_is_local():
    c,a,b,d,sid,ids=setup()
    page=shared(a,sid)
    assert a.get('/api/notes/'+page['id']).json()['role']=='editor'
    assert a.get('/api/notes/'+page['id']).json()['manage'] is False
    for response in (
        a.post(f'/api/social/spaces/{sid}/invites',json={'login':'third'}),
        a.put('/api/notes/'+page['id']+'/permissions',json={'user_id':ids[2],'role':'viewer'}),
        a.post('/api/notes/'+page['id']+'/members',json={'logins':['third'],'role':'viewer'}),
        a.delete('/api/notes/'+page['id']+'/members/'+ids[2]),
        a.put('/api/notes/'+page['id']+'/audience',json={'revision':0,'mode':'restricted','teams':{T1:'viewer'},'members':{}}),
    ): assert response.status_code==403,response.text
    # Role assignment is available even with the expanded view off.
    value=config(c,sid);value['advanced']=False;value['admins']=[ids[1]];update(c,sid,value)
    assert a.get('/api/notes/'+page['id']).json()['manage'] is True
    assert a.get('/api/admin/users').status_code==403
    assert a.put('/api/notes/'+page['id']+'/permissions',json={'user_id':ids[2],'role':'viewer'}).status_code==200
    other=shared(c,sid);audience(a,other['id'],{T1:'viewer'})
    # Removing an author's management privilege must not create invalid revocations.
    assert c.delete('/api/notes/'+page['id']+'/members/'+ids[1]).status_code==422
    value=config(c,sid);value['admins']=[];update(c,sid,value)
    assert a.get('/api/notes/'+page['id']).json()['manage'] is False
    assert c.put('/api/notes/'+page['id']+'/permissions',json={'user_id':ids[1],'role':'viewer'}).status_code==200
    assert a.get('/api/notes/'+page['id']).json()['role']=='viewer'
    assert c.delete('/api/notes/'+page['id']+'/members/'+ids[1]).status_code==200
    assert a.get('/api/notes/'+page['id']).status_code==404
    from app.note_access import validate_archive
    with m.db() as db:validate_archive(db,lambda:pytest.fail('invalid author rule'))


def test_audience_preview_is_read_only_includes_inherited_children_and_enforces_manager():
    c,a,b,d,sid,ids=setup();root=shared(c,sid);child=shared(c,sid,root['id']);independent=shared(c,sid,root['id'])
    audience(c,independent['id'],mode='workspace')
    value=config(c,sid);value['teams']={T1:{'name':'dev','members':[ids[1]]}};update(c,sid,value)
    payload={'revision':0,'mode':'restricted','teams':{T1:'viewer'},'members':{}}
    path='/api/notes/'+root['id']+'/audience/preview'
    assert a.post(path,json=payload).status_code==403
    response=c.post(path,json=payload);assert response.status_code==200,response.text
    preview=response.json();assert preview['pages']==3
    changes={u['id']:u for u in preview['changes']}
    assert changes[ids[1]]['before']=='editor' and changes[ids[1]]['after']=='viewer' and changes[ids[1]]['pages']==2
    assert changes[ids[2]]['after'] is None and changes[ids[2]]['pages']==2
    assert a.get('/api/notes/'+root['id']).status_code==200
    assert c.get('/api/notes/'+root['id']+'/audience').json()['revision']==0
    assert c.post(path,json={**payload,'teams':{'foreign-team':'viewer'}}).status_code==422
