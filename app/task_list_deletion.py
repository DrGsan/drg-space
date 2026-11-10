"""Recoverable list deletion in the existing encrypted service store."""
import json
import re

PREFIX = 'task-list-deletion:'
LIFETIME = 86400


def validate_archive(c, fail):
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX + '%',)):
        lid = row['key'][len(PREFIX):]
        value = json.loads(row['value'])
        if not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', lid) or not isinstance(value, dict) or set(value) != {'created', 'delete_at'}:
            fail()
        if any(type(value[key]) is not int or not 0 <= value[key] <= 32503680000 for key in value) or value['delete_at'] - value['created'] != LIFETIME:
            fail()
        listing = c.execute('SELECT is_default FROM task_lists WHERE id=?', (lid,)).fetchone()
        if listing and listing[0]:
            fail()


def pending(c, lid):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (PREFIX + lid,)).fetchone()
    return json.loads(row[0]) if row else None


def schedule(c, row, stamp):
    current = pending(c, row['id'])
    if current:
        return current
    value = {'created': stamp, 'delete_at': stamp + LIFETIME}
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', (PREFIX + row['id'], json.dumps(value)))
    c.execute('UPDATE task_lists SET updated=?,version=version+1 WHERE id=?', (stamp, row['id']))
    return value


def restore(c, lid, stamp):
    value = pending(c, lid)
    if value:
        # Completed reminders get their remaining retention period back.
        c.execute('UPDATE task_details SET completed_at=completed_at+?,version=version+1 WHERE list_id=? AND completed_at IS NOT NULL',
                  (max(0, stamp - value['created']), lid))
        c.execute('DELETE FROM app_settings WHERE key=?', (PREFIX + lid,))
        c.execute('UPDATE task_lists SET updated=?,version=version+1 WHERE id=?', (stamp, lid))


def purge(c, stamp):
    from app.task_invitations import pattern
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX + '%',)).fetchall():
        lid = row['key'][len(PREFIX):]
        exists = c.execute('SELECT is_default FROM task_lists WHERE id=?', (lid,)).fetchone()
        if exists and not exists[0] and json.loads(row['value'])['delete_at'] <= stamp:
            c.execute('DELETE FROM tasks WHERE id IN (SELECT task_id FROM task_details WHERE list_id=?)', (lid,))
            c.execute('DELETE FROM task_lists WHERE id=?', (lid,))
            from app.task_order import PREFIX as ORDER_PREFIX
            c.execute('DELETE FROM app_settings WHERE key=?',(ORDER_PREFIX+lid,))
            c.execute("DELETE FROM app_settings WHERE key LIKE ? ESCAPE '!'", (pattern(lid),))
            exists = None
        if not exists or exists[0]:
            c.execute('DELETE FROM app_settings WHERE key=?', (row['key'],))
