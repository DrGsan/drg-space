"""Message edits preserve identity, permissions, history and delivery cursors."""
import json

import pytest

from app.chat_storage import EDIT_SECONDS,cutoff,validate_archive
from app.backups import BackupError
from tests.test_chat_workspace import conversations
from tests.test_security import reset,client
from app import main as m
from tests.test_chat_retention import send,upload
from tests.test_social import family,enabled,hello,next_event
from tests.test_next_features import ok


def edit(c,sid,message,body,**fields):
    return c.put(f"/api/chat/{sid}/messages/{message['id']}",json={'body':body,'revision':message['revision'],**fields})


def test_edit_authorization_original_deadline_and_validation(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,g,o,sid=conversations();message=ok(send(c,sid,body='Первоначальный текст'));other=family(c)
    assert message['edited']==0 and message['revision']==0 and message['editable_until']==stamp+EDIT_SECONDS
    assert edit(g,sid,message,'Чужая правка').status_code==403
    assert edit(o,sid,message,'Чужая беседа').status_code==404
    assert edit(c,other,message,'Чужой ID').status_code==404
    assert edit(client(),sid,message,'Без входа').status_code==401
    for fields in ({'revision':True},{'revision':-1},{'revision':'0'},{'attachments':[]}):
        assert edit(c,sid,message,'Правка',**fields).status_code==422
    assert edit(c,sid,message,'   ').status_code==422
    assert edit(c,sid,message,'я'*4001).status_code==422
    with m.db() as db:db.execute('UPDATE messages SET created=? WHERE id=?',(stamp-EDIT_SECONDS+1,message['id']))
    changed=ok(edit(c,sid,message,'За секунду до окончания'));assert changed['edited']==stamp
    stamp+=1
    assert edit(c,sid,changed,'Ровно 24 часа').status_code==403
    enabled(c,False);assert edit(c,sid,changed,'Модуль выключен').status_code==403


def test_edits_keep_attachment_reply_idempotency_and_notification_count(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,g,o,sid=conversations();source=ok(send(g,sid,body='Источник'));file=upload(c,sid)
    data={'body':'Подпись','attachments':[file['id']],'reply_to':source['id'],'client_id':'edit-idempotent-123'}
    message=ok(c.post('/api/chat/'+sid+'/messages',json=data))
    before=ok(g.get('/api/chat/rooms'))['unread']
    with m.db() as db:notifications=db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0];pushes=db.execute('SELECT COUNT(*) FROM push_deliveries').fetchone()[0]
    noop=ok(edit(c,sid,message,' Подпись '));assert noop['edited']==0 and noop['revision']==0
    with g.websocket_connect('/api/live/chat/'+sid) as ws:
        hello(ws,g);next_event(ws,'ready')
        changed=ok(edit(c,sid,message,'Новая подпись'))
        assert next_event(ws,'message_edited')['message']==changed
    assert changed['id']==message['id'] and changed['created']==message['created'] and changed['reply']==message['reply'] and changed['attachments']==message['attachments']
    assert changed['edited']==stamp and changed['revision']==1
    assert ok(edit(c,sid,message,'Новая подпись'))['revision']==1  # Lost PUT acknowledgement is safe to retry.
    assert edit(c,sid,message,'Устаревший редактор').status_code==409
    assert ok(c.post('/api/chat/'+sid+'/messages',json=data))['body']=='Новая подпись'
    # Removing a caption preserves the attachment, so the message is not empty.
    captionless=ok(edit(c,sid,changed,''));assert captionless['body']=='' and captionless['attachments']==message['attachments'] and captionless['revision']==2
    assert c.get(file['url']).content==b'hello world'
    assert ok(g.get('/api/chat/rooms'))['unread']==before
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM notifications').fetchone()[0]==notifications
        assert db.execute('SELECT COUNT(*) FROM push_deliveries').fetchone()[0]==pushes
        payloads=[json.loads(row[0]) for row in db.execute("SELECT payload FROM notifications WHERE kind='message'")]
        assert next(p for p in payloads if p['message_id']==message['id'])['preview']=='Вложение'


def test_edit_cursor_is_independent_of_history_ids_and_seconds(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,g,o,sid=conversations();first=ok(send(c,sid,body='Первое'));second=ok(send(c,sid,body='Второе'))
    reference=ok(g.post('/api/chat/'+sid+'/messages',json={'body':'Ответ','reply_to':first['id'],'client_id':'edited-source-reply'}))
    initial=ok(g.get('/api/chat/'+sid+'/messages'));cursor=initial['items'][-1]['id']
    edited_second=ok(edit(c,sid,second,'Второе изменено'));edited_first=ok(edit(c,sid,first,'Первое изменено'))
    again=ok(edit(c,sid,edited_second,'Второе изменено дважды'))
    assert [edited_second['revision'],edited_first['revision'],again['revision']]==[1,2,3]
    changes=ok(g.get(f'/api/chat/{sid}/messages?after={cursor}&edited_after=0'))
    assert changes['items']==[] and [v['id'] for v in changes['edits']]==[first['id'],second['id']]
    assert changes['edit_cursor']==3 and not changes['edit_more']
    assert ok(g.get(f'/api/chat/{sid}/messages?after={cursor}&edited_after=3'))['edits']==[]
    assert ok(g.get(f'/api/chat/{sid}/search?q=дважды'))['items'][0]['body']==again['body']
    history=ok(g.get('/api/chat/'+sid+'/messages'))['items'];assert history[-1]['id']==reference['id'] and history[-1]['reply']['body']==edited_first['body']
    assert ok(g.get('/api/chat/rooms'))['items'][0]['last_message']['id']==reference['id']
    # Purging all retained messages does not reset the room's edit sequence.
    with m.db() as db:
        db.execute('UPDATE messages SET created=? WHERE space_id=?',(cutoff(stamp),sid));db.execute('DELETE FROM messages WHERE space_id=?',(sid,))
    fresh=ok(send(c,sid,body='После очистки'));assert ok(edit(c,sid,fresh,'Новая правка'))['revision']==4


def test_edit_cursor_paginates_without_skipping_same_second_changes():
    c,g,o,sid=conversations();uid=c.get('/api/me').json()['id'];stamp=m.now()
    with m.db() as db:
        for index in range(105):
            db.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created,edited,revision) VALUES(?,?,?,?,?,?,?)',(sid,uid,'edit-page-'+str(index),'Правка '+str(index),stamp,stamp,index+1))
        db.execute('UPDATE spaces SET chat_revision=105 WHERE id=?',(sid,))
    first=ok(g.get('/api/chat/'+sid+'/messages?after=0&edited_after=0'));assert len(first['edits'])==100 and first['edit_more'] and first['edit_cursor']==100
    second=ok(g.get('/api/chat/'+sid+'/messages?after=0&edited_after=100'));assert len(second['edits'])==5 and not second['edit_more'] and second['edit_cursor']==105
    assert len({v['id'] for v in first['edits']+second['edits']})==105


def test_full_backup_roundtrip_retains_edit_metadata_and_rejects_invalid_revisions():
    import pyotp
    from tests.test_admin import backup,headers
    c,g,o,sid=conversations();message=ok(send(c,sid,body='До изменения'));changed=ok(edit(c,sid,message,'После изменения'))
    archive=backup(c);ok(c.post('/api/admin/backups/restore',content=archive,headers=headers()))
    c=client();logged=ok(c.post('/api/login',json={'login':'owner','password':'Owner-password-123'}));c.headers['x-csrf-token']=logged['csrf']
    with m.db() as db:seed=m.cipher.decrypt(db.execute("SELECT totp FROM users WHERE login='owner'").fetchone()[0].encode()).decode()
    verified=ok(c.post('/api/2fa/verify',json={'code':pyotp.TOTP(seed).at(m.now())}));c.headers['x-csrf-token']=verified['csrf']
    restored=ok(c.get('/api/chat/'+sid+'/messages'))['items'][0];assert restored['body']==changed['body'] and restored['revision']==changed['revision'] and restored['edited']==changed['edited']
    assert ok(edit(c,sid,restored,'После восстановления'))['revision']==2
    def invalid():raise BackupError('Invalid edit')
    with m.db() as db:
        db.execute('UPDATE messages SET revision=99 WHERE id=?',(restored['id'],))
        with pytest.raises(BackupError):validate_archive(db,invalid)
