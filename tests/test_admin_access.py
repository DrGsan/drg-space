from tests.test_security import m,owner,member,client,reset,enroll
from app.admin_access import AdminAccess,COOKIE,IDLE_SECONDS,PREFIX


def lock(c):
    assert c.post('/api/admin/access/lock').status_code==200


def unlock(c):
    r=c.post('/api/admin/access/unlock',json={'password':'Owner-password-123'})
    assert r.status_code==200,r.text
    return r.json()


def test_admin_entry_is_required_for_reads_writes_and_photo_access(monkeypatch):
    c,_,_=owner();g,_=member(c);lock(c)
    for path in ('/api/admin/users','/api/admin/registration','/api/family-photos/admin','/api/presence?scope=admin'):
        r=c.get(path);assert r.status_code==423,path
        assert r.headers['x-admin-unlock']=='required'
        assert g.get(path).status_code==403 and client().get(path).status_code==401
    assert c.put('/api/admin/registration',json={'enabled':True}).status_code==423
    assert c.post('/api/invites',json={}).status_code==423
    assert c.get('/api/notes').status_code==200  # no account-wide logout
    assert c.post('/api/admin/access/unlock',json={'password':'wrong'}).status_code==403
    assert not c.get('/api/admin/access').json()['unlocked']
    assert g.post('/api/admin/access/unlock',json={'password':'Friend-password-123'}).status_code==403
    unlock(c)
    assert c.get('/api/admin/users').status_code==200
    assert c.put('/api/admin/registration',json={'enabled':True}).status_code==200


def test_idle_expiry_is_server_side_and_get_polls_do_not_extend_it(monkeypatch):
    c,_,_=owner();stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    unlocked=unlock(c)
    assert unlocked['expires_at']==stamp+IDLE_SECONDS
    for _ in range(3):
        stamp+=200
        assert c.get('/api/admin/users').status_code==200
        assert c.get('/api/admin/access').json()['expires_at']==unlocked['expires_at']
    stamp+=300
    assert c.post('/api/refresh').status_code==200
    r=c.put('/api/admin/registration',json={'enabled':True});assert r.status_code==423
    assert c.post('/api/admin/access/activity').status_code==423
    assert not c.get('/api/admin/access').json()['unlocked']
    assert c.get('/api/notes').status_code==200


def test_only_explicit_activity_extends_grant_and_other_workers_share_state(monkeypatch):
    c,_,_=owner();stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    unlock(c);stamp+=600
    state=c.post('/api/admin/access/activity').json();assert state['expires_at']==stamp+900
    stamp+=400;assert c.post('/api/refresh').status_code==200
    assert c.get('/api/admin/users').status_code==200
    # A distinct service instance uses the same persisted server-side grant.
    other=AdminAccess(m.db,m.owner,m.fail,m.protection,m.check_password,lambda:stamp,m.SECURE)
    from app.http import Request
    request=Request({'method':'GET','path':'/api/admin/access'})
    request.cookies=dict(c.cookies.items())
    assert other.state(request,m.owner(request))['unlocked']
    stamp+=500
    assert c.get('/api/admin/users').status_code==423


def test_unlock_cookie_and_live_account_session_are_both_required(monkeypatch):
    c,secret,codes=owner();unlock(c)
    response=c.post('/api/admin/access/unlock',json={'password':'Owner-password-123'})
    cookie='; '.join(response.headers.get_list('set-cookie'))
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie and 'Path=/api' in cookie
    raw=c.cookies.get(COOKIE)
    assert raw
    with m.db() as db:
        stored=db.execute('SELECT value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)).fetchone()[0]
        assert raw not in stored and 'Owner-password-123' not in stored
    for cookie in c.cookies.jar:
        if cookie.name==COOKIE:cookie.value='tampered'
    assert c.get('/api/admin/users').status_code==423
    unlock(c)
    # A separate login to the same owner account must unlock independently.
    g=client();r=g.post('/api/login',json={'login':'owner','password':'Owner-password-123'})
    g.headers['x-csrf-token']=r.json()['csrf']
    verified=g.post('/api/2fa/verify',json={'code':codes[0]});assert verified.status_code==200,verified.text
    g.headers['x-csrf-token']=verified.json()['csrf']
    assert g.get('/api/admin/users').status_code==423
    unlock(g);assert g.get('/api/admin/users').status_code==200
    assert c.get('/api/admin/users').status_code==200
    with m.db() as db:
        db.execute("UPDATE users SET password=? WHERE login='owner'",(m.ph.hash('New-owner-password-123'),))
    assert c.get('/api/admin/users').status_code==423
    assert g.get('/api/admin/users').status_code==423


def test_unlock_requires_csrf_and_logout_revokes_current_grant():
    c,_,_=owner();lock(c);csrf=c.headers.pop('x-csrf-token')
    assert c.post('/api/admin/access/unlock',json={'password':'Owner-password-123'}).status_code==403
    c.headers['x-csrf-token']=csrf;unlock(c)
    assert c.post('/api/logout').status_code==200
    assert c.get('/api/admin/users').status_code==401


def test_admin_unlock_grants_are_excluded_from_backup_snapshot():
    c,_,_=owner();unlock(c)
    with m.db() as db:
        assert db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)).fetchone()
    backups=m.admin_router.backups
    with backups.temp() as directory:
        backups.snapshot(directory)
        with backups.snapshot_database(directory) as snapshot:
            assert not snapshot.execute('SELECT 1 FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)).fetchone()
    assert c.get('/api/admin/users').status_code==200
