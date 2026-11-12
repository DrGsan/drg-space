"""Workspace retention, audit and account offboarding in encrypted settings."""
from app.i18n import message as localized_message
import hashlib
import json
import secrets

from app import workspace_management as management

DELETE_PREFIX = 'space-deletion:'
AUDIT_PREFIX = 'space-audit:'
INVITE_PREFIX = 'space-invite:'
PERIODS = {'organization': 30 * 86400, 'project': 7 * 86400, 'family': 86400, 'group': 86400}


def read(c, key):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def deletions(c):
    return {r['key'][len(DELETE_PREFIX):]: json.loads(r['value'])
            for r in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (DELETE_PREFIX + '%',))}


def pending(c, sid):
    return read(c, DELETE_PREFIX + sid)


def audit(c, sid, uid, event, details, stamp):
    # One encrypted row per event avoids a growing document and concurrent appends.
    key = AUDIT_PREFIX + sid + ':' + str(stamp).zfill(16) + ':' + secrets.token_hex(16)
    management.write(c, key, {'version': 1, 'actor': uid, 'event': event, 'details': details, 'created': stamp})


def schedule(c, space, uid, stamp):
    sid = space['id']
    current = pending(c, sid)
    if current: return current  # Repeated requests never extend the deadline.
    from app.space_profiles import templates
    template = templates(c).get(sid, 'family') if space['kind'] == 'family' else 'group'
    value = {'version': 1, 'created': stamp, 'delete_at': stamp + PERIODS[template]}
    management.write(c, DELETE_PREFIX + sid, value)
    from app.tracker_issue_lifecycle import refresh_workspace
    refresh_workspace(c, sid, stamp)
    audit(c, sid, uid, 'deletion_scheduled', {'delete_at': value['delete_at']}, stamp)
    return value


def public_links(c, sid):
    return [dict(r) for r in c.execute('SELECT s.token,s.note_id,n.title FROM shares s JOIN notes n ON n.id=s.note_id WHERE n.space_id=? AND s.revoked=0', (sid,))]


def revoke_links(c, sid):
    c.execute('DELETE FROM share_sessions WHERE share_token IN (SELECT token FROM shares WHERE note_id IN (SELECT id FROM notes WHERE space_id=?))', (sid,))
    c.execute('UPDATE shares SET revoked=1 WHERE note_id IN (SELECT id FROM notes WHERE space_id=?)', (sid,))
    management.invalidate(c, dirty=True)


def preview(c, sid, uid):
    value = management.get(c, sid)
    return {'teams': [t['name'] for t in value['teams'].values() if uid in t['members']],
            'notes': c.execute('SELECT COUNT(*) FROM notes WHERE space_id=? AND user_id=?', (sid, uid)).fetchone()[0],
            'invitations': c.execute('SELECT COUNT(*) FROM social_requests WHERE space_id=? AND sender_id=?', (sid, uid)).fetchone()[0],
            'publications': len(public_links(c, sid))}


def transfer_shared_notes(c, uid, fail, stamp, space_id=None):
    """Transfer common documents at account deletion or scoped work offboarding."""
    owned = c.execute("SELECT name FROM spaces WHERE kind='family' AND owner_id=?", (uid,)).fetchall()
    if owned and space_id is None: fail(409, 'Сначала передайте владение пространствами: ' + ', '.join(r[0] for r in owned))
    # All managed shared spaces (families/groups/projects/organizations).
    spaces=c.execute("SELECT id,owner_id FROM spaces WHERE kind='family'"+(' AND id=?' if space_id else ''),(space_id,) if space_id else ()).fetchall()
    for space in spaces:
        sid, owner = space['id'], space['owner_id']
        if hasattr(c,'__dict__'):
            if not hasattr(c,'quota_transfer_owners'):c.quota_transfer_owners=set()
            c.quota_transfer_owners.add(owner)
        pages = {r[0] for r in c.execute('SELECT id FROM notes WHERE space_id=? AND user_id=?', (sid, uid))}
        for nid in pages:
            c.execute('UPDATE notes SET user_id=? WHERE id=?', (owner, nid))
        from app.personal_data import ASSET_LINK
        for page in c.execute('SELECT body,blocks FROM notes WHERE space_id=?',(sid,)):
            for match in ASSET_LINK.finditer((page['body'] or '')+(page['blocks'] or '')):
                c.execute('UPDATE media SET user_id=? WHERE id=? AND user_id=?',(owner,match[1],uid))
        # A colleague can upload a cover to somebody else's page as well.
        c.execute('UPDATE media SET user_id=? WHERE user_id=? AND id IN (SELECT a.id FROM note_assets a JOIN notes n ON n.id=a.note_id WHERE n.space_id=?)', (owner, uid, sid))
        folders = {r['id'] for r in c.execute("SELECT id,mime FROM files WHERE kind='folder'") if r['mime']=='application/x-drg-note;space=' + sid}
        rows = list(c.execute('SELECT id,parent_id,user_id FROM files'))
        ids = set(folders)
        while True:
            children = {r['id'] for r in rows if r['parent_id'] in ids}
            if children <= ids: break
            ids.update(children)
        for row in rows:
            if row['id'] in ids and row['user_id'] == uid:
                c.execute('UPDATE files SET user_id=? WHERE id=?', (owner, row['id']))
        if pages: audit(c, sid, owner, 'documents_transferred', {'notes': len(pages)}, stamp)
    if space_id is None:
        # Deleted identities are not retained in workspace logs.
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (AUDIT_PREFIX + '%',)).fetchall():
            value = json.loads(row['value'])
            if value.get('actor') == uid: value['actor'] = None
            def redact(item):
                if isinstance(item,dict):return {k:redact(v) for k,v in item.items() if k!=uid and not (k=='user_id' and v==uid)}
                if isinstance(item,list):return [redact(v) for v in item if v!=uid]
                return None if item==uid else item
            value['details']=redact(value['details'])
            management.write(c, row['key'], value)
    management.invalidate(c, dirty=True)


def destroy(c, sid, stamp):
    from app.modules.note_assets import purge_page_assets
    from app.note_access import remove_page_settings
    from app.file_trash import purge_file_rows
    from app.chat_storage import attachment_room
    from app.modules.social import clean_chat_widgets
    pages = {r[0] for r in c.execute('SELECT id FROM notes WHERE space_id=?', (sid,))}
    purge_page_assets(c, pages, stamp)
    remove_page_settings(c, pages)
    rows = list(c.execute('SELECT id,parent_id,mime FROM files'))
    ids = {r['id'] for r in rows if attachment_room(r['id']) == sid or r['mime'] == 'application/x-drg-note;space=' + sid}
    while True:
        children = {r['id'] for r in rows if r['parent_id'] in ids}
        if children <= ids: break
        ids.update(children)
    purge_file_rows(c, ids, stamp)
    relation=c.execute('SELECT first_id,second_id FROM friendships WHERE space_id=?',(sid,)).fetchone()
    if relation:
        a,b=relation
        c.execute('DELETE FROM task_list_members WHERE (user_id=? AND list_id IN (SELECT id FROM task_lists WHERE owner_id=?)) OR (user_id=? AND list_id IN (SELECT id FROM task_lists WHERE owner_id=?))',(a,b,b,a))
    # Avatars are inline encrypted settings, removed by clean_chat_widgets.
    # Workspace removal revokes grants but preserves projects privately for
    # their existing owners, including any separate project-recovery deadline.
    for project in c.execute('SELECT id,owner_id FROM tracker_projects WHERE space_id=?',(sid,)).fetchall():
        c.execute('UPDATE tracker_members SET state=?,revision=revision+1 WHERE project_id=?',('revoked',project['id']))
        c.execute('UPDATE tracker_projects SET space_id=NULL,updated=?,version=version+1 WHERE id=?',(stamp,project['id']))
        from app.tracker import history
        history(c,project['id'],None,project['owner_id'],'','workspace_removed',{},stamp)
        from app.tracker_issue_lifecycle import refresh_clocks
        refresh_clocks(c, project['id'], stamp)
    c.execute('DELETE FROM spaces WHERE id=?', (sid,))
    for prefix in (DELETE_PREFIX, AUDIT_PREFIX):
        c.execute('DELETE FROM app_settings WHERE key=? OR key LIKE ?', (prefix + sid, prefix + sid + ':%'))
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (INVITE_PREFIX + '%',)).fetchall():
        if json.loads(row['value'])['space_id'] == sid:
            c.execute('DELETE FROM invites WHERE token=?', (row['key'][len(INVITE_PREFIX):],))
            c.execute('DELETE FROM app_settings WHERE key=?', (row['key'],))
    for row in c.execute('SELECT id,payload FROM notifications').fetchall():
        if json.loads(row['payload']).get('space_id') == sid: c.execute('DELETE FROM notifications WHERE id=?', (row['id'],))
    clean_chat_widgets(c)
    management.invalidate(c, dirty=True)


def purge(c, stamp):
    spaces={r[0] for r in c.execute('SELECT id FROM spaces')}
    for sid, value in deletions(c).items():
        if sid not in spaces or value['delete_at'] <= stamp: destroy(c, sid, stamp)
    for row in c.execute('SELECT key FROM app_settings WHERE key LIKE ?',(AUDIT_PREFIX+'%',)).fetchall():
        if row[0][len(AUDIT_PREFIX):].split(':')[0] not in spaces:c.execute('DELETE FROM app_settings WHERE key=?',(row[0],))
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (INVITE_PREFIX + '%',)).fetchall():
        token = row['key'][len(INVITE_PREFIX):]
        value = json.loads(row['value'])
        if (not c.execute('SELECT 1 FROM invites WHERE token=? AND used=0 AND expires>?', (token, stamp)).fetchone()
                or not management.is_manager(c, value['space_id'], value['sender'])):
            c.execute('DELETE FROM invites WHERE token=?', (token,))
            c.execute('DELETE FROM app_settings WHERE key=?', (row['key'],))


def invitation(c, token, stamp, fail):
    hashed = hashlib.sha256(token.encode()).hexdigest()
    value = read(c, INVITE_PREFIX + hashed)
    row = c.execute('SELECT * FROM invites WHERE token=? AND used=0 AND expires>?', (hashed, stamp)).fetchone()
    if (not row or not value or pending(c, value['space_id'])
            or not management.is_manager(c, value['space_id'], value['sender'])):
        fail(404, localized_message('invitationCancelledOrExpired'))
    return hashed, value


def join(c, token, uid, stamp, fail):
    hashed, value = invitation(c, token, stamp, fail)
    sid = value['space_id']
    if c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?', (sid, uid)).fetchone():
        fail(409, localized_message('youAreAlreadyAMemberOfTheWorkspace'))
    if c.execute('SELECT COUNT(*) FROM space_members WHERE space_id=?', (sid,)).fetchone()[0] >= 50: fail(422, localized_message('theWorkspaceHasNoMoreThan50Members'))
    if c.execute("SELECT COUNT(*) FROM space_members m JOIN spaces s ON s.id=m.space_id WHERE m.user_id=? AND s.kind='family'", (uid,)).fetchone()[0] >= 20: fail(422, localized_message('noMoreThan20Workspaces'))
    c.execute('INSERT INTO space_members VALUES(?,?,?)', (sid, uid, stamp))
    c.execute('UPDATE invites SET used=1 WHERE token=?', (hashed,))
    c.execute('DELETE FROM app_settings WHERE key=?', (INVITE_PREFIX + hashed,))
    audit(c, sid, uid, 'invitation_accepted', {}, stamp)
    return sid


def validate_archive(c, fail):
    try:
        spaces = {r[0] for r in c.execute('SELECT id FROM spaces')}
        for sid, value in deletions(c).items():
            if (sid not in spaces or type(value['version']) is not int or set(value) != {'version', 'created', 'delete_at'} or value['version'] != 1
                    or type(value['created']) is not int or type(value['delete_at']) is not int
                    or value['created'] < 0 or value['delete_at'] - value['created'] not in PERIODS.values()): raise ValueError()
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (AUDIT_PREFIX + '%',)):
            sid = row['key'][len(AUDIT_PREFIX):].split(':')[0]; value = json.loads(row['value'])
            if (sid not in spaces or type(value['version']) is not int or value['version'] != 1 or not isinstance(value['details'], dict)
                    or not isinstance(value['event'], str) or type(value['created']) is not int
                    or value['actor'] is not None and not management.uid(value['actor'])): raise ValueError()
        for row in c.execute('SELECT value FROM app_settings WHERE key LIKE ?', (INVITE_PREFIX + '%',)):
            value = json.loads(row[0])
            if (set(value)!={'version','space_id','sender','created'} or type(value['version']) is not int or value['version']!=1
                    or value['space_id'] not in spaces or not management.uid(value['sender']) or type(value['created']) is not int): raise ValueError()
    except (ValueError, TypeError, KeyError): fail()
