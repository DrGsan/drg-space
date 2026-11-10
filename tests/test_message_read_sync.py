"""Chat reads update inbox history on other devices without reading backwards."""
from tests.test_security import reset
from tests.test_web_push import queued,message,push_service
from tests.test_chat_notification_activity import auth
from tests.test_social import next_event,family,join
from app import main as m


def test_read_watermark_delta_reaches_another_device_even_with_zero_bell_count(push_service):
    sender,recipient,sid,_,first=queued()
    recipient.post('/api/notifications/read-all')
    before=recipient.get('/api/notifications').json()
    assert sid not in before['message_reads']
    assert recipient.get('/api/chat/rooms').json()['unread']==1
    with recipient.websocket_connect('/api/live/inbox') as ws:
        ready=auth(ws,recipient,False)
        assert ready['unread']==0
        result=recipient.post('/api/chat/'+sid+'/read',json={'message_id':first['id']})
        assert result.status_code==200
        changed=next_event(ws,'notifications')
        assert changed['items']==[] and changed['unread']==0
        assert changed['message_reads'][sid]==first['id']
        delta=recipient.get('/api/notifications?after='+str(before['cursor'])).json()
        assert delta['items']==[] and delta['message_reads'][sid]==first['id']
    assert not m.web_push.deliver_one() or not push_service


def test_bounded_reads_keep_new_messages_other_rooms_and_bell_history(push_service):
    sender,recipient,sid,_,first=queued()
    second=message(sender,sid,cid='sync-second')
    other=family(sender);join(sender,recipient,other)
    third=message(sender,other,cid='sync-other-room')
    recipient.post('/api/chat/'+sid+'/read',json={'message_id':first['id']})
    inbox=recipient.get('/api/notifications').json()
    messages={n['payload']['message_id']:n for n in inbox['items'] if n['kind']=='message'}
    assert messages[first['id']]['read'] and not messages[second['id']]['read'] and not messages[third['id']]['read']
    assert inbox['message_reads'][sid]==first['id'] and other not in inbox['message_reads']
    # Bell actions do not advance the durable conversation watermark.
    recipient.patch('/api/notifications/'+str(messages[second['id']]['id']))
    recipient.delete('/api/notifications/'+str(messages[third['id']]['id']))
    assert recipient.get('/api/notifications').json()['message_reads'][sid]==first['id']
    assert recipient.get('/api/chat/rooms').json()['unread']==2
    recipient.post('/api/chat/'+sid+'/read',json={'message_id':second['id']})
    recipient.post('/api/chat/'+sid+'/read',json={'message_id':first['id']})
    assert recipient.get('/api/notifications').json()['message_reads'][sid]==second['id']
