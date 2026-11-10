"""Pending reminder invitations in the existing encrypted service store."""
import json
import secrets

PREFIX = 'task-invitation:'
LIFETIME = 7 * 86400


def key(list_id, invitation_id):
    return PREFIX + list_id + ':' + invitation_id


def pattern(list_id):
    return PREFIX + list_id.replace('_', '!_') + ':%'


def pending(c, list_id, stamp):
    from app.modules.tasks import _friend
    result = []
    owner = c.execute('SELECT owner_id FROM task_lists WHERE id=?', (list_id,)).fetchone()
    if not owner:
        return result
    for row in c.execute("SELECT key,value FROM app_settings WHERE key LIKE ? ESCAPE '!'", (pattern(list_id),)):
        item = json.loads(row['value'])
        if item['list_id'] == list_id and item['expires'] > stamp and item['sender_id'] == owner[0] and _friend(c, owner[0], item['recipient_id']):
            result.append(item)
    return result


def get(c, list_id, invitation_id, stamp):
    return next((item for item in pending(c, list_id, stamp) if item['id'] == invitation_id), None)


def create(c, row, recipient, stamp):
    from app.notifications import notify
    from app.modules.tasks import _actor
    item = {'id': secrets.token_hex(16), 'list_id': row['id'], 'sender_id': row['owner_id'],
            'recipient_id': recipient, 'created': stamp, 'expires': stamp + LIFETIME}
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',
              (key(row['id'], item['id']), json.dumps(item)))
    notify(c, recipient, 'task_invitation', {'list_id': row['id'], 'list_name': json.loads(row['payload'])['name'],
           'invitation_id': item['id'], 'actor_id': row['owner_id'], 'actor': _actor(c, row['owner_id'], stamp)['login']}, stamp)
    return item


def purge(c, stamp):
    from app.modules.tasks import _friend
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX + '%',)).fetchall():
        item = json.loads(row['value'])
        owner = c.execute('SELECT owner_id FROM task_lists WHERE id=?', (item['list_id'],)).fetchone()
        if item['expires'] <= stamp or not owner or owner[0] != item['sender_id'] or not _friend(c, owner[0], item['recipient_id']):
            c.execute('DELETE FROM app_settings WHERE key=?', (row['key'],))
