"""Final pages stay locked; editing leases belong to one authenticated session."""
from app.i18n import message as localized_message
from app.http import Router, Request
from pydantic import BaseModel
from typing import Literal
from app.modules.social import note_access

LEASE_SECONDS = 900

def lock_status(c,n,session,now):
    cache=getattr(c,'note_catalog_metadata',None)
    if cache is not None and n['id'] in cache['ids']:expires=cache['unlocks'].get(n['id'])
    else:
        lease=c.execute('SELECT expires FROM note_unlocks WHERE note_id=? AND session_token=? AND expires>?',(n['id'],session['token'],now())).fetchone()
        expires=lease[0] if lease else None
    return {'locked':bool(n['locked']), 'unlock_until':expires if n['locked'] else None}

def require_unlocked(c,n,session,now,fail,*,content=True):
    if content:
        from app.notion_sync.access import require_writable
        require_writable(c,n,fail)
    if n['locked'] and (not session or not c.execute('SELECT 1 FROM note_unlocks WHERE note_id=? AND session_token=? AND expires>?',(n['id'],session['token'],now())).fetchone()):
        fail(423,localized_message('pageIsLockedUnlockItBeforeMakingChanges'))

class LockAction(BaseModel):
    mode:Literal['lock','unlock','temporary','relock']

def create_lock_router(db,ready,now,fail):
    from app.modules.notion import can_manage
    r=Router()
    @r.post('/api/notes/{nid}/lock')
    def action(nid:str,data:LockAction,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');n=note_access(c,nid,s['user_id'],fail)
            if data.mode=='relock':
                c.execute('DELETE FROM note_unlocks WHERE note_id=? AND session_token=?',(nid,s['token']))
            else:
                if not can_manage(c,n,s['user_id']):fail(403,localized_message('pageLockingIsManagedByThePageAuthor'))
                if data.mode=='temporary':
                    if not n['locked']:fail(409,localized_message('pageIsAlreadyUnlocked'))
                    c.execute('INSERT INTO note_unlocks VALUES(?,?,?) ON CONFLICT(note_id,session_token) DO UPDATE SET expires=excluded.expires',(nid,s['token'],now()+LEASE_SECONDS))
                else:
                    c.execute('UPDATE notes SET locked=? WHERE id=?',(int(data.mode=='lock'),nid))
                    c.execute('DELETE FROM note_unlocks WHERE note_id=?',(nid,))
            return lock_status(c,c.execute('SELECT * FROM notes WHERE id=?',(nid,)).fetchone(),s,now)
    @r.get('/api/favorites')
    def favorites(request:Request):
        s=ready(request)
        with db() as c:
            from app.note_access import visible_ids
            allowed=visible_ids(c,s['user_id'])
            return [dict(n) for n in c.execute('SELECT n.id,n.title,n.icon,n.space_id,n.updated FROM notes n JOIN note_favorites f ON f.note_id=n.id WHERE f.user_id=? AND n.deleted IS NULL ORDER BY f.created DESC',(s['user_id'],)) if n['id'] in allowed]
    return r
