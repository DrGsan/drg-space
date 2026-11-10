"""Application namespaces within the existing encrypted object store."""
from collections import defaultdict
from app.chat_storage import attachment_room


def note_attachment_ids(c, uid=None):
    query = 'SELECT id,parent_id,kind,mime FROM files'
    rows = c.execute(query+' WHERE user_id=?', (uid,)) if uid else c.execute(query)
    children, roots = defaultdict(list), []
    for row in rows:
        children[row['parent_id']].append(row['id'])
        if row['kind'] == 'folder' and row['mime'].startswith('application/x-drg-note;space='):
            roots.append(row['id'])
    hidden = set()
    while roots:
        identity = roots.pop()
        if identity not in hidden:
            hidden.add(identity)
            roots.extend(children[identity])
    return hidden


def is_note_attachment(c, row):
    seen = set()
    while row and row['id'] not in seen:
        seen.add(row['id'])
        if row['kind'] == 'folder' and row['mime'].startswith('application/x-drg-note;space='):
            return True
        row = c.execute('SELECT id,parent_id,kind,mime FROM files WHERE id=?', (row['parent_id'],)).fetchone() if row['parent_id'] else None
    return False


def file_module_rows(c, rows, uid=None):
    rows = list(rows)
    lock_tracker_file_projects(c, rows)
    hidden = note_attachment_ids(c, uid)
    hidden.update(tracker_file_ids(c,uid))
    return [row for row in rows if row['id'] not in hidden and not attachment_room(row['id'])]


def tracker_file_ids(c, uid=None):
    """Tracker owns both explicit links and legacy service-folder descendants.

    Explicit links remain authoritative if an older Files client moved a file.
    Do not use SQL predicates on encrypted MIME metadata.
    """
    query = 'SELECT id,parent_id,kind,mime FROM files'
    rows = [dict(row) for row in (c.execute(query+' WHERE user_id=?',(uid,)) if uid else c.execute(query))]
    available = {row['id'] for row in rows}
    ids = {row['id'] for row in rows if row['kind']=='folder' and row['mime'].startswith('application/x-drg-tracker;project=')}
    ids.update(row[0] for row in c.execute('SELECT id FROM tracker_attachments') if row[0] in available)
    children = defaultdict(list)
    for row in rows:
        children[row['parent_id']].append(row['id'])
    pending = list(ids)
    while pending:
        for child in children[pending.pop()]:
            if child not in ids:
                ids.add(child);pending.append(child)
    return ids


def is_tracker_file(c, row):
    lock_tracker_file_projects(c,[row])
    if c.execute('SELECT 1 FROM tracker_attachments WHERE id=?',(row['id'],)).fetchone():
        return True
    seen=set()
    while row and row['id'] not in seen:
        seen.add(row['id'])
        if row['kind']=='folder' and row['mime'].startswith('application/x-drg-tracker;project='):
            return True
        row=c.execute('SELECT id,parent_id,kind,mime FROM files WHERE id=?',(row['parent_id'],)).fetchone() if row['parent_id'] else None
    return False


def lock_tracker_file_projects(c, selected):
    """Serialize generic file operations with project deletion on PostgreSQL.

    SQLite mutations already hold BEGIN IMMEDIATE. A shared project-row lock
    keeps an ordinary Files operation from passing its access check immediately
    before the project's deletion marker is committed by another worker.
    """
    if not selected or not hasattr(c,'backend') or c.backend.kind != 'postgres':
        return
    rows = {row['id']:dict(row) for row in c.execute('SELECT id,parent_id,kind,mime FROM files')}
    ids = {row['id'] for row in selected}
    projects = set()
    for identity in ids:
        seen = set()
        row = rows.get(identity)
        while row and row['id'] not in seen:
            seen.add(row['id'])
            if row['kind']=='folder' and row['mime'].startswith('application/x-drg-tracker;project='):
                projects.add(row['mime'].split('=',1)[1])
            row = rows.get(row['parent_id'])
    identities = sorted(ids)
    for start in range(0,len(identities),256):
        batch = identities[start:start+256]
        projects.update(row[0] for row in c.execute('SELECT i.project_id FROM tracker_attachments a JOIN tracker_issues i ON i.id=a.issue_id WHERE a.id IN ('+','.join('?' for _ in batch)+')',tuple(batch)))
    for pid in sorted(projects):
        c.raw.execute('SELECT id FROM tracker_projects WHERE id=%s FOR SHARE',(pid,))
