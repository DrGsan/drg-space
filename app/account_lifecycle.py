"""Delayed account deletion. State lives in existing encrypted app settings."""
from app.i18n import message as localized_message
import json
from app.workspace_management import write
from app.workspace_lifecycle import read

PREFIX='account-deletion:'
PERIOD=86400

def pending(c,uid):return read(c,PREFIX+uid)

def ensure_transfer(c,uid,fail):
    if c.execute('SELECT 1 FROM tracker_projects WHERE owner_id=?',(uid,)).fetchone():
        fail(409,localized_message('trackerTransferRequired'))
    from app.secret_vault import exists
    if exists(c,uid):fail(409,localized_message('beforeDeletingTheAccountDeleteTheSecretStorage'))
    owned=c.execute("SELECT name FROM spaces WHERE kind='family' AND owner_id=?",(uid,)).fetchall()
    if owned:fail(409,'Сначала передайте владение пространствами: '+', '.join(r[0] for r in owned))

def schedule(c,uid,actor,stamp,fail):
    user=c.execute('SELECT role FROM users WHERE id=?',(uid,)).fetchone()
    if not user:fail(404,localized_message('accountNotFound'))
    if user['role']=='owner':fail(409,localized_message('theWebsiteOwnerSAccountCannotBeDeleted'))
    if user['role']=='admin':fail(409,localized_message('theOwnerMustFirstRemoveTheWebsiteAdministrator'))
    current=pending(c,uid)
    if current:return current
    ensure_transfer(c,uid,fail)
    value={'version':1,'created':stamp,'delete_at':stamp+PERIOD,'actor':actor}
    write(c,PREFIX+uid,value)
    return value

def cancel(c,uid,stamp,fail):
    value=pending(c,uid)
    if not value:fail(409,localized_message('theAccountIsNotBeingDeleted'))
    if stamp>=value['delete_at']:fail(409,localized_message('accountRecoveryPeriodHasExpired'))
    c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+uid,))

def destroy(c,uid,stamp,fail):
    user=c.execute('SELECT login FROM users WHERE id=?',(uid,)).fetchone()
    if not user:return
    from app.secret_vault import exists
    if exists(c,uid):fail(409,localized_message('firstDeleteTheUserSSecretStorage'))
    from app.federation.revocations import account_deleted
    account_deleted(c,uid,stamp)
    from app.workspace_lifecycle import transfer_shared_notes,destroy as destroy_space
    transfer_shared_notes(c,uid,fail,stamp)
    for table in ('shares','file_shares'):
        for row in c.execute(f"SELECT token,allowed FROM {table} WHERE mode='users'").fetchall():
            allowed=json.loads(row['allowed'])
            if user['login'] in allowed:c.execute(f'UPDATE {table} SET allowed=? WHERE token=?',(json.dumps([x for x in allowed if x!=user['login']]),row['token']))
    for row in c.execute("SELECT s.id FROM spaces s JOIN space_members m ON m.space_id=s.id WHERE s.kind='friends' AND m.user_id=?",(uid,)).fetchall():destroy_space(c,row[0],stamp)
    pages={r[0] for r in c.execute('SELECT id FROM notes WHERE user_id=?',(uid,))}
    from app.modules.note_assets import purge_page_assets
    from app.note_access import remove_page_settings
    purge_page_assets(c,pages,stamp);remove_page_settings(c,pages)
    from app.file_trash import purge_file_rows
    purge_file_rows(c,{r[0] for r in c.execute('SELECT id FROM files WHERE user_id=?',(uid,))},stamp)
    for row in c.execute('SELECT id FROM media WHERE user_id=?',(uid,)).fetchall():c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(row[0],'media',stamp))
    from app.passkeys import clear_passkeys
    clear_passkeys(c,uid)
    from app.import_receipts import clear as clear_import_receipts
    clear_import_receipts(c,uid)
    c.execute('DELETE FROM invites WHERE login=?',(user['login'],))
    c.execute('DELETE FROM notes WHERE user_id=?',(uid,))
    c.execute('DELETE FROM tasks WHERE user_id=?',(uid,))
    c.execute('DELETE FROM users WHERE id=?',(uid,))
    from app.modules.social import clean_chat_widgets
    clean_chat_widgets(c)

def purge(c,stamp):
    def fail(code,message):
        # Refuse deletion if ownership changed unexpectedly; retry maintenance.
        raise ValueError(message)
    existing={r[0] for r in c.execute('SELECT id FROM users')}
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)).fetchall():
        uid=row['key'][len(PREFIX):];value=json.loads(row['value'])
        if uid not in existing:
            c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],));continue
        if value['delete_at']<=stamp:
            destroy(c,uid,stamp,fail)
            c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],))
    # Deleted requesters must not survive in another account's pending state.
    users={r[0] for r in c.execute('SELECT id FROM users')}
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)).fetchall():
        value=json.loads(row['value'])
        if value['actor'] is not None and value['actor'] not in users:value['actor']=None;write(c,row['key'],value)

def validate_archive(c,fail):
    from app.workspace_management import uid as valid_uid
    users={r['id']:r['role'] for r in c.execute('SELECT id,role FROM users')}
    try:
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)):
            uid=row['key'][len(PREFIX):];value=json.loads(row['value'])
            if c.execute("SELECT 1 FROM spaces WHERE kind='family' AND owner_id=?",(uid,)).fetchone():raise ValueError()
            if (users.get(uid)!='member' or set(value)!={'version','created','delete_at','actor'}
                or type(value['version']) is not int or value['version']!=1
                or type(value['created']) is not int or value['created']<0 or type(value['delete_at']) is not int
                or value['delete_at']-value['created']!=PERIOD or value['actor'] is not None and (not valid_uid(value['actor']) or value['actor'] not in users)):raise ValueError()
    except (ValueError,TypeError,KeyError):fail()
