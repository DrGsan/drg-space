"""Thirty-day project recovery in existing encrypted service metadata."""
import json
import re

PREFIX = 'tracker-project-deletion:'
LIFETIME = 30 * 86400


def pending(c, pid):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (PREFIX + pid,)).fetchone()
    return json.loads(row[0]) if row else None


def owned(c, pid, uid):
    from app import tracker
    row = tracker.project_row(c, pid)
    if row['owner_id'] != uid:
        tracker.access(c, pid, uid)
        tracker.fail(403, 'trackerReadOnly')
    from app.account_lifecycle import pending as deleting_account
    from app.workspace_lifecycle import pending as deleting_space
    if deleting_account(c, uid) or row['space_id'] and deleting_space(c, row['space_id']):
        tracker.fail(409, 'trackerProjectRestoreBlocked')
    return row


def locked_owner(c, pid, uid):
    from app import tracker
    first = owned(c, pid, uid)
    tracker.lock_owner(c, first['owner_id'])
    tracker.lock_project(c, pid)
    return owned(c, pid, uid)


def schedule(c, pid, uid, version, confirmation, stamp):
    from app import tracker
    row = locked_owner(c, pid, uid)
    current = pending(c, pid)
    if current:
        return current
    if row['version'] != version:
        tracker.fail(409, 'trackerConflict')
    if confirmation != json.loads(row['payload'])['name']:
        tracker.fail(422, 'trackerDeleteConfirmation')
    value = {'created': stamp, 'delete_at': stamp + LIFETIME}
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', (PREFIX + pid, tracker.dump(value)))
    c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?', (stamp, pid))
    # Preserve states while invalidating in-flight grants and uploads.
    c.execute('UPDATE tracker_members SET revision=revision+1 WHERE project_id=?', (pid,))
    c.execute('DELETE FROM tracker_uploads WHERE issue_id IN (SELECT id FROM tracker_issues WHERE project_id=?)', (pid,))
    # Recovering/removing existing data must remain possible at full quota.
    # Keep the real ledger charge for the small lifecycle history entries.
    c.quota_transfer_owners = getattr(c, 'quota_transfer_owners', set()) | {uid}
    tracker.history(c, pid, None, uid, '', 'project_deletion_scheduled', value, stamp)
    return value


def restore(c, pid, uid, version, stamp):
    from app import tracker
    row = locked_owner(c, pid, uid)
    value = pending(c, pid)
    if not value:
        return tracker.serialize_project(c, row, uid, stamp)
    if stamp >= value['delete_at']:
        tracker.fail(409, 'trackerProjectRecoveryExpired')
    if row['version'] != version:
        tracker.fail(409, 'trackerConflict')
    paused = max(0, stamp - value['created'])
    for fid in associated_files(c, pid):
        c.execute('UPDATE files SET deleted=deleted+?,updated=?,version=version+1 WHERE id=? AND deleted IS NOT NULL', (paused, stamp, fid))
    c.execute('DELETE FROM app_settings WHERE key=?', (PREFIX + pid,))
    c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?', (stamp, pid))
    c.execute('UPDATE tracker_members SET revision=revision+1 WHERE project_id=?', (pid,))
    c.quota_transfer_owners = getattr(c, 'quota_transfer_owners', set()) | {uid}
    tracker.history(c, pid, None, uid, '', 'project_restored', {}, stamp)
    return tracker.serialize_project(c, tracker.project_row(c, pid), uid, stamp)


def deleted_projects(c, uid):
    from app.account_lifecycle import pending as deleting_account
    if deleting_account(c, uid):
        return []
    result = []
    for row in c.execute('SELECT * FROM tracker_projects WHERE owner_id=? ORDER BY updated DESC,id', (uid,)):
        value = pending(c, row['id'])
        if not value:
            continue
        payload = json.loads(row['payload'])
        result.append({key: row[key] for key in ('id', 'owner_id', 'space_id', 'version', 'created', 'updated')} |
                      {'name': payload['name'], 'key': payload['key'], 'role': 'owner', 'deletion': value})
    return result


def associated_files(c, pid):
    project = c.execute('SELECT owner_id FROM tracker_projects WHERE id=?',(pid,)).fetchone()
    if not project:
        return set()
    rows = [dict(r) for r in c.execute('SELECT id,user_id,parent_id,kind,mime FROM files')]
    ids = {r['id'] for r in rows if r['user_id']==project['owner_id'] and r['kind'] == 'folder' and r['mime'] == 'application/x-drg-tracker;project=' + pid}
    linked = list(c.execute('SELECT a.id,i.project_id FROM tracker_attachments a JOIN tracker_issues i ON i.id=a.issue_id'))
    ids.update(r['id'] for r in linked if r['project_id'] == pid)
    foreign = {r['id'] for r in linked if r['project_id'] != pid}
    foreign.update(r['id'] for r in rows if r['user_id']!=project['owner_id'])
    foreign.update(r['id'] for r in rows if r['kind']=='folder' and r['mime'].startswith('application/x-drg-tracker;project=') and r['mime']!='application/x-drg-tracker;project='+pid)
    ids.difference_update(foreign)
    while True:
        children = {r['id'] for r in rows if r['parent_id'] in ids and r['id'] not in foreign}
        if children <= ids:
            return ids
        ids.update(children)


def purge(c, stamp):
    from app import tracker
    from app.file_trash import purge_file_rows
    queued = []
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX + '%',)).fetchall():
        project = c.execute('SELECT owner_id FROM tracker_projects WHERE id=?', (row['key'][len(PREFIX):],)).fetchone()
        queued.append((project['owner_id'] if project else '', row['key'], dict(row)))
    for owner_id, _, row in sorted(queued, key=lambda item:item[:2]):
        pid = row['key'][len(PREFIX):]
        project = c.execute('SELECT owner_id FROM tracker_projects WHERE id=?', (pid,)).fetchone()
        if project and project['owner_id'] != owner_id:
            continue
        if project and json.loads(row['value'])['delete_at'] <= stamp:
            tracker.lock_owner(c, owner_id)
            tracker.lock_project(c, pid)
            value = pending(c, pid)
            # Recheck after the lock: another request may have restored it.
            if not value or value['delete_at'] > stamp:
                continue
            project = c.execute('SELECT owner_id FROM tracker_projects WHERE id=?', (pid,)).fetchone()
            if project and project['owner_id'] != owner_id:
                # Ownership changed while waiting. Retry with fresh owner locks.
                continue
            if project:
                purge_file_rows(c, associated_files(c, pid), stamp, include_pending_tracker=True)
                c.quota_recount.add(project['owner_id'])
                c.execute('DELETE FROM tracker_projects WHERE id=?', (pid,))
                for notice in c.execute("SELECT id,payload FROM notifications WHERE kind IN ('tracker','tracker_invitation')").fetchall():
                    if json.loads(notice['payload']).get('project_id') == pid:
                        c.execute('DELETE FROM notifications WHERE id=?', (notice['id'],))
            project = None
        if not project:
            c.execute('DELETE FROM app_settings WHERE key=?', (row['key'],))


def validate_archive(c, invalid):
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX + '%',)):
        pid = row['key'][len(PREFIX):]
        value = json.loads(row['value'])
        if not re.fullmatch(r'[A-Za-z0-9_-]{16}', pid) or not isinstance(value, dict) or set(value) != {'created', 'delete_at'}:
            invalid()
        if any(type(value[k]) is not int or not 0 <= value[k] <= 32503680000 for k in value) or value['delete_at'] - value['created'] != LIFETIME:
            invalid()
        if not c.execute('SELECT 1 FROM tracker_projects WHERE id=?', (pid,)).fetchone():
            invalid()
