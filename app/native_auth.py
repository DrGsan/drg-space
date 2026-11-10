"""One-use, PKCE-bound handoff from browser WebAuthn to a native session.

No bearer/session token is placed in a callback URL. Transient records share the
passkey ceremony prefix, encryption, retention and backup exclusions.
"""
from app.i18n import message as localized_message
import base64
import hashlib
import hmac
import json
import secrets

from pydantic import BaseModel, ConfigDict, Field
from app.http import Router, Request, Response

PREFIX = 'passkey:challenge:native:'
TOKEN = r'^[A-Za-z0-9_-]{43}$'
START_TTL = 180
GRANT_TTL = 60


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def account_proof(user):
    settings = json.loads(user['settings'])
    return digest(json.dumps([user['password'], user['totp'], user['role'],
                              settings.get('_two_factor_disabled')], separators=(',', ':')))


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid')
    challenge: str = Field(pattern=TOKEN)
    state: str = Field(pattern=TOKEN)


class Exchange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=TOKEN)
    verifier: str = Field(pattern=TOKEN)


def grant(c, flow, user, passkey, now, fail):
    """Called only after full, UV-required WebAuthn verification, in its transaction."""
    key = PREFIX + 'start:' + digest(flow)
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
    pending = json.loads(row['value']) if row else None
    if not pending or pending.get('kind') != 'native-start' or pending['expires'] <= now():
        fail(401, localized_message('applicationLoginSessionHasExpiredPleaseStartAgain'))
    code = secrets.token_urlsafe(32)
    value = {'kind': 'native-grant', 'challenge': pending['challenge'],
             'user_id': user['id'], 'account_proof': account_proof(user),
             'credential_id': passkey['credential_id'], 'public_key': digest(passkey['public_key']),
             'expires': now() + GRANT_TTL}
    c.execute('DELETE FROM app_settings WHERE key=?', (key,))
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',
              (PREFIX + 'grant:' + digest(code), json.dumps(value)))
    return {'code': code, 'state': pending['state']}


def create_native_auth_router(db, tokens, protection, now, fail, public_url):
    router = Router()

    @router.post('/api/native/passkeys/start')
    def start(data: Start, request: Request):
        protection.rate(request, 'native-passkey-start', 30)
        flow = secrets.token_urlsafe(32)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            active = 0
            for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',
                                 ('passkey:challenge:%',)).fetchall():
                if json.loads(row['value'])['expires'] <= now():
                    c.execute('DELETE FROM app_settings WHERE key=?', (row['key'],))
                else:
                    active += 1
            if active >= 1000:
                fail(429, localized_message('tooManyLoginAttemptsTryAgainLater'))
            value = {'kind': 'native-start', 'challenge': data.challenge,
                     'state': data.state, 'expires': now() + START_TTL}
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',
                      (PREFIX + 'start:' + digest(flow), json.dumps(value)))
        return {'url': public_url.rstrip('/') + '/native/passkey?flow=' + flow}

    @router.post('/api/native/passkeys/exchange')
    def exchange(data: Exchange, request: Request, response: Response):
        protection.rate(request, 'native-passkey-exchange', 30)
        key = PREFIX + 'grant:' + digest(data.code)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
            pending = json.loads(row['value']) if row else None
            challenge = base64.urlsafe_b64encode(hashlib.sha256(data.verifier.encode()).digest()).decode().rstrip('=')
            if (not pending or pending.get('kind') != 'native-grant' or pending['expires'] <= now()
                    or not hmac.compare_digest(challenge, pending['challenge'])):
                fail(401, localized_message('loginConfirmationHasExpiredOrIsInvalidPlease'))
            user = c.execute('SELECT * FROM users WHERE id=?', (pending['user_id'],)).fetchone()
            if not user or not hmac.compare_digest(account_proof(user), pending['account_proof']):
                fail(401, localized_message('accountSecuritySettingsHaveBeenChangedPleaseSign'))
            keys = json.loads(user['settings']).get('_passkeys', [])
            if not any(item['credential_id'] == pending['credential_id']
                       and hmac.compare_digest(digest(item['public_key']), pending['public_key']) for item in keys):
                fail(401, localized_message('passkeyHasBeenRevokedPleaseSignInAgain'))
            csrf = tokens.issue(response, user['id'], True, connection=c, request=request)
            c.execute('DELETE FROM app_settings WHERE key=?', (key,))
        return {'csrf': csrf}

    return router
