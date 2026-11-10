import json,secrets
from datetime import datetime,timezone
import pytest
from tests.test_security import m,owner,member,client,reset,note
from tests.test_social import family,join,enabled
from tests.test_widgets import image_bytes,enable
from tests.test_next_features import ok
from app.chat_storage import cutoff,decode,attachment_room,MAX_ATTACHMENT
from app.retention import cleanup
from app.file_store import file_store


def room():
    c,_,_=owner();g,_=member(c);o,_=member(c,'outsider');sid=family(c);join(c,g,sid)
    for user in (c,g,o):enabled(user)
    return c,g,o,sid

def upload(c,sid,name='hello.txt',body=b'hello world'):
    return ok(c.post('/api/chat/'+sid+'/attachments',params={'name':name},content=body))

def send(c,sid,files=(),body='',cid=None):
    return c.post('/api/chat/'+sid+'/messages',json={'body':body,'attachments':[f['id'] for f in files],'client_id':cid or secrets.token_hex(12)})

def test_calendar_month_cutoff_and_history_boundaries(monkeypatch):
    stamp=int(datetime(2026,5,31,12,tzinfo=timezone.utc).timestamp())
    assert cutoff(stamp)==int(datetime(2026,2,28,12,tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(m,'now',lambda:stamp);c,g,o,sid=room();first=ok(send(c,sid,body='old'));second=ok(send(c,sid,body='recent'))
    with m.db() as db:
        db.execute('UPDATE messages SET created=? WHERE id=?',(cutoff(stamp),first['id']))
        db.execute('UPDATE messages SET created=? WHERE id=?',(cutoff(stamp)+1,second['id']))
    assert send(c,sid,body='old',cid=first['client_id']).status_code==409
    result=ok(g.get('/api/chat/'+sid+'/messages'));assert [x['body'] for x in result['items']]==['recent']
    cleanup(m.db,stamp)
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==1
    assert ok(g.get('/api/chat/'+sid+'/messages?after=0'))['items'][0]['id']==second['id']


def test_attachment_scope_binding_idempotence_ranges_and_private_exports():
    c,g,o,sid=room();file=upload(c,sid);path=file['url']
    assert client().get(path).status_code==401
    assert g.get(path).status_code==404 and o.get(path).status_code==404
    assert c.get(path).content==b'hello world'
    assert send(g,sid,[file]).status_code==404
    foreign_sid=family(c);assert send(c,foreign_sid,[file]).status_code==404
    assert send(c,sid,[file,file]).status_code==422
    sent=ok(send(c,sid,[file],body='with attachment',cid='repeat-123'));assert sent['attachments'][0]['id']==file['id']
    assert ok(send(c,sid,[file],body='with attachment',cid='repeat-123'))['id']==sent['id']
    assert send(c,sid,[file]).status_code==404
    assert g.get(path).content==b'hello world'
    assert o.get(path).status_code==404
    assert c.delete(path).status_code==409
    for header,body,content_range in [('bytes=1-4',b'ello','bytes 1-4/11'),('bytes=-5',b'world','bytes 6-10/11'),('bytes=6-',b'world','bytes 6-10/11')]:
        response=g.get(path,headers={'range':header});assert response.status_code==206
        assert response.content==body and response.headers['content-range']==content_range
    for value in ('bytes=11-','bytes=4-2','bytes=0-1,4-5','bytes=-0'):
        response=g.get(path,headers={'range':value});assert response.status_code==416
    enable(c,'files')
    assert c.get('/api/files').json()['items']==[]
    assert c.get('/api/files/'+file['id']+'/content').status_code==404
    assert c.post('/api/files/download',json={'ids':[file['id']]}).status_code==404
    from tests.test_personal_data import export,unseal
    manifest,_=unseal(export(c));assert manifest['data']['files']==[]
    assert file['id'] not in json.dumps(manifest)
    uid=g.get('/api/me').json()['id'];ok(c.delete('/api/social/families/'+sid+'/members/'+uid))
    assert g.get(path).status_code==404


def test_images_videos_and_unsafe_files_are_classified_safely():
    c,g,o,sid=room();png=upload(c,sid,'photo.png',image_bytes());video=upload(c,sid,'clip.mp4',b'\x00\x00\x00\x18ftypisom'+b'0'*100)
    unsafe=upload(c,sid,'unsafe.html',b'<script>alert(1)</script>')
    disguised=upload(c,sid,'fake.png',b'<html>not an image</html>')
    assert png['kind']=='image' and video['kind']=='video' and unsafe['kind']==disguised['kind']=='file'
    ok(send(c,sid,[png,video,unsafe,disguised]))
    assert g.get(png['url']).headers['content-type'].startswith('image/png')
    assert g.get(video['url'],headers={'range':'bytes=0-11'}).headers['content-type'].startswith('video/mp4')
    response=g.get(unsafe['url']);assert response.headers['content-type']=='application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment') and response.headers['x-content-type-options']=='nosniff'
    assert 'sandbox' in response.headers['content-security-policy']
    assert g.get(png['url']+'?download=true').headers['content-disposition'].startswith('attachment')
    with m.db() as db:
        row=db.execute('SELECT object_id FROM files WHERE id=?',(png['id'],)).fetchone()
        raw=(m.DATA/'files'/row[0]).read_bytes()
        assert (image_bytes() not in raw) == m.data_cipher.enabled
        assert file_store(m.db,m.DATA).read(row[0]) == image_bytes()


def test_expired_messages_and_draft_files_are_purged_without_touching_normal_files(monkeypatch):
    c,g,o,sid=room();sent_file=upload(c,sid);draft=upload(c,sid);keep=upload(c,sid);msg=ok(send(c,sid,[sent_file]));stamp=m.now()
    enable(c,'files');ordinary=ok(c.post('/api/files/upload?name=keep.txt',content=b'keep'))
    with m.db() as db:
        db.execute('UPDATE messages SET created=? WHERE id=?',(cutoff(stamp),msg['id']))
        db.execute('UPDATE files SET created=? WHERE id=?',(stamp-86400,draft['id']))
        removed=[db.execute('SELECT object_id FROM files WHERE id=?',(f['id'],)).fetchone()[0] for f in (sent_file,draft)]
    assert g.get(sent_file['url']).status_code==404
    cleanup(m.db,stamp);m.files_router.cleanup()
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM messages WHERE id=?',(msg['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM files WHERE id=?',(keep['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM files WHERE id=?',(ordinary['id'],)).fetchone()
        for identity in removed:assert identity not in file_store(m.db,m.DATA).ids()
    deletion=ok(c.delete('/api/social/spaces/'+sid))
    with m.db() as db:
        from app.workspace_lifecycle import purge
        purge(db,deletion['deletion']['delete_at'])
    cleanup(m.db,stamp);m.files_router.cleanup()
    with m.db() as db:assert not db.execute('SELECT 1 FROM files WHERE id=?',(keep['id'],)).fetchone()


def test_attachment_size_quota_and_failure_rollback(monkeypatch):
    c,g,o,sid=room();uid=c.get('/api/me').json()['id'];used=c.get('/api/account/storage').json()['used_bytes']
    before=file_store(m.db,m.DATA).ids()
    ok(c.put('/api/admin/users/'+uid+'/quota',json={'admin_password':'Owner-password-123','limit_bytes':used+1}))
    assert c.post('/api/chat/'+sid+'/attachments?name=file.txt',content=b'x'*1024).status_code==413
    assert file_store(m.db,m.DATA).ids()==before
    assert c.post('/api/chat/'+sid+'/attachments?name=file.txt',content=b'x',headers={'content-length':str(MAX_ATTACHMENT+1)}).status_code==413
    with m.db() as db:assert not db.execute('SELECT 1 FROM files WHERE user_id=?',(uid,)).fetchone()


def test_full_backup_retains_live_chat_files_and_restores_downloads(monkeypatch):
    from tests.test_admin import backup,headers
    c,g,o,sid=room();file=upload(c,sid,'photo.png',image_bytes());ok(send(c,sid,[file],body='Remember this'))
    archive=backup(c)
    response=c.post('/api/admin/backups/restore',content=archive,headers=headers());assert response.status_code==200,response.text
    c=client();r=ok(c.post('/api/login',json={'login':'owner','password':'Owner-password-123'}));c.headers['x-csrf-token']=r['csrf']
    import pyotp
    with m.db() as db:seed=m.cipher.decrypt(db.execute("SELECT totp FROM users WHERE login='owner'").fetchone()[0].encode()).decode()
    r=ok(c.post('/api/2fa/verify',json={'code':pyotp.TOTP(seed).at(m.now())}));c.headers['x-csrf-token']=r['csrf']
    assert c.get(file['url']).content==image_bytes()
    assert ok(c.get('/api/chat/'+sid+'/messages'))['items'][0]['body']=='Remember this'

    newer=upload(c,sid,'new.txt',b'new')
    assert ok(send(c,sid,[newer]))['id']>ok(c.get('/api/chat/'+sid+'/messages'))['items'][0]['id']
    future=m.now()+120*86400;monkeypatch.setattr(m,'now',lambda:future)
    c=client();r=ok(c.post('/api/login',json={'login':'owner','password':'Owner-password-123'}));c.headers['x-csrf-token']=r['csrf']
    r=ok(c.post('/api/2fa/verify',json={'code':pyotp.TOTP(seed).at(future)}));c.headers['x-csrf-token']=r['csrf']
    ok(c.post('/api/admin/access/unlock',json={'password':'Owner-password-123'}))
    response=c.post('/api/admin/backups/restore',content=archive,headers=headers());assert response.status_code==200,response.text
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM messages').fetchone()
        assert not db.execute('SELECT 1 FROM files WHERE id=?',(file['id'],)).fetchone()


def test_unavailable_draft_references_can_be_removed_and_do_not_block_plain_text():
    c,g,o,sid=room();expired=upload(c,sid);missing=upload(c,sid);live=upload(c,sid)
    with m.db() as db:db.execute('UPDATE files SET created=? WHERE id=?',(m.now()-86400,expired['id']))
    ok(c.delete(missing['url']));ok(c.delete(missing['url']))
    failed=send(c,sid,[expired,missing,live],body='Сохранённый текст')
    assert failed.status_code==404
    assert set(failed.json()['detail']['unavailable_attachments'])=={expired['id'],missing['id']}
    ok(c.delete(expired['url']));ok(c.delete(expired['url']))
    text=ok(send(c,sid,body='Можно отправить текст'));assert text['body']=='Можно отправить текст' and text['attachments']==[]
    # Other unsent rows are not automatically attached and do not block sends.
    assert c.get(live['url']).status_code==200
    with m.db() as db:assert db.execute('SELECT version FROM files WHERE id=?',(live['id'],)).fetchone()[0]==1
    assert o.delete(live['url']).status_code==404
    assert g.delete(live['url']).status_code==404
    foreign=family(c);assert c.delete('/api/chat/'+foreign+'/attachments/'+live['id']).status_code==404
