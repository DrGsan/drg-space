"""Real inbox/push/read flows against a private archive transport; no live sends."""
import json
import time
import httpx
import pytest
from app.modules.family_photo_events import state
from tests.test_security import owner,member,reset
from app import main as m
from tests.test_family_photos import grant,enable,G1,G2
from tests.test_web_push import register,push_service

@pytest.fixture
def archive_events(monkeypatch):
    bridge=m.family_photos
    for key,value in {'url':'http://archive.internal:8090','token':'private-service-'*4,'cached':None,'checked_at':0,'ever_available':False,'reader_id':99}.items():monkeypatch.setattr(bridge,key,value)
    messages={G1:{10:dict(id=10,created=m.now()-100,text='Old import',sender='Анна',sender_id='42',media=[],version='v10',edited=0)},G2:{}}
    network={'down':False}
    def handler(request):
        assert request.headers['authorization']=='Bearer '+bridge.token
        if network['down']:raise httpx.ConnectError('offline',request=request)
        path=request.url.path
        if path=='/v1/status':return httpx.Response(200,json={'service':'family-photo','api_version':1,'worker_online':True,'can_send':False,'reader_id':99})
        if path=='/v1/groups':return httpx.Response(200,json={'items':[{'id':gid,'title':'Семья '+gid,'latest_message_id':max(rows,default=0)} for gid,rows in messages.items()]})
        parts=path.split('/');gid=parts[3] if len(parts)>3 else ''
        rows=messages.get(gid,{})
        if path.endswith('/updates'):
            after=int(request.url.params['after']);return httpx.Response(200,json={'items':[rows[mid] for mid in sorted(rows) if mid>after][:100]})
        if path.endswith('/unread'):
            after=int(request.url.params['after']);excluded=request.url.params.get('exclude_sender')
            return httpx.Response(200,json={'unread':sum(mid>after and str(message['sender_id'])!=excluded for mid,message in rows.items())})
        if path.endswith('/message-states'):
            ids=json.loads(request.content)['ids'];return httpx.Response(200,json={'items':[{'id':mid,'edited':rows[mid]['edited'],'fingerprint':rows[mid]['version']} for mid in ids if mid in rows]})
        if len(parts)==6 and parts[4]=='messages':
            message=rows.get(int(parts[5]));return httpx.Response(200 if message else 404,json=message or {'detail':'missing'})
        return httpx.Response(404,json={'detail':'missing'})
    monkeypatch.setattr(bridge,'client',lambda timeout=5:httpx.Client(base_url=bridge.url+'/',headers={'Authorization':'Bearer '+bridge.token},transport=httpx.MockTransport(handler)))
    def add(mid=11,gid=G1,**values):
        message=dict(id=mid,created=m.now(),text='Новое сообщение',sender='Анна',sender_id='42',media=[],version='v'+str(mid),edited=0)
        message.update(values);messages[gid][mid]=message;return message
    return bridge,messages,add,network


def connected(archive_events):
    bridge,_,_,_=archive_events
    admin,_,_=owner();guest,_=member(admin)
    uid=guest.get('/api/me').json()['id']
    assert grant(admin,uid,[G1]).status_code==200
    enable(admin)
    bridge.poll_messages(m.now())
    return admin,guest,uid


def notices(client):return [row for row in client.get('/api/notifications').json()['items'] if row['kind']=='family_message']

@pytest.mark.parametrize('body,media,expected',[('Привет',[],'Привет'),('',[{'kind':'photo','state':'pending'}],'Фото'),('',[{'kind':'video','state':'ready'}],'Видео')])
def test_new_text_photo_video_deliver_once_with_real_unread_and_push(archive_events,push_service,body,media,expected):
    bridge,_,add,_=archive_events
    admin,guest,_=connected(archive_events)
    register(guest)
    assert notices(guest)==[]
    add(text=body,media=media)
    bridge.poll_messages(m.now());bridge.poll_messages(m.now())
    assert len(notices(guest))==1 and notices(guest)[0]['payload']['preview']==expected
    assert guest.get('/api/family-photos/unread').json()['unread']==1
    assert guest.get('/api/family-photos/groups').json()['items'][0]['unread']==1
    assert m.web_push.deliver_one() and not m.web_push.deliver_one()
    assert len(push_service)==1
    payload=push_service[0][1]
    assert payload['url']=='/#chat/telegram:'+G1 and payload['body']==expected


@pytest.mark.parametrize('kind,media,expected',[('voice',[],'Аудиосообщение'),('sticker',[{'kind':'sticker','state':'ready'}],'Стикер')])
def test_voice_and_stickers_have_notifications_and_plain_previews(archive_events,push_service,kind,media,expected):
    bridge,_,add,_=archive_events;admin,guest,_=connected(archive_events);register(guest)
    add(text='',content_type=kind,duration=14,media=media)
    bridge.poll_messages(m.now())
    assert notices(guest)[0]['payload']['preview']==expected
    assert m.web_push.deliver_one() and push_service[0][1]['body']==expected


def test_bell_actions_do_not_read_chat_and_visible_chat_receipt_does(archive_events):
    bridge,_,add,_=archive_events;admin,guest,_=connected(archive_events)
    add(11);add(12);bridge.poll_messages(m.now())
    guest.post('/api/notifications/read-all')
    for row in notices(guest):guest.delete('/api/notifications/'+str(row['id']))
    assert guest.get('/api/family-photos/unread').json()['unread']==2
    assert guest.post('/api/family-photos/groups/'+G1+'/read',json={'message_id':12}).status_code==200
    assert guest.get('/api/family-photos/unread').json()['unread']==0
    add(13);bridge.poll_messages(m.now())
    assert guest.get('/api/family-photos/unread').json()['unread']==1


@pytest.mark.parametrize('revoke',['grant','module','chat'])
def test_revocation_cancels_already_queued_delivery(archive_events,push_service,revoke):
    bridge,_,add,_=archive_events;admin,guest,uid=connected(archive_events)
    register(guest);add();bridge.poll_messages(m.now())
    if revoke=='grant':assert grant(admin,uid,[],False).status_code==200
    else:assert guest.put('/api/modules',json={'enabled':{'family_photos' if revoke=='module' else 'chat':False}}).status_code==200
    assert notices(guest)==[]
    assert m.web_push.deliver_one() and push_service==[]


def test_groups_are_private_and_owner_reader_messages_do_not_self_alert(archive_events):
    bridge,_,add,_=archive_events;admin,guest,_=connected(archive_events)
    add(gid=G2);bridge.poll_messages(m.now())
    assert notices(guest)==[] and len(notices(admin))==1
    add(12,sender_id='99');bridge.poll_messages(m.now())
    assert len(notices(admin))==1 and len(notices(guest))==1
    assert admin.get('/api/family-photos/unread').json()['unread']==1


def test_restart_watermarks_and_pagination_never_repeat_import(archive_events):
    bridge,_,add,_=archive_events;_,guest,_=connected(archive_events)
    for mid in range(11,116):add(mid)
    bridge.poll_messages(m.now())
    assert len(notices(guest))==100
    bridge.poll_messages(m.now());bridge.poll_messages(m.now())
    uid=guest.get('/api/me').json()['id']
    with m.db() as c:
        assert state(c)['cursors'][G1]==115
        count=c.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND kind='family_message'",(uid,)).fetchone()[0]
    assert count==105


def test_edits_update_preview_and_deletes_remove_notice_and_pending_push(archive_events,push_service):
    bridge,messages,add,_=archive_events;_,guest,_=connected(archive_events)
    register(guest);add();bridge.poll_messages(m.now())
    messages[G1][11].update(text='Исправлено',edited=m.now(),version='edited')
    bridge.poll_messages(m.now())
    assert notices(guest)[0]['payload']['preview']=='Исправлено'
    messages[G1].pop(11)
    assert m.web_push.deliver_one() and push_service==[]
    bridge.poll_messages(m.now())
    assert notices(guest)==[] and guest.get('/api/family-photos/unread').json()['unread']==0
    with m.db() as c:assert state(c)['cursors'][G1]==11


def test_outage_keeps_push_for_retry_and_checks_latest_source_on_delivery(archive_events,push_service,monkeypatch):
    bridge,messages,add,network=archive_events;_,guest,_=connected(archive_events)
    register(guest);add();bridge.poll_messages(m.now());network['down']=True
    assert m.web_push.deliver_one() and push_service==[]
    with m.db() as c:assert c.execute('SELECT COUNT(*) FROM push_deliveries').fetchone()[0]==1
    network['down']=False;messages[G1][11]['text']='Актуальный текст'
    monkeypatch.setattr(m.web_push,'now',lambda:int(time.time())+31)
    assert m.web_push.deliver_one() and push_service[0][1]['body']=='Актуальный текст'


def test_receipt_rejects_foreign_and_missing_ids_and_boolean(archive_events):
    _,_,add,_=archive_events;_,guest,_=connected(archive_events)
    add(gid=G2)
    assert guest.post('/api/family-photos/groups/'+G2+'/read',json={'message_id':11}).status_code==403
    assert guest.post('/api/family-photos/groups/'+G1+'/read',json={'message_id':999}).status_code==404
    assert guest.post('/api/family-photos/groups/'+G1+'/read',json={'message_id':True}).status_code==422


def test_new_grant_starts_with_current_history_and_album_announces_once(archive_events):
    bridge,_,add,_=archive_events;admin,guest,uid=connected(archive_events)
    assert grant(admin,uid,[],False).status_code==200
    add(11);bridge.poll_messages(m.now())
    assert grant(admin,uid,[G1]).status_code==200
    assert guest.get('/api/family-photos/unread').json()['unread']==0
    for mid in (12,13,14):add(mid,album_id='album-1',text='',media=[{'kind':'photo'}])
    bridge.poll_messages(m.now())
    assert len(notices(guest))==1 and notices(guest)[0]['payload']['message_id']==14
    assert guest.get('/api/family-photos/unread').json()['unread']==3


def test_visible_messages_suppress_duplicate_alerts_without_reading_the_family_room(archive_events,push_service):
    import asyncio,secrets
    bridge,_,add,_=archive_events;_,guest,uid=connected(archive_events)
    register(guest)
    with m.db() as c:token=c.execute("SELECT token FROM sessions WHERE user_id=? AND stage='full'",(uid,)).fetchone()[0]
    lease=m.process_coordinator._register('inbox',uid,('inbox',uid),secrets.token_hex(8),{'chat_active':True,'session_token':token,'seen':m.now()})
    try:
        add();bridge.poll_messages(m.now())
        assert notices(guest)==[] and guest.get('/api/family-photos/unread').json()['unread']==1
        assert not m.web_push.deliver_one() and push_service==[]
    finally:asyncio.run(lease.close())


def test_revocation_during_archive_fetch_is_rechecked_before_sending(archive_events,push_service,monkeypatch):
    bridge,_,add,_=archive_events;admin,guest,uid=connected(archive_events)
    register(guest);add();bridge.poll_messages(m.now())
    original=bridge.push_message
    def revoke(payload):
        message=original(payload)
        assert grant(admin,uid,[],False).status_code==200
        return message
    monkeypatch.setattr(bridge,'push_message',revoke)
    assert m.web_push.deliver_one() and push_service==[]


def test_read_cursor_rechecks_a_concurrent_revocation(archive_events,monkeypatch):
    bridge,_,add,_=archive_events;admin,guest,uid=connected(archive_events)
    add();original=bridge.fetch
    def revoke(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path.endswith('/messages/11'):assert grant(admin,uid,[],False).status_code==200
        return result
    monkeypatch.setattr(bridge,'fetch',revoke)
    assert guest.post('/api/family-photos/groups/'+G1+'/read',json={'message_id':11}).status_code==403
    with m.db() as c:
        settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
        assert 'family_photo_access' not in settings and 'family_photo_reads' not in settings


def test_ordinary_rooms_never_contact_the_archive(archive_events,monkeypatch):
    bridge,_,add,network=archive_events;_,guest,_=connected(archive_events)
    add();bridge.poll_messages(m.now())
    assert guest.get('/api/family-photos/unread').json()['unread']==1
    def forbidden(*args,**kwargs):
        raise AssertionError('Ordinary rooms must not contact family-photo')
    with monkeypatch.context() as patch:
        patch.setattr(bridge,'client',forbidden)
        response=guest.get('/api/chat/rooms')
        assert response.status_code==200 and response.json()['unread']==0
    network['down']=True
    assert guest.get('/api/family-photos/unread').status_code==503
    assert guest.get('/api/chat/rooms').status_code==200
    network['down']=False
    assert guest.get('/api/family-photos/unread').json()['unread']==1


def test_site_author_does_not_receive_own_notification_and_late_binding_removes_it(archive_events,push_service):
    bridge,rows,add,_=archive_events;admin,guest,uid=connected(archive_events);register(guest)
    add(sender='Бот',sender_id='77',site_actor_id=uid)
    bridge.poll_messages(m.now())
    assert not notices(guest) and len(notices(admin))==1
    incoming=add(12,sender='Бот',sender_id='77')
    bridge.poll_messages(m.now());assert len(notices(guest))==1
    incoming.update(site_actor_id=uid,version='v12-site')
    bridge.poll_messages(m.now());assert not notices(guest)
    while m.web_push.deliver_one():pass
    assert not push_service


def test_unchanged_source_checks_reuse_notice_ids_without_reading_history_again(archive_events,monkeypatch):
    bridge,messages,add,_=archive_events;_,guest,_=connected(archive_events)
    add();bridge.poll_messages(m.now())
    original=bridge.db;queries=[]
    from contextlib import contextmanager
    from tests.test_idle_database_work import Counted
    @contextmanager
    def tracked():
        with original() as c:yield Counted(c,queries)
    monkeypatch.setattr(bridge,'db',tracked)
    bridge.refresh_notices(m.now()+5)
    assert not queries,'Unchanged source fingerprints must not open SQL transactions.'
    messages[G1][11].update(text='Edited without a new message ID',version='new-version')
    bridge.refresh_notices(m.now()+10)
    assert notices(guest)[0]['payload']['preview']=='Edited without a new message ID'
    assert not any('created>?' in q for q in queries),'An edit updates its own notices, without rescanning history.'
    queries.clear();bridge.refresh_notices(m.now()+61)
    assert any('created>?' in q for q in queries),'Periodic recovery still reconciles the complete retained ID set.'


def test_notice_cache_only_acknowledges_a_committed_update(archive_events,monkeypatch):
    from contextlib import contextmanager
    bridge,messages,add,_=archive_events;_,guest,_=connected(archive_events)
    add();bridge.poll_messages(m.now())
    messages[G1][11].update(text='Retry this edit',version='after-rollback')
    original=bridge.db
    @contextmanager
    def rolled_back():
        with original() as c:
            yield c
            raise RuntimeError('simulated commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(bridge,'db',rolled_back)
        with pytest.raises(RuntimeError,match='simulated commit failure'):bridge.refresh_notices(m.now()+5)
    bridge.refresh_notices(m.now()+10)
    assert notices(guest)[0]['payload']['preview']=='Retry this edit'


def test_cached_source_edit_never_recreates_a_user_deleted_notice(archive_events):
    bridge,messages,add,_=archive_events;_,guest,_=connected(archive_events)
    add();bridge.poll_messages(m.now());nid=notices(guest)[0]['id']
    assert guest.delete('/api/notifications/'+str(nid)).status_code==200
    messages[G1][11].update(text='Edit after user deletion',version='edited-after-delete')
    bridge.refresh_notices(m.now()+5)
    assert not notices(guest)


def test_fully_read_groups_skip_count_rpc_but_new_messages_are_seen_next_refresh(archive_events):
    bridge,rows,add,_=archive_events
    admin,guest,uid=connected(archive_events)
    calls=[];fetch=bridge.fetch
    def counted(path,*args,**kwargs):
        calls.append(path)
        return fetch(path,*args,**kwargs)
    bridge.fetch=counted
    try:
        assert guest.get('/api/family-photos/unread').json()['unread']==0
        assert not any('/unread?' in path for path in calls)
        add(11)
        calls.clear()
        assert guest.get('/api/family-photos/unread').json()['unread']==1
        assert sum('/unread?' in path for path in calls)==1
        assert not any(G2 in path for path in calls if '/unread?' in path), 'Inaccessible groups must not be queried.'
        assert guest.post('/api/family-photos/groups/'+G1+'/read',json={'message_id':11}).status_code==200
        calls.clear()
        assert guest.get('/api/family-photos/unread').json()['unread']==0
        assert not any('/unread?' in path for path in calls)
    finally:bridge.fetch=fetch


def test_legacy_or_untrusted_latest_metadata_keeps_exact_unread_query(archive_events):
    bridge,_,_,_=archive_events
    admin,guest,uid=connected(archive_events)
    with m.db() as c:session=dict(c.execute('SELECT role,settings FROM users WHERE id=?',(uid,)).fetchone())
    session['user_id']=uid
    calls=[];fetch=bridge.fetch
    def counted(path,*args,**kwargs):
        calls.append(path)
        return fetch(path,*args,**kwargs)
    bridge.fetch=counted
    try:
        for value in [None,True,'10',-1,2**53]:
            calls.clear();group={'id':G1,'cursor':10}
            if value is not None:group['latest_message_id']=value
            bridge.family_counts(session,[group])
            assert any('/unread?' in path for path in calls), 'Only a valid authoritative latest ID permits skipping the count.'
        calls.clear()
        assert bridge.family_counts(session,[{'id':G2,'latest_message_id':0}])=={}
        assert not calls
    finally:bridge.fetch=fetch
