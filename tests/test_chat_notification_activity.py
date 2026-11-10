"""Visible Messages suppresses duplicate alerts without reading other rooms."""
import asyncio
import secrets

import pytest

from tests.test_security import reset
from app import main as m
from app.http import WebSocketDisconnect
from app.multiprocess import ProcessCoordinator
from app.notifications import CHAT_ACTIVITY_TTL,chat_is_foreground
from tests.test_social import next_event
from tests.test_web_push import queued,message,push_service


def auth(ws,client,active=True):
    ws.send_json({'type':'auth','csrf':client.headers['x-csrf-token'],'chat_active':active})
    return next_event(ws,'ready')


def activity(ws,active):
    ws.send_json({'type':'activity','chat_active':active})
    assert next_event(ws,'activity')['chat_active'] is active


def test_visible_messages_skips_new_bell_and_push_but_keeps_chat_unread(push_service):
    sender,recipient,sid,_,first=queued()
    before=recipient.get('/api/notifications').json()
    with recipient.websocket_connect('/api/live/inbox') as ws:
        auth(ws,recipient)
        second=message(sender,sid,cid='foreground-second')
        assert next_event(ws,'chat_changed')=={'type':'chat_changed'}
        after=recipient.get('/api/notifications').json()
        assert after['items']==before['items'] and after['unread']==before['unread']
        room=recipient.get('/api/chat/rooms').json()['items'][0]
        assert room['unread']==2 and room['last_message']['id']==second['id']
        # A queued alert from before entering Messages is cancelled at delivery.
        assert m.web_push.deliver_one() and not m.web_push.deliver_one()
        assert push_service==[]
        activity(ws,False)
        message(sender,sid,cid='background-third')
        assert next_event(ws,'notifications')['unread']==before['unread']+1
        assert m.web_push.deliver_one()
        assert len(push_service)==1
        # Returning to Messages suppresses future alerts, not existing ones.
        activity(ws,True)
        message(sender,sid,cid='foreground-fourth')
        next_event(ws,'chat_changed')
        assert recipient.get('/api/notifications').json()['unread']==before['unread']+1
    message(sender,sid,cid='closed-inbox-fifth')
    assert m.web_push.deliver_one()
    assert len(push_service)==2


def test_activity_is_shared_between_workers_expires_and_rechecks_session():
    _,recipient,_,_,_=queued()
    uid=recipient.get('/api/me').json()['id']
    with m.db() as db:
        token=db.execute("SELECT token FROM sessions WHERE user_id=? AND stage='full'",(uid,)).fetchone()[0]
    other=ProcessCoordinator(m.database,m.DATA,m.data_cipher,m.storage_gate)
    stamp=m.now()
    lease=other._register('inbox',uid,('inbox',uid),secrets.token_hex(8),{'chat_active':True,'session_token':token,'seen':stamp})
    try:
        with m.db() as db:
            assert chat_is_foreground(db,uid,stamp,m.process_coordinator)
            assert not chat_is_foreground(db,uid,stamp+CHAT_ACTIVITY_TTL,m.process_coordinator)
            assert not chat_is_foreground(db,'another-user',stamp,m.process_coordinator)
            db.execute('UPDATE sessions SET expires=? WHERE token=?',(stamp-1,token))
            assert not chat_is_foreground(db,uid,stamp,m.process_coordinator)
    finally:asyncio.run(lease.close())


@pytest.mark.parametrize('value',['true',1,None])
def test_activity_requires_boolean_and_never_survives_rejected_socket(value):
    _,recipient,_,_,_=queued()
    with recipient.websocket_connect('/api/live/inbox') as ws:
        auth(ws,recipient,False)
        ws.send_json({'type':'activity','chat_active':value})
        with pytest.raises(WebSocketDisconnect):ws.receive_json()
    uid=recipient.get('/api/me').json()['id']
    with m.db() as db:assert not chat_is_foreground(db,uid,m.now(),m.process_coordinator)
