"""Conversation navigation, private searches, bounded reads and durable replies."""
import json
import secrets

import pytest

from tests.test_security import owner,member,client,reset
from app import main as m
from app.backups import BackupError
from app.chat_storage import PREFIX,REPLY_PREFIX,cutoff,decode,encode,purge,validate_archive
from app.personal_data import validate_settings
from tests.test_social import family,join,enabled,hello,next_event
from tests.test_chat_retention import send,upload
from tests.test_next_features import ok


def conversations():
    c,_,_=owner();g,_=member(c);o,_=member(c,'outsider');sid=family(c);join(c,g,sid)
    for user in (c,g,o):enabled(user)
    return c,g,o,sid


def test_rooms_preview_unread_preferences_and_relationship_scope():
    c,g,o,sid=conversations();empty=family(c);foreign=family(o)
    first=ok(send(c,sid,body='Посмотрим фильм?'))
    result=ok(g.get('/api/chat/rooms'));assert result['unread']==1
    room=result['items'][0];assert room['id']==sid and room['last_message']['id']==first['id'] and room['last_message']['body']=='Посмотрим фильм?'
    assert room['unread']==1 and room['favorite'] is False and room['pinned'] is False
    assert foreign not in {item['id'] for item in ok(c.get('/api/chat/rooms'))['items']}
    assert ok(c.put('/api/chat/'+empty+'/preferences',json={'pinned':True,'favorite':True}))=={'pinned':True,'favorite':True}
    assert ok(c.put('/api/chat/'+empty+'/preferences',json={'favorite':False}))=={'pinned':True,'favorite':False}
    own=ok(c.get('/api/chat/rooms'));assert own['items'][0]['id']==empty and own['items'][0]['last_message'] is None
    assert ok(g.get('/api/chat/rooms'))['items'][0]['pinned'] is False
    assert o.put('/api/chat/'+sid+'/preferences',json={'favorite':True}).status_code==404
    assert c.put('/api/chat/'+sid+'/preferences',json={'pinned':None}).status_code==422
    assert c.put('/api/chat/'+sid+'/preferences',json={'pinned':'true'}).status_code==422
    uid=g.get('/api/me').json()['id'];ok(g.put('/api/chat/'+sid+'/preferences',json={'favorite':True}))
    ok(c.delete('/api/social/families/'+sid+'/members/'+uid))
    assert ok(g.get('/api/chat/rooms'))=={'items':[],'unread':0}
    assert g.post('/api/chat/'+sid+'/read',json={'message_id':first['id']}).status_code==404
    enabled(c,False);assert c.get('/api/chat/rooms').status_code==403
    assert client().get('/api/chat/rooms').status_code==401


def test_read_cursor_does_not_mark_newer_messages_or_another_room():
    c,g,o,sid=conversations();other=family(c);join(c,g,other)
    ok(g.post('/api/notifications/read-all'))
    first=ok(send(c,sid,body='Сначала'));second=ok(send(c,sid,body='Пришло позже'));elsewhere=ok(send(c,other,body='Другая беседа'))
    assert ok(g.post('/api/chat/'+sid+'/read',json={'message_id':first['id']}))['read']==1
    result=ok(g.get('/api/chat/rooms'));rooms={item['id']:item for item in result['items']}
    assert result['unread']==2 and rooms[sid]['unread']==1 and rooms[other]['unread']==1
    assert ok(g.post('/api/chat/'+sid+'/read',json={'message_id':first['id']}))['read']==0
    assert g.post('/api/chat/'+sid+'/read',json={'message_id':elsewhere['id']}).status_code==404
    assert g.post('/api/chat/'+sid+'/read',json={'message_id':second['id']+10000}).status_code==404
    assert o.post('/api/chat/'+sid+'/read',json={'message_id':second['id']}).status_code==404
    assert ok(g.post('/api/chat/'+sid+'/read',json={'message_id':second['id']}))['read']==1
    assert ok(g.get('/api/notifications'))['unread']==1


def test_preferences_limit_does_not_partially_apply_other_changes():
    c,g,o,sid=conversations();uid=c.get('/api/me').json()['id'];ids=[]
    with m.db() as db:
        for index in range(256):
            space=secrets.token_urlsafe(12);ids.append(space)
            db.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',(space,'family','List '+str(index),uid,m.now()))
            db.execute('INSERT INTO space_members VALUES(?,?,?)',(space,uid,m.now()))
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]);settings['chat']={'favorites':ids[:-1]+[sid],'pinned':ids}
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    assert c.put('/api/chat/'+sid+'/preferences',json={'favorite':False,'pinned':True}).status_code==422
    assert c.put('/api/chat/'+ids[-1]+'/preferences',json={'favorite':True}).status_code==422
    with m.db() as db:assert json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])['chat']==settings['chat']
    assert ok(c.put('/api/chat/'+ids[0]+'/preferences',json={'favorite':True}))=={'favorite':True,'pinned':True}
    ok(c.put('/api/chat/'+ids[0]+'/preferences',json={'favorite':False,'pinned':False}))
    assert ok(c.put('/api/chat/'+ids[-1]+'/preferences',json={'favorite':True}))=={'favorite':True,'pinned':True}
    assert ok(c.put('/api/chat/'+sid+'/preferences',json={'pinned':True}))=={'favorite':True,'pinned':True}


def test_search_uses_decrypted_text_and_bounded_cursor_across_sparse_pages():
    c,g,o,sid=conversations();foreign=family(c);uid=c.get('/api/me').json()['id'];stamp=m.now()
    with m.db() as db:
        for index in range(1010):
            body='СЕКРЕТНЫЙ фильм' if index in (0,5,1009) else 'Другой текст'
            db.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',(sid,uid,secrets.token_hex(12),body,stamp))
        old=db.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',(sid,uid,secrets.token_hex(12),'секретный старый',cutoff(stamp))).lastrowid
        db.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',(foreign,uid,secrets.token_hex(12),'секретный чужой',stamp))
        if m.database.kind=='postgres' and m.data_cipher.enabled:
            stored=db.raw.execute('SELECT body FROM messages WHERE space_id=%s ORDER BY id LIMIT 1',(sid,)).fetchone()[0]
            assert 'СЕКРЕТНЫЙ' not in stored
    first=ok(g.get('/api/chat/'+sid+'/search',params={'q':'  секретный  '}))
    assert len(first['items'])==1 and first['items'][0]['body']=='СЕКРЕТНЫЙ фильм' and first['more'] and first['next_before']
    second=ok(g.get('/api/chat/'+sid+'/search',params={'q':'секретный','before':first['next_before']}))
    assert len(second['items'])==2 and not second['more'] and second['next_before'] is None
    assert [item['id'] for item in second['items']]==sorted(item['id'] for item in second['items'])
    assert old not in {item['id'] for item in first['items']+second['items']}
    assert o.get('/api/chat/'+sid+'/search?q=секретный').status_code==404
    assert g.get('/api/chat/'+foreign+'/search?q=секретный').status_code==404
    assert g.get('/api/chat/'+sid+'/search?q= ').status_code==422
    assert g.get('/api/chat/'+sid+'/search?q='+('a'*121)).status_code==422
    assert g.get('/api/chat/'+sid+'/search',params={'q':'секретный','before':2**64}).status_code==422
    assert ok(g.get('/api/chat/'+sid+'/search?q=несуществующий'))['items']==[]


def test_search_results_paginate_without_repeating_matches():
    c,g,o,sid=conversations();uid=c.get('/api/me').json()['id']
    with m.db() as db:
        for index in range(105):db.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',(sid,uid,secrets.token_hex(12),'find '+str(index),m.now()))
    first=ok(g.get('/api/chat/'+sid+'/search?q=find'));assert len(first['items'])==100 and first['more']
    second=ok(g.get('/api/chat/'+sid+'/search',params={'q':'find','before':first['next_before']}))
    assert len(second['items'])==5 and not second['more']
    assert len({item['id'] for item in first['items']+second['items']})==105


def test_replies_are_durable_scoped_and_delivered_over_the_live_channel():
    c,g,o,sid=conversations();source=ok(send(c,sid,body='Исходное сообщение '+('я'*300)));other=family(c);foreign=ok(send(c,other,body='Чужая тайна'))
    data={'body':'Согласен','client_id':'reply-test-123','reply_to':source['id']}
    assert g.post('/api/chat/'+sid+'/messages',json=data|{'reply_to':foreign['id']}).status_code==404
    assert g.post('/api/chat/'+sid+'/messages',json=data|{'reply_to':True}).status_code==422
    with c.websocket_connect('/api/live/chat/'+sid) as ws:
        hello(ws,c);next_event(ws,'ready')
        saved=ok(g.post('/api/chat/'+sid+'/messages',json=data))
        event=next_event(ws,'message')['message'];assert event['id']==saved['id'] and event['reply']==saved['reply']
        ws.send_json({'type':'message','body':'Ответ через WebSocket','client_id':'ws-reply-test-123','reply_to':source['id']})
        for _ in range(20):
            socket_reply=next_event(ws,'message')['message']
            if socket_reply['client_id']=='ws-reply-test-123':break
        else:pytest.fail('The WebSocket reply was not delivered.')
        assert socket_reply['reply']==saved['reply'] and socket_reply['body']=='Ответ через WebSocket'
    assert {key:saved['reply'][key] for key in ('id','sender','body','unavailable')}=={'id':source['id'],'sender':'owner','body':source['body'][:240],'unavailable':False}
    assert saved['reply']['sender_id']==source['sender_id'] and saved['reply']['sender_profile']['login']=='owner'
    assert ok(g.post('/api/chat/'+sid+'/messages',json=data))['id']==saved['id']
    assert ok(c.get('/api/chat/'+sid+'/messages'))['items'][-1]['reply']==saved['reply']
    assert ok(c.get('/api/chat/rooms'))['items'][0]['last_message']['reply']==saved['reply']
    assert ok(g.get('/api/chat/'+sid+'/messages'))['items'][-1]['id']==socket_reply['id']
    with m.db() as db:
        stored=db.execute('SELECT body FROM messages WHERE id=?',(saved['id'],)).fetchone()[0]
        assert stored.startswith(REPLY_PREFIX) and decode(stored)==('Согласен',[])
        stored_reply=db.execute('SELECT body FROM messages WHERE id=?',(socket_reply['id'],)).fetchone()[0]
        assert stored_reply.startswith(REPLY_PREFIX) and decode(stored_reply)==('Ответ через WebSocket',[])


def test_expired_reply_is_unavailable_without_resurrecting_or_leaking_text():
    c,g,o,sid=conversations();source=ok(send(c,sid,body='Текст с истекшим сроком'))
    saved=ok(g.post('/api/chat/'+sid+'/messages',json={'body':'Ответ останется','client_id':'reply-expiry-123','reply_to':source['id']}))
    with m.db() as db:db.execute('UPDATE messages SET created=? WHERE id=?',(cutoff(m.now()),source['id']))
    history=ok(c.get('/api/chat/'+sid+'/messages'))['items'];assert len(history)==1
    assert history[0]['reply']=={'id':source['id'],'sender':None,'body':'','unavailable':True,'sender_id':None,'sender_profile':{},'mentions':[]}
    assert c.post('/api/chat/'+sid+'/messages',json={'body':'Новый ответ','client_id':'reply-expiry-456','reply_to':source['id']}).status_code==404
    with m.db() as db:
        purge(db,m.now());validate_archive(db,lambda:pytest.fail('An expired reply target is allowed.'))
        assert db.execute('SELECT 1 FROM messages WHERE id=?',(saved['id'],)).fetchone()
    assert ok(c.get('/api/chat/'+sid+'/messages'))['items'][0]['reply']['unavailable']


def test_reply_and_legacy_envelopes_preserve_full_backups_and_prefix_text():
    from tests.test_admin import backup,headers
    import pyotp
    c,g,o,sid=conversations();file=upload(c,sid);source=ok(send(c,sid,[file],body='С вложением'))
    reply=ok(g.post('/api/chat/'+sid+'/messages',json={'body':'Ответ','client_id':'reply-backup-123','reply_to':source['id']}))
    for prefix in (PREFIX,REPLY_PREFIX):assert decode(encode(prefix+'literal',[]))==(prefix+'literal',[])
    assert decode('Старое обычное сообщение')==('Старое обычное сообщение',[])
    assert decode(PREFIX+json.dumps({'text':'Старая вставка','attachments':[]}))==('Старая вставка',[])
    ok(c.put('/api/chat/'+sid+'/preferences',json={'favorite':True,'pinned':True}))
    archive=backup(c);ok(c.post('/api/admin/backups/restore',content=archive,headers=headers()))
    c=client();logged=ok(c.post('/api/login',json={'login':'owner','password':'Owner-password-123'}));c.headers['x-csrf-token']=logged['csrf']
    with m.db() as db:seed=m.cipher.decrypt(db.execute("SELECT totp FROM users WHERE login='owner'").fetchone()[0].encode()).decode()
    verified=ok(c.post('/api/2fa/verify',json={'code':pyotp.TOTP(seed).at(m.now())}));c.headers['x-csrf-token']=verified['csrf']
    assert ok(c.get('/api/chat/'+sid+'/messages'))['items'][-1]['reply']==reply['reply']
    assert c.get(file['url']).content==b'hello world'
    assert ok(c.get('/api/chat/rooms'))['items'][0]['favorite'] and ok(c.get('/api/chat/rooms'))['items'][0]['pinned']


def test_personal_archives_do_not_transfer_shared_room_preferences():
    from tests.test_personal_data import export,unseal,restore,seal
    c,g,o,sid=conversations();ok(c.put('/api/chat/'+sid+'/preferences',json={'favorite':True}))
    content=export(c);manifest,files=unseal(content)
    assert 'chat' not in manifest['data']['settings'] and sid not in json.dumps(manifest)
    assert validate_settings({'chat':{'favorites':[sid],'pinned':[]}},set())=={}
    for value in ({'favorites':[sid,sid]},{'pinned':['invalid']},{'favorites':'bad'},{'hidden':[]}):
        with pytest.raises(BackupError):validate_settings({'chat':value},set())
    manifest['data']['settings']['chat']={'favorites':[sid],'pinned':[]}
    ok(restore(c,seal(manifest,files)))
    assert ok(c.get('/api/chat/rooms'))['items'][0]['favorite']


def test_archive_reply_rejects_cross_room_and_future_references():
    c,g,o,sid=conversations();other=family(c);target=ok(send(c,other,body='Private'));msg=ok(send(c,sid,body='Safe'))
    def invalid():raise BackupError('Invalid reference')
    with m.db() as db:
        for reference in (target['id'],msg['id']+1):
            db.execute('UPDATE messages SET body=? WHERE id=?',(encode('reply',[],reference),msg['id']))
            with pytest.raises(BackupError):validate_archive(db,invalid)


def test_read_receipts_are_durable_monotonic_private_and_in_full_backup():
    from tests.test_admin import backup,headers
    c,g,o,sid=conversations();first=ok(send(c,sid,body='Первое'));second=ok(send(c,sid,body='Второе'));uid=g.get('/api/me').json()['id']
    assert c.get('/api/chat/'+sid+'/messages').json()['reads']=={}
    # Reading notification lists or marking all notifications does not read chat.
    ok(g.post('/api/notifications/read-all'))
    assert c.get('/api/chat/'+sid+'/messages').json()['reads']=={}
    assert ok(g.get('/api/chat/rooms'))['unread']==2
    ok(g.post('/api/chat/'+sid+'/read',json={'message_id':second['id']}))
    ok(g.post('/api/chat/'+sid+'/read',json={'message_id':first['id']}))
    assert c.get('/api/chat/'+sid+'/messages?after='+str(second['id'])).json()['reads']=={uid:second['id']}
    assert o.get('/api/chat/'+sid+'/messages').status_code==404
    body=backup(c)
    with m.db() as db:db.execute('DELETE FROM chat_reads')
    restored=c.post('/api/admin/backups/restore',content=body,headers=headers());assert restored.status_code==200,restored.text
    # Restore intentionally invalidates all sessions. Verify persisted rows
    # directly, rather than trying to reuse a revoked browser cookie.
    with m.db() as db:assert db.execute('SELECT message_id FROM chat_reads WHERE space_id=? AND user_id=?',(sid,uid)).fetchone()[0]==second['id']



def test_typing_is_ephemeral_server_identified_and_room_scoped(monkeypatch):
    c,g,o,sid=conversations();uid=c.get('/api/me').json()['id'];stamp=m.now()
    with c.websocket_connect('/api/live/chat/'+sid) as a,g.websocket_connect('/api/live/chat/'+sid) as b:
        hello(a,c,11);hello(b,g,22);next_event(a,'ready');next_event(b,'ready')
        a.send_json({'type':'presence','state':{'typing':True}})
        state=next_event(b,'presence')['state'];assert state['user']['id']==uid and stamp+6<=state['until']<=stamp+8
        assert g.get('/api/chat/typing').json()['rooms'][sid][0]['user']['id']==uid
        assert o.get('/api/chat/rooms').json()['items']==[]
        a.send_json({'type':'presence','state':{'typing':True,'user':{'id':'imposter'}}})
        assert next_event(a,'error')['status']==422
        monkeypatch.setattr(m,'now',lambda:stamp+7)
        assert g.get('/api/chat/typing').json()['rooms']=={}
        a.send_json({'type':'presence','state':None});assert next_event(b,'presence')['state'] is None


def test_lightweight_unread_matches_rooms_and_preserves_read_and_expiry_rules():
    c,g,o,sid=conversations();other=family(c);join(c,g,other)
    first=ok(send(c,sid,body='First'));second=ok(send(c,other,body='Elsewhere'))
    ok(send(g,sid,body='Own message'))
    assert ok(g.get('/api/chat/unread'))=={'unread':2}
    assert ok(o.get('/api/chat/unread'))=={'unread':0}
    ok(g.post('/api/notifications/read-all'))
    assert ok(g.get('/api/chat/unread'))['unread']==2
    ok(g.post('/api/chat/'+sid+'/read',json={'message_id':first['id']}))
    assert ok(g.get('/api/chat/unread'))['unread']==1
    with m.db() as db:db.execute('UPDATE messages SET expires=? WHERE id=?',(m.now()-1,second['id']))
    assert ok(g.get('/api/chat/unread'))['unread']==ok(g.get('/api/chat/rooms'))['unread']==0
    assert client().get('/api/chat/unread').status_code==401
    enabled(g,False);assert g.get('/api/chat/unread').status_code==403


def test_optional_redis_counter_preserves_membership_module_and_expiry_rules(monkeypatch):
    import os
    from app.redis_cache import RedisCache
    url=os.getenv('DRG_TEST_REDIS_URL')
    if not url:pytest.skip('Requires optional real Redis for the counter path')
    cache=RedisCache(m.database,url);cache.start()
    monkeypatch.setattr(m.database,'redis_cache',cache)
    try:
        c,g,o,sid=conversations()
        msg=ok(send(c,sid,body='Cached incoming'))
        stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
        with m.db() as db:db.execute('UPDATE messages SET expires=? WHERE id=?',(stamp+1,msg['id']))
        assert ok(g.get('/api/chat/unread'))=={'unread':1}
        hits=cache.stats()['hits']
        assert ok(g.get('/api/chat/unread'))=={'unread':1}
        assert cache.stats()['hits']==hits+1
        monkeypatch.setattr(m,'now',lambda:stamp+1)
        assert ok(g.get('/api/chat/unread'))=={'unread':0}
        another=ok(send(c,sid,body='Another message'))
        assert ok(g.get('/api/chat/unread'))=={'unread':1}
        # Calendar retention can expire without a SQL mutation, like a timer.
        with m.db() as db:
            db.execute('UPDATE messages SET created=? WHERE id=?',(cutoff(stamp+2),another['id']))
            db.execute('UPDATE space_members SET created=1 WHERE space_id=?',(sid,))
        assert ok(g.get('/api/chat/unread'))=={'unread':1}
        monkeypatch.setattr(m,'now',lambda:stamp+2)
        assert ok(g.get('/api/chat/unread'))=={'unread':0}
        ok(send(c,sid,body='Fresh before membership removal'))
        assert ok(g.get('/api/chat/unread'))=={'unread':1}
        uid=ok(g.get('/api/me'))['id']
        ok(c.delete('/api/social/families/'+sid+'/members/'+uid))
        assert ok(g.get('/api/chat/unread'))=={'unread':0}
        enabled(g,False);assert g.get('/api/chat/unread').status_code==403
        assert client().get('/api/chat/unread').status_code==401
    finally:
        keys=list(cache.client.scan_iter(match=cache.prefix+':*',count=100))
        if keys:cache.client.delete(*keys)
        cache.close()
