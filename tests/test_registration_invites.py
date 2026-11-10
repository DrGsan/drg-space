import json
import secrets
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.test_security import m,owner,member,client,reset,enroll
from tests.test_site_roles import administrator,role
from tests.test_admin import backup,headers
from app import registration_invites as invites


def create(c,friend=False,login=None):
    result=c.post('/api/invites',json={'add_creator_as_friend':friend,**({'login':login} if login else {})})
    assert result.status_code==200,result.text
    return result.json()


def register(link,name='new_member'):
    guest=client();result=guest.post('/api/register',json={'login':name,'password':'Friend-password-123','invite':link['url'].split('/')[-1]})
    assert result.status_code==200,result.text
    guest.headers['x-csrf-token']=result.json()['csrf']
    return guest,guest.get('/api/me').json()['id']


def skip(guest):
    result=guest.post('/api/2fa/skip');assert result.status_code==200,result.text
    guest.headers['x-csrf-token']=result.json()['csrf'];return result.json()


def assert_pending(uid,exists):
    with m.db() as c:
        assert bool(invites.read(c,invites.PENDING+uid))==exists


def test_independent_links_default_off_and_concurrent_creation():
    c,_,_=owner();first=create(c);second=create(c)
    def one(_):
        other=client();other.cookies.update(c.cookies);other.headers['x-csrf-token']=c.headers['x-csrf-token'];return create(other)
    with ThreadPoolExecutor(max_workers=3) as pool:links=[first,second,*pool.map(one,range(3))]
    assert len({x['url'] for x in links})==5
    assert all(not x['add_creator_as_friend'] and x['expires']-x['created']==7*86400 for x in links)
    listing=c.get('/api/invites').json()['invitations'];assert {x['id'] for x in links}=={x['id'] for x in listing}
    guest,uid=register(first);assert_pending(uid,False);assert 'invitation_friendship' not in skip(guest)
    assert c.delete('/api/invites/'+second['id']).status_code==200
    for x in links[2:]:assert client().get('/api/invites/'+x['url'].split('/')[-1]).status_code==200
    for index,x in enumerate(links[2:]):
        another,_=register(x,'independent_'+str(index));skip(another)
    assert c.get('/api/invites').json()['invitations']==[]
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM invites').fetchone()
        assert not db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?', (invites.PREFIX+'%',)).fetchone()
    assert client().get('/api/invites/'+first['url'].split('/')[-1]).status_code==404
    assert client().get('/api/invites').status_code==401
    assert guest.get('/api/invites').status_code==403
    assert c.post('/api/invites',json={'add_creator_as_friend':'false'}).status_code==422


def test_creator_identity_is_scoped_to_each_admin_and_friendship_deferred():
    c,_,_=owner();a,aid=administrator(c,'admin_one');b,bid=administrator(c,'admin_two')
    link_a=create(a,True);link_b=create(b,True)
    assert [x['id'] for x in a.get('/api/invites').json()['invitations']]==[link_a['id']]
    assert b.delete('/api/invites/'+link_a['id']).status_code==404
    info=client().get('/api/invites/'+link_b['url'].split('/')[-1]).json()
    assert info['friend']['id']==bid and info['friend']['login']=='admin_two'
    guest,uid=register(link_a);assert_pending(uid,True)
    with m.db() as db:assert not db.execute('SELECT 1 FROM friendships WHERE first_id=? OR second_id=?',(uid,uid)).fetchone()
    assert a.get('/api/invites').json()['invitations']==[]
    with m.db() as db:
        assert not invites.read(db,invites.PREFIX+link_a['id'])
        assert not db.execute('SELECT 1 FROM invites WHERE token=?',(link_a['id'],)).fetchone()
    # Admin privileges are unrelated to the creator's consent to friendship.
    assert role(c,aid,'member','admin').status_code==200
    assert skip(guest)['invitation_friendship']=={'status':'created'};assert_pending(uid,False)
    with m.db() as db:
        actual=db.execute('SELECT first_id,second_id FROM friendships').fetchall();assert [tuple(x) for x in actual]==[tuple(sorted((aid,uid)))]
    assert guest.post('/api/2fa/skip').status_code==409
    other,oid=register(link_b,'second_member');enroll(other,'Friend-password-123');assert_pending(oid,False)
    with m.db() as db:assert db.execute('SELECT 1 FROM friendships WHERE first_id=? AND second_id=?',tuple(sorted((bid,oid)))).fetchone()


def test_pending_revocation_is_independent_and_does_not_remove_new_account():
    c,_,_=owner();link=create(c,True);keep=create(c,True);guest,uid=register(link)
    assert c.delete('/api/invites/'+link['id']).status_code==200
    assert c.delete('/api/invites/'+link['id']).status_code==200
    result=skip(guest)['invitation_friendship'];assert result['status']=='unavailable' and result['reason']=='revoked' and result['message'];assert_pending(uid,False)
    assert guest.get('/api/notes').status_code==200
    assert client().get('/api/invites/'+keep['url'].split('/')[-1]).status_code==200
    with m.db() as db:assert not db.execute('SELECT 1 FROM friendships').fetchone()


@pytest.mark.parametrize('deleted',[False,True])
def test_unavailable_creator_does_not_trap_enrollment(deleted):
    c,_,_=owner();admin,aid=administrator(c,'other_admin');link=create(admin,True);guest,uid=register(link)
    assert role(c,aid,'member','admin').status_code==200
    assert c.post('/api/admin/users/delete',json={'user_id':aid,'confirmation':'other_admin'}).status_code==200
    if deleted:
        from tests.deletion_helpers import finish_account
        finish_account(aid)
    guest.headers['accept-language']='en'
    result=skip(guest)['invitation_friendship'];assert result['reason']=='creator_unavailable' and result['message'].startswith('Registration complete.');assert_pending(uid,False)
    with m.db() as db:assert not db.execute('SELECT 1 FROM friendships').fetchone()


def test_friend_limit_skips_only_friendship_and_removes_pending():
    from app.modules.social import ensure_friendship
    c,_,_=owner();creator=c.get('/api/me').json()['id'];guest,uid=register(create(c,True))
    with m.db() as db:
        db.execute('BEGIN IMMEDIATE')
        for i in range(200):
            target=secrets.token_hex(16)
            db.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',(target,'limit_'+str(i),'test-hash','member','{}',m.now()))
            ensure_friendship(db,creator,target,m.now(),m.fail)
    assert skip(guest)['invitation_friendship']['reason']=='friend_limit';assert_pending(uid,False)
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM friendships').fetchone()[0]==200


def test_database_failure_rolls_back_final_enrollment_and_pending(monkeypatch):
    import app.modules.social as social
    c,_,_=owner();guest,uid=register(create(c,True));original=social.ensure_friendship
    def broken(db,*args):
        original(db,*args)
        raise RuntimeError('simulated storage failure')
    monkeypatch.setattr(social,'ensure_friendship',broken)
    assert guest.post('/api/2fa/skip').status_code==500
    assert_pending(uid,True)
    assert guest.get('/api/me').json()['needs_enrollment']
    with m.db() as db:assert not db.execute('SELECT 1 FROM friendships').fetchone()
    monkeypatch.setattr(social,'ensure_friendship',original)
    assert skip(guest)['invitation_friendship']['status']=='created'


def test_legacy_hash_only_links_and_expiry_unchanged(monkeypatch):
    c,_,_=owner();token=secrets.token_urlsafe(32)
    with m.db() as db:db.execute('INSERT INTO invites VALUES(?,?,?,0)',(m.digest(token),None,m.now()+7*86400))
    info=client().get('/api/invites/'+token).json();assert info['friend'] is None and not info['add_creator_as_friend']
    assert c.get('/api/invites').json()['invitations']==[]
    guest,uid=register({'url':'/invite/'+token});assert 'invitation_friendship' not in skip(guest)
    link=create(c,True);stamp=link['expires'];monkeypatch.setattr(m,'now',lambda:stamp)
    assert client().get('/api/invites/'+link['url'].split('/')[-1]).status_code==404
    assert client().post('/api/register',json={'login':'expired','password':'Friend-password-123','invite':link['url'].split('/')[-1]}).status_code==403


def test_pending_consent_stays_revocable_after_original_link_expiry():
    from app.retention import purge_service_records
    c,_,_=owner();link=create(c,True);guest,uid=register(link)
    with m.db() as db:
        purge_service_records(db,link['expires']+1)
        assert not invites.read(db,invites.PREFIX+link['id'])
        pending=invites.read(db,invites.PENDING+uid)
        assert set(pending)=={'version','creator','invite','created','revoked'}
        assert link['url'].split('/')[-1] not in json.dumps(pending)
        invites.revoke(db,link['id'],pending['creator'],link['expires']+1,m.fail)
        result=invites.complete(db,uid,link['expires']+1,m.fail)
        assert result['reason']=='revoked'
        purge_service_records(db,link['expires']+1)
        assert not invites.read(db,invites.PREFIX+link['id'])


def test_full_encrypted_backup_restores_pending_and_links_personal_archive_excludes_them():
    from tests.test_personal_data import export,unseal,restore
    c,_,_=owner();active=create(c,True);pending=create(c,True);guest,uid=register(pending)
    personal=export(c);manifest,_=unseal(personal);raw=json.dumps(manifest)
    assert active['url'].split('/')[-1] not in raw and invites.PREFIX not in raw and invites.PENDING not in raw
    body=backup(c);assert active['url'].split('/')[-1].encode() not in body
    with m.db() as db:
        db.execute('DELETE FROM app_settings WHERE key LIKE ? OR key LIKE ?',(invites.PREFIX+'%',invites.PENDING+'%'))
        db.execute('DELETE FROM invites')
    result=c.post('/api/admin/backups/restore',content=body,headers=headers());assert result.status_code==200,result.text
    assert client().get('/api/invites/'+active['url'].split('/')[-1]).status_code==200;assert_pending(uid,True)
    fresh=client();result=fresh.post('/api/login',json={'login':'new_member','password':'Friend-password-123'});assert result.status_code==200;fresh.headers['x-csrf-token']=result.json()['csrf']
    assert skip(fresh)['invitation_friendship']['status']=='created'
    with m.db() as db:before=[tuple(x) for x in db.execute('SELECT * FROM friendships')];metadata=[tuple(x) for x in db.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (invites.PREFIX+'%',))]
    result=restore(fresh,personal,'Friend-password-123');assert result.status_code==200,result.text
    with m.db() as db:
        assert [tuple(x) for x in db.execute('SELECT * FROM friendships')]==before
        assert [tuple(x) for x in db.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (invites.PREFIX+'%',))]==metadata


def test_existing_friendship_completes_without_duplicate_space():
    from app.modules.social import ensure_friendship
    c,_,_=owner();creator=c.get('/api/me').json()['id'];guest,uid=register(create(c,True))
    with m.db() as db:original=ensure_friendship(db,creator,uid,m.now(),m.fail)
    assert skip(guest)['invitation_friendship']['status']=='already_friends';assert_pending(uid,False)
    with m.db() as db:
        assert [row['space_id'] for row in db.execute('SELECT space_id FROM friendships')]==[original]
        assert db.execute("SELECT COUNT(*) FROM spaces WHERE kind='friends'").fetchone()[0]==1


def test_backup_validation_rejects_mismatched_pending_friendship_target():
    from app.http import HTTPError
    c,_,_=owner();link=create(c,True);guest,uid=register(link)
    with m.db() as db:
        invites.validate_archive(db,m.fail)
        pending=invites.read(db,invites.PENDING+uid);pending['creator']=uid
        invites.write(db,invites.PENDING+uid,pending)
        with pytest.raises(HTTPError):invites.validate_archive(db,m.fail)


@pytest.mark.parametrize('entrypoint',['public','register','list'])
def test_expiry_boundary_physically_removes_ticket_and_metadata(entrypoint,monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,_,_=owner();ending=create(c,True);keep=create(c)
    boundary=stamp+1
    with m.db() as db:
        value=invites.read(db,invites.PREFIX+ending['id']);value.update(created=boundary-invites.SECONDS,expires=boundary)
        invites.write(db,invites.PREFIX+ending['id'],value);db.execute('UPDATE invites SET expires=? WHERE token=?',(boundary,ending['id']))
    token=ending['url'].split('/')[-1]
    assert client().get('/api/invites/'+token).status_code==200
    stamp=boundary
    if entrypoint=='public':assert client().get('/api/invites/'+token).status_code==404
    elif entrypoint=='register':assert client().post('/api/register',json={'login':'too_late','password':'Friend-password-123','invite':token}).status_code==403
    else:assert [item['id'] for item in c.get('/api/invites').json()['invitations']]==[keep['id']]
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM invites WHERE token=?',(ending['id'],)).fetchone()
        assert not invites.read(db,invites.PREFIX+ending['id'])
    assert client().get('/api/invites/'+token).status_code==404
    assert client().post('/api/register',json={'login':'too_late','password':'Friend-password-123','invite':token}).status_code==403
    assert client().get('/api/invites/'+keep['url'].split('/')[-1]).status_code==200


def test_read_reconciles_rc1_history_without_deleting_active_legacy_or_workspace():
    from app.workspace_lifecycle import INVITE_PREFIX
    c,_,_=owner();creator=c.get('/api/me').json()['id'];active=create(c)
    space=c.post('/api/social/spaces',json={'name':'Preserved space'}).json()['id']
    workspace=c.post('/api/social/spaces/'+space+'/invite-links',json={}).json()
    legacy=secrets.token_urlsafe(32);inactive=[]
    with m.db() as db:
        db.execute('INSERT INTO invites VALUES(?,?,?,0)',(m.digest(legacy),None,m.now()+invites.SECONDS))
        for status in ('used','revoked','expired','missing'):
            token=secrets.token_urlsafe(32);identity=m.digest(token);inactive.append(identity)
            value=invites.remember(db,token,creator,None,False,m.now()-invites.SECONDS if status=='expired' else m.now())
            if status=='used':value.update(status='used',used_at=m.now())
            if status=='revoked':value.update(status='revoked',revoked_at=m.now())
            invites.write(db,invites.PREFIX+identity,value)
            if status in ('used','expired'):db.execute('INSERT INTO invites VALUES(?,?,?,?)',(identity,None,value['expires'],int(status=='used')))
    assert [item['id'] for item in c.get('/api/invites').json()['invitations']]==[active['id']]
    with m.db() as db:
        for identity in inactive:
            assert not invites.read(db,invites.PREFIX+identity)
            assert not db.execute('SELECT 1 FROM invites WHERE token=?',(identity,)).fetchone()
        assert invites.read(db,INVITE_PREFIX+workspace['id'])
        assert {r['token'] for r in db.execute('SELECT token FROM invites')}=={active['id'],m.digest(legacy),workspace['id']}
    assert client().get('/api/invites/'+legacy).status_code==200
    assert client().get('/api/social/invite-links/'+workspace['url'].split('/')[-1]).status_code==200


def test_rc1_encrypted_backup_cleans_history_before_returning_restored_data(monkeypatch):
    c,_,_=owner();active=create(c);used=create(c,True);revoked=create(c)
    with m.db() as db:
        old_used=invites.read(db,invites.PREFIX+used['id']);old_revoked=invites.read(db,invites.PREFIX+revoked['id'])
    guest,uid=register(used);assert c.delete('/api/invites/'+revoked['id']).status_code==200
    with m.db() as db:
        old_used.update(status='used',used_at=invites.read(db,invites.PENDING+uid)['created'])
        old_revoked.update(status='revoked',revoked_at=m.now())
        invites.write(db,invites.PREFIX+used['id'],old_used);invites.write(db,invites.PREFIX+revoked['id'],old_revoked)
        invites.validate_archive(db,m.fail)
    # Emulate rc.1 export, which retained used/revoked metadata until expiry.
    with monkeypatch.context() as patch:
        patch.setattr(invites,'purge',lambda *args:None)
        body=backup(c)
    result=c.post('/api/admin/backups/restore',content=body,headers=headers());assert result.status_code==200,result.text
    with m.db() as db:
        assert {row['key'] for row in db.execute('SELECT key FROM app_settings WHERE key LIKE ?',(invites.PREFIX+'%',))}=={invites.PREFIX+active['id']}
        assert {row['token'] for row in db.execute('SELECT token FROM invites')}=={active['id']}
        assert invites.read(db,invites.PENDING+uid)['creator']==old_used['creator']
    fresh=client();result=fresh.post('/api/login',json={'login':'new_member','password':'Friend-password-123'});fresh.headers['x-csrf-token']=result.json()['csrf']
    assert skip(fresh)['invitation_friendship']['status']=='created'


def test_revoke_racing_registration_removes_link_and_cancels_pending_consent():
    from threading import Barrier
    c,_,_=owner();link=create(c,True);guest=client();gate=Barrier(2)
    def consume():
        gate.wait();return guest.post('/api/register',json={'login':'racing_member','password':'Friend-password-123','invite':link['url'].split('/')[-1]})
    def revoke():
        gate.wait();return c.delete('/api/invites/'+link['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        registered=pool.submit(consume);revoked=pool.submit(revoke)
        registration=registered.result();revocation=revoked.result()
    assert revocation.status_code==200,revocation.text
    assert registration.status_code in (200,403),registration.text
    if registration.status_code==200:
        guest.headers['x-csrf-token']=registration.json()['csrf'];assert skip(guest)['invitation_friendship']['reason']=='revoked'
    with m.db() as db:
        assert not invites.read(db,invites.PREFIX+link['id'])
        assert not db.execute('SELECT 1 FROM invites WHERE token=?',(link['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM friendships').fetchone()
        assert not db.execute('SELECT 1 FROM app_settings WHERE key LIKE ?',(invites.PENDING+'%',)).fetchone()
