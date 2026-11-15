"""Application namespaces within the existing encrypted object store."""
from collections import defaultdict
from app.chat_storage import attachment_room


NOTE_PREFIX = 'application/x-drg-note;space='
TRACKER_PREFIX = 'application/x-drg-tracker;project='


def ancestors(c, selected):
    """Read only selected objects and their ancestors, including legacy roots.

    UNION terminates malformed cycles. Do not filter ancestors by owner: a
    legacy linked attachment may have moved across an ordinary folder boundary.
    """
    identities = sorted({row['id'] for row in selected})
    rows = {}
    for start in range(0, len(identities), 256):
        batch = identities[start:start+256]
        query = ('WITH RECURSIVE ancestors(id) AS ('
                 'SELECT id FROM files WHERE id IN (' + ','.join('?' for _ in batch) + ') '
                 'UNION SELECT f.parent_id FROM files f JOIN ancestors a ON f.id=a.id WHERE f.parent_id IS NOT NULL) '
                 'SELECT f.id,f.parent_id,f.kind,f.mime FROM files f JOIN ancestors a ON f.id=a.id')
        rows.update((row['id'], dict(row)) for row in c.execute(query, tuple(batch)))
    return rows


def protected_ids(rows, prefixes, linked=()):
    """Classify shared ancestor paths once; keep broken cycles bounded."""
    known = dict.fromkeys(linked, True)
    for identity in rows:
        path, seen, current = [], set(), identity
        while current in rows and current not in known and current not in seen:
            seen.add(current)
            row = rows[current]
            if row['kind']=='folder' and row['mime'].startswith(prefixes):
                known[current] = True
                break
            path.append(current)
            current = row['parent_id']
        protected = known.get(current, False)
        for item in path:
            known[item] = protected
    return {identity for identity, protected in known.items() if protected}


def tracker_links(c, rows):
    result = {}
    identities = sorted(rows)
    for start in range(0, len(identities), 256):
        batch = identities[start:start+256]
        result.update((row['id'], row['project_id']) for row in c.execute(
            'SELECT a.id,i.project_id FROM tracker_attachments a JOIN tracker_issues i ON i.id=a.issue_id '
            'WHERE a.id IN (' + ','.join('?' for _ in batch) + ')', tuple(batch)))
    return result


def lock_projects(c, rows, links):
    if not hasattr(c, 'backend') or c.backend.kind!='postgres':
        return
    projects = set(links.values())
    projects.update(row['mime'].split('=', 1)[1] for row in rows.values()
                    if row['kind']=='folder' and row['mime'].startswith(TRACKER_PREFIX))
    for pid in sorted(projects):
        c.raw.execute('SELECT id FROM tracker_projects WHERE id=%s FOR SHARE', (pid,))


def note_attachment_ids(c, uid=None, *, selected=None):
    if selected is not None:
        selected = list(selected)
        hidden = protected_ids(ancestors(c, selected), (NOTE_PREFIX,))
        return {row['id'] for row in selected if row['id'] in hidden}
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
    if not rows:
        return []
    scoped = ancestors(c, rows)
    links = tracker_links(c, scoped)
    lock_projects(c, scoped, links)
    hidden = protected_ids(scoped, (NOTE_PREFIX, TRACKER_PREFIX), links)
    return [row for row in rows if row['id'] in scoped and row['id'] not in hidden and not attachment_room(row['id'])]


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
    scoped = ancestors(c, [row])
    links = tracker_links(c, scoped)
    lock_projects(c, scoped, links)
    return row['id'] in protected_ids(scoped, (TRACKER_PREFIX,), links)


def lock_tracker_file_projects(c, selected):
    """Serialize generic file operations with project deletion on PostgreSQL.

    SQLite mutations already hold BEGIN IMMEDIATE. A shared project-row lock
    keeps an ordinary Files operation from passing its access check immediately
    before the project's deletion marker is committed by another worker.
    """
    if not selected or not hasattr(c,'backend') or c.backend.kind != 'postgres':
        return
    rows = ancestors(c, selected)
    lock_projects(c, rows, tracker_links(c, rows))
