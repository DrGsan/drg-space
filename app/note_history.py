"""Content checkpoints share a rolling retention window on every write path."""
import json
import secrets

HISTORY_DAYS = 90
HISTORY_SECONDS = HISTORY_DAYS * 86400
APPEARANCE_PREFIX = 'note-history:appearance:'


def purge_history(c, stamp, note_id=None):
    query = 'DELETE FROM note_revisions WHERE created<=?'
    params = (stamp-HISTORY_SECONDS,)
    if note_id is not None:
        query += ' AND note_id=?'
        params += (note_id,)
    c.execute(query, params)
    if note_id is None:
        c.execute('DELETE FROM app_settings WHERE key LIKE ? AND NOT EXISTS '
                  '(SELECT 1 FROM note_revisions WHERE app_settings.key=?||note_revisions.id)',
                  (APPEARANCE_PREFIX + '%', APPEARANCE_PREFIX))


def revision_appearance(c, revision_id):
    """Read the optional decorations saved with an assistant checkpoint."""
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (APPEARANCE_PREFIX + revision_id,)).fetchone()
    if row:
        data = json.loads(row[0])
        return {field: data[field] for field in ('icon', 'cover')}
    return {}


def snapshot(c, note, stamp, interval=0, *, with_appearance=False):
    purge_history(c, stamp, note['id'])
    last = c.execute('SELECT * FROM note_revisions WHERE note_id=? ORDER BY rowid DESC LIMIT 1', (note['id'],)).fetchone()
    if last:
        if interval and last['created'] > stamp-interval:
            return
        if (all(last[key] == note[key] for key in ('title', 'body', 'blocks'))
                and (not with_appearance or revision_appearance(c, last['id']) == {key: note[key] for key in ('icon', 'cover')})):
            return
    rid = secrets.token_urlsafe(12)
    c.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)', (rid, note['id'], note['title'], note['body'], note['blocks'], stamp))
    if with_appearance:
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', (APPEARANCE_PREFIX + rid, json.dumps({key: note[key] for key in ('icon', 'cover')}, ensure_ascii=False)))
