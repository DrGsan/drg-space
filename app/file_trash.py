"""Seven-day file trash; object removal is retried by the existing blob queue."""
FILE_TRASH_SECONDS = 7 * 86400


def purge_file_rows(c, ids, stamp, *, include_pending_tracker=False):
    """Delete selected rows without cascading into another retained trash batch."""
    ids = set(ids)
    if ids and not include_pending_tracker:
        from app.file_scopes import tracker_file_ids,lock_tracker_file_projects
        lock_tracker_file_projects(c,[{'id':identity} for identity in ids])
        ids.difference_update(tracker_file_ids(c))
    if not ids:
        return
    rows = [dict(row) for row in c.execute('SELECT id,parent_id,object_id FROM files')]
    for row in rows:
        if row['parent_id'] in ids and row['id'] not in ids:
            c.execute('UPDATE files SET parent_id=NULL WHERE id=?', (row['id'],))
    for row in rows:
        if row['id'] in ids and row['object_id']:
            c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)', (row['object_id'], 'file', stamp))
    for fid in ids:
        c.execute('DELETE FROM files WHERE id=?', (fid,))


def purge_file_trash(c, stamp, uid=None):
    query = 'SELECT id FROM files WHERE deleted IS NOT NULL AND deleted<=?'
    params = (stamp - FILE_TRASH_SECONDS,)
    if uid is not None:
        query += ' AND user_id=?'
        params += (uid,)
    from app.file_scopes import note_attachment_ids
    hidden = note_attachment_ids(c, uid)
    purge_file_rows(c, (row['id'] for row in c.execute(query, params) if row['id'] not in hidden), stamp)
