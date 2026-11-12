"""Retention policies for trash, tasks, messages and service records."""
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TRASH_SECONDS = 30 * 86400

def device_timezone(value):
    try:
        if not isinstance(value, str) or len(value) > 100: return 'UTC'
        ZoneInfo(value)
        return value
    except (ZoneInfoNotFoundError, ValueError): return 'UTC'

def purge_tasks(c, uid, settings, stamp):
    from app.modules.tasks import ensure_task_details,ListPayload
    ensure_task_details(c,uid,settings,stamp)
    completed = settings.get('_task_completed_at', {})
    if not isinstance(completed,dict):completed={}
    kept = {}
    rows=c.execute('SELECT t.id,t.day,d.completed_at,l.payload FROM tasks t JOIN task_details d ON d.task_id=t.id JOIN task_lists l ON l.id=d.list_id WHERE t.user_id=? AND t.done=1',(uid,)).fetchall()
    for task in rows:
        at = task['completed_at']
        if at is None:at=completed.get(task['id'])
        if not isinstance(at,(int,float)) or not 0<=at<=32503680000:at=None
        # Old completed reminders without a reliable timestamp get a full day
        # from first observation; never infer completion from the scheduled day.
        if at is None:
            at=stamp
            c.execute('UPDATE task_details SET completed_at=?,version=version+1 WHERE task_id=?',(at,task['id']))
        kept[task['id']]=at
    from app.task_list_deletion import pending
    paused={row[0] for item in c.execute('SELECT id FROM task_lists WHERE owner_id=?',(uid,)) if pending(c,item[0]) for row in c.execute('SELECT task_id FROM task_details WHERE list_id=?',(item[0],))}
    expired={task['id'] for task in rows if task['id'] not in paused and not ListPayload.model_validate(json.loads(task['payload'])).retain_completed and kept[task['id']]<=stamp-86400}
    if expired:
        tree={row['task_id']:row['parent_id'] for row in c.execute('SELECT d.task_id,d.parent_id FROM task_details d JOIN tasks t ON t.id=d.task_id WHERE t.user_id=?',(uid,))}
        # An expired parent waits for every descendant. Retained or unfinished
        # children must not become detached or disappear with their parent.
        protected=set(tree)-expired
        for tid in list(protected):
            seen=set()
            while tree.get(tid) and tid not in seen:
                seen.add(tid);tid=tree[tid];protected.add(tid)
        for tid in expired-protected:
            c.execute('DELETE FROM tasks WHERE id=? AND user_id=?',(tid,uid))
            kept.pop(tid,None)
    if kept != settings.get('_task_completed_at',{}):
        settings['_task_completed_at'] = kept
        return True
    return False

def purge_trash(c, stamp):
    expired = {r['id'] for r in c.execute('SELECT id FROM notes WHERE deleted IS NOT NULL AND deleted<=?', (stamp-TRASH_SECONDS,))}
    # Retire the short-lived 3.0.x override without changing trash timestamps.
    if c.execute('SELECT 1 FROM app_settings WHERE key LIKE ? LIMIT 1',('note-deletion:%',)).fetchone():
        c.execute('DELETE FROM app_settings WHERE key LIKE ?',('note-deletion:%',))
    if not expired:return
    # A database must remain until its retained records also expire.
    for row in c.execute('SELECT database_id,note_id FROM database_records'):
        if row['note_id'] not in expired:expired.discard(row['database_id'])
    # Detach retained children before foreign-key cascades, including the case
    # where two expired ancestors sit above a newly deleted grandchild.
    for row in c.execute('SELECT id,parent_id FROM notes WHERE parent_id IS NOT NULL').fetchall():
        if row['parent_id'] in expired and row['id'] not in expired:
            from app.note_access import pin_before_move
            page=c.execute('SELECT * FROM notes WHERE id=?',(row['id'],)).fetchone()
            pin_before_move(c,page,None)
            c.execute('UPDATE notes SET parent_id=NULL WHERE id=?', (row['id'],))
    from app.modules.note_assets import purge_page_assets
    purge_page_assets(c,expired,stamp)
    from app.note_access import remove_page_settings
    remove_page_settings(c,expired)
    for nid in expired:c.execute('DELETE FROM notes WHERE id=?', (nid,))

def purge_service_records(c,stamp):
    from app.tracker_lifecycle import purge as purge_tracker_projects
    purge_tracker_projects(c,stamp)
    from app.tracker_issue_lifecycle import purge as purge_tracker_issues
    purge_tracker_issues(c,stamp)
    from app.task_list_deletion import purge as purge_task_lists
    purge_task_lists(c,stamp)
    # Unfinished transfers expire; completed project work never does.
    c.execute('DELETE FROM tracker_uploads WHERE created<=?',(stamp-3600,))
    from app.task_invitations import purge as purge_task_invitations
    purge_task_invitations(c,stamp)
    from app.account_lifecycle import purge as purge_accounts
    purge_accounts(c,stamp)
    from app.task_order import purge as purge_task_order
    purge_task_order(c)
    from app.people_names import purge as purge_people_names
    purge_people_names(c)
    from app.workspace_lifecycle import purge as purge_workspaces
    purge_workspaces(c,stamp)
    from app.reactions import purge as purge_reactions
    purge_reactions(c)
    from app.note_access import purge_orphans
    purge_orphans(c)
    from app.modules.family_avatars import purge_avatars
    purge_avatars(c)
    # Delete grants first so a revoked or expired parent cannot retain access.
    for sessions,shares in (('share_sessions','shares'),('file_share_sessions','file_shares')):
        c.execute(f'DELETE FROM {sessions} WHERE expires<=? OR share_token IN (SELECT token FROM {shares} WHERE revoked=1 OR expires<=?)',(stamp,stamp))
        c.execute(f'DELETE FROM {shares} WHERE revoked=1 OR expires<=?',(stamp,))
    for table in ('invites','password_resets','social_requests','note_unlocks','refresh_tokens','sessions','expense_shares'):
        c.execute(f'DELETE FROM {table} WHERE expires<=?',(stamp,))
    c.execute('DELETE FROM invites WHERE used=1')
    from app.registration_invites import purge as purge_registration_invites
    from app.import_receipts import purge as purge_import_receipts
    purge_import_receipts(c,stamp)
    purge_registration_invites(c,stamp)
    c.execute('DELETE FROM throttle WHERE stamp<=?',(stamp-86400,))
    from app.protection import positive_env
    c.execute('DELETE FROM auth_failures WHERE stamp<=?',(stamp-positive_env('AUTH_FAILURE_WINDOW',900,86400),))
    c.execute('DELETE FROM security_bans WHERE until<=? AND updated<=?',(stamp,stamp-30*86400))
    from app.security_monitor import purge_security
    purge_security(c,stamp)
    # Ceremonies and administrative unlocks otherwise survive indefinitely when
    # their owner never returns to the corresponding screen.
    from app.admin_access import IDLE_SECONDS
    from app.passkeys import index_key
    sessions={r[0] for r in c.execute('SELECT token FROM sessions')}
    indexes={index_key(key['credential_id']) for user in c.execute('SELECT settings FROM users')
             for key in json.loads(user[0]).get('_passkeys',[])}
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ? OR key LIKE ? OR key LIKE ?',
                         ('admin-access:%','passkey:challenge:%','passkey:index:%')).fetchall():
        key=row['key']
        try:
            value=json.loads(row['value'])
            if key.startswith('admin-access:'):
                stale=(key[len('admin-access:'):] not in sessions or type(value.get('last_activity')) is not int or value['last_activity']<=stamp-IDLE_SECONDS)
            elif key.startswith('passkey:challenge:'):
                stale=type(value.get('expires')) is not int or value['expires']<=stamp
            else:stale=key not in indexes
        except (ValueError,TypeError,AttributeError):stale=True
        if stale:c.execute('DELETE FROM app_settings WHERE key=?',(key,))

def cleanup(db, stamp):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        from app.file_sharing import purge as purge_file_retention
        purge_file_retention(c,stamp)
        purge_service_records(c,stamp)
        from app.modules.calls import purge_calls
        purge_calls(c,stamp)
        from app.modules.guest_calls import purge_guest_calls
        purge_guest_calls(c,stamp)
        from app.chat_storage import purge
        purge(c,stamp)
        purge_trash(c, stamp)
        from app.note_history import purge_history
        purge_history(c,stamp)
        from app.modules.note_files import purge_unused_attachments
        purge_unused_attachments(c,stamp)
        from app.modules.note_assets import purge_unused_assets
        purge_unused_assets(c,stamp)
        from app.file_trash import purge_file_trash
        purge_file_trash(c, stamp)
        c.execute('DELETE FROM notifications WHERE created<=?',(stamp-7*86400,))
        for user in c.execute('SELECT id,settings FROM users').fetchall():
            settings = json.loads(user['settings'])
            changed=False
            if isinstance(settings.get('_pending_until'),int) and settings['_pending_until']<=stamp:
                from app.passwords import clear_enrollment
                clear_enrollment(settings);changed=True
            if purge_tasks(c, user['id'], settings, stamp) or changed:
                c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), user['id']))
