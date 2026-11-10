"""Deleting one owned message reuses the existing store and invalidates all history pages."""
import json
import pytest
from tests.test_security import m
from app.chat_storage import HISTORY_VERSION_PREFIX,history_version,purge,validate_archive
from app.backups import BackupError
from tests.test_chat_workspace import conversations
from tests.test_chat_retention import send,upload
from tests.test_social import hello,next_event,family
from tests.test_security import client,reset
from tests.test_next_features import ok


def test_delete_permissions_csrf_retry_and_independent_messages():
    c,g,o,sid=conversations();owned=ok(send(c,sid,body='Удалить'));other=ok(send(g,sid,body='Сохранить'))
    url=f"/api/chat/{sid}/messages/{owned['id']}"
    assert g.delete(url).status_code==403
    assert o.delete(url).status_code==404
    assert client().delete(url).status_code==401
    assert c.delete(f"/api/chat/{sid}/messages/{other['id']}").status_code==403
    csrf=c.headers.pop('x-csrf-token');assert c.delete(url).status_code==403;c.headers['x-csrf-token']=csrf
    initial=ok(g.get('/api/chat/'+sid+'/messages'));assert initial['history_version']==0
    with g.websocket_connect('/api/live/chat/'+sid) as ws:
        hello(ws,g);next_event(ws,'ready');ok(c.delete(url))
        state=next_event(ws,'chat_state');assert state['history_version']>0 and state['chat_cleared']==0
    current=ok(g.get(f"/api/chat/{sid}/messages?after={other['id']}&edited_after=0"))
    assert current['history_version']==state['history_version']
    assert [v['id'] for v in ok(g.get('/api/chat/'+sid+'/messages'))['items']]==[other['id']]
    ok(c.delete(url));assert ok(g.get('/api/chat/'+sid+'/messages'))['history_version']==state['history_version']
    # A phone retrying an ambiguously acknowledged send must not resurrect it.
    assert send(c,sid,body='Удалить',cid=owned['client_id']).status_code==409
    assert ok(g.get('/api/chat/rooms'))['unread']==0


def test_delete_retires_only_its_files_notifications_reactions_and_quotes():
    c,g,o,sid=conversations();file=upload(c,sid);draft=upload(c,sid)
    message=ok(c.post('/api/chat/'+sid+'/messages',json={'body':'Удалить с файлом','client_id':'owned-delete-file','attachments':[file['id']]}))
    reply=ok(g.post('/api/chat/'+sid+'/messages',json={'body':'Ответ останется','client_id':'reply-to-deleted-source','reply_to':message['id']}))
    ok(g.post(f"/api/chat/{sid}/messages/{message['id']}/reactions",json={'emoji':'👍'}))
    ok(c.delete(f"/api/chat/{sid}/messages/{message['id']}"))
    history=ok(g.get('/api/chat/'+sid+'/messages'));assert history['items'][0]['id']==reply['id']
    assert history['items'][0]['reply']['unavailable'] and history['items'][0]['reply']['body']==''
    assert c.get(file['url']).status_code==404
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM files WHERE id=?',(file['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM files WHERE id=?',(draft['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',('message-reactions:'+sid+':'+str(message['id']),)).fetchone()
        notices=[json.loads(r[0]) for r in db.execute("SELECT payload FROM notifications WHERE kind='message'")]
        assert not any(n.get('message_id')==message['id'] and n.get('space_id')==sid for n in notices)
    assert ok(g.get('/api/chat/rooms'))['items'][0]['last_message']['id']==reply['id']


def test_history_version_changes_for_each_old_message_and_survives_backup_restore():
    import pyotp
    from tests.test_admin import backup,headers
    c,g,o,sid=conversations();first=ok(send(c,sid,body='Первое'));second=ok(send(c,sid,body='Второе'));keep=ok(send(g,sid,body='Оставить'))
    for message in (second,first):
        ok(c.delete(f"/api/chat/{sid}/messages/{message['id']}"))
        version=ok(c.get('/api/chat/'+sid+'/messages'))['history_version']
        if message is second:previous=version
        else:assert version>previous
    archive=backup(c);ok(c.post('/api/admin/backups/restore',content=archive,headers=headers()))
    c=client();logged=ok(c.post('/api/login',json={'login':'owner','password':'Owner-password-123'}));c.headers['x-csrf-token']=logged['csrf']
    with m.db() as db:seed=m.cipher.decrypt(db.execute("SELECT totp FROM users WHERE login='owner'").fetchone()[0].encode()).decode()
    verified=ok(c.post('/api/2fa/verify',json={'code':pyotp.TOTP(seed).at(m.now())}));c.headers['x-csrf-token']=verified['csrf']
    result=ok(c.get('/api/chat/'+sid+'/messages'));assert result['history_version']==version and [v['id'] for v in result['items']]==[keep['id']]
    with m.db() as db:
        validate_archive(db,lambda:pytest.fail('Valid deletion state must remain restorable'))
        db.execute('UPDATE app_settings SET value=? WHERE key=?',('999999999',HISTORY_VERSION_PREFIX+sid))
        def invalid():raise BackupError('Invalid history version')
        with pytest.raises(BackupError):validate_archive(db,invalid)


def test_deleted_room_scalar_is_cleaned_and_new_message_edit_still_works():
    c,g,o,sid=conversations();message=ok(send(c,sid,body='Удалить'))
    ok(c.delete(f"/api/chat/{sid}/messages/{message['id']}"))
    fresh=ok(send(c,sid,body='Новое'));changed=ok(c.put(f"/api/chat/{sid}/messages/{fresh['id']}",json={'body':'Правка','revision':0}))
    assert changed['revision']>ok(c.get('/api/chat/'+sid+'/messages'))['history_version']
    with m.db() as db:
        db.execute('DELETE FROM spaces WHERE id=?',(sid,));purge(db,m.now())
        assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',(HISTORY_VERSION_PREFIX+sid,)).fetchone()
