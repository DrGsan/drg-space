import json
import pyotp
from tests.test_security import m,owner,member,client,reset
from tests.test_next_features import event,ok
from tests.test_widgets import image_bytes,enable
from tests.test_personal_data import export,unseal,headers


def test_display_name_keeps_login_avatar_and_is_owner_scoped():
    c,_,_=owner();g,_=member(c)
    default=ok(c.get('/api/me'))['settings']['profile'];guest_before=ok(g.get('/api/me'))['settings']
    assert ok(c.put('/api/account/profile',json={'display_name':'  DrG  '}))=={**default,'display_name':'DrG'}
    before=ok(c.get('/api/me'))
    assert before['login']=='owner' and before['settings']['profile']['display_name']=='DrG'
    avatar=ok(c.post('/api/account/avatar',content=image_bytes()))
    assert avatar['display_name']=='DrG' and avatar['avatar_id']
    assert ok(c.put('/api/account/profile',json={'display_name':'Володя'}))['avatar_id']==avatar['avatar_id']
    ok(c.delete('/api/account/avatar'))
    assert ok(c.get('/api/me'))['settings']['profile']=={'display_name':'Володя'}
    assert ok(g.get('/api/me'))['settings']==guest_before
    assert c.put('/api/account/profile',json={'display_name':'x'*81}).status_code==422
    assert c.put('/api/account/profile',json={'display_name':'x\ny'}).status_code==422
    assert c.put('/api/account/profile',json={'display_name':'X','login':'different'}).status_code==422
    assert ok(c.put('/api/account/profile',json={'display_name':''}))=={'display_name':''}


def test_photos_limit_encryption_isolation_shares_and_garbage_collection(monkeypatch):
    c,_,_=owner();g,_=member(c);enable(c,'media');enable(g,'expenses');e=event(c);body=image_bytes()
    e=ok(c.post('/api/expenses/'+e['id']+'/photos',content=body));aid=e['photos'][0]
    raw=(m.DATA/'media'/aid).read_bytes()
    assert (body not in raw) == m.data_cipher.enabled
    assert m.data_cipher.open(raw,'media:'+aid) == body
    assert g.get('/api/expenses/'+e['id']+'/photos/'+aid).status_code==404
    assert g.post('/api/expenses/'+e['id']+'/photos',content=body).status_code==404
    assert g.delete('/api/expenses/'+e['id']+'/photos/'+aid).status_code==404
    assert c.get('/api/expenses/'+e['id']+'/photos/'+aid).content==body
    assert c.delete('/api/media/'+aid).status_code==409
    assert aid not in [x['id'] for x in ok(c.get('/api/media'))]
    e=ok(c.put('/api/expenses/'+e['id'],json={'title':'Фото сохранены','participants':e['participants']+[{'id':'new','name':'Новый друг'}],'retention_days':30,'version':e['version']}))
    assert e['photos']==[aid]
    e=ok(c.post('/api/expenses/'+e['id']+'/photos',content=body));second=e['photos'][1]
    third=ok(c.post('/api/expenses/'+e['id']+'/photos',content=body))['photos'][-1]
    e=ok(c.delete('/api/expenses/'+e['id']+'/photos/'+third))
    s=ok(c.post('/api/expenses/'+e['id']+'/shares',json={'duration':13,'unit':'minutes'}));anonymous=client()
    path='/api/shared-expenses/'+s['token']+'/photos/'
    response=anonymous.get(path+aid+'?download=true');assert response.content==body
    assert 'attachment' in response.headers['content-disposition'] and 'no-store' in response.headers['cache-control']
    foreign=ok(g.post('/api/expenses',json={'title':'Другой','participants':e['participants']}))
    foreign=ok(g.post('/api/expenses/'+foreign['id']+'/photos',content=body))
    assert anonymous.get(path+foreign['photos'][0]).status_code==404
    ok(c.delete('/api/expense-shares/'+s['token']));assert anonymous.get(path+aid).status_code==404
    s=ok(c.post('/api/expenses/'+e['id']+'/shares',json={'duration':1,'unit':'minutes'}))
    stamp=s['expires'];monkeypatch.setattr(m,'now',lambda:stamp)
    assert anonymous.get('/api/shared-expenses/'+s['token']+'/photos/'+aid).status_code==404
    e=ok(c.delete('/api/expenses/'+e['id']+'/photos/'+aid));m.files_router.cleanup()
    assert not (m.DATA/'media'/aid).exists() and e['photos']==[second]
    stamp=e['purge_at'];m.expenses_router.cleanup();m.files_router.cleanup()
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM expense_events WHERE id=?',(e['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM media WHERE id=?',(second,)).fetchone()
    assert not (m.DATA/'media'/second).exists()


def test_personal_archive_display_name_and_photo_ids_remap():
    c,_,_=owner();g,_=member(c);enable(g,'expenses');ok(c.put('/api/account/profile',json={'display_name':'DrG'}))
    e=event(c);body=image_bytes();e=ok(c.post('/api/expenses/'+e['id']+'/photos',content=body))
    own=e['photos'][0];other=event(g);other=ok(g.post('/api/expenses/'+other['id']+'/photos',content=image_bytes(True)))
    archive=export(c);manifest,_=unseal(archive)
    assert other['photos'][0] not in json.dumps(manifest)
    expected_profile=ok(c.get('/api/me'))['settings']['profile']
    assert manifest['data']['settings']['profile']==expected_profile
    ok(g.post('/api/account/data/import',content=archive,headers=headers(password='Friend-password-123')))
    copies=ok(g.get('/api/expenses'));copy=next(x for x in copies if x['id']!=other['id'])
    assert copy['photos'][0]!=own
    assert g.get('/api/expenses/'+copy['id']+'/photos/'+copy['photos'][0]).content==body
    assert ok(g.get('/api/me'))['settings']['profile']==expected_profile
    ok(g.delete('/api/expenses/'+copy['id']));m.files_router.cleanup()
    assert c.get('/api/expenses/'+e['id']+'/photos/'+own).content==body


def test_expired_event_photos_do_not_become_permanent_gallery_on_import(monkeypatch):
    c,_,_=owner();g,_=member(c);e=event(c);e=ok(c.post('/api/expenses/'+e['id']+'/photos',content=image_bytes()))
    archive=export(c);stamp=e['purge_at'];monkeypatch.setattr(m,'now',lambda:stamp)
    # Make a fresh session at the future date; account remains the same.
    g=client();r=ok(g.post('/api/login',json={'login':'friend','password':'Friend-password-123'}));g.headers['x-csrf-token']=r['csrf']
    with m.db() as db:
        seed=m.cipher.decrypt(db.execute("SELECT totp FROM users WHERE login='friend'").fetchone()[0].encode()).decode()
    r=ok(g.post('/api/2fa/verify',json={'code':pyotp.TOTP(seed).at(stamp)}));g.headers['x-csrf-token']=r['csrf']
    result=ok(g.post('/api/account/data/import',content=archive,headers=headers(password='Friend-password-123')))
    assert result['imported']['expenses']==0 and result['imported']['images']==0
    uid=ok(g.get('/api/me'))['id']
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM media WHERE user_id=?',(uid,)).fetchone()


def test_photo_quota_failure_rolls_back_event_media_and_encrypted_object():
    c,_,_=owner();e=event(c);uid=ok(c.get('/api/me'))['id'];used=ok(c.get('/api/account/storage'))['used_bytes']
    ok(c.put('/api/admin/users/'+uid+'/quota',json={'admin_password':'Owner-password-123','limit_bytes':used+1}))
    before=set((m.DATA/'media').iterdir())
    response=c.post('/api/expenses/'+e['id']+'/photos',content=image_bytes())
    assert response.status_code==413,response.text
    saved=ok(c.get('/api/expenses/'+e['id']));assert saved['photos']==[] and saved['version']==e['version']
    assert set((m.DATA/'media').iterdir())==before
    with m.db() as db:assert not db.execute('SELECT 1 FROM media WHERE user_id=?',(uid,)).fetchone()
