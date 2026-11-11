"""Opt-in message pushes with encrypted device grants and a durable outbox.

One kernel lease owns delivery across Gunicorn workers. Notifications and jobs
are committed together; no request handler waits for a remote push service.
"""
from app.i18n import message as localized_message
import asyncio
from app.people_names import display_name as personal_name,mention_text
import base64
import hashlib
import json
import logging
import os
import re
import secrets
from contextlib import suppress
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.http import Router, Request
from app.process_locks import try_file_lock
from app.storage import blocking

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subscriptions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,session_token TEXT NOT NULL REFERENCES sessions(token) ON DELETE CASCADE,payload TEXT NOT NULL,generation TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS push_subscription_owner_idx ON push_subscriptions(user_id);
CREATE TABLE IF NOT EXISTS push_deliveries(id TEXT PRIMARY KEY,subscription_id TEXT NOT NULL REFERENCES push_subscriptions(id) ON DELETE CASCADE,notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,generation TEXT NOT NULL,available INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS push_delivery_due_idx ON push_deliveries(available,created);
CREATE INDEX IF NOT EXISTS push_delivery_expiry_idx ON push_deliveries(created);
"""
TTL = 86400
KEY_SETTING = '_web_push_vapid_v1'
logger = logging.getLogger('drg.push')


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def endpoint_id(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()


def validate_endpoint(value):
    """Accept actual browser push providers, never arbitrary outbound URLs."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or re.search(r'[\s\\\x00-\x1f\x7f]', value):
        raise ValueError(localized_message('invalidPushSubscriptionAddress'))
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        allowed = host in {'web.push.apple.com', 'fcm.googleapis.com', 'updates.push.services.mozilla.com'} or bool(host and re.fullmatch(r'[a-z0-9-]+\.notify\.windows\.com', host))
        if not allowed or parsed.scheme != 'https' or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment or not parsed.path.startswith('/') or parsed.path == '/':
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError(localized_message('thisAddressDoesNotBelongToASupported')) from None
    return value


def decode_key(value, size):
    if not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', value):
        raise ValueError(localized_message('invalidPushSubscriptionKey'))
    try:
        decoded = base64.b64decode(value.rstrip('=') + '=' * (-len(value.rstrip('=')) % 4), altchars=b'-_', validate=True)
        if len(decoded) != size:
            raise ValueError()
        return decoded
    except (ValueError, TypeError):
        raise ValueError(localized_message('invalidPushSubscriptionKey')) from None


class PushKeys(BaseModel):
    model_config = ConfigDict(extra='forbid')
    p256dh: str = Field(min_length=86, max_length=90)
    auth: str = Field(min_length=22, max_length=24)

    @field_validator('p256dh')
    @classmethod
    def point(cls, value):
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decode_key(value, 65))
        return value

    @field_validator('auth')
    @classmethod
    def authentication(cls, value):
        decode_key(value, 16)
        return value


from app.i18n import normalize_language, recipient_language, system_text, text as localized_text, current_language

def validate_subscription_language(value):
    if value is not None and normalize_language(value) != value:
        raise ValueError(localized_message('chooseASupportedLanguage'))
    return value


class Subscription(BaseModel):
    model_config = ConfigDict(extra='forbid')
    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeys
    expirationTime: int | None = Field(default=None, ge=0, le=32503680000000)
    device_name: str = Field(default='', max_length=120)
    language: str = Field(default='ru', max_length=16)
    _endpoint = field_validator('endpoint')(validate_endpoint)
    _language = field_validator('language')(validate_subscription_language)


class Unsubscribe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: str | None = Field(default=None, max_length=16)
    endpoint: str = Field(min_length=1, max_length=2048)
    _endpoint = field_validator('endpoint')(validate_endpoint)
    _language = field_validator('language')(validate_subscription_language)


def enqueue(c, uid, notification_id, stamp):
    for row in c.execute('SELECT id,generation FROM push_subscriptions WHERE user_id=?', (uid,)).fetchall():
        identity = hashlib.sha256(f'{notification_id}:{row["id"]}:{row["generation"]}'.encode()).hexdigest()
        c.execute('INSERT OR IGNORE INTO push_deliveries(id,subscription_id,notification_id,generation,available,created) VALUES(?,?,?,?,?,?)',
                  (identity, row['id'], notification_id, row['generation'], stamp, stamp))


class WebPush:
    def __init__(self, db, root, gate, now, public_url, *, coordinator=None):
        self.db, self.root, self.gate, self.now, self.public_url = db, root, gate, now, public_url
        self.coordinator=coordinator
        raw = os.getenv('WEB_PUSH_ENABLED', 'true').lower()
        if raw not in ('true', '1', 'yes', 'false', '0', 'no'):
            raise RuntimeError('WEB_PUSH_ENABLED must be true or false.')
        self.enabled = raw in ('true', '1', 'yes') and urlsplit(public_url).scheme == 'https'
        self.task = None

    def keys(self):
        # No process-local cache: a restore may replace the persistent key.
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT value FROM app_settings WHERE key=?', (KEY_SETTING,)).fetchone()
            if row:
                return json.loads(row[0])
            private = ec.generate_private_key(ec.SECP256R1())
            value = {'private': b64(private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
                     'public': b64(private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))}
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', (KEY_SETTING, json.dumps(value)))
            return value

    def router(self, ready, rate, fail):
        r = Router()

        @r.get('/api/push')
        def configuration(request: Request):
            ready(request)
            return {'configured': self.enabled, 'public_key': self.keys()['public'] if self.enabled else None}

        @r.post('/api/push/status')
        def status(data: Unsubscribe, request: Request):
            s = ready(request)
            with self.db() as c:
                row = c.execute('SELECT payload,generation FROM push_subscriptions WHERE id=? AND user_id=? AND session_token=?', (endpoint_id(data.endpoint), s['user_id'], s['token'])).fetchone()
                if row and data.language:
                    payload = json.loads(row['payload'])
                    if payload.get('language') != data.language:
                        payload['language'] = data.language
                        c.execute('UPDATE push_subscriptions SET payload=? WHERE id=? AND user_id=? AND session_token=? AND generation=?', (json.dumps(payload), endpoint_id(data.endpoint), s['user_id'], s['token'], row['generation']))
            return {'subscribed': bool(row)}

        @r.post('/api/push/subscriptions')
        def subscribe(data: Subscription, request: Request):
            s = ready(request)
            if not self.enabled:
                fail(503, localized_message('pushNotificationsAreUnavailableAtThisAddress'))
            rate(request, 'push-subscribe', 30, subject=s['user_id'])
            if data.expirationTime is not None and data.expirationTime <= self.now() * 1000:
                fail(422, localized_message('subscriptionHasExpiredReEnableNotifications'))
            if normalize_language(data.language) != data.language:
                fail(422, localized_message('chooseASupportedLanguage'))
            identity = endpoint_id(data.endpoint)
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                existing = c.execute('SELECT id FROM push_subscriptions WHERE id=?', (identity,)).fetchone()
                count = c.execute('SELECT COUNT(*) FROM push_subscriptions WHERE user_id=?', (s['user_id'],)).fetchone()[0]
                if count >= 20 and not c.execute('SELECT 1 FROM push_subscriptions WHERE id=? AND user_id=?', (identity, s['user_id'])).fetchone():
                    fail(409, localized_message('deviceLimitReachedDisableNotificationsOnOneOf'))
                # Rebinding an endpoint never replays another session's queue.
                if existing:
                    c.execute('DELETE FROM push_subscriptions WHERE id=?', (identity,))
                c.execute('INSERT INTO push_subscriptions(id,user_id,session_token,payload,generation,created,updated) VALUES(?,?,?,?,?,?,?)',
                          (identity, s['user_id'], s['token'], data.model_dump_json(exclude={'language'} if 'language' not in data.model_fields_set else set()), secrets.token_hex(16), self.now(), self.now()))
            return {'subscribed': True}

        @r.delete('/api/push/subscriptions')
        def unsubscribe(data: Unsubscribe, request: Request):
            s = ready(request)
            with self.db() as c:
                c.execute('DELETE FROM push_subscriptions WHERE id=? AND user_id=?', (endpoint_id(data.endpoint), s['user_id']))
            return {'ok': True}

        @r.post('/api/push/test')
        async def test_device(data: Unsubscribe, request: Request):
            s=ready(request)
            if not self.enabled:fail(503,localized_message('pushNotificationsAreDisabledOnTheServer'))
            rate(request,'push-test',5,period=60,subject=s['user_id'])
            with self.db() as c:
                row=c.execute('SELECT payload FROM push_subscriptions WHERE id=? AND user_id=? AND session_token=?',(endpoint_id(data.endpoint),s['user_id'],s['token'])).fetchone()
            if not row:fail(409,localized_message('firstEnableNotificationsOnThisDevice'))
            try:code,_=await blocking(self.send,json.loads(row['payload']),{'type':'push-test','url':'/','tag':'drg-push-test','body':localized_text('pushTest'),'language':current_language.get()},300)
            except Exception:fail(503,localized_message('unableToConnectToThePushServiceRetry'))
            if code in (404,410):
                with self.db() as c:c.execute('DELETE FROM push_subscriptions WHERE id=? AND user_id=? AND session_token=?',(endpoint_id(data.endpoint),s['user_id'],s['token']))
                fail(409,localized_message('subscriptionHasExpiredDisableAndReEnableNotifications'))
            if not 200<=code<300:fail(503,'Push-сервис отклонил проверку (HTTP '+str(code)+').')
            return {'accepted':True}

        return r

    def send(self, subscription, payload, ttl):
        from pywebpush import WebPusher
        from py_vapid import Vapid
        endpoint = validate_endpoint(subscription['endpoint'])
        parsed = urlsplit(endpoint)
        # Fresh claims for each service, without endpoint tokens in logs/errors.
        headers = Vapid.from_string(self.keys()['private']).sign({'aud': f'{parsed.scheme}://{parsed.netloc}', 'sub': self.public_url, 'exp': self.now() + 12 * 3600})
        body = WebPusher(subscription).encode(json.dumps(payload, ensure_ascii=False).encode())['body']
        headers.update({'Content-Encoding': 'aes128gcm', 'Content-Type': 'application/octet-stream', 'TTL': str(ttl),
                        'Urgency': 'high' if payload.get('type')=='call' else 'normal', 'Topic': b64(hashlib.sha256(payload['tag'].encode()).digest())[:32]})
        with httpx.Client(timeout=httpx.Timeout(10, connect=5), trust_env=False, follow_redirects=False) as client:
            response = client.post(endpoint, content=body, headers=headers)
        return response.status_code, response.headers.get('Retry-After')

    def deliver_one(self):
        stamp = self.now()
        family_id=None;family_message=None
        with self.db() as c:
            candidate=c.execute('SELECT d.id,s.user_id,n.kind,n.payload FROM push_deliveries d JOIN push_subscriptions s ON s.id=d.subscription_id JOIN notifications n ON n.id=d.notification_id WHERE d.available<=? ORDER BY d.available,d.created,d.id LIMIT 1',(stamp,)).fetchone()
            if candidate and candidate['kind'] in {'family_message','family_export_ready'}:
                from app.modules.family_photo_events import notice_access
                payload=json.loads(candidate['payload'])
                family_id=candidate['id']
                from app.chat_notifications import muted
                if candidate['kind']=='family_export_ready':
                    from app.modules.family_export_events import notice_access as export_access
                    permitted=export_access(c,candidate['user_id'],payload,stamp)
                else:permitted=notice_access(c,candidate['user_id'],payload) and not muted(c,candidate['user_id'],payload.get('space_id'),stamp)
            else:permitted=False
        if permitted and getattr(self,'family_photos',None):
            if stamp<getattr(self,'_family_retry_after',0):
                with self.db() as c:c.execute('UPDATE push_deliveries SET available=? WHERE id=?',(self._family_retry_after,family_id))
                return True
            try:
                family_message=(self.family_photos.push_export(payload,candidate['user_id']) if candidate['kind']=='family_export_ready' else self.family_photos.push_message(payload))
                if candidate['kind']=='family_message' and family_message and family_message.get('site_actor_id')==candidate['user_id']:family_message=None
                self._family_retry_after=0
            except Exception:
                # An unavailable archive must not discard a queued push.
                self._family_retry_after=stamp+30
                with self.db() as c:c.execute('UPDATE push_deliveries SET available=? WHERE id=?',(stamp+30,family_id))
                return True
        with self.db() as c:
            c.execute('DELETE FROM push_deliveries WHERE created<=?', (stamp - TTL,))
            row = c.execute('SELECT d.*,s.user_id,s.session_token,s.payload AS subscription,s.generation AS current_generation,n.kind,n.payload AS notification,n.read FROM push_deliveries d JOIN push_subscriptions s ON s.id=d.subscription_id JOIN notifications n ON n.id=d.notification_id WHERE d.available<=? ORDER BY d.available,d.created,d.id LIMIT 1', (stamp,)).fetchone()
            if not row:
                return False
            row = dict(row)
            notice = json.loads(row['notification'])
            subscription = json.loads(row['subscription'])
            uid, sid = row['user_id'], notice.get('space_id')
            session = c.execute("SELECT 1 FROM sessions WHERE token=? AND user_id=? AND stage='full' AND expires>?", (row['session_token'], uid, stamp)).fetchone()
            user = c.execute('SELECT settings,role FROM users WHERE id=?', (uid,)).fetchone()
            selected_language = recipient_language(json.loads(user['settings']) if user else {}, subscription.get('language'))
            localize = lambda value: system_text(value, language=selected_language)
            from app.modules.preferences import module_enabled
            security_notice = row['kind'] == 'security'
            export_notice = row['kind'] == 'family_export_ready'
            enabled = user and (user['role']=='owner' if security_notice or export_notice else module_enabled(json.loads(user['settings']), 'chat'))
            valid = session and enabled and row['kind'] in ('message','family_message','call','security','family_export_ready') and not row['read'] and row['generation'] == row['current_generation']
            from app.chat_notifications import muted
            if row['kind'] in ('message','family_message'):
                valid=valid and not notice.get('silent') and not muted(c,uid,sid,stamp)
            if row['kind']=='family_message':
                if family_id!=row['id']:return True
                from app.modules.family_photo_events import notice_access
                valid=valid and notice_access(c,uid,notice) and bool(family_message)
            elif export_notice:
                if family_id!=row['id']:return True
                from app.modules.family_export_events import notice_access as export_access
                valid=valid and export_access(c,uid,notice,stamp) and bool(family_message)
            elif not security_notice:valid = valid and bool(c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?', (sid, uid)).fetchone())
            sender=None;message_text=''
            if security_notice:
                sender=[localize('DrG Space · Безопасность')]
                message_text=localize('Обнаружено событие безопасности. Откройте администрирование для проверки.')
            elif export_notice:
                sender=[localized_text('familyPhotoArchiveIsReady',language=selected_language)]
                message_text=localized_text('groupExportBody',language=selected_language,title=notice.get('group_title') or localized_text('familyPhotos',language=selected_language))
            elif row['kind']=='family_message':
                from app.modules.family_photo_events import preview
                sender=[(notice.get('space_name') or localized_text('familyPhotos',language=selected_language))+' · '+(family_message.get('sender') or notice.get('actor',''))] if family_message else None
                message_text=(preview(family_message) if family_message.get('text','').strip() else localize(preview(family_message))) if family_message else ''
            elif row['kind']=='call':
                from app.notifications import actor
                person=actor(c,notice.get('actor_id'),stamp);sender=[personal_name(c,uid,person)]
                valid = valid and bool(c.execute("SELECT 1 FROM calls WHERE id=? AND space_id=? AND recipient_id=? AND status='ringing' AND created>?",(notice.get('call_id'),sid,uid,stamp-60)).fetchone())
            else:
                from app.chat_storage import decode,cutoff
                message=c.execute('SELECT sender_id,body FROM messages WHERE id=? AND space_id=? AND created>? AND (expires=0 OR expires>?)',(notice.get('message_id'),sid,cutoff(stamp),stamp)).fetchone() if valid else None
                valid=valid and bool(message)
                if message:
                    from app.notifications import actor
                    person=actor(c,message['sender_id'],stamp);sender=[personal_name(c,uid,person)]
                    text,attachments=decode(message['body']);message_text=mention_text(c,uid,sid,text)[:500] if text else (localize('Вложение') if len(attachments)==1 else localized_text('attachmentsCount',language=selected_language,count=len(attachments)))
            valid = valid and (subscription.get('expirationTime') is None or subscription['expirationTime'] > stamp * 1000)
            if valid and row['kind'] in ('message','family_message'):
                from app.notifications import chat_is_foreground
                valid=not chat_is_foreground(c,uid,stamp,self.coordinator)
            if not valid:
                c.execute('DELETE FROM push_deliveries WHERE id=?', (row['id'],))
                return True
            from app.notifications import unread_count
            badge = unread_count(c,uid,stamp)
        payload = {'title': sender[0][:80] if sender else localized_text('messagesTitle',language=selected_language), 'body': message_text or localized_text('newMessage',language=selected_language), 'language':selected_language,
                   'url': '/#admin' if security_notice or export_notice else '/#chat/' + sid,
                   'tag': 'drg-security' if security_notice or export_notice else 'drg-message:' + sid, 'badge': badge}
        if row['kind'] in ('message','family_message'):
            payload.update(account_id=uid,message_id=notice.get('message_id'))
        if export_notice:payload.update(type='family-export',url='/#admin/family-photos/'+notice['family_group_id'],tag='drg-family-export:'+notice['export_id'])
        if row['kind']=='call':payload.update(type='call',body=localized_text('incomingVoiceCall',language=selected_language),tag='drg-call:'+notice['call_id'])
        try:
            code, retry_after = self.send(subscription, payload, max(1, min(row['created'] + (60 if row['kind']=='call' else TTL),notice['expires'] if export_notice else stamp+TTL)-stamp))
        except Exception:
            # Transport exceptions can contain endpoint capability tokens.
            code, retry_after = 503, None
            logger.warning('Message push transport failed; delivery will retry.')
        with self.db() as c:
            if code in (404, 410):
                c.execute('DELETE FROM push_subscriptions WHERE id=? AND generation=?', (row['subscription_id'], row['generation']))
            elif 200 <= code < 300 or (code not in (408, 425, 429) and code < 500) or row['attempts'] >= 8:
                c.execute('DELETE FROM push_deliveries WHERE id=? AND generation=?', (row['id'], row['generation']))
                if not 200 <= code < 300 and code not in (404, 410):
                    logger.warning('Push service rejected a delivery (HTTP %s).', code)
            else:
                delay = min(3600, 15 * 2 ** min(row['attempts'], 8))
                if retry_after:
                    try:
                        requested = int(retry_after) if retry_after.isdecimal() else int(parsedate_to_datetime(retry_after).timestamp()) - stamp
                        delay = max(delay, min(TTL, requested))
                    except (ValueError, TypeError, OverflowError):
                        pass
                c.execute('UPDATE push_deliveries SET attempts=?,available=? WHERE id=? AND generation=?',
                          (row['attempts'] + 1, stamp + delay, row['id'], row['generation']))
        return True

    async def run(self):
        lease = None
        changed=asyncio.Event()
        async def invalidate():changed.set()
        if self.coordinator:self.coordinator.subscribe_changes(invalidate)
        try:
            while True:
                if lease is None:
                    lease = try_file_lock(self.root, 'push-delivery.lock')
                worked = False
                changed.clear()
                delay=1
                if lease:
                    try:
                        # Exclude restore while a job is selected and sent, but
                        # never hold a database transaction during HTTP I/O.
                        async with self.gate.shared_wait():
                            worked = await blocking(self.deliver_one)
                            if not worked:
                                def deadline():
                                    with self.db() as c:
                                        return c.execute('SELECT MIN(available) FROM push_deliveries').fetchone()[0]
                                due=await blocking(deadline)
                                delay=min(30,max(0.05,due-self.now())) if due is not None else 30
                    except Exception:
                        logger.error('Push queue processing failed; retrying.')
                if worked:await asyncio.sleep(0.05)
                else:
                    with suppress(asyncio.TimeoutError):await asyncio.wait_for(changed.wait(),delay)
        finally:
            if self.coordinator:self.coordinator.unsubscribe_changes(invalidate)
            if lease:
                lease.close()

    def start(self):
        if self.enabled and self.task is None:
            self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
