"""Ready-to-use managed accounts, bounded admin membership and atomic avatars."""
import base64,json
import pytest
from tests.test_security import m,owner,member,client,reset,enroll
from tests.test_admin import proof
from tests.test_social import family,join,enabled
from tests.test_widgets import image_bytes
from tests.test_next_features import ok


def create(c,**fields):
    return c.post('/api/admin/users',json=proof(**({'login':'grandma','password':'Grandma-password-123','display_name':'Бабушка Нина'}|fields)))


def test_admin_last_activity_survives_logout_and_older_visits_without_contact_access(monkeypatch):
    c,_,_=owner();g,_=member(c);uid=ok(g.get('/api/me'))['id']
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    def admin_profile():return next(u['profile'] for u in ok(c.get('/api/admin/users'))['users'] if u['id']==uid)
    # Authenticated requests already record device activity; explicit presence
    # refreshes that timestamp rather than creating the first visit.
    first_seen=admin_profile()['last_seen']
    assert isinstance(first_seen,int) and 0<=stamp-first_seen<90
    assert g.get('/api/presence?scope=admin').status_code==403
    assert client().get('/api/presence?scope=admin').status_code==401
    assert c.get('/api/presence?scope=unknown').status_code==422
    assert uid not in {u['id'] for u in ok(c.get('/api/presence'))['users']}
    assert g.post('/api/presence').status_code==200
    assert admin_profile()['online'] is True and admin_profile()['last_seen']==stamp
    assert g.post('/api/logout').status_code==200
    assert admin_profile()['online'] is False and admin_profile()['last_seen']==stamp
    old=stamp-10*86400
    with m.db() as db:db.execute('UPDATE user_presence SET last_seen=? WHERE user_id=?',(old,uid))
    profile=admin_profile();assert profile['last_seen']==old and profile['online'] is False
    live=next(u for u in ok(c.get('/api/presence?scope=admin'))['users'] if u['id']==uid)
    assert live['last_seen']==old and live['online'] is False
    # The ordinary contact profile retains its existing one-day boundary.
    from app.notifications import actor
    with m.db() as db:assert actor(db,uid,stamp)['last_seen'] is None


def test_create_ready_account_profile_avatar_friend_and_family_without_invites():
    c,_,_=owner();sid=family(c);before=c.get('/api/me').json()['id']
    result=ok(create(c,add_friend=True,family_ids=[sid],avatar=base64.b64encode(image_bytes()).decode()));uid=result['id']
    assert result['profile']['display_name']=='Бабушка Нина' and result['profile']['avatar_url']
    assert c.get('/api/me').json()['id']==before  # Never sign the admin into the new account.
    g=client();login=ok(g.post('/api/login',json={'login':'grandma','password':'Grandma-password-123'}));g.headers['x-csrf-token']=login['csrf']
    assert not login['needs_2fa'] and not login['needs_enrollment']
    me=ok(g.get('/api/me'));assert me['role']=='member' and me['settings']['profile']['display_name']=='Бабушка Нина'
    assert g.get('/api/account/avatar').headers['content-type'].startswith('image/webp')
    rooms=ok(g.get('/api/chat/rooms'))['items'];assert {s['kind'] for s in rooms}=={'friends','family'}
    assert next(s for s in rooms if s['kind']=='family')['id']==sid
    assert ok(g.get('/api/social'))['requests']==[]
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM social_requests').fetchone()[0]==0
        stored=db.execute('SELECT password FROM users WHERE id=?',(uid,)).fetchone()[0];assert stored!='Grandma-password-123' and m.check_password('Grandma-password-123',stored)
        aid=me['settings']['profile']['avatar_id'];assert db.execute('SELECT user_id FROM media WHERE id=?',(aid,)).fetchone()[0]==uid
    assert g.get('/api/admin/users').status_code==403
    row=next(u for u in ok(c.get('/api/admin/users'))['users'] if u['id']==uid)
    assert row['friend'] and row['families']==[sid]
    # The user can opt into ordinary 2FA later using their own password.
    enroll(g,'Grandma-password-123')
    with m.db() as db:assert db.execute('SELECT totp FROM users WHERE id=?',(uid,)).fetchone()[0]


def test_admin_profile_connections_are_additive_idempotent_and_keep_security():
    c,_,_=owner();g,_=member(c);uid=ok(g.get('/api/me'))['id'];sid=family(c)
    before=ok(g.get('/api/me'));data=proof(display_name='Дедушка',family_ids=[sid],add_friend=True,avatar=base64.b64encode(image_bytes()).decode())
    first=ok(c.put('/api/admin/users/'+uid+'/profile',json=data));old=first['profile']['avatar_url']
    data.pop('avatar');ok(c.put('/api/admin/users/'+uid+'/profile',json=data))
    after=ok(g.get('/api/me'));assert after['settings']['profile']['display_name']=='Дедушка'
    assert after['settings']['profile']['avatar_id'] in old
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM friendships').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM space_members WHERE user_id=?',(uid,)).fetchone()[0]==2
        assert db.execute('SELECT totp FROM users WHERE id=?',(uid,)).fetchone()[0]
    # Omitting relationship checkboxes never removes existing membership.
    ok(c.put('/api/admin/users/'+uid+'/profile',json=proof(display_name='Новое имя',remove_avatar=True)))
    assert g.get('/api/account/avatar').status_code==404
    assert len(ok(g.get('/api/chat/rooms'))['items'])==2
    assert c.get(old).status_code==404


def test_validation_permissions_and_failures_do_not_create_partial_accounts():
    c,_,_=owner();g,_=member(c);foreign=family(g);owned=family(c)
    assert g.post('/api/admin/users',json=proof(login='grandma',password='Grandma-password-123')).status_code==403
    assert client().post('/api/admin/users',json=proof(login='grandma',password='Grandma-password-123')).status_code==401
    assert c.post('/api/admin/users',json={'admin_password':'wrong','login':'grandma','password':'Grandma-password-123'}).status_code==403
    for fields in ({'family_ids':[owned,foreign]},{'family_ids':[owned,owned]},{'avatar':'not base64'},{'avatar':base64.b64encode(b'not image').decode()},{'avatar':'','remove_avatar':True},{'role':'owner'}):
        assert create(c,**fields).status_code in (403,422)
        with m.db() as db:
            assert not db.execute("SELECT 1 FROM users WHERE login='grandma'").fetchone()
            assert db.execute('SELECT COUNT(*) FROM media').fetchone()[0]==0
    for login,password in [('bad login','Grandma-password-123'),('ab','Grandma-password-123'),('valid','short')]:
        assert c.post('/api/admin/users',json=proof(login=login,password=password)).status_code==422
    ok(create(c));assert create(c).status_code==409
    del c.headers['x-csrf-token'];assert create(c).status_code==403


def test_existing_profile_and_family_changes_roll_back_together_and_keep_pending_requests():
    c,_,_=owner();g,_=member(c);uid=ok(g.get('/api/me'))['id'];sid=family(c);foreign=family(g)
    before_profile=ok(g.get('/api/me'))['settings']['profile']
    q=ok(c.post('/api/social/families/'+sid+'/invites',json={'login':'friend'}));friend=ok(c.post('/api/social/friends',json={'login':'friend'}))
    assert c.put('/api/admin/users/'+uid+'/profile',json=proof(display_name='Не сохранять',add_friend=True,family_ids=[sid,foreign])).status_code==403
    with m.db() as db:
        assert json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])['profile']==before_profile
        assert db.execute('SELECT COUNT(*) FROM friendships').fetchone()[0]==0
        assert not db.execute('SELECT 1 FROM space_members WHERE user_id=? AND space_id=?',(uid,sid)).fetchone()
        assert db.execute('SELECT COUNT(*) FROM social_requests').fetchone()[0]==2
    ok(c.put('/api/admin/users/'+uid+'/profile',json=proof(display_name='Готово',add_friend=True,family_ids=[sid])))
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM social_requests').fetchone()[0]==0
    assert g.post('/api/social/requests/'+q['id']+'/accept').status_code==404
    assert g.post('/api/social/requests/'+friend['id']+'/accept').status_code==404
    assert c.put('/api/admin/users/'+'0'*32+'/profile',json=proof(display_name='Нет')).status_code==404


def test_avatar_failure_rolls_back_user_relationships_and_written_blob(monkeypatch):
    c,_,_=owner();sid=family(c)
    from app.database import Connection
    from app.http import HTTPError
    original=Connection.execute
    before={p.name for p in (m.DATA/'media').glob('*')}
    def failing(self,query,parameters=()):
        if query=='UPDATE users SET settings=? WHERE id=?' and json.loads(parameters[0]).get('profile',{}).get('avatar_id'):
            raise HTTPError(413,'Simulated quota rejection')
        return original(self,query,parameters)
    monkeypatch.setattr(Connection,'execute',failing)
    assert create(c,add_friend=True,family_ids=[sid],avatar=base64.b64encode(image_bytes()).decode()).status_code==413
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM friendships').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM media').fetchone()[0]==0
    assert {p.name for p in (m.DATA/'media').glob('*')}==before


def test_normalization_family_limits_and_unmanaged_options():
    c,_,_=owner();g,_=member(c);foreign=family(g);owned=family(c)
    assert {f['id'] for f in ok(c.get('/api/admin/users'))['families']}=={owned}
    uid=ok(create(c,login=' GRANDMA '))['id'];assert next(u for u in ok(c.get('/api/admin/users'))['users'] if u['id']==uid)['login']=='grandma'
    with m.db() as db:
        for index in range(20):
            sid=__import__('secrets').token_urlsafe(12)
            db.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',(sid,'family','Limit',uid,m.now()))
            db.execute('INSERT INTO space_members VALUES(?,?,?)',(sid,uid,m.now()))
    assert c.put('/api/admin/users/'+uid+'/profile',json=proof(display_name='Лимит',family_ids=[owned])).status_code==422
    with m.db() as db:assert json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])['profile']['display_name']=='Бабушка Нина'


def test_managed_account_full_backup_keeps_ready_login_avatar_and_membership():
    from tests.test_admin import backup,headers
    c,_,_=owner();sid=family(c);created=ok(create(c,add_friend=True,family_ids=[sid],avatar=base64.b64encode(image_bytes()).decode()))
    archive=backup(c);assert ok(c.post('/api/admin/backups/restore',content=archive,headers=headers()))['login']=='owner'
    g=client();login=ok(g.post('/api/login',json={'login':'grandma','password':'Grandma-password-123'}));g.headers['x-csrf-token']=login['csrf']
    assert not login['needs_enrollment'] and not login['needs_2fa']
    assert ok(g.get('/api/me'))['id']==created['id']
    assert g.get('/api/account/avatar').status_code==200
    assert {r['kind'] for r in ok(g.get('/api/chat/rooms'))['items']}=={'friends','family'}


def test_setting_password_revokes_existing_reset_links_without_disabling_2fa():
    c,_,_=owner();g,_=member(c);uid=ok(g.get('/api/me'))['id']
    link=ok(c.post('/api/admin/users/'+uid+'/password-reset',json=proof()))['url'].split('/')[-1]
    ok(c.put('/api/admin/users/'+uid+'/password',json=proof(password='Changed-grandma-password-123')))
    assert g.get('/api/me').status_code==401
    assert client().post('/api/password-reset',json={'token':link,'password':'Another-new-password-123'}).status_code==403
    assert ok(client().post('/api/login',json={'login':'friend','password':'Changed-grandma-password-123'}))['needs_2fa']


def test_account_table_has_security_storage_and_scoped_workspace_roles():
    from tests.test_workspace_teams import setup,config,update,T1
    c,a,b,d,sid,ids=setup()
    value=config(c,sid);value['admins']=[ids[1]];update(c,sid,value)
    result=ok(c.get('/api/admin/users'))
    user=next(u for u in result['users'] if u['id']==ids[1])
    assert user['role']=='member' and user['role_label']=='Пользователь'
    space=next(s for s in user['workspaces'] if s['id']==sid)
    assert space['role']=='admin' and space['template']=='organization'
    assert 'Продажи' in space['teams']
    assert sid in {s['id'] for s in result['workspaces']}
    assert user['sessions']>=1 and user['passkeys']==0 and user['file_count']==0
    assert not {'password','totp','settings'}&set(user)
    assert a.get('/api/admin/users').status_code==403
