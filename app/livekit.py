"""Optional SFU for invitation rooms. Credentials never leave the application server.

Control/ownership stay in DrG. No network I/O is performed with a DB transaction
open. A single maintenance lease reconciles remote media with local permissions.
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
import jwt


class Unavailable(Exception):
    pass


def settings(*, enabled=True):
    if enabled and os.getenv('CALL_LIVEKIT_ENABLED', 'false').lower() != 'true':
        return None
    url = os.getenv('CALL_LIVEKIT_URL', '').strip().rstrip('/')
    if any(ord(c)<33 for c in url):return None
    key = os.getenv('CALL_LIVEKIT_API_KEY', '').strip()
    secret = os.getenv('CALL_LIVEKIT_API_SECRET', '')
    try:
        public = urlsplit(url)
        if public.port is not None and not 1<=public.port<=65535:return None
    except ValueError:return None
    if public.scheme != 'wss' or not public.hostname or not re.fullmatch(r'[a-zA-Z0-9.\[\]:_-]+',public.netloc) or public.username or public.password or public.query or public.fragment or public.path:
        return None
    if not key or len(key) > 256 or len(secret) < 32:
        return None
    url=urlunsplit(('wss',public.netloc,'','',''))
    api = os.getenv('CALL_LIVEKIT_API_URL', '').strip().rstrip('/') or urlunsplit(('https', public.netloc, '', '', ''))
    if any(ord(c)<33 for c in api):return None
    try:
        parts = urlsplit(api)
        if parts.port is not None and not 1<=parts.port<=65535:return None
    except ValueError:return None
    if parts.scheme not in ('https', 'http') or not parts.hostname or not re.fullmatch(r'[a-zA-Z0-9.\[\]:_-]+',parts.netloc) or parts.username or parts.password or parts.query or parts.fragment or parts.path:
        return None
    return {'url': url, 'api': api, 'key': key, 'secret': secret}


def csp_sources():
    config=settings()
    return (' '+config['url']+' '+config['url'].replace('wss:','https:',1)) if config else ''


def namespace():
    # Stable per installation; no application or TURN secret in room names.
    return 'drg-' + hashlib.sha256(os.getenv('PUBLIC_URL', 'http://localhost:8000').rstrip('/').encode()).hexdigest()[:16] + '-'


def room_name(rid):
    return namespace() + rid


def access_token(config, identity, grant, *, stamp=None, lifetime=90):
    stamp = int(time.time()) if stamp is None else stamp
    return jwt.encode({'iss': config['key'], 'sub': identity, 'nbf': stamp - 5,
                       'exp': stamp + lifetime, 'video': grant}, config['secret'], algorithm='HS256')


_http = httpx.Client(timeout=httpx.Timeout(3, connect=1.5), trust_env=False,
                     limits=httpx.Limits(max_connections=4, max_keepalive_connections=2))


def rpc(method, body, grant, *, config=None):
    config = config or settings(enabled=False)
    if not config:
        raise Unavailable()
    token = access_token(config, 'drg-control', grant)
    try:
        with _http.stream('POST', config['api'] + '/twirp/livekit.RoomService/' + method,
                          headers={'Authorization': 'Bearer ' + token}, json=body) as response:
            if response.status_code == 404 and method in ('DeleteRoom', 'RemoveParticipant'):
                return {}
            if response.status_code != 200:
                raise Unavailable()
            result = bytearray()
            for chunk in response.iter_bytes():
                result.extend(chunk)
                if len(result) > 4 * 1024 * 1024:
                    raise Unavailable()
            return json.loads(result)
    except (httpx.HTTPError, ValueError, TypeError) as error:
        # Do not log response bodies, URLs with credentials, or bearer tokens.
        raise Unavailable() from None


_health_lock = threading.Lock()
_health = {'key': None, 'until': 0, 'ok': False}


def available():
    config = settings()
    if not config:
        return False
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).digest()
    with _health_lock:
        if _health['key'] == fingerprint and _health['until'] > time.monotonic():
            return _health['ok']
        try:
            rpc('ListRooms', {'names': [namespace() + 'health']}, {'roomList': True}, config=config)
            ok = True
        except Unavailable:
            ok = False
        _health.update(key=fingerprint, until=time.monotonic() + (20 if ok else 10), ok=ok)
        return ok


def connect(rid, identity, expires, stamp):
    config = settings()
    if not config or expires <= stamp:
        raise Unavailable()
    name = room_name(rid)
    rpc('CreateRoom', {'name': name, 'max_participants': 8, 'empty_timeout': 60,
                       'departure_timeout': 30,
                       'metadata': json.dumps({'drg_site': namespace(), 'room_id': rid})},
        {'roomCreate': True}, config=config)
    return {'url': config['url'], 'token': access_token(config, identity,
        {'roomJoin': True, 'room': name, 'canPublish': True, 'canSubscribe': True,
         'canPublishData': False, 'canPublishSources': ['microphone']},
        stamp=stamp, lifetime=max(1, min(90, expires - stamp))),
        'room': name, 'identity': identity, 'expires': min(expires, stamp + 90)}


def delete_room(rid):
    rpc('DeleteRoom', {'room': room_name(rid)}, {'roomCreate': True})


def remove_participant(rid, identity):
    name = room_name(rid)
    rpc('RemoveParticipant', {'room': name, 'identity': identity}, {'roomAdmin': True, 'room': name})


def notify_appearance(rid):
    name=room_name(rid)
    try:
        rpc('UpdateRoomMetadata', {'room':name,'metadata':json.dumps(
            {'drg_site':namespace(),'room_id':rid,'revision':secrets.token_hex(8)})},
            {'roomAdmin':True,'room':name})
    except Unavailable:
        # The local change is saved. The ordinary heartbeat also delivers it;
        # do not report a failed save after the DB commit already succeeded.
        pass


INDEX='livekit:room:'


def register(c, rid, expires, *, closing=False):
    # Small persistent index; do not decrypt all invitation avatars every sweep.
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (INDEX+rid,json.dumps({'expires':expires,'closing':closing})))


_membership = {}
_membership_key = None


def reconcile(db, stamp):
    """Retry revocations after outages/restarts, including deleted local rooms.

    The caller holds the existing maintenance lease. One bounded snapshot and
    remote requests per reconciliation, never per note/user or UI poll.
    """
    global _membership_key
    config=settings(enabled=False)
    if not config:
        return
    fingerprint=hashlib.sha256((json.dumps(config,sort_keys=True)+namespace()).encode()).digest()
    if fingerprint!=_membership_key:
        _membership.clear();_membership_key=fingerprint
    with db() as c:
        rooms={}
        for row in c.execute("SELECT i.key,i.value,r.id,r.expires FROM app_settings i LEFT JOIN guest_call_rooms r ON i.key=? || r.id WHERE i.key LIKE ?",(INDEX,INDEX+'%')):
            rid=row['key'][len(INDEX):]
            try:entry=json.loads(row['value'])
            except (ValueError,TypeError):entry={}
            rooms[rid]={'exists':row['id'] is not None,'expires':row['expires'] or 0,
                        'closing':entry.get('closing') is True}
        allowed={}
        for row in c.execute("SELECT p.id,p.room_id FROM guest_call_participants p JOIN app_settings i ON i.key=? || p.room_id WHERE i.key LIKE ? AND p.seen>?",(INDEX,INDEX+'%',stamp-90)):
            allowed.setdefault(row['room_id'],set()).add(row['id'])
    remote=rpc('ListRooms',{}, {'roomList':True}).get('rooms',[])
    prefix=namespace()
    existing={room.get('name') for room in remote if isinstance(room,dict)}
    def forget(rid,closing=False):
        with db() as c:
            # Closed state cannot be reopened by join/appearance requests.
            row=c.execute('SELECT value FROM app_settings WHERE key=?',(INDEX+rid,)).fetchone()
            if closing and row and json.loads(row['value']).get('closing'):
                c.execute('DELETE FROM guest_call_rooms WHERE id=?',(rid,))
            c.execute('DELETE FROM app_settings WHERE key=?',(INDEX+rid,))
    for rid,entry in rooms.items():
        if room_name(rid) not in existing and (not entry['exists'] or entry['expires']<=stamp or entry['closing']):
            forget(rid,entry['closing'])
    for name in list(_membership):
        if name not in existing:_membership.pop(name,None)
    for room in remote:
        name=room.get('name','')
        if not isinstance(name,str) or not name.startswith(prefix):continue
        rid=name[len(prefix):]
        if len(rid)!=32 or any(c not in '0123456789abcdef' for c in rid):continue
        entry=rooms.get(rid,{})
        if not entry.get('exists') or entry.get('expires',0)<=stamp or entry.get('closing'):
            delete_room(rid);_membership.pop(name,None);forget(rid,entry.get('closing',False));continue
        members=frozenset(allowed.get(rid,set()))
        count=room.get('num_participants',room.get('numParticipants'))
        if type(count) is int and (_membership.get(name)==(members,count) or count==0):
            _membership[name]=(members,count);continue
        people=rpc('ListParticipants',{'room':name},{'roomAdmin':True,'room':name}).get('participants',[])
        remaining=0
        for person in people:
            identity=person.get('identity','')
            if identity not in members:remove_participant(rid,identity)
            else:remaining+=1
        _membership[name]=(members,remaining)
