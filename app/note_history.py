"""Content checkpoints share a rolling retention window on every write path."""
import secrets

HISTORY_DAYS = 90
HISTORY_SECONDS = HISTORY_DAYS * 86400


def purge_history(c, stamp, note_id=None):
    query = 'DELETE FROM note_revisions WHERE created<=?'
    params = (stamp-HISTORY_SECONDS,)
    if note_id is not None:
        query += ' AND note_id=?'
        params += (note_id,)
    c.execute(query, params)


def snapshot(c, note, stamp, interval=0):
    purge_history(c, stamp, note['id'])
    last = c.execute('SELECT * FROM note_revisions WHERE note_id=? ORDER BY rowid DESC LIMIT 1', (note['id'],)).fetchone()
    if last:
        if interval and last['created'] > stamp-interval:
            return
        if all(last[key] == note[key] for key in ('title', 'body', 'blocks')):
            return
    c.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)', (secrets.token_urlsafe(12), note['id'], note['title'], note['body'], note['blocks'], stamp))
