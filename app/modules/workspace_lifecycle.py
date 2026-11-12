"""Scoped deletion recovery, offboarding preview, audit and single-use invites."""
from app.i18n import message as localized_message
import hashlib
import json
import os
import secrets
from app.http import Request
from app import workspace_lifecycle as lifecycle
from app import workspace_management as management
from app.notifications import actor


def register_lifecycle(router, db, ready, now, fail, rate, space_access):
    @router.post('/api/social/spaces/{sid}/restore')
    def restore(sid: str, request: Request):
        session = ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            space = space_access(c, sid, session['user_id'], fail, allow_pending=True)
            if space['owner_id'] != session['user_id']: fail(403, localized_message('deletionCanOnlyBeCancelledByTheOwner'))
            value = lifecycle.pending(c, sid)
            if not value: return {'ok': True}
            if value['delete_at'] <= now(): fail(409, localized_message('recoveryPeriodHasExpired'))
            c.execute('DELETE FROM app_settings WHERE key=?', (lifecycle.DELETE_PREFIX + sid,))
            from app.tracker_issue_lifecycle import refresh_workspace
            refresh_workspace(c, sid, now())
            lifecycle.audit(c, sid, session['user_id'], 'deletion_cancelled', {}, now())
        return {'ok': True}

    @router.get('/api/social/spaces/{sid}/members/{uid}/offboarding')
    def offboarding(sid: str, uid: str, request: Request):
        session = ready(request)
        with db() as c:
            space_access(c, sid, session['user_id'], fail)
            if not management.is_manager(c, sid, session['user_id']): fail(403, localized_message('manageWorkspaceAvailable'))
            if not c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?', (sid, uid)).fetchone(): fail(404, localized_message('userNotFound'))
            return lifecycle.preview(c, sid, uid)

    @router.get('/api/social/spaces/{sid}/audit')
    def events(sid: str, request: Request, before: str = ''):
        session = ready(request)
        with db() as c:
            space_access(c, sid, session['user_id'], fail, allow_pending=True)
            if not management.is_manager(c, sid, session['user_id']): fail(403, localized_message('logIsAvailableToTheOwnerAndAdministrators'))
            prefix = lifecycle.AUDIT_PREFIX + sid + ':'
            rows = []
            query = 'SELECT key,value FROM app_settings WHERE key LIKE ?'
            args = [prefix + '%']
            if before:
                query += ' AND key<?'; args.append(prefix + before)
            query += ' ORDER BY key DESC LIMIT 31'
            for row in c.execute(query, tuple(args)):
                value = json.loads(row['value']); value['id'] = row['key'][len(prefix):]
                value['cursor'] = value['id']
                value['person'] = actor(c, value['actor'], now()) if value['actor'] and c.execute('SELECT 1 FROM users WHERE id=?', (value['actor'],)).fetchone() else None
                display = dict(value['details'])
                if 'user_id' in display:
                    identity = display.pop('user_id')
                    user = c.execute('SELECT login FROM users WHERE id=?',(identity,)).fetchone()
                    display['user'] = user[0] if user else 'Удалённый аккаунт'
                if 'note_id' in display:
                    identity = display.pop('note_id')
                    page = c.execute('SELECT title FROM notes WHERE id=? AND space_id=?',(identity,sid)).fetchone()
                    display['page'] = page[0] if page else 'Удалённая страница'
                for field in ('added_members','removed_members'):
                    if field in display:
                        display[field]=[r[0] if (r:=c.execute('SELECT login FROM users WHERE id=?',(uid,)).fetchone()) else 'Удалённый аккаунт' for uid in display[field]]
                roles={'viewer':'Просмотр','commenter':'Комментирование','editor':'Редактирование','none':'Нет доступа','admin':'Администратор','member':'Участник','owner':'Владелец'}
                for field in ('before_access','after_access'):
                    if field not in display:continue
                    policy=display[field];parts=[];teams=management.get(c,sid)['teams']
                    for tid,role in policy['teams'].items():parts.append((teams.get(tid) or {'name':'Удалённая команда'})['name']+' — '+roles[role])
                    for uid,role in policy['members'].items():
                        user=c.execute('SELECT login FROM users WHERE id=?',(uid,)).fetchone()
                        parts.append((user[0] if user else 'Удалённый аккаунт')+' — '+roles[role])
                    display[field]={'inherit':'По наследованию','workspace':'Всё пространство','restricted':'Выбранные участники'}.get(policy['mode'],policy['mode'])+(': '+', '.join(parts) if parts else '')
                for field in ('before_role','after_role'):
                    if field in display:display[field]=roles.get(display[field],display[field])
                subject_ids=set()
                if value['details'].get('user_id'):subject_ids.add(value['details']['user_id'])
                for field in ('added_members','removed_members'):subject_ids.update(value['details'].get(field,[]))
                for field in ('before_access','after_access'):subject_ids.update(value['details'].get(field,{}).get('members',{}))
                value['subjects']=[{key:person.get(key,'') for key in ('id','login','display_name')} for uid in subject_ids if c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone() for person in [actor(c,uid,now())]]
                value['display'] = display
                rows.append(value)
            return {'events': rows[:30], 'next': rows[29]['cursor'] if len(rows) > 30 else None}

    @router.post('/api/social/spaces/{sid}/invite-links')
    def create_invite(sid: str, request: Request):
        session = ready(request); rate(request, 'space-invite-link', 30, subject=session['user_id'])
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); space_access(c, sid, session['user_id'], fail)
            if not management.is_manager(c, sid, session['user_id']): fail(403, localized_message('ownerAndAdministratorsCanInvite'))
            active = [r for r in c.execute('SELECT value FROM app_settings WHERE key LIKE ?', (lifecycle.INVITE_PREFIX + '%',)) if json.loads(r[0])['space_id'] == sid]
            if len(active) >= 50: fail(422, localized_message('noMoreThan50PendingLinks'))
            token = secrets.token_urlsafe(32); hashed = hashlib.sha256(token.encode()).hexdigest(); expires = now() + 7 * 86400
            c.execute('INSERT INTO invites VALUES(?,?,?,0)', (hashed, None, expires))
            management.write(c, lifecycle.INVITE_PREFIX + hashed, {'version': 1, 'space_id': sid, 'sender': session['user_id'], 'created': now()})
            lifecycle.audit(c, sid, session['user_id'], 'invitation_created', {}, now())
        return {'id': hashed, 'url': os.getenv('PUBLIC_URL', 'http://localhost:8000').rstrip('/') + '/invite/' + token, 'expires': expires}

    @router.get('/api/social/spaces/{sid}/invite-links')
    def list_invites(sid: str, request: Request):
        session = ready(request)
        with db() as c:
            space_access(c, sid, session['user_id'], fail)
            if not management.is_manager(c, sid, session['user_id']): fail(403, localized_message('manageWorkspaceAvailable'))
            rows = []
            for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (lifecycle.INVITE_PREFIX + '%',)):
                value = json.loads(row['value'])
                if value['space_id'] != sid: continue
                invite = c.execute('SELECT expires FROM invites WHERE token=? AND used=0 AND expires>?', (row['key'][len(lifecycle.INVITE_PREFIX):], now())).fetchone()
                if invite: rows.append({'id': row['key'][len(lifecycle.INVITE_PREFIX):], 'created': value['created'], 'expires': invite['expires']})
            return rows

    @router.delete('/api/social/spaces/{sid}/invite-links/{identity}')
    def revoke_invite(sid: str, identity: str, request: Request):
        session = ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); space_access(c, sid, session['user_id'], fail)
            if not management.is_manager(c, sid, session['user_id']): fail(403, localized_message('manageWorkspaceAvailable'))
            value = lifecycle.read(c, lifecycle.INVITE_PREFIX + identity)
            if not value or value['space_id'] != sid: fail(404, localized_message('invitationNotFound'))
            c.execute('DELETE FROM invites WHERE token=?', (identity,)); c.execute('DELETE FROM app_settings WHERE key=?', (lifecycle.INVITE_PREFIX + identity,))
            lifecycle.audit(c, sid, session['user_id'], 'invitation_revoked', {}, now())
        return {'ok': True}

    @router.get('/api/social/invite-links/{token}')
    def describe_invite(token: str, request: Request):
        with db() as c:
            _, value = lifecycle.invitation(c, token, now(), fail)
            return {'name': c.execute('SELECT name FROM spaces WHERE id=?', (value['space_id'],)).fetchone()[0]}

    @router.post('/api/social/invite-links/{token}/accept')
    def accept_invite(token: str, request: Request):
        session = ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return {'space_id': lifecycle.join(c, token, session['user_id'], now(), fail)}
