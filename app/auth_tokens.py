"""Signed JWT cookies with short access, rotating refresh and immediate revocation."""
from app.i18n import message as localized_message
import json
import logging
import hashlib
import hmac
import re
import secrets
import threading
from pydantic import BaseModel, ConfigDict, Field
from contextlib import nullcontext

import jwt
from app.http import Router, Request, Response

ACCESS_SECONDS = 900
CHALLENGE_SECONDS = 300
SESSION_SECONDS = 604800
ABSOLUTE_SECONDS = 180 * 86400
TRUST_SECONDS = 7 * 86400


class SessionName(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(max_length=80)


def session_device(agent):
    """Display hints only. User-Agent is never proof of identity or permission."""
    if agent.startswith('DrG-iOS/'):
        return {'platform': 'iOS', 'browser': 'DrG', 'client': 'app', 'device': 'iOS · DrG App'}
    lower = agent.lower()
    platform = ('iPadOS' if 'ipad' in lower or 'drg-webapp/ipados' in lower else 'iOS' if 'iphone' in lower or 'ipod' in lower else 'Android' if 'android' in lower else 'Windows' if 'windows' in lower else 'macOS' if 'macintosh' in lower or 'mac os x' in lower else 'Linux' if 'linux' in lower else 'Неизвестная ОС')
    browser = next((name for marker, name in [('edg/', 'Edge'), ('edgios/', 'Edge'), ('opr/', 'Opera'), ('fxios/', 'Firefox'), ('firefox/', 'Firefox'), ('crios/', 'Chrome'), ('chrome/', 'Chrome'), ('safari/', 'Safari')] if marker in lower), 'Браузер')
    client = 'web-app' if agent.startswith('DrG-WebApp') else 'web'
    return {'platform': platform, 'browser': browser, 'client': client, 'device': platform+' · '+browser+' · '+('Web App' if client == 'web-app' else 'Web')}


class AuthTokens:
    def __init__(self, db, now, fail, app_secret, secure):
        self.db, self.now, self.fail, self.secure = db, now, fail, secure
        self.monitor = None
        self.policy = None
        self._activity = {}
        self._activity_lock = threading.Lock()
        # Separate keys for account sessions and publication access/edit grants.
        self.keys = {kind: hmac.digest(app_secret.encode(), f'drg-site:jwt:{kind}:v1'.encode(), 'sha256')
                     for kind in ('access', 'refresh', 'share', 'share-edit', 'password-hint','file-share','file-edit','note-cover','folder-upload','file-download','file-retention','file-retention-change')}

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def encode(self, kind, subject, expires, **claims):
        stamp = self.now()
        return jwt.encode({'iss': 'drg-site', 'aud': f'drg-site:{kind}', 'type': kind,
                           'sub': subject, 'iat': stamp, 'nbf': stamp, 'exp': expires,
                           'jti': secrets.token_urlsafe(32), **claims}, self.keys[kind], algorithm='HS256')

    def decode(self, raw, kind, *, allow_expired=False):
        if not raw or len(raw) > 8192:
            self.fail(401, localized_message('signInToYourAccount'))
        try:
            data = jwt.decode(raw, self.keys[kind], algorithms=['HS256'], issuer='drg-site',
                              audience=f'drg-site:{kind}', options={
                                  'require': ['iss', 'aud', 'type', 'sub', 'iat', 'nbf', 'exp', 'jti'],
                                  # Use the injected clock for deterministic expiry and tests.
                                  'verify_exp': False, 'verify_iat': False, 'verify_nbf': False})
            if data['type'] != kind:
                raise ValueError('Wrong token purpose')
            for field in ('sub', 'jti'):
                if not isinstance(data[field], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', data[field]):
                    raise ValueError('Invalid identity')
            stamp = self.now()
            if any(type(data[field]) is not int for field in ('iat', 'nbf', 'exp')):
                raise ValueError('Invalid timestamp')
            if (data['exp'] <= stamp and not (allow_expired and kind == 'access')
                    or data['nbf'] > stamp or data['iat'] > stamp or data['exp'] <= data['iat']):
                raise ValueError('Expired or future token')
            max_lifetime = {'access': ACCESS_SECONDS, 'refresh': SESSION_SECONDS, 'share': 3600, 'share-edit': 900, 'password-hint':300,'file-share':3600,'file-edit':900,'note-cover':1800,'folder-upload':3600,'file-download':86400,'file-retention':300,'file-retention-change':300}[kind]
            if data['exp'] - data['iat'] > max_lifetime:
                raise ValueError('Lifetime exceeds policy')
            return data
        except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
            self.fail(401, localized_message('yourSessionHasExpiredOrTheTokenIs'))

    def check_csrf(self, request, csrf):
        if not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), csrf.encode()):
            # Refresh verifies CSRF inside its write transaction. Defer HTTP
            # audit until the transaction has rolled back instead of opening a
            # second writer (which deadlocks on PostgreSQL's advisory lock).
            request._security_csrf_rejected = True
            if self.monitor is not None and not hasattr(request, 'url'):
                # The realtime handshake uses a synthetic request outside a DB
                # transaction and has no HTTP response hook to record the event.
                self.monitor.record(request, 'csrf_rejected', outcome='blocked')
            self.fail(403, localized_message('invalidCsrfToken'))

    def session(self, request, verified=True, csrf=True):
        data = self.decode(request.cookies.get('drg_session', ''), 'access')
        sid, stage = data.get('sid'), data.get('stage')
        if not isinstance(sid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{40,60}', sid):
            self.fail(401, localized_message('invalidSession'))
        with self.db() as c:
            row = c.execute('SELECT s.*,u.login,u.role,u.totp,u.settings FROM sessions s JOIN users u ON u.id=s.user_id WHERE token=? AND expires>?',
                            (self.digest(sid), self.now())).fetchone()
            from app.account_lifecycle import pending
            deletion=pending(c,row['user_id']) if row else None
        if not row or row['user_id'] != data['sub'] or row['stage'] != stage:
            self.fail(401, localized_message('sessionHasBeenRevokedOrExpired'))
        if verified and stage == 'pending':
            self.fail(401, localized_message('verifyLoginWith2FaCode'))
        request._security_actor = {'user_id': row['user_id'], 'login': row['login'], 'session': row['token']}
        if self.policy is not None:
            self.policy.check_session(request, row)
        if csrf and request.method not in ('GET', 'HEAD'):
            self.check_csrf(request, row['csrf'])
        if deletion:
            if self.now()>=deletion['delete_at']:self.fail(401,localized_message('accountRecoveryPeriodHasExpired'))
            path=getattr(getattr(request,'url',None),'path',None)
            authenticating=stage!='full' and path is not None and path.startswith('/api/2fa/')
            if (path is None or path.startswith('/ws/') or request.method not in ('GET','HEAD') and not authenticating and path not in {'/api/logout','/api/refresh','/api/account/deletion','/api/account/deletion/restore'}):
                self.fail(423,localized_message('accountIsAwaitingDeletionEditingIsDisabled'),headers={'X-Account-Deletion':'pending'})
        if stage == 'full' and getattr(request, 'url', None) is not None:
            # ready() may run inside a route's write transaction (e.g. vault
            # auto-lock). A second connection would wait on our own PostgreSQL
            # advisory lock. Flush only after the handler releases its writer.
            request._session_activity = {'token': row['token'], 'user_id': row['user_id']}
        return dict(row) | {'verified': stage != 'pending', 'access_expires': data['exp'], 'account_deletion':deletion}

    def flush_activity(self, request):
        session = getattr(request, '_session_activity', None)
        if session is None: return
        request._session_activity = None
        try:
            self.record_activity(request, session)
        except Exception as error:
            # Optional device/presence metadata must not turn a committed
            # mutation or a valid login into an error. Never log credentials.
            logging.getLogger('drg.sessions').warning('Session activity update failed (%s).', type(error).__name__)

    def record_activity(self, request, session):
        stamp = self.now(); token = session['token']
        # At most one metadata write per session/30s/worker, independent of how
        # many images a chat loads. Recheck ownership inside the transaction.
        with self._activity_lock:
            if stamp - self._activity.get(token, -1000000) < 30: return
            if len(self._activity) >= 4096: self._activity = {k:v for k,v in self._activity.items() if stamp-v < 120}
            self._activity[token] = stamp
        agent = request.headers.get('user-agent', 'Неизвестное устройство')[:300]
        if request.headers.get('x-drg-client') == 'web-app' and not agent.startswith('DrG-iOS/'):
            hint = '/iPadOS' if request.headers.get('x-drg-platform') == 'iPadOS' else ''
            agent = 'DrG-WebApp'+hint+'; '+agent
        ip = self.monitor.protection.ip(request) if self.monitor else request.client.host if request.client else 'unknown'
        try:
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                if not c.execute("SELECT 1 FROM sessions WHERE token=? AND user_id=? AND stage='full' AND expires>?", (token, session['user_id'], stamp)).fetchone(): return
                c.execute('INSERT INTO session_presence VALUES(?,?) ON CONFLICT(session_token) DO UPDATE SET last_seen=excluded.last_seen WHERE session_presence.last_seen<excluded.last_seen', (token, stamp))
                c.execute('INSERT INTO user_presence VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen WHERE user_presence.last_seen<excluded.last_seen', (session['user_id'], stamp))
                if not request.headers.get('x-drg-client') and not agent.startswith('DrG-iOS/'):
                    old = c.execute('SELECT agent FROM session_info WHERE session_token=?', (token,)).fetchone()
                    if old and old[0].startswith('DrG-WebApp') and old[0].endswith('; '+agent): agent = old[0]
                c.execute('INSERT INTO session_info VALUES(?,?,?) ON CONFLICT(session_token) DO UPDATE SET agent=excluded.agent,ip=excluded.ip', (token, agent, ip))
        except Exception:
            with self._activity_lock: self._activity.pop(token, None)
            raise

    def cookies(self, response, sid, uid, stage, csrf, expires, refresh=None):
        access = self.encode('access', uid, min(expires, self.now() + (ACCESS_SECONDS if stage == 'full' else CHALLENGE_SECONDS)), sid=sid, stage=stage)
        age = max(0, expires - self.now())
        # Keep expired access in the browser until the current inactivity deadline;
        # its signature/exp are checked on every request, then refresh is used.
        response.set_cookie('drg_session', access, httponly=True, secure=self.secure, samesite='lax', max_age=age, path='/')
        response.set_cookie('drg_csrf', csrf, httponly=False, secure=self.secure, samesite='lax', max_age=age, path='/')
        if refresh:
            response.set_cookie('drg_refresh', refresh, httponly=True, secure=self.secure, samesite='strict', max_age=age, path='/api/refresh')
        else:
            response.delete_cookie('drg_refresh', path='/api/refresh', secure=self.secure, httponly=True, samesite='strict')

    def new_refresh(self, c, sid, uid, expires):
        jti = secrets.token_urlsafe(32)
        token = self.encode('refresh', uid, expires, sid=sid, jti=jti)
        c.execute('INSERT INTO refresh_tokens VALUES(?,?,?,?,0)', (self.digest(jti), self.digest(sid), expires, self.now()))
        return token

    def require_trusted(self,c,s):
        if self.now()-s["created"]<TRUST_SECONDS and c.execute("SELECT 1 FROM sessions WHERE user_id=? AND token!=? AND created<? AND expires>?",(s["user_id"],s["token"],s["created"],self.now())).fetchone():
            self.fail(403,localized_message('thisSessionIsTooNewToRevokeOlder'))

    def issue(self, response, uid, verified, connection=None,created=None,request=None):
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with (self.db() if connection is None else nullcontext(connection)) as c:
            if connection is None:
                c.execute('BEGIN IMMEDIATE')
            if self.monitor is not None:
                state = self.monitor.get_mode(c)
                if state['mode'] == 'lockdown' or state['mode'] != 'normal' and created is None:
                    self.fail(503, localized_message('newLoginsAreTemporarilySuspendedByTheAdministrator'),
                              headers={'Retry-After':'60','X-Service-Mode':state['mode']})
            user = c.execute('SELECT role,totp,settings FROM users WHERE id=?', (uid,)).fetchone()
            can_enter = bool(user['totp']) or (user['role'] != 'admin' and json.loads(user['settings']).get('_two_factor_disabled') is True)
            stage = 'pending' if not verified else 'full' if can_enter else 'enrollment'
            started=self.now() if created is None else created
            expires = min(self.now()+SESSION_SECONDS,started+ABSOLUTE_SECONDS) if stage=='full' else self.now()+CHALLENGE_SECONDS
            c.execute('DELETE FROM sessions WHERE expires<=?', (self.now(),))
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', (self.digest(sid), uid, csrf, stage, expires, started))
            refresh = self.new_refresh(c, sid, uid, expires) if stage == 'full' else None
            if request is not None and stage=='full' and created is None:
                from app.notifications import notify
                agent=request.headers.get('user-agent','Неизвестное устройство')[:300]
                ip=self.monitor.protection.ip(request) if self.monitor else request.client.host if request.client else 'unknown'
                c.execute('INSERT INTO session_info VALUES(?,?,?)',(self.digest(sid),agent,ip))
                notify(c,uid,'session',{'agent':agent,'ip':ip,'session_created':started},self.now())
                if self.monitor is not None:
                    login=c.execute('SELECT login FROM users WHERE id=?',(uid,)).fetchone()[0]
                    self.monitor.record(request,'login_success',subject=login,user_id=uid,outcome='success',connection=c)
        self.cookies(response, sid, uid, stage, csrf, expires, refresh)
        return csrf

    def clear_cookies(self, response):
        response.delete_cookie('drg_admin', path='/api', secure=self.secure, httponly=True, samesite='strict')
        for name, path in [('drg_session', '/'), ('drg_csrf', '/'), ('drg_refresh', '/api/refresh')]:
            response.delete_cookie(name, path=path, secure=self.secure, httponly=name != 'drg_csrf', samesite='strict' if name == 'drg_refresh' else 'lax')

    def router(self, ready, rate):
        router = Router()

        @router.post('/api/refresh')
        def refresh(request: Request, response: Response):
            rate(request, 'refresh', 120, 60)
            data = self.decode(request.cookies.get('drg_refresh', ''), 'refresh')
            sid = data.get('sid')
            if not isinstance(sid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{40,60}', sid):
                self.fail(401, localized_message('invalidSession'))
            replay = False
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                s = c.execute('SELECT * FROM sessions WHERE token=? AND expires>?', (self.digest(sid), self.now())).fetchone()
                if not s or s['stage'] != 'full' or s['user_id'] != data['sub']:
                    self.fail(401, localized_message('sessionHasBeenRevokedOrExpired'))
                self.check_csrf(request, s['csrf'])
                old = c.execute('SELECT * FROM refresh_tokens WHERE token=? AND session_token=? AND expires>?',
                                (self.digest(data['jti']), s['token'], self.now())).fetchone()
                if not old:
                    self.fail(401, localized_message('refreshTokenIsInvalid'))
                if old['used']:
                    # Commit revocation before reporting the failure.
                    c.execute('DELETE FROM sessions WHERE token=?', (s['token'],))
                    replay = True
                else:
                    c.execute('UPDATE refresh_tokens SET used=1 WHERE token=?', (old['token'],))
                    c.execute('DELETE FROM refresh_tokens WHERE expires<=?',(self.now(),))
                    expires=min(self.now()+SESSION_SECONDS,s['created']+ABSOLUTE_SECONDS)
                    c.execute('UPDATE sessions SET expires=? WHERE token=?',(expires,s['token']))
                    new = self.new_refresh(c, sid, s['user_id'], expires)
            if replay:
                if self.monitor is not None:
                    self.monitor.record(request,'refresh_replay',user_id=s['user_id'],severity='high',outcome='blocked')
                self.fail(401, localized_message('tokenRefreshIsBeingReusedSessionHasBeen'))
            self.cookies(response, sid, s['user_id'], 'full', s['csrf'], expires, new)
            return {'csrf': s['csrf']}

        @router.get('/api/security/sessions')
        def list_sessions(request: Request):
            s = ready(request)
            # This endpoint owns no surrounding transaction; show the current
            # client/IP in this response rather than waiting for the next poll.
            self.flush_activity(request)
            with self.db() as c:
                settings = json.loads(c.execute('SELECT settings FROM users WHERE id=?', (s['user_id'],)).fetchone()[0])
                names = settings.get('_session_names', {})
                return [dict(row) | session_device(row['agent'] or '') | {'name': names.get(row['id'], ''), 'current': row['id'] == s['token'], 'online': bool(row['last_seen'] and row['last_seen'] >= self.now()-90), 'can_revoke': row['id']==s['token'] or self.now()-s['created']>=TRUST_SECONDS or row['created']>=s['created'], 'trusted_after':s['created']+TRUST_SECONDS}
                        for row in c.execute("SELECT s.token AS id,s.created,s.expires,i.agent,i.ip,p.last_seen FROM sessions s LEFT JOIN session_info i ON i.session_token=s.token LEFT JOIN session_presence p ON p.session_token=s.token WHERE s.user_id=? AND s.stage='full' AND s.expires>? ORDER BY s.created DESC", (s['user_id'], self.now()))]

        @router.patch('/api/security/sessions/{session_id}')
        def rename_session(session_id: str, data: SessionName, request: Request):
            s = ready(request); name = data.name.strip()
            if any(ord(x)<32 or ord(x)==127 for x in name): self.fail(422, localized_message('sessionNameShouldNotContainControlCharacters'))
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                valid = {r[0] for r in c.execute("SELECT token FROM sessions WHERE user_id=? AND stage='full' AND expires>?", (s['user_id'], self.now()))}
                if session_id not in valid: self.fail(404, localized_message('sessionNotFound'))
                settings = json.loads(c.execute('SELECT settings FROM users WHERE id=?', (s['user_id'],)).fetchone()[0])
                names = {k:v for k,v in settings.get('_session_names', {}).items() if k in valid}
                if name: names[session_id] = name
                else: names.pop(session_id, None)
                settings['_session_names'] = names
                c.execute('UPDATE users SET settings=? WHERE id=?', (json.dumps(settings), s['user_id']))
            return {'ok': True, 'name': name}

        @router.post('/api/security/sessions/revoke-others')
        def revoke_others(request: Request):
            s = ready(request)
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                if self.now()-s['created']<TRUST_SECONDS and c.execute('SELECT 1 FROM sessions WHERE user_id=? AND token!=? AND created<? AND expires>?',(s['user_id'],s['token'],s['created'],self.now())).fetchone():
                    self.fail(403,localized_message('thisSessionIsTooNewToCompleteOlder'))
                count = c.execute('DELETE FROM sessions WHERE user_id=? AND token!=?', (s['user_id'], s['token'])).rowcount
            return {'ok': True, 'revoked': count}

        @router.delete('/api/security/sessions/{session_id}')
        def revoke_session(session_id: str, request: Request, response: Response):
            s = ready(request)
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                target=c.execute('SELECT created FROM sessions WHERE token=? AND user_id=?',(session_id,s['user_id'])).fetchone()
                if session_id!=s['token'] and target and target[0]<s['created'] and self.now()-s['created']<TRUST_SECONDS:self.fail(403,localized_message('aNewSessionCannotCompleteAnOlderOne'))
                r = c.execute('DELETE FROM sessions WHERE token=? AND user_id=?', (session_id, s['user_id']))
                if not r.rowcount:
                    self.fail(404, localized_message('sessionNotFound'))
            if session_id == s['token']:
                self.clear_cookies(response)
            return {'ok': True, 'current': session_id == s['token']}

        return router
