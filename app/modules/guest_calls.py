"""Twelve-hour invitation rooms. Anonymous credentials grant this room only."""
from app.i18n import message as localized_message, text as localized_text
import hashlib
import asyncio
import base64
import hmac
import json
import re
import secrets
from typing import Literal

from pydantic import Field, StrictBool
from app.http import Request, Response
from app.modules import calls
from app.modules.preferences import module_enabled
from app.notifications import actor
from app.modules.profile import AVATAR_LIMIT, thumbnail
from app.storage import blocking
from PIL import Image, UnidentifiedImageError
from app import livekit

SCHEMA = '''
CREATE TABLE IF NOT EXISTS guest_call_rooms(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,token_hash TEXT UNIQUE NOT NULL,created INTEGER NOT NULL,expires INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS guest_room_expiry_idx ON guest_call_rooms(expires);
CREATE INDEX IF NOT EXISTS guest_room_owner_idx ON guest_call_rooms(owner_id,expires);
CREATE TABLE IF NOT EXISTS guest_call_participants(id TEXT PRIMARY KEY,room_id TEXT NOT NULL REFERENCES guest_call_rooms(id) ON DELETE CASCADE,user_id TEXT UNIQUE REFERENCES users(id) ON DELETE CASCADE,secret_hash TEXT UNIQUE NOT NULL,created INTEGER NOT NULL,seen INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS guest_participant_room_idx ON guest_call_participants(room_id,seen);
CREATE TABLE IF NOT EXISTS guest_call_links(room_id TEXT NOT NULL REFERENCES guest_call_rooms(id) ON DELETE CASCADE,first_id TEXT NOT NULL REFERENCES guest_call_participants(id) ON DELETE CASCADE,second_id TEXT NOT NULL REFERENCES guest_call_participants(id) ON DELETE CASCADE,payload TEXT NOT NULL,PRIMARY KEY(first_id,second_id));
'''
LIFETIME = 12 * 3600
MAX_PEOPLE = 8
ID = r'^[a-f0-9]{32}$'
TOKEN = r'^[a-zA-Z0-9_-]{43}$'


class Create(calls.Model):
    id: str = Field(pattern=ID)
    transport: Literal['auto', 'webrtc', 'livekit'] | None = None


class Appearance(calls.Model):
    title: str = Field(min_length=1, max_length=80)
    avatar: str | None = Field(default=None, max_length=4*((AVATAR_LIMIT+2)//3))
    remove_avatar: StrictBool = False


class Join(Create):
    nonce: str = Field(pattern=r'^[a-zA-Z0-9_-]{43,80}$')
    name: str = Field(default='', max_length=60)


class Description(calls.Model):
    type: Literal['offer', 'answer']
    sdp: str = Field(min_length=1, max_length=32000)


class PeerSignal(calls.Model):
    peer_id: str = Field(pattern=ID)
    description: Description | None = None
    candidates: list[calls.Candidate] = Field(default_factory=list, max_length=100)


class Sync(calls.Model):
    muted: StrictBool
    signals: list[PeerSignal] = Field(default_factory=list, max_length=MAX_PEOPLE - 1)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def purge_guest_calls(c, stamp):
    c.execute('DELETE FROM guest_call_rooms WHERE expires<=?', (stamp,))
    c.execute('DELETE FROM guest_call_participants WHERE seen<=?', (stamp - 90,))


def create_guest_calls_router(db, ready, rate, now, fail, public_url, *, federation_busy=None):
    from app.http import Router
    r = Router(); avatar_lock = asyncio.Semaphore(2)

    def load_room(c, rid=None, token=None):
        row = c.execute('SELECT * FROM guest_call_rooms WHERE ' + ('id=?' if rid else 'token_hash=?') + ' AND expires>?',
                        (rid or digest(token), now())).fetchone()
        if not row:
            fail(410, localized_message('linkIsInvalidOrExpired'))
        if json.loads(row['payload']).get('closing'):
            fail(410, localized_message('guestCallEnded'))
        owner = c.execute('SELECT settings FROM users WHERE id=?', (row['owner_id'],)).fetchone()
        if not owner or not module_enabled(json.loads(owner['settings']), 'chat'):
            fail(410, localized_message('guestCallEnded'))
        return row

    def room_info(c, room, private=False):
        result = {k: room[k] for k in ('id', 'created', 'expires')}
        payload = json.loads(room['payload'])
        result.update(title=payload.get('title','Гостевой звонок'), transport=payload.get('transport','webrtc'),
                      join_disabled_reason=(localized_text('legacyGuestInvitationClosed') if payload.get('transport','webrtc')!='livekit' else None),
                      avatar_url=(f"/api/guest-calls/{payload['token']}/avatar?v={payload['avatar_version']}" if payload.get('avatar') else None),
                      server_time=now(), limit=MAX_PEOPLE,
                      count=c.execute('SELECT COUNT(*) FROM guest_call_participants WHERE room_id=? AND seen>?',
                                      (room['id'], now() - 90)).fetchone()[0])
        if private:
            result['url'] = public_url + '/call/' + payload['token']
        return result

    def auth(c, rid, request):
        room = load_room(c, rid=rid)
        header = request.headers.get('authorization', '')
        token = header[7:] if header.startswith('Bearer ') else ''
        if not re.fullmatch(TOKEN, token):
            fail(401, localized_message('joinTheCallViaInvitation'))
        participant = c.execute('SELECT * FROM guest_call_participants WHERE room_id=? AND secret_hash=? AND seen>?',
                                (rid, digest(token), now() - 90)).fetchone()
        if not participant:
            fail(401, localized_message('callParticipationEndedJoinAgain'))
        return room, participant

    def state(c, room, me):
        participants = []
        for row in c.execute('SELECT * FROM guest_call_participants WHERE room_id=? AND seen>? ORDER BY created,id', (room['id'], now() - 90)):
            payload = json.loads(row['payload'])
            participants.append({'id': row['id'], 'name': payload['name'], 'muted': payload['muted'], 'host': row['user_id'] == room['owner_id']})
        participants.sort(key=lambda person: not person['host'])
        peers = []
        links=[] if json.loads(room['payload']).get('transport')=='livekit' else c.execute('SELECT * FROM guest_call_links WHERE room_id=? AND (first_id=? OR second_id=?)', (room['id'], me['id'], me['id']))
        for row in links:
            side = 'second' if row['first_id'] == me['id'] else 'first'
            data = json.loads(row['payload'])
            peers.append({'id': row[side + '_id'], 'description': data.get(side + '_sdp'), 'candidates': data.get(side + '_ice', [])})
        return {'room': room_info(c, room), 'self': me['id'], 'participants': participants, 'peers': peers}

    def join(c, room, data, uid=None):
        name = data.name.strip()
        if uid:
            person = actor(c, uid, now())
            name = person.get('display_name') or ' '.join(filter(None, [person.get('first_name'), person.get('last_name')])) or person['login']
        if not name or any(ord(char) < 32 or ord(char) == 127 for char in name):
            fail(422, localized_message('enterAName1To60Characters'))
        sfu=json.loads(room['payload']).get('transport')=='livekit'
        if not sfu:
            fail(409, localized_message('legacyGuestInvitationClosed'))
        if sfu and data.transport!='livekit':
            fail(422, localized_message('livekitClientUpdateRequired'))
        previous = c.execute('SELECT * FROM guest_call_participants WHERE id=?', (data.id,)).fetchone()
        if previous:
            payload = json.loads(previous['payload'])
            if previous['room_id'] != room['id'] or not hmac.compare_digest(payload['nonce'], digest(data.nonce)):
                fail(409, localized_message('retryConnection'))
            me = previous
        else:
            if uid and federation_busy and federation_busy(c,uid,now()):
                fail(409, localized_message('firstCompleteTheCurrentInterServerCall'))
            if uid and (c.execute('SELECT 1 FROM guest_call_participants WHERE user_id=?', (uid,)).fetchone() or
                        c.execute("SELECT 1 FROM calls WHERE (caller_id=? OR recipient_id=?) AND status IN ('ringing','connecting','active')", (uid, uid)).fetchone()):
                fail(409, localized_message('firstEndTheCurrentCall'))
            if room_info(c, room)['count'] >= MAX_PEOPLE:
                fail(409, localized_message('theCallAlreadyHas8ParticipantsTryAgain'))
            token = secrets.token_urlsafe(32)
            payload = {'name': name[:60], 'token': token, 'nonce': digest(data.nonce), 'muted': True}
            c.execute('INSERT INTO guest_call_participants(id,room_id,user_id,secret_hash,created,seen,payload) VALUES(?,?,?,?,?,?,?)',
                      (data.id, room['id'], uid, digest(token), now(), now(), json.dumps(payload)))
            me = c.execute('SELECT * FROM guest_call_participants WHERE id=?', (data.id,)).fetchone()
        c.execute('UPDATE guest_call_participants SET seen=? WHERE id=?', (now(), me['id']))
        return state(c, room, me) | {'credential': payload['token'],
            'configuration': {'iceServers':[],'available':True} if sfu else calls.ice_config('guest-' + me['id'], now(), fail, lifetime=room['expires'] - now())}

    def connect_media(result):
        if result['room']['transport']=='livekit':
            try:result['livekit']=livekit.connect(result['room']['id'],result['self'],result['room']['expires'],now())
            except livekit.Unavailable:fail(503,localized_message('livekitUnavailable'))
        return result

    @r.post('/api/calls/guest-rooms')
    def create(data: Create, request: Request):
        s = ready(request)
        rate(request, 'guest-room-create', 12, period=3600, subject=s['user_id'])
        if data.transport not in ('auto','livekit'):
            fail(422,localized_message('livekitClientUpdateRequired'))
        if not livekit.available():
            fail(503,localized_message('groupCallsRequireLivekit'))
        transport='livekit'
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); purge_guest_calls(c, now())
            room = c.execute('SELECT * FROM guest_call_rooms WHERE id=? AND owner_id=?', (data.id, s['user_id'])).fetchone()
            if room:
                return room_info(c, room, True)
            if c.execute('SELECT COUNT(*) FROM guest_call_rooms WHERE owner_id=?', (s['user_id'],)).fetchone()[0] >= 5:
                fail(409, localized_message('youAlreadyHave5ActiveInvitationsCompleteOne'))
            if c.execute('SELECT 1 FROM guest_call_rooms WHERE id=?', (data.id,)).fetchone():
                fail(409, localized_message('retryCreatingTheInvitation'))
            token = secrets.token_urlsafe(32)
            c.execute('INSERT INTO guest_call_rooms VALUES(?,?,?,?,?,?)', (data.id, s['user_id'], digest(token), now(), now() + LIFETIME, json.dumps({'token': token,'transport':transport})))
            if transport=='livekit':livekit.register(c,data.id,now()+LIFETIME)
            return room_info(c, load_room(c, rid=data.id), True)

    @r.get('/api/calls/guest-rooms')
    def rooms(request: Request):
        s = ready(request)
        with db() as c:
            return {'items': [room_info(c, row, True) for row in c.execute('SELECT * FROM guest_call_rooms WHERE owner_id=? AND expires>? ORDER BY created DESC', (s['user_id'], now()))]}

    @r.put('/api/calls/guest-rooms/{rid}')
    async def appearance(rid: str, data: Appearance, request: Request):
        s = ready(request)
        rate(request, 'guest-room-appearance', 30, period=60, subject=s['user_id'])
        def owned(c):
            room = load_room(c, rid=rid)
            if room['owner_id'] != s['user_id']:fail(404, localized_message('callNotFound'))
            return room
        with db() as c:owned(c)
        title = data.title.strip()
        if not title or any(ord(char)<32 or ord(char)==127 for char in title):
            fail(422, localized_message('callName1To80CharactersSingleLine'))
        if data.avatar is not None and data.remove_avatar:
            fail(422, localized_message('chooseToReplaceOrDeleteTheAvatar'))
        body = None
        if data.avatar is not None:
            async with avatar_lock:
                try:
                    raw = base64.b64decode(data.avatar, validate=True)
                    if len(raw)>AVATAR_LIMIT:fail(413, localized_message('theAvatarMustBeNoMoreThan10'))
                    body = await blocking(thumbnail, raw)
                except (ValueError,EOFError,UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):
                    fail(422, localized_message('couldNotOpenPhotoSelectPngJpegWebp'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');ready(request);room=owned(c)
            payload=json.loads(room['payload']);payload['title']=title
            if body is not None:
                payload['avatar']=base64.b64encode(body).decode('ascii')
                payload['avatar_version']=hashlib.sha256(body).hexdigest()[:16]
            elif data.remove_avatar:
                payload.pop('avatar',None);payload.pop('avatar_version',None)
            c.execute('UPDATE guest_call_rooms SET payload=? WHERE id=?',(json.dumps(payload),rid))
            result=room_info(c,load_room(c,rid=rid),True)
        if result['transport']=='livekit':await blocking(livekit.notify_appearance,rid)
        return result

    @r.post('/api/calls/guest-rooms/{rid}/join')
    def owner_join(rid: str, data: Join, request: Request):
        s = ready(request)
        if data.transport=='livekit' and not livekit.available():fail(503,localized_message('livekitUnavailable'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); purge_guest_calls(c, now()); calls.purge_calls(c, now())
            room = load_room(c, rid=rid)
            if room['owner_id'] != s['user_id']:
                fail(404, localized_message('callNotFound'))
            result=join(c, room, data, s['user_id'])
        return connect_media(result)

    @r.post('/api/calls/guest-rooms/{rid}/end')
    def end(rid: str, request: Request):
        s = ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            room=c.execute('SELECT * FROM guest_call_rooms WHERE id=? AND owner_id=?',(rid,s['user_id'])).fetchone()
            payload=json.loads(room['payload']) if room else {}
            if payload.get('transport')=='livekit':
                payload['closing']=True
                c.execute('UPDATE guest_call_rooms SET payload=? WHERE id=?',(json.dumps(payload),rid))
                livekit.register(c,rid,room['expires'],closing=True)
        if payload.get('transport')=='livekit':
            try:livekit.delete_room(rid)
            except livekit.Unavailable:fail(503,localized_message('livekitUnavailable'))
        with db() as c:
            c.execute('DELETE FROM guest_call_rooms WHERE id=? AND owner_id=?', (rid, s['user_id']))
            if payload.get('transport')=='livekit':c.execute('DELETE FROM app_settings WHERE key=?',(livekit.INDEX+rid,))
        return {'ended': True}

    @r.get('/api/guest-calls/{token}')
    def metadata(token: str, request: Request):
        if not re.fullmatch(TOKEN, token):
            fail(410, localized_message('linkIsInvalidOrExpired'))
        with db() as c:
            return room_info(c, load_room(c, token=token))

    @r.get('/api/guest-calls/{token}/avatar')
    def avatar(token: str, request: Request):
        if not re.fullmatch(TOKEN,token):fail(410, localized_message('linkIsInvalidOrExpired'))
        with db() as c:payload=json.loads(load_room(c,token=token)['payload'])
        if not payload.get('avatar'):fail(404, localized_message('callAvatarNotSet'))
        return Response(base64.b64decode(payload['avatar']),media_type='image/webp',
                        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @r.post('/api/guest-calls/{token}/join')
    def guest_join(token: str, data: Join, request: Request):
        if not re.fullmatch(TOKEN, token):
            fail(410, localized_message('linkIsInvalidOrExpired'))
        rate(request, 'guest-call-join', 24, period=60, subject=digest(token))
        if data.transport=='livekit' and not livekit.available():fail(503,localized_message('livekitUnavailable'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); purge_guest_calls(c, now())
            result=join(c, load_room(c, token=token), data)
        return connect_media(result)

    @r.post('/api/guest-calls/rooms/{rid}/sync')
    def sync(rid: str, data: Sync, request: Request):
        header = request.headers.get('authorization', '')
        if len(header) != 50 or not header.startswith('Bearer '):
            fail(401, localized_message('joinTheCallViaInvitation'))
        rate(request, 'guest-sync-ip', 1200, period=60)
        rate(request, 'guest-sync-session', 180, period=60, subject=digest(header))
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); purge_guest_calls(c, now())
            room, me = auth(c, rid, request)
            if json.loads(room['payload']).get('transport')=='livekit' and data.signals:
                fail(422,localized_message('livekitSignalingNotAccepted'))
            payload = json.loads(me['payload']); payload['muted'] = data.muted
            c.execute('UPDATE guest_call_participants SET payload=?,seen=? WHERE id=?', (json.dumps(payload), now(), me['id']))
            for signal in data.signals:
                other = c.execute('SELECT id FROM guest_call_participants WHERE id=? AND room_id=? AND seen>?', (signal.peer_id, rid, now() - 90)).fetchone()
                if not other or signal.peer_id == me['id']:
                    continue
                first, second = sorted([me['id'], signal.peer_id]); side = 'first' if me['id'] == first else 'second'
                link = c.execute('SELECT payload FROM guest_call_links WHERE first_id=? AND second_id=?', (first, second)).fetchone()
                content = json.loads(link['payload']) if link else {}
                if signal.description:
                    description = signal.description.model_dump()
                    if description['type'] != ('offer' if side == 'first' else 'answer'):
                        fail(422, localized_message('incorrectConnectionDirection'))
                    if content.get(side + '_sdp') not in (None, description):
                        fail(409, localized_message('connectionAlreadySetUpConnectAgain'))
                    content[side + '_sdp'] = description
                candidates = content.setdefault(side + '_ice', [])
                for candidate in signal.candidates:
                    value = candidate.model_dump()
                    if value not in candidates:
                        candidates.append(value)
                if len(candidates) > 100:
                    fail(422, localized_message('tooManyCallNetworkAddresses'))
                if link:
                    c.execute('UPDATE guest_call_links SET payload=? WHERE first_id=? AND second_id=?', (json.dumps(content), first, second))
                else:
                    c.execute('INSERT INTO guest_call_links VALUES(?,?,?,?)', (rid, first, second, json.dumps(content)))
            return state(c, room, me)

    @r.post('/api/guest-calls/rooms/{rid}/leave')
    def leave(rid: str, request: Request):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            room, me = auth(c, rid, request)
            sfu=json.loads(room['payload']).get('transport')=='livekit'
            c.execute('DELETE FROM guest_call_participants WHERE id=?', (me['id'],))
        if sfu:
            try:livekit.remove_participant(rid,me['id'])
            except livekit.Unavailable:pass  # Maintenance retries using authoritative local membership.
        return {'left': True}

    return r
