"""Per-user mute affects alerts on every device without reading the conversation."""
import json
import pytest
from tests.test_security import client, m, reset
from tests.test_chat_workspace import conversations
from tests.test_chat_retention import send
from tests.test_next_features import ok
from tests.test_web_push import register, push_service
from tests.test_family_photo_events import archive_events, connected, notices
from tests.test_family_photos import G1, G2


def mute(c, sid, seconds):
    return c.put('/api/chat/'+sid+'/notifications', json={'seconds':seconds})


@pytest.mark.parametrize('seconds', [3600, 28800, 86400, 604800])
def test_temporary_mute_is_personal_expires_and_keeps_unread(monkeypatch, push_service, seconds):
    stamp=m.now();monkeypatch.setattr(m, 'now', lambda:stamp)
    c,g,o,sid=conversations();register(g)
    assert ok(mute(g,sid,seconds))=={'mute_until':stamp+seconds}
    assert next(r for r in ok(g.get('/api/chat/rooms'))['items'] if r['id']==sid)['mute_until']==stamp+seconds
    assert next(r for r in ok(c.get('/api/chat/rooms'))['items'] if r['id']==sid)['mute_until']==0
    ok(send(c,sid,body='Тихое сообщение'))
    assert ok(g.get('/api/chat/rooms'))['unread']==1
    assert ok(g.get('/api/notifications'))['items'][0]['silent']
    assert not m.web_push.deliver_one() and push_service==[]
    from app.chat_notifications import KEY,mute_until
    uid=ok(g.get('/api/me'))['id']
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
        assert mute_until(settings,sid,stamp+seconds-1)==stamp+seconds
        assert mute_until(settings,sid,stamp+seconds)==0
        settings[KEY][sid]=stamp
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    assert next(r for r in ok(g.get('/api/chat/rooms'))['items'] if r['id']==sid)['mute_until']==0
    ok(send(c,sid,body='Уведомления снова работают',cid='after-mute-123'))
    assert m.web_push.deliver_one() and len(push_service)==1
    assert ok(g.get('/api/chat/rooms'))['unread']==2
    # The old suppressed alert must stay silent after expiry and reconnect.
    assert next(n for n in ok(g.get('/api/notifications'))['items'] if n['payload']['preview']=='Тихое сообщение')['silent']


def test_mute_rejects_foreign_anonymous_and_permanent_regular_rooms():
    c,g,o,sid=conversations()
    assert mute(o,sid,3600).status_code==404
    assert mute(client(),sid,3600).status_code==401
    for value in (-1,True,'3600',None,1,-2,604801):
        assert mute(g,sid,value).status_code==422
    assert g.put('/api/chat/'+sid+'/notifications',json={'seconds':3600,'user_id':'other'}).status_code==422
    ok(mute(g,sid,3600));ok(g.put('/api/chat/'+sid+'/preferences',json={'pinned':True}))
    assert ok(g.get('/api/chat/rooms'))['items'][0]['mute_until']>m.now()
    assert ok(mute(g,sid,0))=={'mute_until':0}


def test_muting_cancels_already_queued_push_without_reading(push_service):
    c,g,o,sid=conversations();register(g);ok(send(c,sid,body='Уже в очереди'))
    ok(mute(g,sid,3600));ok(mute(g,sid,0))
    assert not m.web_push.deliver_one() and push_service==[]
    assert ok(g.get('/api/chat/rooms'))['unread']==1


def test_family_toggle_keeps_unread_is_private_and_cancels_queued(archive_events,push_service):
    bridge,_,add,_=archive_events;admin,guest,_=connected(archive_events);sid='telegram:'+G1
    register(guest);add();bridge.poll_messages(m.now())
    assert ok(mute(guest,sid,-1))=={'mute_until':-1}
    add(12);bridge.poll_messages(m.now())
    assert not m.web_push.deliver_one() and push_service==[]
    groups=ok(guest.get('/api/family-photos/groups'))['items']
    assert groups[0]['mute_until']==-1 and groups[0]['unread']==2
    assert ok(guest.get('/api/family-photos/unread'))['unread']==2
    assert notices(guest)[0]['silent']
    assert all(g['mute_until']==0 for g in ok(admin.get('/api/family-photos/groups'))['items'])
    assert mute(guest,'telegram:'+G2,-1).status_code==403
    assert mute(guest,'telegram:invalid',-1).status_code==403
    assert ok(mute(guest,sid,0))=={'mute_until':0}
    add(13);bridge.poll_messages(m.now());assert m.web_push.deliver_one() and len(push_service)==1
    assert ok(guest.get('/api/family-photos/unread'))['unread']==3


def test_worker_rechecks_mute_before_sending_an_existing_delivery(push_service):
    c,g,o,sid=conversations();register(g);ok(send(c,sid,body='Queued before mute'))
    uid=ok(g.get('/api/me'))['id']
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
        settings['chat_notification_mutes']={sid:m.now()+3600}
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    assert m.web_push.deliver_one() and push_service==[]
    assert not m.web_push.deliver_one()
    assert ok(g.get('/api/chat/rooms'))['unread']==1
