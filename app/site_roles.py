"""Site roles are independent of document and workspace permissions."""
from app.i18n import message as localized_message
import re

LABELS = {'owner': 'Владелец', 'admin': 'Администратор', 'member': 'Пользователь'}


def administrator_route(request):
    """Explicit delegation: new administration endpoints stay owner-only."""
    path, method = request.url.path, request.method
    if (method, path) in {
        ('GET', '/api/admin/users'), ('POST', '/api/admin/users'),
        ('POST', '/api/admin/users/delete'), ('POST', '/api/admin/users/restore'),
        ('GET', '/api/admin/registration'), ('POST', '/api/invites'), ('GET', '/api/invites'),
        ('GET', '/api/presence'),
    }:
        return True
    if method == 'DELETE' and re.fullmatch(r'/api/invites/[a-f0-9]{64}', path):return True
    match = re.fullmatch(r'/api/admin/users/[a-f0-9]{32}/(profile|quota|password|password-reset|reset-2fa|avatar)', path)
    return bool(match and method == ('GET' if match[1]=='avatar' else 'POST' if match[1] in {'password-reset', 'reset-2fa'} else 'PUT'))


def current_manager(c, session, now, fail):
    """Recheck authority within the mutation transaction, after acquiring its lock."""
    row = c.execute("SELECT u.role,u.totp FROM users u JOIN sessions s ON s.user_id=u.id WHERE u.id=? AND s.token=? AND s.stage='full' AND s.expires>?",
                    (session['user_id'], session['token'], now)).fetchone()
    if not row or row['role'] not in ('owner', 'admin') or row['role'] == 'admin' and not row['totp']:
        fail(403, localized_message('permissionsHaveBeenChangedPleaseSignInAgain'))
    return row['role']


def managed_user(c, uid, session, now, fail):
    role = current_manager(c, session, now, fail)
    row = c.execute('SELECT id,login,role FROM users WHERE id=?', (uid,)).fetchone()
    if not row:
        fail(404, localized_message('accountNotFound'))
    if role != 'owner' and row['role'] != 'member':
        fail(403, localized_message('administratorsCanOnlyManageRegularUsers'))
    return row
