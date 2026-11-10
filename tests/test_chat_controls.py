"""Room history controls preserve scope and enforce message deadlines everywhere."""
import json
import pytest
from app.chat_storage import purge,validate_archive
from tests.test_security import reset,client,m
from tests.test_chat_workspace import conversations
from tests.test_chat_retention import send,upload
from tests.test_social import family,hello,next_event
from tests.test_chat_editing import edit
from tests.test_next_features import ok


def timer(c,sid,seconds):return c.put('/api/chat/'+sid+'/timer',json={'seconds':seconds})


def test_timer_authority_validation_and_existing_message_deadlines(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,g,o,sid=conversations();old=ok(send(c,sid,body='Останется три месяца'))
    for user,status in ((g,403),(o,404),(client(),401)):
        assert timer(user,sid,86400).status_code==status
        assert user.post('/api/chat/'+sid+'/clear').status_code==status
    for value in (True,'86400',-1,1,7776000):assert timer(c,sid,value).status_code==422
    for seconds in (86400,604800,2592000):
        assert ok(timer(c,sid,seconds))['chat_ttl']==seconds
        assert ok(send(g,sid,body='Быстрый таймер'))['expires']==stamp+seconds
    ok(timer(c,sid,0));assert ok(send(c,sid,body='Снова три месяца'))['expires']==0
    history=ok(g.get('/api/chat/'+sid+'/messages'));assert history['items'][0]['id']==old['id'] and history['items'][0]['expires']==0
    assert history['chat_ttl']==0 and history['retention_months']==3
    # Both friends can manage a personal room, regardless of who invited whom.
    q=ok(c.post('/api/social/friends',json={'login':'friend'}));friends=ok(g.post('/api/social/requests/'+q['id']+'/accept'))['space_id']
    assert ok(timer(g,friends,86400))['chat_ttl']==86400
    assert ok(c.post('/api/chat/'+friends+'/clear'))['chat_cleared']==0


def test_expiry_blocks_history_search_edit_reply_download_reads_and_retry(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,g,o,sid=conversations();ok(timer(c,sid,86400));file=upload(c,sid);message=ok(send(c,sid,[file],body='Пароль',cid='expire-retry-123'))
    ok(timer(c,sid,0));reply=ok(g.post('/api/chat/'+sid+'/messages',json={'body':'Ответ','reply_to':message['id'],'client_id':'expire-reply-123'}))
    with m.db() as db:db.execute('UPDATE messages SET created=?,expires=? WHERE id=?',(stamp-86400,stamp,message['id']))
    assert [x['id'] for x in ok(g.get('/api/chat/'+sid+'/messages'))['items']]==[reply['id']]
    assert ok(g.get('/api/chat/'+sid+'/messages'))['items'][0]['reply']['unavailable']
    assert ok(g.get('/api/chat/'+sid+'/search?q=Пароль'))['items']==[]
    assert edit(c,sid,message,'Сохранить пароль').status_code==404
    assert g.get(file['url']).status_code==404
    assert g.post('/api/chat/'+sid+'/read',json={'message_id':message['id']}).status_code==404
    assert send(c,sid,body='Повтор',cid='expire-retry-123').status_code==409
    assert g.post('/api/chat/'+sid+'/messages',json={'body':'Ответ','reply_to':message['id'],'client_id':'expire-reply-456'}).status_code==404
    assert ok(g.get('/api/chat/rooms'))['unread']==0
    assert not [n for n in ok(g.get('/api/notifications'))['items'] if n['kind']=='message']
    with m.db() as db:
        purge(db,stamp);assert not db.execute('SELECT 1 FROM files WHERE id=?',(file['id'],)).fetchone()
        validate_archive(db,lambda:pytest.fail('Valid expired references'))


def test_clear_all_participants_retires_only_room_sent_files_and_broadcasts():
    c,g,o,sid=conversations();other=family(c);keep=ok(send(c,other,body='Другая беседа'));sent=upload(c,sid);draft=upload(c,sid);message=ok(send(c,sid,[sent],body='Удалить'))
    with g.websocket_connect('/api/live/chat/'+sid) as ws:
        hello(ws,g);next_event(ws,'ready')
        result=ok(c.post('/api/chat/'+sid+'/clear'));assert result['chat_cleared']==message['id']
        assert next_event(ws,'chat_state')['chat_cleared']==message['id']
    for user in (c,g):
        history=ok(user.get('/api/chat/'+sid+'/messages'));assert history['items']==[] and history['chat_cleared']==message['id']
        assert user.get(sent['url']).status_code==404
    assert c.get(draft['url']).status_code==200
    assert ok(c.get('/api/chat/'+other+'/messages'))['items'][0]['id']==keep['id']
    with m.db() as db:
        assert not any(json.loads(n[0]).get('space_id')==sid for n in db.execute("SELECT payload FROM notifications WHERE kind='message'"))
        assert db.execute('SELECT 1 FROM spaces WHERE id=?',(sid,)).fetchone()
    assert ok(g.get('/api/notifications'))['chat_cleared'][sid]==message['id']
    assert ok(c.post('/api/chat/'+sid+'/clear'))==result
    assert ok(send(g,sid,body='После очистки'))['id']>result['chat_cleared']


def test_names_mentions_only_members_and_profile_fallback():
    c,g,o,sid=conversations();ok(g.put('/api/account/profile',json={'display_name':'Мама'}))
    profile=next(p for p in ok(c.get('/api/chat/rooms'))['items'][0]['members'] if p['login']=='friend');assert profile['display_name']=='Мама'
    message=ok(send(c,sid,body='Привет @friend! @outsider owner@friend https://example.com/@friend'))
    assert [p['id'] for p in message['mentions']]==[profile['id']]
    with m.db() as db:
        notices=[json.loads(n[0]) for n in db.execute("SELECT payload FROM notifications WHERE kind='message'")]
        assert notices[-1]['mentioned'] is True
    source=ok(send(g,sid,body='От мамы'));reply=ok(c.post('/api/chat/'+sid+'/messages',json={'body':'Ответ','reply_to':source['id'],'client_id':'named-reply-123'}))
    assert reply['reply']['sender']=='Мама'
    q=ok(c.post('/api/social/friends',json={'login':'friend'}));friends=ok(g.post('/api/social/requests/'+q['id']+'/accept'))['space_id']
    assert next(s for s in ok(c.get('/api/chat/rooms'))['items'] if s['id']==friends)['name']=='Мама'
    assert ok(send(c,friends,body='@friend'))['mentions']==[]


@pytest.mark.parametrize('table,column,value', [('spaces','chat_ttl',1),('spaces','chat_cleared',-1),('messages','expires',123)])
def test_archive_rejects_invalid_timer_or_clear_cursor(table,column,value):
    c,g,o,sid=conversations();ok(send(c,sid,body='Проверка архива'))
    with m.db() as db:
        db.execute(f'UPDATE {table} SET {column}=?',(value,))
        with pytest.raises(ValueError):validate_archive(db,lambda:(_ for _ in ()).throw(ValueError('Invalid chat metadata')))
