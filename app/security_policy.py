"""Persistent service isolation, enforced independently of the admin UI."""
from app.i18n import message as localized_message
import re
from html import escape
from app.i18n import current_language, text
from app.http import HTTPError, Response, WebSocket


LOGIN_PATHS = {
    '/api/login', '/api/register', '/api/setup', '/api/password-reset',
    '/api/password-hint', '/api/passkeys/options', '/api/passkeys/verify',
    '/api/native/passkeys/start', '/api/native/passkeys/exchange',
    '/api/2fa/verify', '/api/2fa/skip', '/api/2fa/enroll',
    '/api/2fa/confirm', '/api/2fa/activate',
}
PUBLIC_ASSETS = {'/favicon.ico', '/manifest.webmanifest', '/push-sw.js'}


class SecurityPolicy:
    def __init__(self, monitor, tokens):
        self.monitor, self.tokens = monitor, tokens

    def identity(self, request, *, allow_expired=False):
        """Check server-side identity even when an access JWT needs refresh."""
        refresh = getattr(getattr(request, 'url', None), 'path', '') == '/api/refresh'
        kind, cookie = ('refresh', 'drg_refresh') if refresh else ('access', 'drg_session')
        try:
            value = self.tokens.decode(request.cookies.get(cookie, ''), kind,
                                       allow_expired=allow_expired and kind == 'access')
            sid = value.get('sid', '')
            if not isinstance(sid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{40,60}', sid):
                return None
            with self.monitor.db() as c:
                row = c.execute('SELECT s.*,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE token=? AND expires>?',
                                (self.tokens.digest(sid), self.monitor.now())).fetchone()
            if row and row['stage'] == 'full' and row['user_id'] == value['sub']:
                return dict(row) | {'access_expired': kind == 'access' and value['exp'] <= self.monitor.now()}
        except HTTPError:
            pass
        return None

    @staticmethod
    def permitted(state, session):
        return bool(session and session['role'] == 'owner' and
                    session['token'] == state.get('allowed_session'))

    def check_session(self, request, session):
        state = self.monitor.get_mode()
        if state['mode'] != 'normal' and getattr(getattr(request, 'url', None), 'path', '') == '/api/admin/backups/restore':
            # A restore may have begun uploading before isolation was enabled.
            # Its second authorization under the storage gate must check again.
            raise HTTPError(409, localized_message('firstDisableTheServiceRestrictionsInTheSecurity'))
        if state['mode'] == 'lockdown' and not self.permitted(state, session):
            raise HTTPError(503, localized_message('theServiceIsTemporarilyClosedByTheAdministrator'), {'Retry-After': '60', 'X-Service-Mode': 'lockdown'})

    def check(self, request):
        path = getattr(getattr(request, 'url', None), 'path', '')
        route = getattr(getattr(request, 'source', None), 'url_rule', None)
        if isinstance(request, WebSocket) or getattr(route, 'websocket', False):
            self.monitor.protection.check(request)
        if path == '/api/health':
            return
        state = self.monitor.get_mode()
        mode = state['mode']
        if mode == 'normal':
            return
        if path == '/api/admin/backups/restore':
            # A full restore revokes all sessions and clears the session-bound
            # emergency grant. Require the explicit MFA-protected reopen first.
            raise HTTPError(409, localized_message('firstDisableTheServiceRestrictionsInTheSecurity'))
        if mode == 'logins_closed':
            if path not in LOGIN_PATHS:
                return
            # Existing full sessions may maintain their own 2FA settings.
            if path in {'/api/2fa/enroll', '/api/2fa/confirm', '/api/2fa/activate'} and self.identity(request):
                return
        else:
            # Only packaged public assets, never user uploads or publications.
            if path.startswith('/assets/') or path in PUBLIC_ASSETS:
                return
            identity = self.identity(request, allow_expired=True)
            if self.permitted(state, identity):
                if identity['access_expired']:
                    # A signed expired access cookie may recover the application
                    # shell only. API requests must use the normal refresh flow;
                    # no private response is authorized by an expired token.
                    if path == '/':
                        return
                    if path.startswith('/api/'):
                        raise HTTPError(401, localized_message('sessionExpiredRefreshTheSession'))
                    raise HTTPError(503, localized_message('theServiceIsTemporarilyClosedByTheAdministrator'),
                                    {'Retry-After': '60', 'X-Service-Mode': 'lockdown'})
                if (path in {'/api/security/password', '/api/security/2fa/disable', '/api/2fa/enroll',
                             '/api/2fa/confirm', '/api/2fa/activate', '/api/logout'}
                        or path.startswith('/api/admin/users/'+identity['user_id']+'/')):
                    raise HTTPError(409, localized_message('firstDisableEmergencyClosureToPreserveAccessTo'))
                return
        raise HTTPError(503, 'Сервис временно закрыт администратором.' if mode == 'lockdown'
                        else 'Новые входы временно приостановлены администратором.',
                        {'Retry-After': '60', 'X-Service-Mode': mode})

    def guard_stream(self, request, body):
        """Stop further chunks of a download after service isolation begins."""
        iterator = iter(body)
        try:
            for chunk in iterator:
                try:
                    self.check(request)
                except Exception:
                    return  # Headers already sent; never send further private data.
                yield chunk
        finally:
            close = getattr(iterator, 'close', None)
            if close:
                close()

    @staticmethod
    def unavailable(error):
        # No sensitive state/reason, scripts, login forms, or external assets.
        return Response(f'<!doctype html><html lang="{current_language.get()}"><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width,initial-scale=1">'
                        f'<title>{escape(text('temporarilyUnavailableTitle'))}</title>'
                        '<style>body{font:18px system-ui;background:#101923;color:#edf4fb;'
                        'max-width:36rem;margin:15vh auto;padding:24px;line-height:1.6}'
                        'h1{font-size:32px}a{color:#91c9f7}</style>'
                        f'<h1>{escape(text('temporarilyUnavailable'))}</h1>'
                        f'<p>{escape(text('accessRestricted'))}</p>'
                        f'<p><a href="/">{escape(text('reloadPage'))}</a></p></html>',
                        status_code=503, headers=error.headers, media_type='text/html')
