"""Independent registration links and deferred, consented creator friendship.

The legacy invites table remains authoritative for one-use registration. Extra
metadata lives in encrypted app_settings; old hash-only links keep working.
"""
import hashlib
import json
import re
from app.i18n import message, text

PREFIX = 'registration-invite:'
PENDING = 'registration-friend:'
SECONDS = 7 * 86400


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def read(c, key):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def write(c, key, value):
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))


def remember(c, token, creator, login, add_friend, stamp):
    value = dict(version=1, token=token, creator=creator, login=login, created=stamp,
                 expires=stamp+SECONDS, add_creator_as_friend=add_friend,
                 status='active', used_at=None, revoked_at=None)
    write(c, PREFIX+digest(token), value)
    return value


def pending_for(c, identity):
    values=[]
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PENDING+'%',)):
        value=json.loads(row['value'])
        if value['invite']==identity:values.append((row['key'],value))
    return values


def serialize(c, identity, value, stamp, public_url):
    # Keep the additive rc.1 response shape for clients already installed.
    # Only active invitations are retained or returned now.
    return dict(id=identity, url=public_url+'/invite/'+value['token'],
                login=value['login'], created=value['created'], expires=value['expires'], status='active',
                add_creator_as_friend=value['add_creator_as_friend'], used_at=None,
                revoked_at=None, friendship_pending=False, can_revoke=True)


def own(c, uid, stamp, public_url):
    purge(c,stamp)
    values=[]
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX+'%',)):
        value=json.loads(row['value'])
        if value['creator']==uid:
            values.append(serialize(c,row['key'][len(PREFIX):],value,stamp,public_url))
    return sorted(values,key=lambda value:(value['created'],value['id']),reverse=True)


def describe(c, token, stamp, fail):
    identity = digest(token)
    row = c.execute('SELECT login,expires FROM invites WHERE token=? AND used=0 AND expires>?', (identity, stamp)).fetchone()
    if not row: fail(404, message('invitationCancelledOrExpired'))
    value = read(c, PREFIX+identity)
    friend = None
    if value and value['add_creator_as_friend']:
        person = c.execute('SELECT id,login,settings FROM users WHERE id=?', (value['creator'],)).fetchone()
        if person:
            friend = dict(id=person['id'], login=person['login'], display_name=json.loads(person['settings']).get('profile', {}).get('display_name', ''))
    return dict(expires=row['expires'], login=row['login'], add_creator_as_friend=bool(value and value['add_creator_as_friend']), friend=friend)


def expire(c, token, stamp):
    """Reconcile this link before rejecting it, outside signup's rollback."""
    identity=digest(token)
    c.execute('DELETE FROM invites WHERE token=? AND (used=1 OR expires<=?)',(identity,stamp))
    value=read(c,PREFIX+identity)
    if value and (value['status']!='active' or value['expires']<=stamp or not c.execute('SELECT 1 FROM invites WHERE token=?',(identity,)).fetchone()):
        c.execute('DELETE FROM invites WHERE token=?',(identity,))
        c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+identity,))


def consume(c, token, uid, stamp):
    identity=digest(token);value=read(c,PREFIX+identity)
    if value and value['add_creator_as_friend']:
        # This is one account's pending consent, never a reusable token or URL.
        write(c,PENDING+uid,dict(version=1,creator=value['creator'],invite=identity,created=stamp,revoked=False))
    c.execute('DELETE FROM invites WHERE token=?',(identity,))
    c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+identity,))


def revoke(c, identity, uid, stamp, fail):
    value=read(c,PREFIX+identity)
    pending=[(key,item) for key,item in pending_for(c,identity) if item['creator']==uid]
    if (value and value['creator']!=uid) or (not value and not pending):fail(404,message('invitationNotFound'))
    c.execute('DELETE FROM invites WHERE token=?',(identity,))
    c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+identity,))
    # Consume may have won the transaction race while the creator still had
    # the active row open. Cancel consent, without undoing the new account.
    for key,item in pending:
        item['revoked']=True;write(c,key,item)


def complete(c, uid, stamp, fail):
    value = read(c, PENDING+uid)
    if not value: return None
    creator = c.execute('SELECT id FROM users WHERE id=?', (value['creator'],)).fetchone()
    from app.account_lifecycle import pending as account_pending
    reason = 'revoked' if value['revoked'] else 'creator_unavailable' if not creator or account_pending(c, value['creator']) else None
    first, second = sorted((uid, value['creator']))
    existing = c.execute('SELECT space_id FROM friendships WHERE first_id=? AND second_id=?', (first, second)).fetchone()
    if not reason and not existing and any(c.execute('SELECT COUNT(*) FROM friendships WHERE first_id=? OR second_id=?', (person, person)).fetchone()[0] >= 200 for person in (uid, value['creator'])):
        reason = 'friend_limit'
    if reason:
        result = dict(status='unavailable', reason=reason, message=text({'revoked':'registrationFriendshipRevoked','creator_unavailable':'registrationFriendshipUnavailable','friend_limit':'registrationFriendshipLimit'}[reason]))
    else:
        from app.modules.social import ensure_friendship
        ensure_friendship(c, value['creator'], uid, stamp, fail)
        result = dict(status='already_friends' if existing else 'created')
    c.execute('DELETE FROM app_settings WHERE key=?', (PENDING+uid,))
    return result


def purge(c, stamp):
    for row in c.execute('SELECT key FROM app_settings WHERE key LIKE ?', (PENDING+'%',)).fetchall():
        if not c.execute('SELECT 1 FROM users WHERE id=?', (row['key'][len(PENDING):],)).fetchone():
            c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],))
    # Clean rc.1 history too, including after restoring an older backup. Pending
    # consent remains independent and does not need the consumed invitation.
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX+'%',)).fetchall():
        value=json.loads(row['value']);identity=row['key'][len(PREFIX):]
        active=c.execute('SELECT 1 FROM invites WHERE token=? AND used=0 AND expires>?',(identity,stamp)).fetchone()
        if value['status']!='active' or value['expires']<=stamp or not active:
            c.execute('DELETE FROM invites WHERE token=?',(identity,))
            c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],))


def validate_archive(c, fail):
    try:
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX+'%',)):
            value = json.loads(row['value']); identity = row['key'][len(PREFIX):]
            if set(value) != {'version','token','creator','login','created','expires','add_creator_as_friend','status','used_at','revoked_at'}: raise ValueError()
            if value['version'] != 1 or type(value['version']) is not int or not re.fullmatch(r'[A-Za-z0-9_-]{43}', value['token']) or digest(value['token']) != identity: raise ValueError()
            if not re.fullmatch(r'[a-f0-9]{32}', value['creator']) or type(value['add_creator_as_friend']) is not bool: raise ValueError()
            if type(value['created']) is not int or type(value['expires']) is not int or value['created'] < 0 or value['expires']-value['created'] != SECONDS: raise ValueError()
            if value['status'] not in ('active','used','revoked') or any(value[key] is not None and (type(value[key]) is not int or value[key] < value['created']) for key in ('used_at','revoked_at')): raise ValueError()
            if (value['status']=='active' and (value['used_at'] is not None or value['revoked_at'] is not None)) or (value['status']=='used' and (value['used_at'] is None or value['revoked_at'] is not None)) or (value['status']=='revoked' and value['revoked_at'] is None):raise ValueError()
            if value['login'] is not None and (not isinstance(value['login'], str) or not re.fullmatch(r'[a-z0-9_.-]{3,40}', value['login'])): raise ValueError()
            invite = c.execute('SELECT login,expires,used FROM invites WHERE token=?', (identity,)).fetchone()
            if invite and (invite['login'] != value['login'] or invite['expires'] != value['expires']): raise ValueError()
            if invite and (value['status']=='revoked' or bool(invite['used']) != (value['status']=='used')):raise ValueError()
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PENDING+'%',)):
            value = json.loads(row['value'])
            if set(value) != {'version','creator','invite','created','revoked'} or type(value['version']) is not int or value['version'] != 1: raise ValueError()
            if not re.fullmatch(r'[a-f0-9]{32}', value['creator']) or not re.fullmatch(r'[a-f0-9]{64}', value['invite']) or type(value['revoked']) is not bool or type(value['created']) is not int or value['created'] < 0: raise ValueError()
            target=row['key'][len(PENDING):]
            if target==value['creator'] or not c.execute('SELECT 1 FROM users WHERE id=?', (target,)).fetchone(): raise ValueError()
            invitation=read(c,PREFIX+value['invite'])
            if invitation and (not invitation['add_creator_as_friend'] or invitation['creator']!=value['creator'] or invitation['used_at']!=value['created'] or invitation['status']!=('revoked' if value['revoked'] else 'used')):raise ValueError()
    except (ValueError, KeyError, TypeError):
        fail(422, message('registrationInvitationsInvalidArchive'))
