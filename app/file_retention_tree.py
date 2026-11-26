"""Owner-scoped Drive tree and the exact schedules shown before cancellation."""
from collections import defaultdict
import hashlib
import json

from app.file_scopes import file_module_rows
from app.file_sharing import fail, schedule_table


def read_tree(c, uid, root, *, preview=False):
    # Keeping each visited parent on the left lets SQLite use both parts of
    # file_owner_idx(user_id, parent_id) instead of rescanning the owner's Drive.
    rows = c.execute(
        'WITH RECURSIVE branch(id) AS ('
        'SELECT id FROM files WHERE id=? AND user_id=? AND deleted IS NULL '
        'UNION SELECT f.id FROM branch b CROSS JOIN files f '
        'WHERE f.parent_id=b.id AND f.user_id=? AND f.deleted IS NULL) '
        'SELECT f.id,f.parent_id,f.kind,f.name,f.mime,'
        'COALESCE(r.version,s.version) AS schedule_version,'
        'COALESCE(r.not_before,s.not_before) AS not_before,'
        'COALESCE(r.due_at,s.due_at) AS due_at '
        'FROM branch b CROSS JOIN files f '
        'LEFT JOIN file_retention r ON f.id=r.file_id '
        'LEFT JOIN file_folder_retention s ON f.id=s.file_id WHERE f.id=b.id ORDER BY f.id',
        (root, uid, uid),
    )
    by_id = {row['id']: row for row in file_module_rows(c, rows, subtree_root=root)}
    if root not in by_id:
        return None

    children = defaultdict(list)
    for row in by_id.values():
        children[row['parent_id']].append(row['id'])

    nodes, schedules, signature = [], [], []
    pending = [(root, 0)]
    ancestry, names, visited = [], [], set()
    while pending:
        fid, depth = pending.pop()
        if fid in visited:
            continue
        visited.add(fid)
        row = by_id[fid]
        node = {'id': fid, 'kind': row['kind'], 'name': row['name']}
        nodes.append({**node, 'parent_id': None if fid == root else row['parent_id']})
        if preview:
            # Materialize full paths only when they will be shown in the dialog.
            ancestry[depth:] = [fid]
            names[depth:] = [row['name']]
        if row['schedule_version'] is not None:
            item = {**node, 'version': row['schedule_version'], 'delete_at': row['due_at']}
            if preview:
                item['path'] = '/'.join(names)
                signature.append((item, tuple(ancestry), row['not_before']))
            schedules.append(item)
        pending.extend((child, depth + 1) for child in reversed(children[fid]))

    if preview:
        payload = json.dumps([root, by_id[root]['parent_id'], signature], separators=(',', ':'))
        return {'files': schedules, 'snapshot': hashlib.sha256(payload.encode()).hexdigest()}
    return {'nodes': nodes, 'files': sorted(schedules, key=lambda item: item['id'])}


def cancel_schedules(c, files):
    # The owner lock covers the recheck and these bounded DELETEs. Never sweep
    # descendants or recompute dates of timers outside the confirmed selection.
    for kind in ('file', 'folder'):
        ids = [entry['id'] for entry in files if entry['kind'] == kind]
        for offset in range(0, len(ids), 256):
            batch = ids[offset:offset + 256]
            placeholders = ','.join('?' for _ in batch)
            changed = c.execute(
                'DELETE FROM ' + schedule_table(kind) + ' WHERE file_id IN (' + placeholders + ')',
                tuple(batch),
            )
            if changed.rowcount != len(batch):
                fail('fileRetentionChanged')
