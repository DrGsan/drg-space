import json
import pytest
from tests.test_security import m, owner, member, client, reset, note
from tests.test_admin import uid, backup, headers


def role(c, identity, value, previous='member'):
    return c.put('/api/admin/users/'+identity+'/role', json={'role':value,'previous_role':previous})


def login_admin(c, identity, codes):
    r=c.post('/api/login',json={'login':identity,'password':'Friend-password-123'})
    assert r.status_code==200,r.text
    c.headers['x-csrf-token']=r.json()['csrf']
    r=c.post('/api/2fa/verify',json={'code':codes[0]})
    assert r.status_code==200,r.text
    c.headers['x-csrf-token']=r.json()['csrf']
    assert c.post('/api/admin/access/unlock',json={'password':'Friend-password-123'}).status_code==200


def administrator(c, name='friend'):
    g,_=member(c,name);identity=uid(c,name)
    with m.db() as db:
        codes=['role-test-recovery-'+name]
        db.execute('INSERT INTO recovery_codes VALUES(?,?)',(identity,m.digest(codes[0].upper())))
    assert role(c,identity,'admin').status_code==200
    assert g.get('/api/me').status_code==401
    login_admin(g,name,codes)
    return g,identity


def test_owner_promotes_demotes_and_revokes_grants_and_reset_links():
    c,_,_=owner();g,_=member(c);identity=uid(c)
    link=c.post('/api/admin/users/'+identity+'/password-reset',json={}).json()['url'].split('/')[-1]
    r=role(c,identity,'admin');assert r.status_code==200,r.text
    assert r.json()['role_label']=='Администратор'
    assert g.get('/api/me').status_code==401
    assert g.post('/api/refresh').status_code==401
    assert client().post('/api/password-reset',json={'token':link,'password':'Replacement-password-123'}).status_code==403
    with m.db() as db:
        codes=['promotion-recovery']
        db.execute('INSERT INTO recovery_codes VALUES(?,?)',(identity,m.digest(codes[0].upper())))
    login_admin(g,'friend',codes)
    assert g.get('/api/me').json()['role_label']=='Администратор'
    assert g.get('/api/admin/users').status_code==200
    assert role(c,identity,'member','admin').status_code==200
    assert g.get('/api/admin/users').status_code==401
    assert g.post('/api/refresh').status_code==401
    owner_id=uid(c,'owner')
    with m.db() as db:
        assert db.execute('SELECT role FROM users WHERE id=?',(identity,)).fetchone()[0]=='member'
        events=[dict(r) for r in db.execute("SELECT user_id,details FROM security_events WHERE event='site_role_changed'")]
        assert len(events)==2
        assert all(e['user_id']==owner_id for e in events)
        assert {json.loads(e['details'])['role'] for e in events}=={'admin','member'}
        assert not db.execute('SELECT 1 FROM app_settings WHERE key LIKE ? AND value LIKE ?',('admin-access:%','%'+identity+'%')).fetchone()


def test_assignment_requires_mfa_and_preserves_owner_and_pending_accounts():
    c,_,_=owner()
    r=c.post('/api/admin/users',json={'login':'without-mfa','password':'Member-password-123'})
    identity=r.json()['id']
    assert role(c,identity,'admin').status_code==409
    assert role(c,uid(c,'owner'),'member','admin').status_code==409
    assert role(c,identity,'owner').status_code==422
    assert role(c,identity,'member','admin').status_code==409
    assert c.post('/api/admin/users/delete',json={'user_id':identity,'confirmation':'without-mfa'}).status_code==200
    assert role(c,identity,'admin').status_code==409
    csrf=c.headers.pop('x-csrf-token')
    assert role(c,identity,'admin').status_code==403
    c.headers['x-csrf-token']=csrf
    assert c.get('/api/me').json()['role_label']=='Владелец'


def test_administrator_manages_members_but_cannot_reach_critical_routes():
    c,_,_=owner();g,identity=administrator(c);other,_=member(c,'ordinary');target=uid(c,'ordinary')
    info=g.get('/api/admin/users').json()
    assert info['can_change_roles'] is False
    assert {u['id'] for u in info['users'] if u['can_manage']}=={target}
    assert g.get('/api/presence?scope=admin').status_code==200
    assert g.get('/api/admin/registration').status_code==200
    assert g.post('/api/invites',json={}).status_code==200
    assert g.post('/api/admin/users',json={'login':'created','password':'Member-password-123'}).status_code==200
    assert g.put('/api/admin/users/'+target+'/profile',json={'display_name':'Updated'}).status_code==200
    assert g.put('/api/admin/users/'+target+'/quota',json={'limit_bytes':12345}).status_code==200
    assert g.post('/api/admin/users/'+target+'/password-reset',json={}).status_code==200
    for path in ('/api/admin/security/overview','/api/admin/bans','/api/family-photos/admin','/api/family-photos/admin/storage','/api/admin/users/'+target+'/friends'):
        assert g.get(path).status_code==403,path
    for method,path,data in (
        ('put','/api/admin/registration',{'enabled':True}),
        ('put','/api/admin/users/'+target+'/role',{'role':'admin','previous_role':'member'}),
        ('post','/api/admin/backups/export',{'backup_password':'Backup-password-123'}),
        ('post','/api/admin/backups/restore',{}),
        ('post','/api/admin/security/mode',{}),
    ):
        assert getattr(g,method)(path,json=data).status_code==403,path
    assert g.post('/api/admin/access/lock').status_code==200
    assert g.get('/api/admin/users').status_code==423
    assert g.post('/api/admin/access/unlock',json={'password':'Owner-password-123'}).status_code==403
    assert g.post('/api/admin/access/unlock',json={'password':'Friend-password-123'}).status_code==200


@pytest.mark.parametrize('target_name',['owner','friend','secondadmin'])
def test_administrator_cannot_modify_privileged_accounts(target_name):
    c,_,_=owner();g,_=administrator(c);administrator(c,'secondadmin');identity=uid(c,target_name)
    for method,suffix,data in (
        ('put','profile',{'display_name':'Taken over'}),('put','quota',{'limit_bytes':0}),
        ('put','password',{'password':'Replacement-password-123'}),
        ('post','reset-2fa',{}),('post','password-reset',{}),
    ):
        r=getattr(g,method)('/api/admin/users/'+identity+'/'+suffix,json=data)
        assert r.status_code==403,(suffix,r.text)
    for action in ('delete','restore'):
        assert g.post('/api/admin/users/'+action,json={'user_id':identity,'confirmation':target_name}).status_code==403


def test_administrator_has_no_private_document_or_workspace_rights():
    c,_,_=owner();private=note(c);g,identity=administrator(c)
    assert g.get('/api/notes/'+private).status_code==404
    assert all(n['id']!=private for n in g.get('/api/notes').json())
    assert g.post('/api/security/2fa/disable',json={'password':'Friend-password-123','code':'000000'}).status_code==403
    assert g.post('/api/account/deletion',json={'password':'Friend-password-123','confirmation':'friend'}).status_code==409
    assert c.post('/api/admin/users/delete',json={'user_id':identity,'confirmation':'friend'}).status_code==409


def test_administrator_reset_requires_reenrollment_and_backup_preserves_roles():
    c,_,_=owner();g,identity=administrator(c)
    body=backup(c)
    assert c.post('/api/admin/backups/restore',content=body,headers=headers()).status_code==200
    with m.db() as db:
        assert db.execute('SELECT role FROM users WHERE id=?',(identity,)).fetchone()[0]=='admin'
        assert not db.execute('SELECT 1 FROM sessions').fetchone()
    # Login owner with a recovery code, then reset the administrator's factor.
    r=c.post('/api/login',json={'login':'owner','password':'Owner-password-123'});c.headers['x-csrf-token']=r.json()['csrf']
    from tests.test_admin_access import unlock
    with m.db() as db:
        db.execute('INSERT INTO recovery_codes VALUES(?,?)',(uid_from_db(db,'owner'),m.digest('OWNER-ROLE-RECOVERY')))
    r=c.post('/api/2fa/verify',json={'code':'owner-role-recovery'});c.headers['x-csrf-token']=r.json()['csrf'];unlock(c)
    assert c.post('/api/admin/users/'+identity+'/reset-2fa',json={}).status_code==200
    r=g.post('/api/login',json={'login':'friend','password':'Friend-password-123'})
    g.headers['x-csrf-token']=r.json()['csrf'];assert r.json()['needs_enrollment']
    assert g.post('/api/2fa/skip').status_code==403
    assert g.get('/api/admin/users').status_code==403
    # A backup during enforced reenrollment remains restorable and cannot grant access.
    body=backup(c)
    assert c.post('/api/admin/backups/restore',content=body,headers=headers()).status_code==200


def uid_from_db(db,login):
    return db.execute('SELECT id FROM users WHERE login=?',(login,)).fetchone()[0]


def test_revoked_manager_is_rechecked_inside_write_transaction():
    from app.site_roles import managed_user, current_manager
    from app.http import Request, HTTPError
    c,_,_=owner();g,identity=administrator(c);member(c,'ordinary');target=uid(c,'ordinary')
    request=Request({'method':'GET','path':'/api/admin/users'})
    request.cookies=dict(g.cookies.items())
    captured=m.administrator(request)
    assert role(c,identity,'member','admin').status_code==200
    with m.db() as db:
        db.execute('BEGIN IMMEDIATE')
        with pytest.raises(HTTPError):current_manager(db,captured,m.now(),m.fail)
        with pytest.raises(HTTPError):managed_user(db,target,captured,m.now(),m.fail)


@pytest.mark.parametrize('action', ['password', 'reset-2fa'])
def test_administrator_password_and_factor_reset_return_success_after_member_update(action):
    c, _, _ = owner()
    admin, _ = administrator(c)
    participant, _ = member(c, 'ordinary')
    target = uid(c, 'ordinary')
    page = note(participant)
    if action == 'password':
        response = admin.put('/api/admin/users/' + target + '/password', json={'password': 'Replacement-member-password-123'})
    else:
        response = admin.post('/api/admin/users/' + target + '/reset-2fa', json={})
    assert response.status_code == 200, response.text
    assert response.json() == {'ok': True, 'can_manage': True, 'current': False}
    assert participant.get('/api/me').status_code == 401
    assert admin.get('/api/admin/users').status_code == 200
    with m.db() as db:
        row = db.execute('SELECT password,totp,role FROM users WHERE id=?', (target,)).fetchone()
        assert row['role'] == 'member'
        assert db.execute('SELECT 1 FROM notes WHERE id=? AND user_id=?', (page, target)).fetchone()
        assert not db.execute('SELECT 1 FROM sessions WHERE user_id=?', (target,)).fetchone()
        if action == 'password':
            assert row['totp'] and m.check_password('Replacement-member-password-123', row['password'])
        else:
            assert row['totp'] is None and m.check_password('Friend-password-123', row['password'])
