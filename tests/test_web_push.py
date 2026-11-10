"""Message push grants, durable delivery and real encryption without live sends."""
import asyncio
import base64
import json
import multiprocessing
import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

import http_ece
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import web_push as push
from app.storage import StorageGate
from tests.test_admin import backup, headers, proof
from tests.test_security import client, enroll, member, owner, reset, setup
from tests.test_social import enabled, family, join
from app import main as m


@pytest.fixture(autouse=True)
def push_service(monkeypatch):
    monkeypatch.setattr(m.web_push, 'enabled', True)
    sent = []

    def send(subscription, payload, ttl):
        sent.append((subscription, payload, ttl))
        return 201, None

    monkeypatch.setattr(m.web_push, 'send', send)
    return sent


def subscription(endpoint=None):
    private = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    return {
        'endpoint': endpoint or 'https://web.push.apple.com/' + secrets.token_hex(16),
        'keys': {
            'p256dh': push.b64(private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
            'auth': push.b64(auth),
        },
        'expirationTime': None,
        'device_name': 'iPhone',
    }, private, auth


def register(c, value=None):
    value = value or subscription()[0]
    response = c.post('/api/push/subscriptions', json=value)
    assert response.status_code == 200, response.text
    assert response.json()['subscribed']
    return value


def counts():
    with m.db() as c:
        return tuple(c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                     for table in ('push_subscriptions', 'push_deliveries'))


def conversation():
    sender, _, _ = owner()
    recipient, _ = member(sender)
    sid = family(sender)
    join(sender, recipient, sid)
    enabled(sender)
    enabled(recipient)
    return sender, recipient, sid


def message(sender, sid, cid='push-message-123', body='Private message contents'):
    response = sender.post('/api/chat/' + sid + '/messages', json={'body': body, 'client_id': cid})
    assert response.status_code == 200, response.text
    return response.json()


def queued():
    sender, recipient, sid = conversation()
    value = register(recipient)
    msg = message(sender, sid)
    return sender, recipient, sid, value, msg


def test_configuration_requires_full_login_and_never_returns_private_key():
    assert client().get('/api/push').status_code == 401
    pending = client()
    setup(pending)
    assert pending.get('/api/push').status_code == 403
    enroll(pending)
    c = pending
    response = c.get('/api/push')
    assert response.status_code == 200
    assert set(response.json()) == {'configured', 'public_key'}
    assert response.json()['configured']
    public = push.decode_key(response.json()['public_key'], 65)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public)
    assert m.web_push.keys()['private'] not in response.text


def test_push_environment_switch_requires_https(monkeypatch):
    monkeypatch.delenv('WEB_PUSH_ENABLED', raising=False)
    assert push.WebPush(m.db, m.DATA, m.storage_gate, m.now, 'https://example.com').enabled
    assert not push.WebPush(m.db, m.DATA, m.storage_gate, m.now, 'http://example.com').enabled
    monkeypatch.setenv('WEB_PUSH_ENABLED', 'false')
    assert not push.WebPush(m.db, m.DATA, m.storage_gate, m.now, 'https://example.com').enabled
    monkeypatch.setenv('WEB_PUSH_ENABLED', 'unexpected')
    with pytest.raises(RuntimeError):
        push.WebPush(m.db, m.DATA, m.storage_gate, m.now, 'https://example.com')


def test_subscription_status_is_session_scoped_and_mutations_require_csrf():
    c, _, codes = owner()
    value = register(c)
    assert c.post('/api/push/status', json={'endpoint': value['endpoint']}).json() == {'subscribed': True}
    other = client()
    response = other.post('/api/login', json={'login': 'owner', 'password': 'Owner-password-123'})
    other.headers['x-csrf-token'] = response.json()['csrf']
    response = other.post('/api/2fa/verify', json={'code': codes[0]})
    other.headers['x-csrf-token'] = response.json()['csrf']
    assert other.post('/api/push/status', json={'endpoint': value['endpoint']}).json() == {'subscribed': False}
    del c.headers['x-csrf-token']
    assert c.post('/api/push/subscriptions', json=value).status_code == 403
    assert c.post('/api/push/status', json={'endpoint': value['endpoint']}).status_code == 403
    assert c.request('DELETE', '/api/push/subscriptions', json={'endpoint': value['endpoint']}).status_code == 403
    assert counts() == (1, 0)


@pytest.mark.parametrize('endpoint', [
    'http://web.push.apple.com/secret', 'https://127.0.0.1/secret',
    'https://192.168.1.10/secret', 'https://evil.example/secret',
    'https://web.push.apple.com.evil.example/secret', 'https://web.push.apple.com:444/secret',
    'https://user@web.push.apple.com/secret', 'https://web.push.apple.com/secret#fragment',
    'https://web.push.apple.com/', 'https://web.push.apple.com/secret\nheader',
])
def test_subscription_rejects_non_provider_or_ambiguous_urls(endpoint):
    c, _, _ = owner()
    value = subscription()[0]
    value['endpoint'] = endpoint
    assert c.post('/api/push/subscriptions', json=value).status_code == 422
    assert counts() == (0, 0)


@pytest.mark.parametrize('field,value', [('auth', push.b64(b'X' * 15)), ('p256dh', push.b64(b'X' * 65))])
def test_subscription_rejects_invalid_auth_secret_and_curve_point(field, value):
    c, _, _ = owner()
    data = subscription()[0]
    data['keys'][field] = value
    assert c.post('/api/push/subscriptions', json=data).status_code == 422
    assert counts() == (0, 0)


def test_unsubscribe_cannot_remove_another_account_device():
    c, _, _ = owner()
    g, _ = member(c)
    value = register(c)
    assert g.request('DELETE', '/api/push/subscriptions', json={'endpoint': value['endpoint']}).status_code == 200
    assert counts() == (1, 0)
    assert c.request('DELETE', '/api/push/subscriptions', json={'endpoint': value['endpoint']}).status_code == 200
    assert counts() == (0, 0)


def test_device_expiration_limit_and_disabled_server(monkeypatch):
    c, _, _ = owner()
    expired = subscription()[0]
    expired['expirationTime'] = m.now() * 1000
    assert c.post('/api/push/subscriptions', json=expired).status_code == 422
    for _ in range(20):
        register(c)
    assert c.post('/api/push/subscriptions', json=subscription()[0]).status_code == 409
    assert counts() == (20, 0)
    monkeypatch.setattr(m.web_push, 'enabled', False)
    assert c.get('/api/push').json() == {'configured': False, 'public_key': None}
    assert c.post('/api/push/subscriptions', json=subscription()[0]).status_code == 503


def test_message_enqueue_is_idempotent_and_delivery_contains_text_and_sender(push_service):
    sender, recipient, sid = conversation()
    value = register(recipient)
    register(sender)
    first = message(sender, sid)
    assert message(sender, sid)['id'] == first['id']
    assert counts() == (2, 1)
    assert m.web_push.deliver_one()
    assert not m.web_push.deliver_one()
    assert counts() == (2, 0)
    assert len(push_service) == 1
    actual, payload, ttl = push_service[0]
    assert actual['endpoint'] == value['endpoint']
    assert payload['url'] == '/#chat/' + sid
    assert payload['body']=='Private message contents'
    assert payload['title']=='owner'
    assert payload['badge']==recipient.get('/api/notifications').json()['unread']
    assert 0 < ttl <= push.TTL


def test_security_push_is_owner_only_and_does_not_require_chat(push_service):
    from app.notifications import notify
    admin,_,_=owner();guest,_=member(admin)
    register(admin);register(guest)
    uid=admin.get('/api/me').json()['id'];other=guest.get('/api/me').json()['id']
    with m.db() as c:
        settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
        settings['modules']={**settings.get('modules',{}),'chat':False}
        c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
        notify(c,uid,'security',{'incident_id':'safe-id','title':'Private details','severity':'high'},m.now())
        notify(c,other,'security',{'incident_id':'must-not-deliver'},m.now())
    while m.web_push.deliver_one():pass
    assert len(push_service)==1
    payload=push_service[0][1]
    assert payload['url']=='/#admin' and payload['tag']=='drg-security'
    assert 'Private details' not in json.dumps(payload)
    assert not [item for item in guest.get('/api/notifications').json()['items'] if item['kind']=='security']


def test_push_badge_matches_visible_unread_inbox_after_reading(push_service):
    sender,recipient,sid,_,first=queued()
    recipient.post('/api/notifications/read-all')
    message(sender,sid,cid='badge-second-message')
    uid=recipient.get('/api/me').json()['id']
    from app.notifications import notify,RETENTION
    with m.db() as db:
        notify(db,uid,'session',{'agent':'expired'},m.now()-RETENTION-1)
        notify(db,uid,'message',{'space_id':sid,'message_id':99999999},m.now())
        notify(db,uid,'message',{'space_id':'inaccessible','message_id':first['id']},m.now())
    assert recipient.get('/api/notifications').json()['unread']==1
    for _ in range(10):
        if not m.web_push.deliver_one():break
    assert len(push_service)==1
    assert push_service[0][1]['badge']==1


def test_notification_and_outbox_rollback_together():
    c, _, _ = owner()
    register(c)
    identity = c.get('/api/me').json()['id']
    from app.notifications import notify
    with pytest.raises(RuntimeError):
        with m.db() as db:
            notify(db, identity, 'message', {'space_id': 'room', 'message_id': 123}, m.now())
            assert db.execute('SELECT COUNT(*) FROM push_deliveries').fetchone()[0] == 1
            raise RuntimeError('Simulate rejected message transaction')
    assert counts() == (1, 0)
    with m.db() as db:
        assert db.execute("SELECT COUNT(*) FROM notifications WHERE kind='message'").fetchone()[0] == 0
        notify(db, identity, 'session', {'agent': 'new login'}, m.now())
        notify(db, identity, 'mention', {'note_id': 'private-page'}, m.now())
    assert counts() == (1, 0)


@pytest.mark.parametrize('change', ['read', 'removed_member', 'disabled_chat', 'expired_session', 'expired_subscription', 'removed_message'])
def test_queued_delivery_rechecks_access_read_state_and_expiration(change, monkeypatch, push_service):
    sender, recipient, sid, value, msg = queued()
    identity = recipient.get('/api/me').json()['id']
    if change == 'read':
        assert recipient.post('/api/notifications/read-all').status_code == 200
    elif change == 'removed_member':
        assert sender.delete('/api/social/families/' + sid + '/members/' + identity).status_code == 200
    elif change == 'disabled_chat':
        enabled(recipient, False)
    elif change == 'expired_session':
        with m.db() as db:
            db.execute('UPDATE sessions SET expires=? WHERE user_id=?', (m.now() - 1, identity))
    elif change == 'expired_subscription':
        value['expirationTime'] = (m.now() + 1) * 1000
        with m.db() as db:
            db.execute('UPDATE push_subscriptions SET payload=? WHERE id=?', (json.dumps(value), push.endpoint_id(value['endpoint'])))
        stamp = m.now() + 2
        monkeypatch.setattr(m, 'now', lambda: stamp)
    elif change == 'removed_message':
        with m.db() as db:
            db.execute('DELETE FROM messages WHERE id=?', (msg['id'],))
    assert m.web_push.deliver_one()
    assert counts()[1] == 0
    assert push_service == []


@pytest.mark.parametrize('action', ['logout', 'password_reset', 'two_factor_reset', 'delete_account'])
def test_session_revocation_and_account_deletion_cascade_push_grants(action):
    sender, recipient, sid, _, _ = queued()
    identity = recipient.get('/api/me').json()['id']
    if action == 'logout':
        response = recipient.post('/api/logout')
    elif action == 'password_reset':
        response = sender.put('/api/admin/users/' + identity + '/password', json=proof(password='Another-password-123'))
    elif action == 'two_factor_reset':
        response = sender.post('/api/admin/users/' + identity + '/reset-2fa', json=proof())
    else:
        response = sender.post('/api/admin/users/delete', json=proof(user_id=identity, confirmation='friend'))
    assert response.status_code == 200, response.text
    if action=='delete_account':
        from tests.deletion_helpers import finish_account
        finish_account(identity)
    assert counts() == (0, 0)


@pytest.mark.parametrize('code', [404, 410])
def test_gone_provider_subscription_is_removed(code, monkeypatch):
    queued()
    monkeypatch.setattr(m.web_push, 'send', lambda *args: (code, None))
    assert m.web_push.deliver_one()
    assert counts() == (0, 0)


def test_old_inflight_gone_response_does_not_delete_rebound_subscription(monkeypatch):
    _, recipient, _, value, _ = queued()

    def changed_while_sending(*args):
        register(recipient, value)
        return 410, None

    monkeypatch.setattr(m.web_push, 'send', changed_while_sending)
    assert m.web_push.deliver_one()
    assert counts() == (1, 0)
    assert recipient.post('/api/push/status', json={'endpoint': value['endpoint']}).json()['subscribed']


@pytest.mark.parametrize('code,retry', [(429, '120'), (503, '120'), (503, 'bad header'), (408, None)])
def test_retry_is_durable_and_cannot_run_before_due_time(code, retry, monkeypatch):
    queued()
    stamp = m.now()
    monkeypatch.setattr(m, 'now', lambda: stamp)
    calls = []

    def transport(*args):
        calls.append(args)
        return code, retry

    monkeypatch.setattr(m.web_push, 'send', transport)
    assert m.web_push.deliver_one()
    assert not m.web_push.deliver_one()
    with m.db() as db:
        row = db.execute('SELECT attempts,available FROM push_deliveries').fetchone()
        assert row['attempts'] == 1
        assert row['available'] >= stamp + (120 if retry == '120' else 15)
        due = row['available']
    assert len(calls) == 1
    monkeypatch.setattr(m, 'now', lambda: due)
    monkeypatch.setattr(m.web_push, 'send', lambda *args: (201, None))
    assert m.web_push.deliver_one()
    assert counts() == (1, 0)


def test_retry_after_http_date_and_expiry_are_bounded(monkeypatch, push_service):
    queued()
    stamp = m.now()
    retry = format_datetime(datetime.fromtimestamp(stamp + 180, timezone.utc), usegmt=True)
    monkeypatch.setattr(m.web_push, 'send', lambda *args: (429, retry))
    assert m.web_push.deliver_one()
    with m.db() as db:
        assert db.execute('SELECT available FROM push_deliveries').fetchone()[0] == stamp + 180
    monkeypatch.setattr(m, 'now', lambda: stamp + push.TTL)
    assert not m.web_push.deliver_one()
    assert counts() == (1, 0)
    assert push_service == []


def test_transport_failure_logs_no_endpoint_secret_and_retry_cap_stops(monkeypatch, caplog):
    _, _, _, value, _ = queued()

    def broken(*args):
        raise httpx.ConnectError('failed ' + value['endpoint'])

    monkeypatch.setattr(m.web_push, 'send', broken)
    assert m.web_push.deliver_one()
    assert value['endpoint'] not in caplog.text
    with m.db() as db:
        db.execute('UPDATE push_deliveries SET attempts=8,available=?', (m.now(),))
    assert m.web_push.deliver_one()
    assert counts() == (1, 0)


def test_provider_redirect_is_not_retried_or_followed(monkeypatch):
    queued()
    monkeypatch.setattr(m.web_push, 'send', lambda *args: (307, None))
    assert m.web_push.deliver_one()
    assert counts() == (1, 0)


def test_keys_survive_instances_and_subscription_payload_is_protected_at_rest():
    c, _, _ = owner()
    data = register(c)
    with ThreadPoolExecutor(max_workers=4) as executor:
        keys = list(executor.map(lambda _: m.web_push.keys(), range(4)))
    assert all(value == keys[0] for value in keys)
    other = push.WebPush(m.db, m.DATA, m.storage_gate, m.now, 'https://notes.example.com')
    assert other.keys() == keys[0]
    with m.db() as db:
        assert json.loads(db.execute('SELECT payload FROM push_subscriptions').fetchone()[0]) == data
        if db.backend.kind == 'postgres':
            raw = db.raw.execute('SELECT payload FROM push_subscriptions').fetchone()[0]
            key_raw = db.raw.execute('SELECT value FROM app_settings WHERE key=%s', (push.KEY_SETTING,)).fetchone()[0]
            if m.data_cipher.enabled:
                assert data['endpoint'] not in raw
                assert data['keys']['auth'] not in raw
                assert keys[0]['private'] not in key_raw
        elif m.data_cipher.enabled:
            raw = b''.join(path.read_bytes() for path in m.DATA.glob('site.sqlite3*'))
            assert data['endpoint'].encode() not in raw
            assert data['keys']['auth'].encode() not in raw
            assert keys[0]['private'].encode() not in raw


def test_backup_restore_never_restores_subscriptions_or_replays_messages():
    sender, _, _, _, _ = queued()
    keys = m.web_push.keys()
    archive = backup(sender)
    response = sender.post('/api/admin/backups/restore', content=archive, headers=headers())
    assert response.status_code == 200, response.text
    assert counts() == (0, 0)
    assert m.web_push.keys() == keys
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_transport_uses_rfc_encryption_valid_vapid_and_no_proxy_or_redirects(monkeypatch):
    data, receiver, auth = subscription()
    captured = []

    class Transport:
        def __init__(self, **kwargs):
            assert kwargs['trust_env'] is False
            assert kwargs['follow_redirects'] is False
            assert kwargs['timeout'].connect == 5

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, endpoint, **kwargs):
            captured.append((endpoint, kwargs))
            return httpx.Response(201)

    monkeypatch.setattr(push.httpx, 'Client', Transport)
    worker = push.WebPush(m.db, m.DATA, m.storage_gate, m.now, 'https://notes.example.com')
    payload = {'title': 'DrG', 'body': 'Новое сообщение', 'tag': 'drg-message:test'}
    assert worker.send(data, payload, 3600) == (201, None)
    endpoint, request = captured[0]
    assert endpoint == data['endpoint']
    assert json.loads(http_ece.decrypt(request['content'], private_key=receiver, auth_secret=auth, version='aes128gcm')) == payload
    outgoing = request['headers']
    assert outgoing['Content-Encoding'] == 'aes128gcm'
    assert outgoing['TTL'] == '3600'
    assert len(outgoing['Topic']) <= 32
    token = outgoing['Authorization'].split('t=', 1)[1].split(',k=', 1)[0]
    private = serialization.load_der_private_key(base64.urlsafe_b64decode(worker.keys()['private'] + '=='), None)
    claims = jwt.decode(token, private.public_key(), algorithms=['ES256'], audience='https://web.push.apple.com')
    assert claims['sub'] == 'https://notes.example.com'


def leader_child(root, pipe):
    root = Path(root)
    worker = push.WebPush(None, root, StorageGate(root), lambda: int(time.time()), 'https://notes.example.com')

    def delivery():
        pipe.send('delivery')
        pipe.recv()
        return False

    worker.deliver_one = delivery
    asyncio.run(worker.run())


def test_only_one_process_delivers_and_worker_death_releases_leadership(tmp_path):
    context = multiprocessing.get_context('spawn')
    first_pipe, first_child = context.Pipe()
    second_pipe, second_child = context.Pipe()
    first = context.Process(target=leader_child, args=(str(tmp_path), first_child))
    second = context.Process(target=leader_child, args=(str(tmp_path), second_child))
    first.start()
    first_child.close()
    try:
        assert first_pipe.poll(15)
        assert first_pipe.recv() == 'delivery'
        inode = (tmp_path / '.drg-process-locks' / 'push-delivery.lock').stat().st_ino
        second.start()
        second_child.close()
        assert not second_pipe.poll(0.3)
        first.kill()
        first.join(10)
        assert second_pipe.poll(15)
        assert second_pipe.recv() == 'delivery'
        assert (tmp_path / '.drg-process-locks' / 'push-delivery.lock').stat().st_ino == inode
    finally:
        for process in (first, second):
            if process.is_alive():
                process.kill()
            if process.pid is not None:
                process.join(10)
        first_pipe.close()
        second_pipe.close()
        second_child.close()


def test_device_check_requires_own_session_and_reports_provider_acceptance(push_service,monkeypatch):
    c,_,_=owner();g,_=member(c);value=register(c)
    assert g.post('/api/push/test',json={'endpoint':value['endpoint']}).status_code==409
    result=c.post('/api/push/test',json={'endpoint':value['endpoint']})
    assert result.status_code==200,result.text
    assert result.json()=={'accepted':True} and push_service[-1][1]['type']=='push-test'
    monkeypatch.setattr(m.web_push,'send',lambda *args:(410,None))
    assert c.post('/api/push/test',json={'endpoint':value['endpoint']}).status_code==409
    assert c.post('/api/push/status',json={'endpoint':value['endpoint']}).json()['subscribed'] is False


def test_delayed_push_uses_current_edited_body_with_a_bounded_preview(push_service):
    sender,recipient,sid,value,msg=queued()
    assert sender.put('/api/chat/'+sid+'/messages/'+str(msg['id']),json={'body':'Обновлённый текст '+('Я'*1000),'revision':0}).status_code==200
    assert m.web_push.deliver_one();payload=push_service[0][1]
    assert payload['body'].startswith('Обновлённый текст') and len(payload['body'])==500
    assert payload['title']=='owner' and 'Private message contents' not in payload['body']


def test_push_uses_display_name_and_expired_quick_timer_does_not_leak_preview(push_service):
    sender,recipient,sid,_,msg=queued()
    assert sender.put('/api/account/profile',json={'display_name':'Мама'}).status_code==200
    assert m.web_push.deliver_one()
    assert push_service[-1][1]['title']=='Мама'
    second=message(sender,sid,cid='push-timer-private',body='Пароль с коротким сроком')
    with m.db() as db:db.execute('UPDATE messages SET created=?,expires=? WHERE id=?',(m.now()-86400,m.now(),second['id']))
    assert m.web_push.deliver_one()
    assert len(push_service)==1
    inbox=recipient.get('/api/notifications').json()
    assert second['id'] not in {n['payload'].get('message_id') for n in inbox['items'] if n['kind']=='message'}


def test_idle_push_sleeps_until_enqueue_or_retry_deadline(monkeypatch,push_service):
    _,_,_,_,_=queued();stamp=m.now()
    with m.db() as c:c.execute('UPDATE push_deliveries SET available=?',(stamp+30,))
    callbacks=set();service=m.web_push;calls=[];deliver=service.deliver_one
    async def run():
        monkeypatch.setattr(service,'coordinator',type('Changes',(),{
            'subscribe_changes':lambda self,cb:callbacks.add(cb),
            'unsubscribe_changes':lambda self,cb:callbacks.discard(cb),
            'peers_sync':lambda *args:[]})())
        def tracked():
            calls.append(1)
            return deliver()
        monkeypatch.setattr(service,'deliver_one',tracked)
        task=asyncio.create_task(service.run())
        try:
            await asyncio.sleep(.1);assert len(calls)==1 and not push_service
            await asyncio.sleep(1.1);assert len(calls)==1,'A future job must not cause one SQL poll per second.'
            monkeypatch.setattr(service,'now',lambda:stamp+31)
            for callback in tuple(callbacks):await callback()
            for _ in range(100):
                if push_service and len(calls)>=3:break
                await asyncio.sleep(.01)
            assert len(push_service)==1 and len(calls)==3
            await asyncio.sleep(1.1);assert len(calls)==3
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            assert not callbacks
    asyncio.run(run())


def test_push_language_uses_recipient_preference_and_keeps_authored_text(push_service):
    sender, recipient, sid = conversation()
    value = subscription()[0] | {'language': 'en'}
    register(recipient, value)
    assert recipient.put('/api/language', json={'language': 'ru'}).status_code == 200
    message(sender, sid, cid='localized-raw-text', body='Новое сообщение')
    assert m.web_push.deliver_one()
    assert push_service[-1][1]['language'] == 'ru'
    assert push_service[-1][1]['body'] == 'Новое сообщение'

    # Server-authored notifications use the recipient, even without an open tab.
    register(sender, subscription()[0] | {'language': 'ru'})
    assert sender.put('/api/language', json={'language': 'en'}).status_code == 200
    from app.notifications import notify
    uid = sender.get('/api/me').json()['id']
    with m.db() as connection:
        notify(connection, uid, 'security', {'incident_id': 'localization-check'}, m.now())
    assert m.web_push.deliver_one()
    assert push_service[-1][1]['language'] == 'en'
    assert 'security' in push_service[-1][1]['body'].lower()
    assert not any('А' <= char <= 'я' for char in push_service[-1][1]['body'])


def test_push_status_updates_system_language_without_rebinding_the_subscription(push_service):
    c, _, _ = owner()
    value = register(c, subscription()[0] | {'language': 'en'})
    with m.db() as connection:
        generation = connection.execute('SELECT generation FROM push_subscriptions').fetchone()[0]
    response = c.post('/api/push/status', json={'endpoint': value['endpoint'], 'language': 'ru'})
    assert response.status_code == 200 and response.json()['subscribed']
    with m.db() as connection:
        row = connection.execute('SELECT generation,payload FROM push_subscriptions').fetchone()
        assert row['generation'] == generation
        assert json.loads(row['payload'])['language'] == 'ru'
