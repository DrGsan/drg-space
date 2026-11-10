"""Device-authorized inter-server WebRTC signaling, canonical on the callee.

Media never passes through this service. Caller and callee keep their own ICE
credentials. A server proof or call ID alone never authorizes reading SDP/ICE.
"""
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from app.chat_storage import cutoff
from app.modules.calls import Candidate, MediaState, ice_config
from app.modules.preferences import module_enabled
from .identity import FederationError
from .actors import canonical

FEDERATION_CALLS_SCHEMA = '''
CREATE TABLE IF NOT EXISTS federation_calls(id TEXT PRIMARY KEY,caller_server_id TEXT NOT NULL,caller_id TEXT NOT NULL,recipient_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,caller_device TEXT NOT NULL,recipient_device TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,created INTEGER NOT NULL,answered INTEGER NOT NULL DEFAULT 0,connected INTEGER NOT NULL DEFAULT 0,ended INTEGER NOT NULL DEFAULT 0,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS federation_call_recipient_idx ON federation_calls(recipient_id,status,created);
CREATE INDEX IF NOT EXISTS federation_call_caller_idx ON federation_calls(caller_server_id,caller_id,status,created);
CREATE TABLE IF NOT EXISTS federation_call_links(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,home_server_id TEXT NOT NULL,actor_id TEXT NOT NULL,device_id TEXT NOT NULL,device TEXT NOT NULL,status TEXT NOT NULL,created INTEGER NOT NULL,seen INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS federation_call_link_user_idx ON federation_call_links(user_id,status,created);
'''

CALL_ID = r'^[A-Za-z0-9_-]{16,80}$'
DEVICE_ID = r'^[a-f0-9]{64}$'
LIVE = ('ringing', 'connecting', 'active')
LINK_LIVE = ('preparing', *LIVE)
TERMINAL = ('ended', 'declined', 'cancelled', 'missed', 'failed')


class CallError(FederationError):
    def __init__(self, code, status_code=409):
        super().__init__(code)
        self.status_code = status_code


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Handle(Model):
    device: str = Field(pattern=CALL_ID)


class Address(Handle):
    id: str = Field(pattern=CALL_ID)


class Invite(Address):
    sdp: str = Field(min_length=1, max_length=32000)
    media: MediaState | None = None


class Signal(Address):
    sdp: str | None = Field(default=None, min_length=1, max_length=32000)
    candidates: list[Candidate] = Field(default_factory=list, max_length=100)
    connected: StrictBool = False
    media: MediaState | None = None


class End(Address):
    reason: Literal['hangup', 'decline', 'failed'] = 'hangup'


class Current(Handle):
    id: str | None = Field(default=None, pattern=CALL_ID)


class History(Model):
    before: str | None = Field(default=None, pattern=CALL_ID)


class OutgoingStatus(Address):
    status: Literal['ringing', 'connecting', 'active', 'ended', 'declined', 'cancelled', 'missed', 'failed']
    answered: int = Field(default=0, ge=0)
    connected: int = Field(default=0, ge=0)
    ended: int = Field(default=0, ge=0)


OPERATION_MODELS = {
    'call_invite': Invite, 'call_accept': Address, 'call_poll': Address,
    'call_signal': Signal, 'call_end': End, 'call_current': Current,
    'call_history': History, 'call_outgoing_status': OutgoingStatus,
    'call_config': Model, 'call_capabilities': Model,
}


def validate_call_payload(operation, payload):
    model = OPERATION_MODELS.get(operation)
    if model is None:
        raise CallError('unsupported_call_operation', 422)
    try:
        return model.model_validate(payload)
    except ValidationError:
        raise CallError('invalid_call_fields', 422) from None


def _finish(c, row, status, now):
    c.execute('UPDATE federation_calls SET status=?,ended=?,payload=? WHERE id=?',
              (status, now, '{}', row['id']))


def purge_federated_calls(c, now):
    for row in c.execute("SELECT * FROM federation_calls WHERE status IN ('ringing','connecting','active')").fetchall():
        payload = json.loads(row['payload'])
        if row['status'] == 'ringing' and row['created'] <= now - 60:
            _finish(c, row, 'missed', now)
        elif row['status'] != 'ringing' and (min(payload.get('caller_seen', 0), payload.get('recipient_seen', 0)) <= now - 45
                                               or row['status'] == 'connecting' and row['answered'] <= now - 40):
            _finish(c, row, 'failed', now)
    for row in c.execute("SELECT * FROM federation_call_links WHERE status IN ('preparing','ringing','connecting','active')").fetchall():
        timeout = 60 if row['status'] in ('preparing', 'ringing') else 45
        if row['seen'] <= now - timeout:
            c.execute('UPDATE federation_call_links SET status=?,payload=? WHERE id=?',
                      ('failed', json.dumps({'answered': 0, 'connected': 0, 'ended': now}), row['id']))
    c.execute('DELETE FROM federation_calls WHERE created<=?', (cutoff(now),))
    c.execute('DELETE FROM federation_call_links WHERE created<=?', (cutoff(now),))


def federated_busy(c, uid, now, exclude=None):
    """Called in the same transaction as local/guest/federated call admission."""
    purge_federated_calls(c, now)
    if c.execute("SELECT 1 FROM federation_calls WHERE recipient_id=? AND status IN ('ringing','connecting','active') AND (? IS NULL OR id!=?)",
                 (uid, exclude, exclude)).fetchone():
        return True
    return bool(c.execute("SELECT 1 FROM federation_call_links WHERE user_id=? AND status IN ('preparing','ringing','connecting','active') AND (? IS NULL OR id!=?)",
                          (uid, exclude, exclude)).fetchone())


class FederatedCalls:
    def __init__(self, own_server_id, *, authorize, available=None, config=None, person=None, notify=None):
        self.server_id = own_server_id
        self.authorize = authorize
        self.available = available or self._available
        self.config = config or self._config
        self.person = person or self._person
        self.notify = notify

    @staticmethod
    def _available(c, uid):
        from app.turn_health import turn_available
        user = c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()
        return bool(user and module_enabled(json.loads(user['settings']), 'chat') and turn_available())

    @staticmethod
    def _config(c, uid, now):
        def fail(code, message):
            raise CallError('call_configuration_unavailable', code)
        return ice_config(uid, now, fail)

    @staticmethod
    def _person(c, local_uid, peer_id, actor_id, now):
        return {'id': actor_id, 'login': actor_id, 'display_name': '', 'avatar_url': None,
                'online': False, 'last_seen': None, 'server_id': peer_id}

    @staticmethod
    def _user(c, uid):
        user = c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()
        if not user or not module_enabled(json.loads(user['settings']), 'chat'):
            raise CallError('calls_disabled', 403)

    def _busy(self, c, uid, now, exclude=None):
        # These tables belong to the current DrG schema. Local and guest calls
        # cannot race a federated admission on the same database transaction.
        from app.modules.calls import purge_calls
        from app.modules.guest_calls import purge_guest_calls
        purge_calls(c, now)
        purge_guest_calls(c, now)
        if federated_busy(c, uid, now, exclude):
            return True
        if c.execute("SELECT 1 FROM calls WHERE (caller_id=? OR recipient_id=?) AND status IN ('ringing','connecting','active')", (uid, uid)).fetchone():
            return True
        return bool(c.execute('SELECT 1 FROM guest_call_participants WHERE user_id=?', (uid,)).fetchone())

    def _authorize(self, c, row, now):
        self._user(c, row['recipient_id'])
        try:
            self.authorize(c, row['recipient_id'], row['caller_server_id'], row['caller_id'], row['caller_device'])
        except FederationError:
            if row['status'] in LIVE:
                # Caller may still retry a fresh packet, but the call state no
                # longer exposes media parameters once the relation is gone.
                _finish(c, row, 'ended', now)
            raise

    def reconcile(self, c, now, uid=None):
        purge_federated_calls(c, now)
        query = "SELECT * FROM federation_calls WHERE status IN ('ringing','connecting','active')"
        parameters = ()
        if uid:
            query += ' AND recipient_id=?'
            parameters = (uid,)
        for row in c.execute(query, parameters).fetchall():
            try:
                self._authorize(c, row, now)
            except FederationError:
                pass
        if uid:
            links = c.execute("SELECT * FROM federation_call_links WHERE user_id=? AND status IN ('preparing','ringing','connecting','active')", (uid,)).fetchall()
            for row in links:
                try:
                    self._user(c, uid)
                    self.authorize(c, uid, row['home_server_id'], row['actor_id'], None)
                except FederationError:
                    c.execute('UPDATE federation_call_links SET status=?,payload=? WHERE id=?',
                              ('ended', json.dumps({'answered': 0, 'connected': 0, 'ended': now}), row['id']))

    def _row(self, c, call_id):
        row = c.execute('SELECT * FROM federation_calls WHERE id=?', (call_id,)).fetchone()
        if not row:
            raise CallError('call_not_found', 404)
        return row

    def _serial(self, c, row, *, caller, device_id, handle, now):
        payload = json.loads(row['payload'])
        peer_id = self.server_id if caller else row['caller_server_id']
        actor_id = row['recipient_id'] if caller else row['caller_id']
        local_uid = row['caller_id'] if caller else row['recipient_id']
        side, opposite = ('caller', 'recipient') if caller else ('recipient', 'caller')
        owned = (row[side + '_device'] == device_id and payload.get(side + '_handle') == handle)
        item = {key: row[key] for key in ('id', 'status', 'created', 'answered', 'connected', 'ended')}
        item.update(space_id='federation:' + peer_id + ':' + actor_id,
                    outgoing=caller, owned=owned,
                    person=self.person(c, local_uid, peer_id, actor_id, now),
                    federation={'home_server_id': self.server_id, 'home_actor_id': row['recipient_id'],
                                'peer_id': peer_id, 'actor_id': actor_id})
        if owned and row['status'] in LIVE:
            item.update(sdp=payload.get(opposite + '_sdp'), candidates=payload.get(opposite + '_ice', []),
                        media=payload.get(opposite + '_media', {}), peer_actions=payload.get(opposite + '_actions', []))
        elif not caller and row['status'] == 'ringing':
            # Authenticate a ring before asking the user to accept it. The
            # signed original invite necessarily includes its offer bytes; it
            # is returned only in a result sealed for this recipient's device.
            # No RTC remote description is applied until a device claims it.
            item['peer_actions'] = payload.get('caller_actions', [])
        return item

    def _link_serial(self, c, row, uid, device_id, handle, now):
        payload = json.loads(row['payload'])
        item = {key: row[key] for key in ('id', 'status', 'created')}
        item.update(answered=payload.get('answered', 0), connected=payload.get('connected', 0), ended=payload.get('ended', 0),
                    space_id='federation:' + row['home_server_id'] + ':' + row['actor_id'], outgoing=True,
                    owned=row['device_id'] == device_id and row['device'] == handle,
                    person=self.person(c, uid, row['home_server_id'], row['actor_id'], now),
                    federation={'home_server_id': row['home_server_id'], 'home_actor_id': row['actor_id'],
                                'peer_id': row['home_server_id'], 'actor_id': row['actor_id']})
        return item

    def reserve_outgoing(self, c, uid, card, peer, target_actor, payload, now):
        data = validate_call_payload('call_invite', payload)
        self.reconcile(c, now, uid)
        self._user(c, uid)
        self.authorize(c, uid, peer.server_id, target_actor, None)
        if not self.available(c, uid):
            raise CallError('call_configuration_unavailable', 503)
        old = c.execute('SELECT * FROM federation_call_links WHERE id=?', (data.id,)).fetchone()
        if old:
            if (old['user_id'], old['device_id'], old['device'], old['home_server_id'], old['actor_id']) != (uid, card.device_id, data.device, peer.server_id, target_actor):
                raise CallError('call_id_conflict')
            if old['status'] not in LINK_LIVE:
                raise CallError('call_already_ended')
            return
        if c.execute('SELECT 1 FROM federation_calls WHERE id=?', (data.id,)).fetchone():
            raise CallError('call_id_conflict')
        if self._busy(c, uid, now):
            raise CallError('call_busy')
        c.execute('INSERT INTO federation_call_links VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (data.id, uid, peer.server_id, target_actor, card.device_id, data.device, 'preparing', now, now, '{}'))

    def outgoing_failed(self, c, uid, card, call_id, now, definitive=False):
        row = c.execute('SELECT * FROM federation_call_links WHERE id=? AND user_id=? AND device_id=?',
                        (call_id, uid, card.device_id)).fetchone()
        if row and row['status'] == 'preparing' and definitive:
            c.execute('UPDATE federation_call_links SET status=?,seen=?,payload=? WHERE id=?',
                      ('failed', now, json.dumps({'answered': 0, 'connected': 0, 'ended': now}), call_id))

    @staticmethod
    def _evidence(evidence, *, operation, payload, server_id, actor_id, device_id):
        if (not isinstance(evidence, dict) or set(evidence) != {'intent', 'proof'}
                or not isinstance(evidence['intent'], dict) or not isinstance(evidence['proof'], str)
                or not 1 <= len(evidence['proof']) <= 4096):
            raise CallError('call_device_evidence_required', 403)
        signed = evidence['intent']
        expected = {'actor_server_id': server_id, 'actor_id': actor_id, 'device_id': device_id,
                    'operation': operation, 'payload': payload}
        if any(signed.get(key) != value for key, value in expected.items()):
            raise CallError('call_device_evidence_mismatch', 403)
        # Proof verification and nonce consumption happen in the shared actor
        # authorization layer. Exact signed bytes are kept for the OTHER device
        # to verify, so a server-key thief cannot substitute a DTLS fingerprint.
        return json.loads(canonical(evidence))

    @staticmethod
    def _remember(stored, side, evidence):
        actions = stored.setdefault(side + '_actions', [])
        # A new signed media state supersedes earlier media-only signals. Keep
        # every offer/answer and candidate proof, while allowing a long call to
        # mute/unmute without exhausting the bounded signaling evidence log.
        if evidence['intent']['payload'].get('media') is not None:
            actions[:] = [action for action in actions
                          if action['intent']['operation'] != 'call_signal'
                          or action['intent']['payload'].get('media') is None
                          or action['intent']['payload'].get('sdp')
                          or action['intent']['payload'].get('candidates')]
        if evidence not in actions:
            actions.append(evidence)
        if len(actions) > 128 or len(canonical(stored)) > 512 * 1024:
            raise CallError('call_signaling_limit', 422)

    def remote_operation(self, c, peer, intent, now, *, evidence=None):
        operation, payload = intent.operation, intent.payload
        data = validate_call_payload(operation, payload)
        self.reconcile(c, now, intent.target_actor_id)
        self._user(c, intent.target_actor_id)
        self.authorize(c, intent.target_actor_id, peer.server_id, intent.actor_id, intent.device_id)
        if operation == 'call_capabilities':
            return {'available': bool(self.available(c, intent.target_actor_id)), 'audio': True, 'video': True,
                    'screen': True, 'shared_turn': False}
        if operation == 'call_invite':
            if not self.available(c, intent.target_actor_id):
                raise CallError('peer_calls_unavailable', 503)
            row = c.execute('SELECT * FROM federation_calls WHERE id=?', (data.id,)).fetchone()
            if row:
                self._remote_access(row, peer, intent)
                stored = json.loads(row['payload'])
                if row['status'] in LIVE and (stored.get('caller_handle') != data.device or stored.get('caller_sdp') != data.sdp):
                    raise CallError('call_id_conflict')
                return self._serial(c, row, caller=True, device_id=intent.device_id, handle=data.device, now=now)
            if c.execute('SELECT 1 FROM federation_call_links WHERE id=?', (data.id,)).fetchone():
                raise CallError('call_id_conflict')
            if self._busy(c, intent.target_actor_id, now):
                raise CallError('call_busy')
            if c.execute("SELECT 1 FROM federation_calls WHERE caller_server_id=? AND caller_id=? AND status IN ('ringing','connecting','active')", (peer.server_id, intent.actor_id)).fetchone():
                raise CallError('call_busy')
            stored = {'caller_handle': data.device, 'caller_sdp': data.sdp, 'caller_seen': now,
                      'recipient_seen': now, 'caller_ice': [], 'recipient_ice': []}
            if data.media is not None:
                stored['caller_media'] = data.media.model_dump()
            action = self._evidence(evidence, operation=operation, payload=payload, server_id=peer.server_id,
                                    actor_id=intent.actor_id, device_id=intent.device_id)
            self._remember(stored, 'caller', action)
            c.execute('INSERT INTO federation_calls(id,caller_server_id,caller_id,recipient_id,caller_device,status,created,payload) VALUES(?,?,?,?,?,?,?,?)',
                      (data.id, peer.server_id, intent.actor_id, intent.target_actor_id, intent.device_id, 'ringing', now, json.dumps(stored)))
            row = self._row(c, data.id)
            if self.notify:
                self.notify(c, intent.target_actor_id, row, now)
            return self._serial(c, row, caller=True, device_id=intent.device_id, handle=data.device, now=now)
        if operation not in ('call_poll', 'call_signal', 'call_end'):
            raise CallError('caller_operation_forbidden', 403)
        row = self._row(c, data.id)
        self._remote_access(row, peer, intent)
        return self._action(c, row, data, operation, caller=True, device_id=intent.device_id, now=now,
                            evidence=evidence, raw_payload=payload, actor_server_id=peer.server_id,
                            actor_id=intent.actor_id)

    @staticmethod
    def _remote_access(row, peer, intent):
        if (row['caller_server_id'], row['caller_id'], row['caller_device'], row['recipient_id']) != (
                peer.server_id, intent.actor_id, intent.device_id, intent.target_actor_id):
            raise CallError('call_not_found', 404)

    def local_operation(self, c, uid, card, operation, payload, now, *, evidence=None):
        data = validate_call_payload(operation, payload)
        self.reconcile(c, now, uid)
        self._user(c, uid)
        if operation == 'call_config':
            result = self.config(c, uid, now)
            return {**result, 'available': bool(result.get('available') and self.available(c, uid))}
        if operation == 'call_current':
            row = c.execute('SELECT * FROM federation_calls WHERE id=? AND recipient_id=?', (data.id,uid)).fetchone() if data.id else c.execute("SELECT * FROM federation_calls WHERE recipient_id=? AND status IN ('ringing','connecting','active') ORDER BY created DESC LIMIT 1", (uid,)).fetchone()
            if row:
                if row['recipient_id'] != uid:
                    raise CallError('call_not_found', 404)
                self._authorize(c, row, now)
                return {'call': self._serial(c, row, caller=False, device_id=card.device_id, handle=data.device, now=now)}
            link = c.execute('SELECT * FROM federation_call_links WHERE user_id=? AND id=?',(uid,data.id)).fetchone() if data.id else c.execute("SELECT * FROM federation_call_links WHERE user_id=? AND status IN ('preparing','ringing','connecting','active') ORDER BY created DESC LIMIT 1", (uid,)).fetchone()
            if data.id and not link:
                raise CallError('call_not_found',404)
            return {'call': self._link_serial(c, link, uid, card.device_id, data.device, now) if link else None}
        if operation == 'call_history':
            return self._history(c, uid, card.device_id, data.before, now)
        if operation == 'call_outgoing_status':
            return self._outgoing_status(c, uid, card.device_id, data, now)
        if operation not in ('call_poll', 'call_accept', 'call_signal', 'call_end'):
            raise CallError('callee_operation_forbidden', 403)
        row = self._row(c, data.id)
        if row['recipient_id'] != uid:
            raise CallError('call_not_found', 404)
        self._authorize(c, row, now)
        if operation == 'call_accept':
            if not self.available(c, uid):
                raise CallError('call_configuration_unavailable', 503)
            stored = json.loads(row['payload'])
            if row['status'] != 'ringing':
                if row['status'] in LIVE and row['recipient_device'] == card.device_id and stored.get('recipient_handle') == data.device:
                    return self._serial(c, row, caller=False, device_id=card.device_id, handle=data.device, now=now)
                raise CallError('call_already_claimed')
            stored.update(recipient_handle=data.device, recipient_seen=now)
            action = self._evidence(evidence, operation=operation, payload=payload, server_id=self.server_id,
                                    actor_id=uid, device_id=card.device_id)
            self._remember(stored, 'recipient', action)
            c.execute('UPDATE federation_calls SET recipient_device=?,status=?,answered=?,payload=? WHERE id=?',
                      (card.device_id, 'connecting', now, json.dumps(stored), row['id']))
            return self._serial(c, self._row(c, row['id']), caller=False, device_id=card.device_id, handle=data.device, now=now)
        return self._action(c, row, data, operation, caller=False, device_id=card.device_id, now=now,
                            evidence=evidence, raw_payload=payload, actor_server_id=self.server_id, actor_id=uid)

    def _action(self, c, row, data, operation, *, caller, device_id, now, evidence=None, raw_payload=None,
                actor_server_id=None, actor_id=None):
        side = 'caller' if caller else 'recipient'
        stored = json.loads(row['payload'])
        owned = row[side + '_device'] == device_id and stored.get(side + '_handle') == data.device
        if operation == 'call_poll':
            if owned and row['status'] in LIVE:
                stored[side + '_seen'] = now
                c.execute('UPDATE federation_calls SET payload=? WHERE id=?', (json.dumps(stored), row['id']))
                row = self._row(c, row['id'])
            return self._serial(c, row, caller=caller, device_id=device_id, handle=data.device, now=now)
        if operation == 'call_end':
            if row['status'] in LIVE:
                if not owned and not (not caller and row['status'] == 'ringing'):
                    raise CallError('call_device_mismatch')
                status = 'failed' if data.reason == 'failed' else 'declined' if not caller and row['status'] == 'ringing' else 'cancelled' if row['status'] == 'ringing' else 'ended'
                _finish(c, row, status, now)
            return self._serial(c, self._row(c, row['id']), caller=caller, device_id=device_id, handle=data.device, now=now)
        if row['status'] not in LIVE:
            raise CallError('call_already_ended')
        if not owned:
            raise CallError('call_device_mismatch')
        changed = False
        if data.sdp:
            previous = stored.get(side + '_sdp')
            if previous and previous != data.sdp:
                raise CallError('call_description_changed')
            stored[side + '_sdp'] = data.sdp
            changed = changed or previous is None
        candidates = stored.get(side + '_ice', [])
        for candidate in data.candidates:
            item = candidate.model_dump()
            if item not in candidates:
                candidates.append(item)
                changed = True
        if len(candidates) > 100:
            raise CallError('too_many_call_candidates', 422)
        stored[side + '_ice'] = candidates
        stored[side + '_seen'] = now
        if data.media is not None:
            changed = changed or stored.get(side + '_media') != data.media.model_dump()
            stored[side + '_media'] = data.media.model_dump()
        status, connected = row['status'], row['connected']
        if data.connected and row['answered'] and stored.get('recipient_sdp'):
            changed = changed or status != 'active'
            status, connected = 'active', connected or now
        if changed:
            action = self._evidence(evidence, operation=operation, payload=raw_payload,
                                    server_id=actor_server_id, actor_id=actor_id, device_id=device_id)
            self._remember(stored, side, action)
        c.execute('UPDATE federation_calls SET status=?,connected=?,payload=? WHERE id=?',
                  (status, connected, json.dumps(stored), row['id']))
        return self._serial(c, self._row(c, row['id']), caller=caller, device_id=device_id, handle=data.device, now=now)

    def _outgoing_status(self, c, uid, device_id, data, now):
        row = c.execute('SELECT * FROM federation_call_links WHERE id=? AND user_id=?', (data.id, uid)).fetchone()
        if not row:
            raise CallError('call_not_found', 404)
        if row['device_id'] != device_id or row['device'] != data.device:
            raise CallError('call_device_mismatch')
        self.authorize(c, uid, row['home_server_id'], row['actor_id'], None)
        if any(stamp > now + 30 or stamp and stamp < row['created'] - 30
               for stamp in (data.answered, data.connected, data.ended)):
            raise CallError('invalid_call_time', 422)
        if data.status == 'connecting' and not data.answered or data.status == 'active' and not (data.answered and data.connected):
            raise CallError('invalid_call_time', 422)
        # A later stale packet cannot reopen a terminal call or move its route.
        if row['status'] in TERMINAL:
            return self._link_serial(c, row, uid, device_id, data.device, now)
        previous = json.loads(row['payload'])
        order = {'preparing': 0, 'ringing': 1, 'connecting': 2, 'active': 3}
        status = row['status'] if data.status in LIVE and order.get(data.status, 0) < order.get(row['status'], 0) else data.status
        ended = data.ended or (now if status in TERMINAL else 0)
        stored = {'answered': max(previous.get('answered', 0), data.answered),
                  'connected': max(previous.get('connected', 0), data.connected), 'ended': ended}
        c.execute('UPDATE federation_call_links SET status=?,seen=?,payload=? WHERE id=?',
                  (status, now, json.dumps(stored), data.id))
        row = c.execute('SELECT * FROM federation_call_links WHERE id=?', (data.id,)).fetchone()
        return self._link_serial(c, row, uid, device_id, data.device, now)

    def _history(self, c, uid, device_id, before, now):
        stamp = None
        if before:
            cursor = c.execute('SELECT created FROM federation_calls WHERE id=? AND recipient_id=?',(before,uid)).fetchone()
            if not cursor:
                cursor = c.execute('SELECT created FROM federation_call_links WHERE id=? AND user_id=?',(before,uid)).fetchone()
            if not cursor:
                raise CallError('call_not_found',404)
            stamp = cursor['created']
        rows = []
        parameters = (uid,stamp,stamp,stamp,before)
        for row in c.execute('SELECT * FROM federation_calls WHERE recipient_id=? AND (? IS NULL OR created<? OR (created=? AND id<?)) ORDER BY created DESC,id DESC LIMIT 101', parameters).fetchall():
            item = self._serial(c, row, caller=False, device_id=device_id, handle=None, now=now)
            item.pop('peer_actions',None)
            rows.append(item)
        for row in c.execute('SELECT * FROM federation_call_links WHERE user_id=? AND (? IS NULL OR created<? OR (created=? AND id<?)) ORDER BY created DESC,id DESC LIMIT 101', parameters).fetchall():
            rows.append(self._link_serial(c, row, uid, device_id, None, now))
        rows.sort(key=lambda row: (row['created'], row['id']), reverse=True)
        return {'items': rows[:100], 'more': len(rows) > 100}

    @staticmethod
    def revoke_contact(c, local_uid, peer_id, actor_id, now):
        for row in c.execute("SELECT * FROM federation_calls WHERE recipient_id=? AND caller_server_id=? AND caller_id=? AND status IN ('ringing','connecting','active')", (local_uid, peer_id, actor_id)).fetchall():
            _finish(c, row, 'ended', now)
        c.execute("UPDATE federation_call_links SET status='ended',payload=? WHERE user_id=? AND home_server_id=? AND actor_id=? AND status IN ('preparing','ringing','connecting','active')",
                  (json.dumps({'answered': 0, 'connected': 0, 'ended': now}), local_uid, peer_id, actor_id))

    @staticmethod
    def revoke_peer(c, peer_id, now):
        for row in c.execute("SELECT * FROM federation_calls WHERE caller_server_id=? AND status IN ('ringing','connecting','active')", (peer_id,)).fetchall():
            _finish(c, row, 'ended', now)
        c.execute("UPDATE federation_call_links SET status='ended',payload=? WHERE home_server_id=? AND status IN ('preparing','ringing','connecting','active')",
                  (json.dumps({'answered': 0, 'connected': 0, 'ended': now}), peer_id))

    @staticmethod
    def revoke_device(c, local_uid, device_id, now):
        for row in c.execute("SELECT * FROM federation_calls WHERE recipient_id=? AND recipient_device=? AND status IN ('ringing','connecting','active')",
                             (local_uid, device_id)).fetchall():
            _finish(c, row, 'ended', now)
        c.execute("UPDATE federation_call_links SET status='ended',payload=? WHERE user_id=? AND device_id=? AND status IN ('preparing','ringing','connecting','active')",
                  (json.dumps({'answered': 0, 'connected': 0, 'ended': now}), local_uid, device_id))


def reset_federated_calls_after_restore(c, now):
    """A backup cannot resurrect old SDP, an accepted device claim or live media."""
    # Snapshot staging also calls this with a raw SQLite connection whose rows
    # are tuples. A bulk update works for both that connection and live stores.
    c.execute("UPDATE federation_calls SET status='failed',ended=?,payload=? WHERE status IN ('ringing','connecting','active')",
              (now, '{}'))
    c.execute("UPDATE federation_call_links SET status='failed',payload=? WHERE status IN ('preparing','ringing','connecting','active')",
              (json.dumps({'answered': 0, 'connected': 0, 'ended': now}),))


def validate_federated_call_archive(c, fail):
    """Structural validation on the decoded portable archive before restore."""
    try:
        for row in c.execute('SELECT * FROM federation_calls'):
            if (not re.fullmatch(CALL_ID, row['id'])
                    or any(not re.fullmatch(r'[a-f0-9]{32}', row[key]) for key in ('caller_server_id', 'caller_id', 'recipient_id'))
                    or not re.fullmatch(DEVICE_ID, row['caller_device'])
                    or row['recipient_device'] and not re.fullmatch(DEVICE_ID, row['recipient_device'])
                    or row['status'] not in (*LIVE, *TERMINAL)
                    or any(type(row[key]) is not int or row[key] < 0 for key in ('created', 'answered', 'connected', 'ended'))):
                fail()
            payload = json.loads(row['payload'])
            if not isinstance(payload, dict) or len(canonical(payload)) > 512 * 1024:
                fail()
            if row['status'] in TERMINAL:
                if payload or not row['ended']:
                    fail()
                continue
            allowed = {side + suffix for side in ('caller', 'recipient')
                       for suffix in ('_handle', '_sdp', '_seen', '_ice', '_media', '_actions')}
            if set(payload) - allowed or not payload.get('caller_sdp') or not payload.get('caller_handle'):
                fail()
            if row['status'] != 'ringing' and (not row['recipient_device'] or not payload.get('recipient_handle') or not row['answered']):
                fail()
            if row['status'] == 'active' and (not row['connected'] or not payload.get('recipient_sdp')):
                fail()
            for side in ('caller', 'recipient'):
                if type(payload.get(side + '_seen')) is not int or payload[side + '_seen'] < 0:
                    fail()
                if side + '_handle' in payload and not re.fullmatch(CALL_ID, payload[side + '_handle']):
                    fail()
                description = payload.get(side + '_sdp')
                if description is not None and (not isinstance(description, str) or not 1 <= len(description) <= 32000):
                    fail()
                candidates = payload.get(side + '_ice', [])
                if not isinstance(candidates, list) or len(candidates) > 100:
                    fail()
                for candidate in candidates:
                    Candidate.model_validate(candidate)
                if side + '_media' in payload:
                    MediaState.model_validate(payload[side + '_media'])
                actions = payload.get(side + '_actions', [])
                if not isinstance(actions, list) or len(actions) > 128:
                    fail()
                for action in actions:
                    if not isinstance(action, dict) or set(action) != {'intent', 'proof'}:
                        fail()
                    intent = action['intent']
                    if (not isinstance(intent, dict) or intent.get('operation') not in ('call_invite', 'call_accept', 'call_signal')
                            or intent.get('payload', {}).get('id') != row['id']
                            or not isinstance(action['proof'], str) or not 1 <= len(action['proof']) <= 4096):
                        fail()
                    validate_call_payload(intent['operation'], intent['payload'])
        for row in c.execute('SELECT * FROM federation_call_links'):
            if (not re.fullmatch(CALL_ID, row['id']) or not re.fullmatch(CALL_ID, row['device'])
                    or any(not re.fullmatch(r'[a-f0-9]{32}', row[key]) for key in ('user_id', 'home_server_id', 'actor_id'))
                    or not re.fullmatch(DEVICE_ID, row['device_id']) or row['status'] not in (*LINK_LIVE, *TERMINAL)
                    or any(type(row[key]) is not int or row[key] < 0 for key in ('created', 'seen'))):
                fail()
            payload = json.loads(row['payload'])
            if (not isinstance(payload, dict) or set(payload) - {'answered', 'connected', 'ended'}
                    or any(type(value) is not int or value < 0 for value in payload.values())
                    or row['status'] in TERMINAL and not payload.get('ended')):
                fail()
    except (ValueError, TypeError, KeyError, AttributeError, ValidationError):
        fail()
