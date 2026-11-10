import base64,json,time
import pyotp,pytest
from tests.test_security import m,owner,member,client,setup,note,share,reset
from tests.test_widgets import enable
from tests.test_personal_data import export,unseal,headers
from app.auth_tokens import SESSION_SECONDS,ABSOLUTE_SECONDS

def ok(response):
    assert response.status_code==200,response.text
    return response.json()
def skip_member(c,name='guest',hint=''):
    url=ok(c.post('/api/invites',json={}))['url'];g=client()
    r=ok(g.post('/api/register',json={'login':name,'password':'Friend-password-123','invite':url.split('/')[-1],'password_hint':hint}));g.headers['x-csrf-token']=r['csrf']
    r=ok(g.post('/api/2fa/skip'));g.headers['x-csrf-token']=r['csrf'];return g

def event(c):
    enable(c,'expenses');return ok(c.post('/api/expenses',json={'title':'Пятница','participants':[{'id':'me','name':'Саша'},{'id':'unknown','name':'Неизвестный'},{'id':'vova','name':'Вова'},{'id':'pasha','name':'Паша'}]}))
def entry(c,e,amount,allocations,payer='me',kind='expense'):
    return ok(c.post('/api/expenses/'+e['id']+'/entries',json={'amount':amount,'allocations':allocations,'payer':payer,'kind':kind,'category':'beer','event_version':e['version']}))

def test_generic_invite_week_closed_registration_optional_2fa_and_owner_mandatory(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c=client();setup(c);assert c.post('/api/2fa/skip').status_code==403
    from tests.test_security import enroll
    enroll(c);ok(c.post('/api/admin/access/unlock',json={'password':'Owner-password-123'}));stamp=m.now();url=ok(c.post('/api/invites',json={}))
    assert url['expires']==stamp+7*86400
    g=client();assert g.post('/api/register',json={'login':'guest','password':'Friend-password-123'}).status_code==403
    r=ok(g.post('/api/register',json={'login':'chosen.login','password':'Friend-password-123','invite':url['url'].split('/')[-1]}));g.headers['x-csrf-token']=r['csrf']
    assert g.get('/api/notes').status_code==403
    r=ok(g.post('/api/2fa/skip'));g.headers['x-csrf-token']=r['csrf']
    assert not ok(g.get('/api/me'))['two_factor'];assert g.get('/api/notes').status_code==200
    assert client().post('/api/register',json={'login':'third','password':'Friend-password-123','invite':url['url'].split('/')[-1]}).status_code==403
    url2=ok(c.post('/api/invites',json={}));monkeypatch.setattr(m,'now',lambda:url2['expires'])
    assert client().post('/api/register',json={'login':'third','password':'Friend-password-123','invite':url2['url'].split('/')[-1]}).status_code==403

def test_admin_open_registration_and_member_cannot_change_policy():
    c,_,_=owner();g=skip_member(c)
    assert g.put('/api/admin/registration',json={'enabled':True,'admin_password':'Friend-password-123'}).status_code==403
    ok(c.put('/api/admin/registration',json={'enabled':True,'admin_password':'Owner-password-123'}))
    assert client().get('/api/status').json()['registration_open']
    assert client().post('/api/register',json={'login':'public','password':'Friend-password-123'}).status_code==200
    ok(c.put('/api/admin/registration',json={'enabled':False,'admin_password':'Owner-password-123'}))
    assert client().post('/api/register',json={'login':'public2','password':'Friend-password-123'}).status_code==403

def test_reset_link_single_use_clears_2fa_sessions_and_requires_password_rules():
    c,_,_=owner();g,_=member(c);uid=ok(g.get('/api/me'))['id']
    assert g.post('/api/admin/users/'+uid+'/password-reset',json={'admin_password':'Friend-password-123'}).status_code==403
    first=ok(c.post('/api/admin/users/'+uid+'/password-reset',json={'admin_password':'Owner-password-123'}))
    second=ok(c.post('/api/admin/users/'+uid+'/password-reset',json={'admin_password':'Owner-password-123'}));anonymous=client()
    assert anonymous.post('/api/password-reset',json={'token':first['url'].split('/')[-1],'password':'New-password-123'}).status_code==403
    data={'token':second['url'].split('/')[-1],'password':'New-password-123','password_hint':'new hint'}
    assert anonymous.post('/api/password-reset',json={**data,'password':'short'}).status_code==422
    result=ok(anonymous.post('/api/password-reset',json=data));anonymous.headers['x-csrf-token']=result['csrf']
    assert g.get('/api/me').status_code==401
    assert not anonymous.get('/api/me').json()['two_factor'];assert anonymous.get('/api/me').json()['needs_enrollment']
    assert client().post('/api/password-reset',json=data).status_code==403
    result=ok(anonymous.post('/api/2fa/skip'));anonymous.headers['x-csrf-token']=result['csrf'];assert anonymous.get('/api/notes').status_code==200

def test_hint_requires_three_failures_cookie_and_matching_ip_and_not_in_me():
    c,_,_=owner();g=skip_member(c,hint='Памятная дата без самого пароля')
    assert 'password_hint' not in json.dumps(g.get('/api/me').json())
    anonymous=client();assert anonymous.get('/api/password-hint').status_code==401
    for attempt in range(3):
        r=anonymous.post('/api/login',json={'login':'guest','password':'wrong'});assert r.status_code==401
        assert r.json().get('hint_available',False)==(attempt==2)
    assert anonymous.get('/api/password-hint').json()['hint']=='Памятная дата без самого пароля'
    assert client().get('/api/password-hint').status_code==401
    with m.db() as db:db.execute('DELETE FROM auth_failures')
    assert anonymous.get('/api/password-hint').status_code==403

def test_lock_all_mutation_paths_shared_editor_and_session_lease_expiration(monkeypatch):
    c,_,codes=owner();nid=note(c);token=share(c,nid,'public',permission='edit');public=client();edit=public.get('/api/shared/'+token).json()['edit_token']
    ok(c.post('/api/notes/'+nid+'/lock',json={'mode':'lock'}))
    assert c.put('/api/notes/'+nid,json={'title':'changed','body':''}).status_code==423
    assert c.patch('/api/notes/'+nid,json={'icon':'X'}).status_code==423
    assert c.delete('/api/notes/'+nid).status_code==423
    assert public.get('/api/shared/'+token).json()['permission']=='view'
    assert public.put('/api/shared/'+token,headers={'x-share-edit-token':edit},json={'title':'bad','body':'','blocks':[],'version':1}).status_code==423
    assert ok(c.patch('/api/notes/'+nid,json={'favorite':True,'version':1}))['favorite']
    lease=ok(c.post('/api/notes/'+nid+'/lock',json={'mode':'temporary'}));assert lease['locked'] and lease['unlock_until']
    ok(c.put('/api/notes/'+nid,json={'title':'small edit','body':'a'}))
    other=client();r=ok(other.post('/api/login',json={'login':'owner','password':'Owner-password-123'}));other.headers['x-csrf-token']=r['csrf'];r=ok(other.post('/api/2fa/verify',json={'code':codes[0]}));other.headers['x-csrf-token']=r['csrf']
    assert other.put('/api/notes/'+nid,json={'title':'other','body':''}).status_code==423
    stamp=lease['unlock_until']-1;monkeypatch.setattr(m,'now',lambda:stamp);ok(c.post('/api/refresh'));assert c.put('/api/notes/'+nid,json={'title':'last edit','body':''}).status_code==200
    stamp+=2;assert c.post('/api/refresh').status_code==200;assert c.put('/api/notes/'+nid,json={'title':'too late','body':''}).status_code==423
    assert c.post('/api/notes/'+nid+'/lock',json={'mode':'unlock'}).status_code==200
    assert c.put('/api/notes/'+nid,json={'title':'unlocked','body':''}).status_code==200

def test_vault_owner_isolation_seed_encryption_and_gate_with_2fa_disabled():
    c,_,_=owner();g=skip_member(c);enable(c,'authenticator');enable(g,'authenticator');seed=pyotp.random_base32()
    a=ok(c.post('/api/authenticators',json={'label':'Gitea','secret':seed}))
    listing=ok(c.get('/api/authenticators'));assert seed not in json.dumps(listing);assert 'secret' not in listing[0]
    assert ok(c.post('/api/authenticators/'+a['id']+'/reveal'))['code']==pyotp.TOTP(seed).at(m.now())
    assert g.get('/api/authenticators').status_code==403;assert g.post('/api/authenticators/'+a['id']+'/reveal').status_code==403
    with m.db() as db:assert seed not in db.execute('SELECT secret FROM authenticators').fetchone()[0]
    assert c.post('/api/authenticators',json={'label':'bad','secret':'NOT!BASE32'}).status_code==422
    assert c.post('/api/authenticators',json={'label':'bad','secret':'otpauth://hotp/x?secret='+seed}).status_code==422

def test_expense_accounting_custom_split_direct_debt_sharing_and_isolation(monkeypatch):
    c,_,_=owner();g,_=member(c);enable(g,'expenses');e=event(c)
    assert g.get('/api/expenses/'+e['id']).status_code==404
    e=entry(c,e,60000,{'me':30000,'vova':30000})
    e=entry(c,e,30000,{'me':20000,'vova':10000},payer='vova')
    assert e['cost']['me']==50000 and e['paid']['me']==60000 and e['balance']['me']==10000
    assert e['debts']==[{'from':'vova','to':'me','amount':10000}]
    e=entry(c,e,30000,{'me':30000},payer='pasha',kind='debt');assert e['cost']['me']==50000 and e['balance']['me']==-20000
    bad=c.post('/api/expenses/'+e['id']+'/entries',json={'amount':100,'allocations':{'me':99},'payer':'me','event_version':e['version']});assert bad.status_code==422
    s=ok(c.post('/api/expenses/'+e['id']+'/shares',json={'duration':13,'unit':'minutes'}));public=client();assert ok(public.get('/api/shared-expenses/'+s['token']))['cost']['me']==50000
    assert g.delete('/api/expense-shares/'+s['token']).status_code==404
    stamp=s['expires'];monkeypatch.setattr(m,'now',lambda:stamp);assert public.get('/api/shared-expenses/'+s['token']).status_code==404
    stamp=e['ends'];assert c.post('/api/refresh').status_code==200
    assert not ok(c.get('/api/expenses/'+e['id']))['active']
    assert c.post('/api/expenses/'+e['id']+'/entries',json={'amount':100,'allocations':{'me':100},'payer':'me','event_version':e['version']}).status_code==409
    stamp=e['purge_at'];m.expenses_router.cleanup()
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM expense_entries').fetchone()[0]==0

def test_new_session_cannot_kick_older_even_by_password_change_and_sliding_refresh(monkeypatch):
    stamp=int(time.time());monkeypatch.setattr(m,'now',lambda:stamp);c,_,codes=owner();uid=c.get('/api/me').json()['id']
    stamp+=60;other=client();r=ok(other.post('/api/login',json={'login':'owner','password':'Owner-password-123'}));other.headers['x-csrf-token']=r['csrf'];r=ok(other.post('/api/2fa/verify',json={'code':codes[0]}));other.headers['x-csrf-token']=r['csrf']
    old_id=next(s['id'] for s in ok(other.get('/api/security/sessions')) if not s['current'])
    assert other.post('/api/security/sessions/revoke-others').status_code==403
    assert other.delete('/api/security/sessions/'+old_id).status_code==403
    assert other.put('/api/security/password',json={'password':'Owner-password-123','new_password':'New-password-123','code':codes[1]}).status_code==403
    assert other.post('/api/admin/access/unlock',json={'password':'Owner-password-123'}).status_code==200
    assert other.put('/api/admin/users/'+uid+'/password',json={'admin_password':'Owner-password-123','password':'New-password-123'}).status_code==403
    assert other.post('/api/admin/users/'+uid+'/reset-2fa',json={'admin_password':'Owner-password-123'}).status_code==403
    # Refresh each day preserves device creation and moves the inactivity deadline.
    created=next(s['created'] for s in other.get('/api/security/sessions').json() if s['current'])
    for _ in range(7):stamp+=86400;ok(c.post('/api/refresh'));ok(other.post('/api/refresh'))
    assert next(s['created'] for s in other.get('/api/security/sessions').json() if s['current'])==created
    assert other.post('/api/security/sessions/revoke-others').status_code==200
    assert c.get('/api/me').status_code==401

def test_personal_export_vault_expenses_isolated_imported_private_favorites_and_lock():
    c,_,_=owner();g,_=member(c);enable(c,'authenticator');enable(g,'authenticator')
    own_seed=pyotp.random_base32();foreign_seed=pyotp.random_base32()
    ok(c.post('/api/authenticators',json={'label':'MY_VAULT','secret':own_seed}));ok(g.post('/api/authenticators',json={'label':'FOREIGN_VAULT','secret':foreign_seed}))
    e=entry(c,event(c),70001,{'me':35001,'vova':35000});foreign_event=event(g)
    g.put('/api/expenses/'+foreign_event['id'],json={'title':'FOREIGN_EXPENSE','participants':foreign_event['participants'],'retention_days':30,'version':foreign_event['version']})
    nid=note(c);ok(c.patch('/api/notes/'+nid,json={'favorite':True}));ok(c.post('/api/notes/'+nid+'/lock',json={'mode':'lock'}))
    body=export(c);manifest,_=unseal(body);raw=json.dumps(manifest)
    assert own_seed in raw and foreign_seed not in raw and 'FOREIGN_' not in raw
    assert own_seed.encode() not in body
    r=g.post('/api/account/data/import',content=body,headers=headers(password='Friend-password-123'));ok(r)
    assert len(ok(g.get('/api/authenticators')))==2
    imported=[p for p in g.get('/api/notes').json() if p['title']=='Private title'][0]
    assert imported['locked'] and imported['favorite'] and imported['space_id'] is None
    events=ok(g.get('/api/expenses'));copy=next(x for x in events if x['title']=='Пятница')
    assert copy['id']!=e['id'] and copy['cost']['me']==35001
    assert c.get('/api/expenses/'+copy['id']).status_code==404

def test_full_backup_new_content_rekeys_external_totp_and_revokes_leases():
    from tests.test_admin import backup,BACKUP_PASSWORD
    from app.backups import Backups
    from app.schema import SCHEMA
    from cryptography.fernet import Fernet,InvalidToken
    from contextlib import contextmanager
    from pathlib import Path
    import tempfile,sqlite3
    c,_,_=owner();enable(c,'authenticator');seed=pyotp.random_base32()
    ok(c.post('/api/authenticators',json={'label':'Gitea','secret':seed}));e=entry(c,event(c),500,{'me':200,'vova':300});nid=note(c)
    ok(c.patch('/api/notes/'+nid,json={'favorite':True}));ok(c.post('/api/notes/'+nid+'/lock',json={'mode':'lock'}));ok(c.post('/api/notes/'+nid+'/lock',json={'mode':'temporary'}))
    from tests.test_widgets import image_bytes
    ok(c.put('/api/account/profile',json={'display_name':'DrG'}))
    photo=image_bytes();e=ok(c.post('/api/expenses/'+e['id']+'/photos',content=photo));aid=e['photos'][0]
    body=backup(c)
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);destination=root/'site.sqlite3'
        with sqlite3.connect(destination) as db:db.executescript(SCHEMA)
        @contextmanager
        def db():
            connection=sqlite3.connect(destination);connection.row_factory=sqlite3.Row;connection.execute('PRAGMA foreign_keys=ON')
            try:yield connection;connection.commit()
            except BaseException:connection.rollback();raise
            finally:connection.close()
        key=Fernet.generate_key();cipher=Fernet(key);service=Backups(db,root,cipher,key.decode(),m.now)
        with service.temp() as stage:metadata=service.prepare(body,BACKUP_PASSWORD,stage);service.restore(stage,metadata)
        with db() as restored:
            secret=restored.execute('SELECT secret FROM authenticators').fetchone()[0]
            assert cipher.decrypt(secret.encode()).decode()==seed
            with pytest.raises(InvalidToken):m.cipher.decrypt(secret.encode())
            assert restored.execute('SELECT COUNT(*) FROM note_unlocks').fetchone()[0]==0
            assert restored.execute('SELECT locked FROM notes WHERE id=?',(nid,)).fetchone()[0]==1
            assert restored.execute('SELECT COUNT(*) FROM note_favorites').fetchone()[0]==1
            assert restored.execute('SELECT COUNT(*) FROM expense_entries').fetchone()[0]==1
            assert json.loads(restored.execute('SELECT payload FROM expense_events').fetchone()[0])['photos']==[aid]
            assert json.loads(restored.execute('SELECT settings FROM users').fetchone()[0])['profile']['display_name']=='DrG'
        assert service.media.read(aid)==photo

def test_absolute_session_limit_still_applies_after_months_of_refresh(monkeypatch):
    stamp=int(time.time());monkeypatch.setattr(m,'now',lambda:stamp);c,_,_=owner();start=stamp
    for _ in range(30):stamp+=5*86400;ok(c.post('/api/refresh'))
    stamp=start+ABSOLUTE_SECONDS-100; # must keep inactivity deadline alive along the way
    # Existing refresh expires at day 157, so continue within each 7-day interval.
    stamp=start+155*86400;ok(c.post('/api/refresh'))
    for day in (160,165,170,175,179):stamp=start+day*86400;ok(c.post('/api/refresh'))
    import jwt
    assert jwt.decode(c.cookies.get('drg_refresh'),options={'verify_signature':False})['exp']==start+ABSOLUTE_SECONDS
    stamp=start+ABSOLUTE_SECONDS;assert c.post('/api/refresh').status_code==401

def test_database_lock_and_shared_favorites_are_personal_even_for_reader():
    from tests.test_notion import database,record,permissions
    from tests.test_social import family,join,shared,initialize
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);page=shared(c,sid)
    permissions(c,page['id'],g,'viewer')
    ok(g.patch('/api/notes/'+page['id'],json={'favorite':True,'version':1}))
    assert not c.get('/api/notes/'+page['id']).json()['favorite']
    assert g.get('/api/favorites').json()[0]['id']==page['id']
    assert g.post('/api/notes/'+page['id']+'/lock',json={'mode':'lock'}).status_code==403
    nid=database(c);rid=record(c,nid);d=c.get('/api/databases/'+nid).json()
    ok(c.post('/api/notes/'+nid+'/lock',json={'mode':'lock'}))
    assert c.post('/api/databases/'+nid+'/records',json={'title':'cannot add'}).status_code==423
    assert c.put('/api/databases/'+nid,json={'properties':d['properties'],'views':d['views'],'version':1}).status_code==423
    assert c.patch('/api/database-records/'+rid,json={'properties':{},'version':1}).status_code==423
    ok(c.post('/api/notes/'+nid+'/lock',json={'mode':'temporary'}))
    assert c.post('/api/databases/'+nid+'/records',json={'title':'can add now'}).status_code==200
    # A locked family page accepts comments but blocks a WebSocket editor.
    ok(c.post('/api/notes/'+page['id']+'/lock',json={'mode':'lock'}))
    from app.http import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect('/api/live/notes/'+page['id']):pass
    ok(c.post('/api/notes/'+page['id']+'/lock',json={'mode':'temporary'}))
    initialize(c,page['id'])
    assert c.post('/api/notes/'+page['id']+'/comments',json={'body':'Согласовано'}).status_code==200

def test_totp_widget_references_are_owned_cleaned_and_remapped_by_personal_import():
    from tests.test_widgets import add
    c,_,_=owner();g,_=member(c);enable(c,'authenticator');enable(g,'authenticator')
    a=ok(c.post('/api/authenticators',json={'label':'Selected','secret':pyotp.random_base32()}))
    layout=c.get('/api/dashboard/layout').json();foreign={**layout,'instances':layout['instances']+[{'id':'bad','module_id':'authenticator','config':{'entry_ids':[a['id']]}}]}
    assert g.put('/api/dashboard/layout',json=foreign).status_code==404
    add(c,'authenticator',{'entry_ids':[a['id']]});body=export(c)
    ok(g.post('/api/account/data/import',content=body,headers=headers(password='Friend-password-123')))
    widget=next(w for w in g.get('/api/dashboard/layout').json()['instances'] if w['id']=='extra_authenticator')
    assert widget['config']['entry_ids'][0]!=a['id']
    assert g.post('/api/authenticators/'+widget['config']['entry_ids'][0]+'/reveal').status_code==200
    ok(c.delete('/api/authenticators/'+a['id']))
    widget=next(w for w in c.get('/api/dashboard/layout').json()['instances'] if w['id']=='extra_authenticator')
    assert widget['hidden'] and widget['config']['entry_ids']==[]


def test_shortening_expense_retention_deletes_event_entries_and_shares(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,_,_=owner();e=entry(c,event(c),1000,{'me':500,'vova':500})
    s=ok(c.post('/api/expenses/'+e['id']+'/shares',json={'duration':1,'unit':'months'}))
    for _ in range(2):stamp+=5*86400;ok(c.post('/api/refresh'))
    result=ok(c.put('/api/expenses/'+e['id'],json={'title':e['title'],'participants':e['participants'],'retention_days':7,'version':e['version']}))
    assert result['deleted']
    assert c.get('/api/expenses/'+e['id']).status_code==404
    assert client().get('/api/shared-expenses/'+s['token']).status_code==404
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM expense_events').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM expense_entries').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM expense_shares').fetchone()[0]==0
