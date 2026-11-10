"""Session-bound, server-enforced administrator reauthentication."""
from app.i18n import message as localized_message
import hashlib
import hmac
import json
import secrets
from pydantic import BaseModel, Field
from app.http import Router, Request, Response

IDLE_SECONDS = 15 * 60
PREFIX = 'admin-access:'
COOKIE = 'drg_admin'


class Unlock(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class AdminAccess:
    def __init__(self, db, owner, fail, protection, check_password, now, secure):
        self.db, self.owner, self.fail = db, owner, fail
        self.protection, self.check_password, self.now, self.secure = protection, check_password, now, secure

    @staticmethod
    def protected(request):
        path = request.url.path
        access = {'/api/admin/access', '/api/admin/access/unlock', '/api/admin/access/activity', '/api/admin/access/lock'}
        return ((path.startswith('/api/admin/') and path not in access)
                or path == '/api/invites'
                or path == '/api/family-photos/admin' or path.startswith('/api/family-photos/admin/')
                or (path == '/api/presence' and request.args.get('scope') == 'admin'))

    @staticmethod
    def key(session):
        return PREFIX + session['token']

    def verify(self, request, session, password):
        uid = session['user_id']
        self.protection.check(request, 'admin-password', uid)
        with self.db() as c:
            row = c.execute('SELECT password FROM users WHERE id=?', (uid,)).fetchone()
        if not row or not self.check_password(password, row[0]):
            self.protection.failure(request, 'admin-password', uid)
            self.fail(403, localized_message('incorrectAdministratorPassword'))
        self.protection.success(request, 'admin-password', uid)
        return hashlib.sha256(row[0].encode()).hexdigest()

    def state(self, request, session, touch=False):
        stamp = self.now(); key = self.key(session)
        with self.db() as c:
            if touch: c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
            password = c.execute('SELECT password FROM users WHERE id=?', (session['user_id'],)).fetchone()
            try:
                value = json.loads(row[0]) if row else {}
                valid = (password and value.get('user_id') == session['user_id']
                    and type(value.get('last_activity')) is int and stamp < value['last_activity'] + IDLE_SECONDS
                    and value['last_activity'] <= stamp
                    and hmac.compare_digest(value.get('password_stamp', ''), hashlib.sha256(password[0].encode()).hexdigest())
                    and hmac.compare_digest(value.get('nonce', ''), hashlib.sha256(request.cookies.get(COOKIE, '').encode()).hexdigest()))
            except (TypeError, ValueError, AttributeError): valid = False
            if valid and touch:
                value['last_activity'] = stamp
                c.execute('UPDATE app_settings SET value=? WHERE key=?', (json.dumps(value), key))
            expires = value['last_activity'] + IDLE_SECONDS if valid else 0
        return dict(unlocked=bool(valid), expires_at=expires, server_time=stamp, idle_seconds=IDLE_SECONDS)

    def require(self, request, touch=False):
        session = self.owner(request)
        from app.site_roles import administrator_route
        if session['role'] != 'owner' and request.url.path not in {
            '/api/admin/access/activity', '/api/admin/access/lock',
        } and not administrator_route(request):
            self.fail(403, localized_message('onlyTheSiteOwnerCanAccessThis'))
        if not self.state(request, session, touch)['unlocked']:
            self.fail(423, localized_message('toManageEnterTheAdministratorPassword'), headers={'X-Admin-Unlock': 'required'})
        return session

    def proof(self, request, password=None):
        session = self.require(request, touch=True)
        self.protection.rate(request, 'admin-action', 30, subject=session['user_id'])
        # Old clients may still send an explicit proof. Never accept a bad one.
        if password is not None: self.verify(request, session, password)
        return session

    def router(self):
        router = Router()

        @router.get('/api/admin/access')
        def status(request: Request):
            return self.state(request, self.owner(request))

        @router.post('/api/admin/access/unlock')
        def unlock(data: Unlock, request: Request, response: Response):
            session = self.owner(request)
            self.protection.rate(request, 'admin-unlock', 10, subject=session['user_id'])
            password_stamp = self.verify(request, session, data.password)
            nonce = secrets.token_urlsafe(32); stamp = self.now()
            value = dict(user_id=session['user_id'], last_activity=stamp, password_stamp=password_stamp,
                         nonce=hashlib.sha256(nonce.encode()).hexdigest())
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                # Bounded transient state, shared by all application workers.
                for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (PREFIX+'%',)).fetchall():
                    try: old = json.loads(row['value']); expired = old.get('last_activity', 0) <= stamp-IDLE_SECONDS
                    except (ValueError, AttributeError, TypeError): expired = True
                    if expired or not c.execute('SELECT 1 FROM sessions WHERE token=? AND expires>?', (row['key'][len(PREFIX):], stamp)).fetchone():
                        c.execute('DELETE FROM app_settings WHERE key=?', (row['key'],))
                c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (self.key(session), json.dumps(value)))
            response.set_cookie(COOKIE, nonce, httponly=True, secure=self.secure, samesite='strict', path='/api', max_age=604800)
            return dict(unlocked=True, expires_at=stamp+IDLE_SECONDS, server_time=stamp, idle_seconds=IDLE_SECONDS)

        @router.post('/api/admin/access/activity')
        def activity(request: Request):
            session = self.require(request, touch=True)
            return self.state(request, session)

        @router.post('/api/admin/access/lock')
        def lock(request: Request, response: Response):
            session = self.owner(request)
            with self.db() as c: c.execute('DELETE FROM app_settings WHERE key=?', (self.key(session),))
            response.delete_cookie(COOKIE, path='/api', httponly=True, secure=self.secure, samesite='strict')
            return dict(unlocked=False, expires_at=0, server_time=self.now(), idle_seconds=IDLE_SECONDS)

        return router
