"""Seven-day recovery of an explicitly deleted issue branch.

Markers use the existing encrypted service metadata. Each member has an indexed
marker so old clients and every normal issue endpoint see the same live set.
Independent child deletions retain their own clocks; enclosing deletions pause
them rather than restoring or renewing them accidentally.
"""
import json
import re

PREFIX = 'tracker-issue-deletion:'
LIFETIME = 7 * 86400


def visible(alias=''):
    column = (alias + '.' if alias else '') + 'id'
    return f"NOT EXISTS (SELECT 1 FROM app_settings deletion WHERE deletion.key='{PREFIX}'||{column})"


def pending(c, iid):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (PREFIX + iid,)).fetchone()
    return json.loads(row[0]) if row else None


def records(c, pid):
    return {r['id']: json.loads(r['value']) for r in c.execute(
        "SELECT i.id,d.value FROM tracker_issues i JOIN app_settings d ON d.key=?||i.id WHERE i.project_id=?", (PREFIX, pid))}


def tree(c, pid, iid):
    return {r[0] for r in c.execute('WITH RECURSIVE branch(id) AS '
        '(SELECT id FROM tracker_issues WHERE id=? AND project_id=? UNION '
        'SELECT i.id FROM tracker_issues i JOIN branch b ON i.parent_id=b.id WHERE i.project_id=?) '
        'SELECT id FROM branch', (iid, pid, pid))}


def _parents(c, pid):
    return {r['id']: r['parent_id'] for r in c.execute('SELECT id,parent_id FROM tracker_issues WHERE project_id=?', (pid,))}


def blocked(root, markers, parents):
    seen = {root}; parent = parents.get(root)
    while parent and parent not in seen:
        if parent in markers and markers[parent]['root_id'] != root:
            return True
        seen.add(parent); parent = parents.get(parent)
    return False


def _write(c, iid, value):
    from app.tracker import dump
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (PREFIX + iid, dump(value)))


def enclosing_paused(c, pid):
    from app.tracker_lifecycle import pending as project_pending
    from app.workspace_lifecycle import pending as space_pending
    if project_pending(c, pid):
        return True
    project = c.execute('SELECT space_id FROM tracker_projects WHERE id=?', (pid,)).fetchone()
    return bool(project and project[0] and space_pending(c, project[0]))


def refresh_workspace(c, sid, stamp):
    from app import tracker
    for project in c.execute('SELECT id,owner_id FROM tracker_projects WHERE space_id=? ORDER BY owner_id,id', (sid,)).fetchall():
        if records(c, project['id']):
            tracker.lock_owner(c, project['owner_id']); tracker.lock_project(c, project['id'])
            refresh_clocks(c, project['id'], stamp)


def refresh_clocks(c, pid, stamp):
    """Called under the project lock after changing enclosing recovery state."""
    markers = records(c, pid)
    if not markers:
        return
    parents = _parents(c, pid); enclosing_project = enclosing_paused(c, pid)
    groups = {}
    for iid, item in markers.items():
        groups.setdefault(item['root_id'], []).append(iid)
    for root, identities in groups.items():
        value = markers.get(root)
        if not value:
            continue
        paused = enclosing_project or blocked(root, markers, parents)
        updated = dict(value)
        if paused and value['paused_at'] is None:
            updated['paused_at'] = stamp
        elif not paused and value['paused_at'] is not None:
            elapsed = max(0, stamp - value['paused_at'])
            updated.update(paused_at=None, paused_seconds=value['paused_seconds'] + elapsed, delete_at=value['delete_at'] + elapsed)
        if updated != value:
            for iid in identities:
                _write(c, iid, updated)


def plan(c, row, project):
    ids = tree(c, project['id'], row['id']); markers = records(c, project['id'])
    return {'count': len(ids), 'already_deleted': len(ids & markers.keys()), 'project_version': project['version']}


def _locked(c, iid, uid, peer_id):
    from app import tracker
    row = tracker.issue_row(c, iid, include_deleted=True)
    first = tracker.access(c, row['project_id'], uid, peer_id, required='editor')
    tracker.lock_owner(c, first['owner_id']); tracker.lock_project(c, first['id'])
    project = tracker.access(c, first['id'], uid, peer_id, required='editor')
    if project['owner_id'] != first['owner_id']:
        tracker.fail(409, 'trackerConflict')
    return tracker.issue_row(c, iid, include_deleted=True), project


def _bump(c, project, stamp):
    c.quota_transfer_owners = getattr(c, 'quota_transfer_owners', set()) | {project['owner_id']}
    c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?', (stamp, project['id']))


def schedule(c, iid, uid, version, project_version, confirmation, stamp, peer_id=''):
    from app import tracker
    row, project = _locked(c, iid, uid, peer_id)
    old = pending(c, iid)
    if old:
        if old['root_id'] != iid:
            tracker.fail(409, 'trackerIssueRestoreParentFirst')
        return old
    if row['version'] != version or project['version'] != project_version:
        tracker.fail(409, 'trackerConflict')
    if confirmation != json.loads(project['payload'])['key'] + '-' + str(row['number']):
        tracker.fail(422, 'trackerIssueDeleteConfirmation')
    ids = tree(c, project['id'], iid)
    value = {'root_id': iid, 'project_id': project['id'], 'created': stamp,
             'delete_at': stamp + LIFETIME, 'paused_at': None, 'paused_seconds': 0}
    existing = records(c, project['id'])
    _bump(c, project, stamp)
    for identity in ids - existing.keys():
        _write(c, identity, value)
        c.execute('UPDATE tracker_issues SET updated=?,version=version+1 WHERE id=?', (stamp, identity))
    # In-flight local uploads reauthorize before commit; remote chunks lose
    # their upload row immediately, including chunks already staged in SQL.
    for identity in ids:
        c.execute('DELETE FROM tracker_uploads WHERE issue_id=?', (identity,))
    refresh_clocks(c, project['id'], stamp)
    tracker.history(c, project['id'], iid, uid, peer_id, 'issue_deletion_scheduled', {'count': len(ids), 'delete_at': value['delete_at']}, stamp)
    return value


def restore(c, iid, uid, version, project_version, stamp, peer_id=''):
    from app import tracker
    row, project = _locked(c, iid, uid, peer_id)
    value = pending(c, iid)
    if not value:
        return tracker.serialize_issue(c, row, project, stamp, detail=True, delete_scope=True)
    if value['root_id'] != iid or blocked(iid, records(c, project['id']), _parents(c, project['id'])):
        tracker.fail(409, 'trackerIssueRestoreParentFirst')
    if value['paused_at'] is not None or stamp >= value['delete_at']:
        tracker.fail(409, 'trackerIssueRecoveryExpired')
    if row['version'] != version or project['version'] != project_version:
        tracker.fail(409, 'trackerConflict')
    _bump(c, project, stamp)
    for identity, item in records(c, project['id']).items():
        if item['root_id'] == iid:
            c.execute('DELETE FROM app_settings WHERE key=?', (PREFIX + identity,))
            c.execute('UPDATE tracker_issues SET updated=?,version=version+1 WHERE id=?', (stamp, identity))
    refresh_clocks(c, project['id'], stamp)
    tracker.history(c, project['id'], iid, uid, peer_id, 'issue_restored', {}, stamp)
    return tracker.serialize_issue(c, tracker.issue_row(c, iid), tracker.project_row(c, project['id']), stamp, detail=True, delete_scope=True)


def deleted_issues(c, pid, uid, stamp, peer_id=''):
    from app import tracker
    project = tracker.access(c, pid, uid, peer_id, required='editor')
    markers = records(c, pid); parents = _parents(c, pid); result = []
    for iid, value in markers.items():
        if value['root_id'] != iid or blocked(iid, markers, parents):
            continue
        row = tracker.issue_row(c, iid, include_deleted=True)
        result.append({'id': iid, 'key': json.loads(project['payload'])['key'] + '-' + str(row['number']),
                       'title': json.loads(row['payload'])['title'], 'version': row['version'],
                       'count': len(tree(c, pid, iid)), 'deletion': value})
    return {'items': sorted(result, key=lambda r: (r['deletion']['created'], r['id']), reverse=True), 'project_version': project['version']}


def clear_project(c, pid):
    for iid in records(c, pid):
        c.execute('DELETE FROM app_settings WHERE key=?', (PREFIX + iid,))


def _purge_branch(c, project, iid, stamp):
    from app import tracker
    from app.tracker_lifecycle import associated_files
    from app.file_trash import purge_file_rows
    ids = tree(c, project['id'], iid)
    purge_file_rows(c, associated_files(c, project['id'], issue_ids=ids), stamp, include_pending_tracker=True)
    _bump(c, project, stamp); c.quota_recount.add(project['owner_id'])
    for identity in ids:
        c.execute('DELETE FROM app_settings WHERE key=?', (PREFIX + identity,))
    # parent_id is SET NULL, so explicitly remove every member of this branch.
    for identity in ids:
        c.execute('DELETE FROM tracker_issues WHERE id=? AND project_id=?', (identity, project['id']))
    for notice in c.execute("SELECT id,payload FROM notifications WHERE kind='tracker'").fetchall():
        payload = json.loads(notice['payload'])
        if payload.get('project_id') == project['id'] and payload.get('issue_id') in ids:
            c.execute('DELETE FROM notifications WHERE id=?', (notice['id'],))


def purge(c, stamp, project_id=None):
    from app import tracker
    # Reap markers whose row was removed by an enclosing cascade or restore.
    if not c.execute('SELECT 1 FROM app_settings WHERE key LIKE ? LIMIT 1', (PREFIX + '%',)).fetchone():
        return
    c.execute('DELETE FROM app_settings WHERE key LIKE ? AND NOT EXISTS '
              '(SELECT 1 FROM tracker_issues i WHERE i.id=substr(app_settings.key,?))', (PREFIX + '%', len(PREFIX) + 1))
    query = 'SELECT DISTINCT p.id,p.owner_id FROM tracker_projects p JOIN tracker_issues i ON i.project_id=p.id JOIN app_settings d ON d.key=?||i.id'
    projects = c.execute(query + (' WHERE p.id=?' if project_id else '') + ' ORDER BY p.owner_id,p.id', (PREFIX, project_id) if project_id else (PREFIX,)).fetchall()
    for first in projects:
        tracker.lock_owner(c, first['owner_id']); tracker.lock_project(c, first['id'])
        project = tracker.project_row(c, first['id'])
        if project['owner_id'] != first['owner_id'] or enclosing_paused(c, first['id']):
            continue
        refresh_clocks(c, project['id'], stamp)
        markers = records(c, project['id']); parents = _parents(c, project['id'])
        for iid, item in markers.items():
            if item['root_id'] == iid and item['paused_at'] is None and item['delete_at'] <= stamp and not blocked(iid, markers, parents) and pending(c, iid):
                _purge_branch(c, project, iid, stamp)


def validate_archive(c, invalid):
    try:
        markers = {}
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX + '%',)):
            iid = row['key'][len(PREFIX):]; value = json.loads(row['value'])
            if not re.fullmatch(r'[A-Za-z0-9_-]{16}', iid) or not isinstance(value, dict) or set(value) != {'root_id','project_id','created','delete_at','paused_at','paused_seconds'}:
                raise ValueError()
            for key in ('root_id','project_id'):
                if not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{16}', value[key]): raise ValueError()
            if any(type(value[k]) is not int or not 0 <= value[k] <= 32503680000 for k in ('created','delete_at','paused_seconds')): raise ValueError()
            if value['delete_at'] - value['created'] != LIFETIME + value['paused_seconds']: raise ValueError()
            if value['paused_at'] is not None and (type(value['paused_at']) is not int or not value['created'] <= value['paused_at'] <= 32503680000): raise ValueError()
            issue = c.execute('SELECT project_id FROM tracker_issues WHERE id=?', (iid,)).fetchone()
            if not issue or issue[0] != value['project_id']: raise ValueError()
            markers[iid] = value
        by_project = {}; children = {}; enclosing = {}
        for iid, value in markers.items():
            root = markers.get(value['root_id'])
            if root != value: raise ValueError()
            pid = value['project_id']
            if pid not in by_project:
                by_project[pid] = _parents(c, pid)
                enclosing[pid] = enclosing_paused(c, pid)
                for child, parent in by_project[pid].items():
                    children.setdefault(parent, set()).add(child)
            parents = by_project[pid]; current = iid; seen = set()
            while current != value['root_id']:
                if not current or current in seen or current not in markers: raise ValueError()
                seen.add(current); current = parents.get(current)
            if any(child not in markers for child in children.get(iid, ())): raise ValueError()
            should_pause = enclosing[pid] or blocked(value['root_id'], markers, parents)
            if (value['paused_at'] is not None) != should_pause: raise ValueError()
    except (ValueError, TypeError, KeyError):
        invalid()
