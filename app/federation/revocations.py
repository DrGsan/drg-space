"""Durable, monotonic revocation notices between already confirmed servers.

Transport authority can remove access, never add a device or grant. Device proof
is deliberately unnecessary here: a lost device must be revocable without its key.
"""
import re
import time
from .identity import FederationError, ServerDescriptor

REVOCATION_PATH = '/_drg/federation/v1/revoke'


def enqueue(c, uid, *, device_id='', peer_id=None):
    peers = [row[0] for row in c.execute('SELECT DISTINCT peer_id FROM contacts WHERE user_id=?' +
             (' AND peer_id=?' if peer_id else ''), (uid, peer_id) if peer_id else (uid,))]
    for peer in peers:
        c.execute('INSERT INTO federation_revocations VALUES(?,?,?,?,?) ON CONFLICT(peer_id,actor_id,device_id,target_actor_id) DO NOTHING',
                  (peer, uid, device_id, '', int(time.time())))


def account_deleted(c, uid, now):
    """Called before user cascades remove contacts; the outbox outlives users."""
    from .calls import FederatedCalls
    for row in c.execute('SELECT peer_id,actor_id FROM federation_contacts WHERE user_id=?',(uid,)).fetchall():
        c.execute('INSERT INTO federation_revocations VALUES(?,?,?,?,?) ON CONFLICT(peer_id,actor_id,device_id,target_actor_id) DO NOTHING',
                  (row['peer_id'],uid,'',row['actor_id'],now))
        FederatedCalls.revoke_contact(c,uid,row['peer_id'],row['actor_id'],now)


def install(service):
    @service.router.post(REVOCATION_PATH)
    def receive():
        data, raw = service.body()
        service.fields(data, ('actor_id','device_id','target_actor_id'))
        if (not isinstance(data['actor_id'],str) or not re.fullmatch(r'[a-f0-9]{32}',data['actor_id']) or
            not isinstance(data['device_id'],str) or data['device_id'] and not re.fullmatch(r'[a-f0-9]{64}',data['device_id']) or
            not isinstance(data['target_actor_id'],str) or
            (data['device_id'] and data['target_actor_id']) or
            (not data['device_id'] and not re.fullmatch(r'[a-f0-9]{32}',data['target_actor_id']))):
            raise FederationError('invalid_revocation')
        peer, verified = service.incoming(raw)
        with service.state.transaction() as c:
            service.peer(c, peer.server_id)
            values = (peer.server_id, data['actor_id'])
            users = [row[0] for row in c.execute('SELECT user_id FROM contacts WHERE peer_id=? AND actor_id=?'+
                     (' AND user_id=?' if not data['device_id'] else ''),
                     (*values,data['target_actor_id']) if not data['device_id'] else values)]
            if data['device_id']:
                if users:
                    c.execute('INSERT INTO federation_device_revocations VALUES(?,?,?,?) ON CONFLICT(peer_id,actor_id,device_id) DO NOTHING',
                              (*values,data['device_id'],int(time.time())))
                c.execute('UPDATE federation_contact_devices SET active=0 WHERE peer_id=? AND actor_id=? AND device_id=?',
                          (*values, data['device_id']))
            else:
                c.execute("UPDATE contacts SET status='blocked',invitation='' WHERE peer_id=? AND actor_id=? AND user_id=?",(*values,data['target_actor_id']))
                c.execute('UPDATE grants SET active=0,revision=revision+1 WHERE peer_id=? AND actor_id=? AND owner_id=? AND active=1',(*values,data['target_actor_id']))
            if service.calls:
                for uid in users: service.calls.revoke_contact(c,uid,*values,int(time.time()))
        return service.signed_result(peer,verified,{'revoked':True})


def deliver(service):
    if not service.identity: return
    with service.state.transaction() as c:
        # Retain notices while locally disabled. A peer can be disabled here and
        # still needs notification using its previously confirmed certificate.
        if not service.enabled(c): return
        rows = [dict(row) for row in c.execute('SELECT r.*,p.descriptor FROM federation_revocations r JOIN peers p ON p.id=r.peer_id ORDER BY r.created LIMIT 8')]
    for row in rows:
        acknowledged = False
        try:
            peer = ServerDescriptor.model_validate_json(row['descriptor'])
            result = service.transport.post(peer,REVOCATION_PATH,
                        {'actor_id':row['actor_id'],'device_id':row['device_id'],'target_actor_id':row['target_actor_id']},int(time.time()))
            acknowledged = result == {'revoked':True}
        except FederationError:
            pass
        with service.state.transaction() as c:
            key = (row['peer_id'],row['actor_id'],row['device_id'],row['target_actor_id'])
            if acknowledged:
                c.execute('DELETE FROM federation_revocations WHERE peer_id=? AND actor_id=? AND device_id=? AND target_actor_id=?',key)
            else:
                # Round-robin priority, even with a frozen/backwards clock.
                # Unavailable old peers cannot starve a healthy new recipient.
                priority = max(int(time.time()),c.execute('SELECT COALESCE(MAX(created),0)+1 FROM federation_revocations').fetchone()[0])
                c.execute('UPDATE federation_revocations SET created=? WHERE peer_id=? AND actor_id=? AND device_id=? AND target_actor_id=?',
                          (priority,*key))
    return bool(rows)
