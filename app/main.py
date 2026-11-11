from app.i18n import message as localized_message
import asyncio, logging
from contextlib import asynccontextmanager,suppress
import base64, calendar, hashlib, hmac, json, os, re, secrets, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from cryptography.fernet import Fernet
from app.http import Application, Request, Response, HTTPError
from app.http import FileResponse, JSONResponse
from app.auth_tokens import AuthTokens
from app.protection import Protection
from app.avatar_presets import default_profile
from app.modules.appearance import new_account_appearance
from app.passwords import NewPassword
from app.version import APP_VERSION, APP_RELEASE
from pydantic import BaseModel, Field
from pydantic import ConfigDict, StrictBool
from typing import Literal

DATA = Path(os.getenv('DATA_DIR', './data')); DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
APP_SECRET = os.getenv('APP_SECRET', '')
if len(APP_SECRET) < 32:
    raise RuntimeError('APP_SECRET must contain at least 32 characters; generate with scripts/init-env.py')
PUBLIC_URL = os.getenv('PUBLIC_URL', 'http://localhost:8000').rstrip('/')
SECURE = urlparse(PUBLIC_URL).scheme == 'https'
DB = DATA / 'site.sqlite3'
ph = PasswordHasher()
cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(APP_SECRET.encode()).digest()))
from app.modules.social import create_social_router, publication_access
from app.database import Database
from app.data_crypto import DataCipher, StorageError
data_cipher = DataCipher.from_env()
database = Database(DATA, cipher=data_cipher)
from app.storage import StorageGate,StorageMiddleware,blocking
from app.process_locks import try_file_lock,cleanup_temps
storage_gate=StorageGate(DATA)
with storage_gate.exclusive_sync():database.initialize()
db = database.connection
from app.multiprocess import ProcessCoordinator
process_coordinator=ProcessCoordinator(database,DATA,data_cipher,storage_gate)
from app.web_push import WebPush
web_push=WebPush(db,DATA,storage_gate,lambda:now(),PUBLIC_URL,coordinator=process_coordinator)
@asynccontextmanager
async def lifespan(app):
    async def maintenance():
        lease=None;initialized=False;next_cleanup=0;next_family_poll=0;next_storage_sweep=0;next_media_reconcile=0;next_public_build_cleanup=0
        async def jobs(actions):
            success=True
            for name,action in actions:
                try:await blocking(action)
                except Exception:
                    success=False
                    logging.getLogger('drg.maintenance').error('Maintenance job %s failed; queued for retry.',name)
            return success
        def temp_jobs():
            return [('backup-temporaries',admin_router.backups.cleanup_temps),
                    ('personal-temporaries',lambda:cleanup_temps(DATA,'.drg-personal-work-')),
                    ('notion-temporaries',lambda:cleanup_temps(DATA,'.drg-notion-work-'))]
        try:
            while True:
                if lease is None:lease=try_file_lock(DATA,'maintenance.lock')
                if lease:
                    try:
                        if not initialized:
                            async with storage_gate.try_exclusive() as available:
                                if available:
                                    await jobs(temp_jobs())
                                    initialized=True
                        if now()>=next_cleanup:
                            async with storage_gate.shared():
                                from app.retention import cleanup
                                await jobs([('sql-retention',lambda:cleanup(db,now())),
                                            ('expense-retention',expenses_router.cleanup),
                                            ('object-queue',files_router.cleanup),('secret-vault-cleanup',cleanup_secret_vaults),*temp_jobs()])
                            next_cleanup=now()+60
                        if now()>=next_storage_sweep:
                            async with storage_gate.try_exclusive() as available:
                                if available:
                                    success=await jobs([('storage-reconciliation',admin_router.backups.collect_orphans),
                                                        ('secret-storage-reconciliation',lambda:secret_vault.reconcile()),
                                                        ('media-transfer',admin_router.backups.media.transfer_local),
                                                        ('unfinished-uploads',admin_router.backups.objects.abort_pending)])
                                    next_storage_sweep=now()+(3600 if success else 300)
                                else:next_storage_sweep=now()+30
                        if now()>=next_family_poll:
                            next_family_poll=now()+5
                            async with storage_gate.shared():
                                await blocking(family_photos.poll_messages,now())
                        if now()>=next_media_reconcile:
                            next_media_reconcile=now()+30
                            from app.livekit import reconcile as reconcile_livekit
                            async with storage_gate.shared():
                                await jobs([('livekit-membership',lambda:reconcile_livekit(db,now()))])
                        if now()>=next_public_build_cleanup:
                            next_public_build_cleanup=now()+3600
                            if STATIC.exists():
                                await jobs([('public-build-retention',packaged_assets.prune)])
                    except Exception:logging.getLogger('drg.maintenance').error('Maintenance failed; retrying.')
                await asyncio.sleep(1)
        finally:
            if lease:lease.close()
    await blocking(database.start_pool)
    await blocking(database.redis_cache.start)
    family_photos.start_http()
    worker=None; federation_worker=None; notion_worker=None
    try:
        await process_coordinator.start()
        web_push.start()
        worker=asyncio.create_task(maintenance())
        async def federated_connections():
            changed=asyncio.Event()
            async def invalidate():changed.set()
            process_coordinator.subscribe_changes(invalidate,fallback=5)
            try:
                while True:
                    changed.clear()
                    delay=5
                    try:
                        async with storage_gate.shared():
                            delay=await blocking(federation_runtime.maintain)
                    except Exception:logging.getLogger('drg.federation').error('Federation listener refresh failed; retrying.')
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(changed.wait(),delay)
            finally:process_coordinator.unsubscribe_changes(invalidate)
        federation_worker=asyncio.create_task(federated_connections())
        if notion_sync_service.config.configured:
            from app.notion_sync.runtime import run as notion_run
            notion_worker=asyncio.create_task(notion_run(notion_sync_service,storage_gate))
        yield
    finally:
        if notion_worker:
            notion_worker.cancel()
            with suppress(asyncio.CancelledError):await notion_worker
        if federation_worker:
            federation_worker.cancel()
            with suppress(asyncio.CancelledError):await federation_worker
        await blocking(federation_runtime.stop)
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):await worker
        try:
            await production_calendar.close()
            await web_push.stop()
            await process_coordinator.stop()
        finally:
            try:await blocking(family_photos.close_http)
            finally:
                try:await blocking(database.redis_cache.close)
                finally:await blocking(database.close_pool)

app = Application(__name__, lifespan=lifespan)

@app.exception_handler(StorageError)
async def storage_error(request,error):
    return JSONResponse({'detail':'Не удалось проверить защищённые данные хранилища. Обратитесь к администратору.'},status_code=503)

app.add_middleware(StorageMiddleware,gate=storage_gate)

def now(): return int(time.time())
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def fail(code, message, headers=None): raise HTTPError(code, message, headers=headers)
tokens = AuthTokens(db, lambda: now(), fail, APP_SECRET, SECURE)
protection = Protection(db, lambda: now())
from app.security_monitor import SecurityMonitor
from app.security_policy import SecurityPolicy
security_monitor = SecurityMonitor(db, lambda: now(), protection)
security_policy = SecurityPolicy(security_monitor, tokens)
protection.monitor = tokens.monitor = security_monitor
tokens.policy = app.security_policy = security_policy

class Credentials(BaseModel):
    login: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=256)
class Setup(Credentials):
    password: NewPassword
    token: str
    password_hint: str = Field(default="",max_length=200)
class Register(Credentials):
    password: NewPassword
    invite: str | None = None
    password_hint: str = Field(default="",max_length=200)
class Code(BaseModel): code: str = Field(min_length=1, max_length=100)
class Password(BaseModel): password: str = Field(max_length=256)
class Share(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode: str
    password: str | None = Field(default=None, max_length=256)
    allowed: list[str] = Field(default_factory=list, max_length=50)
    duration: int | None = Field(default=None, ge=1, le=10000)
    unit: str = 'days'
    permission: Literal['view','edit'] = 'view'
class Invite(BaseModel):
    login: str | None = None
    add_creator_as_friend: StrictBool = False

@app.middleware('http')
async def security(request: Request, call_next):
    response = None
    try:
        await blocking(security_policy.check, request)
    except HTTPError as error:
        response = (JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=error.headers)
                    if request.url.path.startswith('/api/') else security_policy.unavailable(error))
    except StorageError:
        from app.storage_unavailable import document_navigation,unavailable
        if storage_gate.exclusive_pending and document_navigation(request):
            response=unavailable(request,HTTPError(503,localized_message('operationWithDataIsInProgressRetryLater')))
        else:response = JSONResponse({'detail': 'Не удалось проверить режим доступа. Повторите позже.'}, status_code=503)
    if response is None and request.url.path != '/api/health' and not request.url.path.startswith('/assets/'):
        try:
            await blocking(protection.check, request)
        except HTTPError as error:
            response = JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=error.headers)
    if response is None and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != PUBLIC_URL:
            await blocking(security_monitor.record, request, 'origin_rejected', outcome='blocked')
            response = JSONResponse({'detail': 'Origin rejected'}, status_code=403)
        elif request.headers.get('sec-fetch-site') == 'cross-site':
            await blocking(security_monitor.record, request, 'origin_rejected', outcome='blocked')
            response = JSONResponse({'detail': 'Cross-site request rejected'}, status_code=403)
    if response is None:
        try:
            if admin_access.protected(request): await blocking(admin_access.require, request)
        except HTTPError as error:
            response = JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=error.headers)
    if response is None:
        response = await call_next(request)
    await blocking(audit_http_result, request, response)
    if response.is_streamed:
        response.response = security_policy.guard_stream(request, response.response)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive, nosnippet, noimageindex'
    response.headers['Permissions-Policy'] = 'camera=(self), microphone=(self), display-capture=(self), speaker-selection=(self), geolocation=()'
    from app.livekit import csp_sources
    response.headers.setdefault('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; media-src 'self' https: blob:; font-src 'self' data:; connect-src 'self'"+csp_sources()+"; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if not getattr(response,'public_static_asset',False):response.headers['Cache-Control'] = 'no-store'
    if SECURE: response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response

rate = protection.rate

def login_name(s):
    s=s.strip().lower()
    if not re.fullmatch(r'[a-z0-9_.-]{3,40}',s): fail(422,localized_message('username340LatinCharactersNumbersPeriodsHyphensOr'))
    return s

def check_password(password, hashed):
    try: return ph.verify(hashed,password)
    except (VerificationError,InvalidHashError): return False

# A dummy Argon2 hash equalizes failures for nonexistent logins.
DUMMY_HASH=ph.hash(secrets.token_urlsafe(24))
session = tokens.session
issue = tokens.issue

def ready(request):
    s=session(request)
    if s['stage'] != 'full': fail(403,localized_message('setUpTwoFactorAuthenticationFirst'))
    return s

def owner(request):
    s=ready(request)
    if s['role'] != 'owner': fail(403,localized_message('onlyTheSiteOwnerCanAccessThis'))
    return s

def administrator(request):
    s=ready(request)
    if s['role'] not in ('owner','admin'): fail(403,localized_message('availableToWebsiteAdministrator'))
    if s['role']=='admin' and not s['totp']: fail(403,localized_message('enable2FaToManageTheWebsite'))
    return s

from app.site_roles import LABELS as ROLE_LABELS
from app.admin_access import AdminAccess
admin_access = AdminAccess(db, administrator, fail, protection, check_password, lambda: now(), SECURE)
app.include_router(admin_access.router())

def confirm_security_action(request, password, code):
    s = admin_access.proof(request)
    protection.check(request, 'security-confirm', s['user_id'])
    protection.rate(request, 'security-confirm', 15, subject=s['user_id'])
    admin_access.verify(request, s, password)
    if not s['totp']:
        fail(403, localized_message('enableAdministrator2FaToManageSecurity'))
    if not consume_totp(s['user_id'], code):
        protection.failure(request, 'security-confirm', s['user_id'])
        fail(403, localized_message('invalidOrAlreadyUsed2FaCodeUseThe'))
    with db() as c:
        tokens.require_trusted(c, s)
    protection.success(request, 'security-confirm', s['user_id'])
    return s

from app.modules.admin_security import router as security_router
app.include_router(security_router(security_monitor, admin_access, tokens, protection, confirm_security_action))

def audit_http_result(request, response):
    """Record semantic actions and rejects, without request bodies or secrets."""
    path, status = request.url.path, response.status_code
    tokens.flush_activity(request)
    if getattr(request, '_security_csrf_rejected', False):
        security_monitor.record(request, 'csrf_rejected', outcome='blocked', details={'status': status})
    if path == '/api/health' or path.startswith('/api/admin/security') and status < 400:
        return
    if status in (401, 403, 404, 413, 422, 429) and not getattr(request, '_security_recorded', False):
        if status == 404:
            if not re.search(r'(?i)(?:/\.env(?:[./]|$)|/\.git(?:/|$)|/wp-admin(?:/|$)|/wp-login\.php|/phpmyadmin(?:/|$)|/etc/passwd|/\.\.(?:/|$))', path):
                return
            event = 'scan_detected'
        else:
            event = {401: 'session_rejected', 403: 'access_denied', 413: 'request_too_large',
                     422: 'invalid_request', 429: 'rate_limited'}[status]
        security_monitor.record(request, event, outcome='blocked', details={'status': status})
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and 200 <= status < 300:
        sensitive = (path.startswith('/api/admin/') or path in ('/api/invites', '/api/password-reset',
                     '/api/security/password', '/api/security/2fa/disable', '/api/2fa/activate')
                     or path.startswith('/api/security/passkeys') or path.startswith('/api/security/sessions'))
        if sensitive and path not in ('/api/admin/access/activity', '/api/security/passkeys/options'):
            security_monitor.record(request, 'admin_action' if path.startswith('/api/admin/') or path == '/api/invites'
                                    else 'account_security_changed', outcome='success',
                                    severity='high' if path.endswith(('/disable', '/password', '/restore', '/reset-2fa', '/password-reset')) else 'info')

from app.notifications import create_notifications_router
app.include_router(create_notifications_router(db,ready,tokens,rate,storage_gate,lambda:now(),fail,PUBLIC_URL,coordinator=process_coordinator))
app.include_router(web_push.router(ready,rate,fail))
app.include_router(tokens.router(ready,rate))
app.include_router(protection.router(admin_access.require,fail))

@app.get('/api/account/storage')
def account_storage(request:Request):
    from app.quota import summary
    s=ready(request)
    with db() as c:return summary(c,s['user_id'])

@app.get('/api/status')
def status():
    with db() as c: exists=c.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None
    from app.modules.registration import registration_open
    with db() as c:opened=registration_open(c)
    return {'configured':exists,'registration_open':exists and opened}

@app.post('/api/setup')
def setup(data: Setup, request: Request, response: Response):
    protection.check(request,'setup'); rate(request,'setup',20)
    expected=os.getenv('SETUP_TOKEN','')
    if not expected or not hmac.compare_digest(expected.encode(),data.token.encode()):
        protection.failure(request,'setup'); fail(403,localized_message('invalidPrimarySetupKey'))
    login=login_name(data.login)
    hashed=ph.hash(data.password)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM users LIMIT 1').fetchone(): fail(409,localized_message('primarySetupHasAlreadyBeenCompleted'))
        uid=secrets.token_hex(16)
        c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',(uid,login,hashed,'owner',json.dumps({'_password_hint':data.password_hint.strip(),'profile':default_profile(),'appearance':new_account_appearance()}),now()))
        ensure_default_list(c,uid,now())
    protection.success(request,'setup')
    return {'csrf':issue(response,uid,True),'needs_enrollment':True}

@app.post('/api/login')
def login(data: Credentials, request: Request, response: Response):
    target=data.login.strip().lower()
    protection.check(request,'login',target); rate(request,'login-ip',60)
    with db() as c: u=c.execute('SELECT * FROM users WHERE login=?',(data.login.strip().lower(),)).fetchone()
    valid=check_password(data.password,u['password'] if u else DUMMY_HASH)
    if not u or not valid:
        protection.failure(request,'login',target)
        ip=protection.ip(request)
        with db() as c:attempts=c.execute('SELECT COUNT(*) FROM auth_failures WHERE key=? AND stamp>?',(protection.key(ip,'login',target),now()-protection.window)).fetchone()[0]
        if attempts>=3:
            grant=tokens.encode('password-hint',digest(target),now()+300,login=target,ip=digest(ip))
            response=JSONResponse({'detail':'Неверный логин или пароль.','hint_available':True},status_code=401)
            response.set_cookie('drg_hint',grant,max_age=300,secure=SECURE,httponly=True,samesite='strict',path='/api/password-hint')
            return response
        fail(401,localized_message('incorrectUsernameOrPassword'))
    protection.success(request,'login',target)
    disabled=u['role']!='admin' and json.loads(u['settings']).get('_two_factor_disabled') is True
    return {'csrf':issue(response,u['id'],not bool(u['totp']),request=request),'needs_2fa':bool(u['totp']),'needs_enrollment':not bool(u['totp']) and not disabled}

def consume_totp(uid, code):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not u['totp']: return False
        secret=cipher.decrypt(u['totp'].encode()).decode()
        counter=now()//30
        for k in (counter-1,counter,counter+1):
            if k>u['last_counter'] and hmac.compare_digest(pyotp.TOTP(secret).at(k*30).encode(),code.strip().encode()):
                c.execute('UPDATE users SET last_counter=? WHERE id=?',(k,uid)); return True
        # One-use recovery codes are accepted only on login.
    return False

@app.post('/api/2fa/verify')
def verify(data: Code, request: Request, response: Response):
    s=session(request,verified=False)
    if s['stage']!='pending': fail(409,localized_message('noPendingLoginConfirmation'))
    protection.check(request,'2fa',s['user_id']); rate(request,'2fa',30,subject=s['user_id'])
    valid=consume_totp(s['user_id'],data.code)
    if not valid:
        with db() as c:
            r=c.execute('DELETE FROM recovery_codes WHERE user_id=? AND code=?',(s['user_id'],digest(data.code.strip().upper()))); valid=r.rowcount==1
    if not valid:
        protection.failure(request,'2fa',s['user_id']); fail(401,localized_message('invalidOrAlreadyUsedCode'))
    protection.success(request,'2fa',s['user_id'])
    with db() as c:
        removed=c.execute('DELETE FROM sessions WHERE token=?',(s['token'],)).rowcount
        if not removed: fail(401,localized_message('confirmationSessionHasAlreadyBeenUsed'))
        csrf=issue(response,s['user_id'],True,connection=c,request=request)
    return {'csrf':csrf}

from app.modules.account_security import create_account_security_router
app.include_router(create_account_security_router(db,session,ready,fail,check_password,ph.hash,cipher,lambda:now(),tokens,protection))
from app.secret_vault import VaultService
from app.modules.secret_vault import router as secret_vault_router
secret_vault = VaultService(db,DATA,lambda:now(),ready,fail,ph,check_password,cipher,protection,security_monitor)
app.include_router(secret_vault_router(secret_vault,tokens))

def cleanup_secret_vaults():
    from app.secret_vault import PREFIX
    with db() as c:
        ids=[row[0][len(PREFIX):] for row in c.execute('SELECT key FROM app_settings WHERE key LIKE ?',(PREFIX+'%',))]
    for uid in ids:
        secret_vault.cleanup(uid)
from app.passkeys import create_passkey_router
app.include_router(create_passkey_router(db,ready,tokens,protection,check_password,cipher,lambda:now(),fail,PUBLIC_URL))
from app.native_auth import create_native_auth_router
app.include_router(create_native_auth_router(db,tokens,protection,lambda:now(),fail,PUBLIC_URL))

@app.get('/api/me')
def me(request: Request):
    from app.people_names import names
    from app.modules.appearance import preferences as appearance_preferences
    s=session(request,verified=False)
    settings={k:v for k,v in json.loads(s['settings']).items() if not k.startswith('_')}
    settings['appearance']=appearance_preferences(settings)
    with db() as c:
        flag=c.execute("SELECT value FROM federation_metadata WHERE key='enabled'").fetchone()
        federation_enabled=bool(flag and flag[0]=='true' and federation_service.identity)
    return {'federation_enabled':federation_enabled,'id':s['user_id'],'login':s['login'],'role':s['role'],'role_label':ROLE_LABELS[s['role']],'csrf':s['csrf'],'verified':bool(s['verified']),'two_factor':bool(s['totp']),'needs_enrollment':s['stage']=='enrollment','access_expires':s['access_expires'],'app_version':APP_VERSION,'app_release':APP_RELEASE,'account_deletion':s.get('account_deletion') if s['verified'] and s['stage']=='full' else None,'people_names':names(json.loads(s['settings'])) if s['verified'] and s['stage']=='full' else {},'settings':settings}

@app.get('/api/app-update')
def app_update(request: Request):
    administrator(request)
    from app.app_update import check
    return check(DATA,APP_VERSION)

@app.post('/api/logout')
def logout(request: Request,response: Response):
    s=session(request,verified=False)
    with db() as c: c.execute('DELETE FROM sessions WHERE token=?',(s['token'],))
    tokens.clear_cookies(response); return {'ok':True}

@app.post('/api/invites')
def invite(data: Invite,request: Request):
    from app import registration_invites
    s=admin_access.proof(request);rate(request,'create-invite',30,subject=s['user_id']); login=login_name(data.login) if data.login else None; token=secrets.token_urlsafe(32);stamp=now();exp=stamp+registration_invites.SECONDS
    with db() as c:
        from app.site_roles import current_manager
        c.execute('BEGIN IMMEDIATE');current_manager(c,s,now(),fail)
        if login and c.execute('SELECT 1 FROM users WHERE login=?',(login,)).fetchone():fail(409,localized_message('thisUsernameAlreadyExists'))
        c.execute('INSERT INTO invites VALUES(?,?,?,0)',(digest(token),login,exp))
        value=registration_invites.remember(c,token,s['user_id'],login,data.add_creator_as_friend,stamp)
        result=registration_invites.serialize(c,digest(token),value,stamp,PUBLIC_URL)
    return result

@app.get('/api/invites')
def invitations(request: Request):
    from app import registration_invites
    s=admin_access.require(request)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        stamp=now()
        return {'invitations':registration_invites.own(c,s['user_id'],stamp,PUBLIC_URL),'server_time':stamp}

@app.get('/api/invites/{token}')
def describe_invitation(token: str,request: Request):
    from app import registration_invites
    rate(request,'describe-invite',60)
    with db() as c:
        c.execute('BEGIN IMMEDIATE');registration_invites.expire(c,token,now())
    with db() as c:return registration_invites.describe(c,token,now(),fail)

@app.delete('/api/invites/{identity}')
def revoke_invitation(identity: str,request: Request):
    from app import registration_invites
    from app.site_roles import current_manager
    s=admin_access.proof(request)
    with db() as c:
        c.execute('BEGIN IMMEDIATE');current_manager(c,s,now(),fail)
        registration_invites.revoke(c,identity,s['user_id'],now(),fail)
    return {'ok':True}

@app.post('/api/register')
def register(data:Register,request:Request,response:Response):
    from app.modules.registration import registration_open
    protection.check(request,'register');rate(request,'register',20);login=login_name(data.login);hashed=ph.hash(data.password)
    if data.invite:
        from app.registration_invites import expire
        with db() as c:
            c.execute('BEGIN IMMEDIATE');expire(c,data.invite,now())
    try:
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM users LIMIT 1').fetchone():fail(403,localized_message('theAdministratorMustFirstConfigureTheSite'))
            if data.invite:
                inv=c.execute('SELECT * FROM invites WHERE token=? AND used=0 AND expires>?',(digest(data.invite),now())).fetchone()
                if not inv or (inv['login'] and inv['login']!=login):fail(403,localized_message('invalidInvitationOrIncorrectLogin'))
            elif not registration_open(c):fail(403,localized_message('registrationIsClosedGetAnInvitationFromThe'))
            if c.execute('SELECT 1 FROM users WHERE login=?',(login,)).fetchone():fail(409,localized_message('theLoginIsAlreadyInUse'))
            uid=secrets.token_hex(16)
            c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',(uid,login,hashed,'member',json.dumps({'_password_hint':data.password_hint.strip(),'profile':default_profile(),'appearance':new_account_appearance()}),now()))
            ensure_default_list(c,uid,now())
            if data.invite:
                from app.workspace_lifecycle import read,INVITE_PREFIX,join
                if read(c,INVITE_PREFIX+digest(data.invite)):join(c,data.invite,uid,now(),fail)
                else:
                    from app.registration_invites import consume
                    consume(c,data.invite,uid,now())
            csrf=issue(response,uid,True,connection=c)
    except HTTPError as error:
        if error.status_code==403:protection.failure(request,'register')
        raise
    protection.success(request,'register')
    return {'csrf':csrf,'needs_enrollment':True}


@app.get('/api/users')
def users(request: Request):
    owner(request)
    with db() as c: return [dict(r) for r in c.execute('SELECT login,role FROM users ORDER BY login')]

from app.modules.family_photos import FamilyPhotos
family_photos = FamilyPhotos(db, ready, owner, fail, check_password, protection, admin_access=admin_access, coordinator=process_coordinator)
web_push.family_photos=family_photos
app.include_router(family_photos.router)
from app.modules.localization import create_localization_router
app.include_router(create_localization_router(db,ready,fail))
from app.modules.preferences import create_module_services
modules_router, module_guard = create_module_services(db, ready, fail, integration=family_photos)
app.include_router(modules_router)
notes_ready = module_guard('notes')
from app.modules.notes import create_notes_router
notes_router, owned_note = create_notes_router(db, notes_ready, lambda:now(), fail,DATA,lambda *event:realtime_router.publication_update(*event))
app.include_router(notes_router)
from app.modules.note_members import create_note_members_router
app.include_router(create_note_members_router(db,notes_ready,rate,lambda:now(),fail))
from app.modules.note_links import create_note_links_router
app.include_router(create_note_links_router(notes_ready,rate,lambda:now()))
from app.modules.notion import create_notion_router
app.include_router(create_notion_router(db,notes_ready,lambda:now(),fail))
app.include_router(create_social_router(db,ready,lambda:now(),fail,rate))
from app.modules.realtime import create_realtime_router
realtime_router=create_realtime_router(db,ready,module_guard,tokens,protection,storage_gate,lambda:now(),fail,PUBLIC_URL,coordinator=process_coordinator)
app.include_router(realtime_router)
from app.modules.note_blocks import create_note_blocks_router
app.include_router(create_note_blocks_router(db,notes_ready,owned_note,lambda:now(),fail,realtime_router.publication_update))
from app.modules.calls import create_calls_router
from app.federation.calls import federated_busy
app.include_router(create_calls_router(db,module_guard('chat'),rate,lambda:now(),fail,federation_busy=federated_busy))
from app.modules.guest_calls import create_guest_calls_router
app.include_router(create_guest_calls_router(db,module_guard('chat'),rate,lambda:now(),fail,PUBLIC_URL,federation_busy=federated_busy))
from app.modules.chat_attachments import create_chat_attachments_router
app.include_router(create_chat_attachments_router(db,module_guard('chat'),protection,lambda:now(),fail,DATA))
from app.modules.dashboard import create_dashboard_router
from app.modules.tasks import create_tasks_router, ensure_default_list
from app.modules.weather import create_weather_router
app.include_router(create_dashboard_router(db,ready,fail))
app.include_router(create_tasks_router(db,module_guard('tasks'),lambda:now(),fail))
from app.modules.tracker import create_tracker_router
app.include_router(create_tracker_router(db,module_guard('tracker'),lambda:now(),fail,rate,DATA))
app.include_router(create_weather_router(db,module_guard('weather'),rate,lambda:now(),fail))
from app.production_calendar import ProductionCalendar
from app.modules.calendar import create_calendar_router
production_calendar=ProductionCalendar(DATA,lambda:now(),cipher=data_cipher)
app.include_router(create_calendar_router(production_calendar,module_guard('calendar'),fail))

from app.modules.appearance import create_appearance_router
from app.modules.rss import create_rss_router
app.include_router(create_appearance_router(db,ready,fail))
app.include_router(create_rss_router(db,module_guard('rss'),rate,lambda:now(),fail))
from app.modules.note_assets import create_note_assets_router
app.include_router(create_note_assets_router(db,notes_ready,tokens,rate,lambda:now(),fail,DATA))
from app.modules.note_files import create_note_files_router
app.include_router(create_note_files_router(db,notes_ready,rate,lambda:now(),fail,DATA))
from app.modules.site_icons import create_site_icons_router
app.include_router(create_site_icons_router(db,module_guard('bookmarks'),rate,lambda:now(),fail))
from app.modules.profile import create_profile_router
app.include_router(create_profile_router(db,ready,rate,lambda:now(),fail,DATA))
from app.modules.files import create_files_router
files_router=create_files_router(db,ready,module_guard('files'),tokens,protection,lambda:now(),fail,DATA,PUBLIC_URL,ph)
app.include_router(files_router)
from app.modules.media import create_media_router
app.include_router(create_media_router(db,module_guard('media'),rate,lambda:now(),fail,DATA))

from app.modules.admin import create_admin_router
admin_router=create_admin_router(db,administrator,fail,check_password,ph.hash,protection,lambda:now(),tokens,DATA,cipher,base64.urlsafe_b64encode(hashlib.sha256(APP_SECRET.encode()).digest()).decode(),storage_gate,admin_access)
app.include_router(admin_router)
from app.modules.account_lifecycle import create_account_lifecycle_router
app.include_router(create_account_lifecycle_router(db,ready,check_password,lambda:now(),fail,storage_gate,protection))
from app.modules.personal_data import create_personal_data_router
app.include_router(create_personal_data_router(db,ready,fail,check_password,protection,lambda:now(),DATA,PUBLIC_URL,storage_gate,cipher))
from app.modules.notion_import import create_notion_import_router
app.include_router(create_notion_import_router(db,ready,rate,lambda:now(),fail,DATA,PUBLIC_URL,storage_gate))
app.include_router(create_notion_import_router(db,ready,rate,lambda:now(),fail,DATA,PUBLIC_URL,storage_gate,source="outline"))
from app.notion_sync.config import NotionConfig
from app.notion_sync.service import SyncService
from app.notion_sync.access import prepare as prepare_notion_access
from app.modules.notion_sync import create_notion_sync_router
notion_sync_service=SyncService(db,NotionConfig.from_env(PUBLIC_URL),DATA,PUBLIC_URL,clock=lambda:now())
prepare_notion_access(db,notion_sync_service.config.configured,notion_sync_service.config.write_configured)
app.include_router(create_notion_sync_router(notion_sync_service,notes_ready,rate,fail,administrator))

def expiry(duration,unit):
    if duration is None: return None
    units={'minutes':60,'hours':3600,'days':86400,'weeks':604800}
    if unit in units: return now()+duration*units[unit]
    if unit not in ('months','years'): fail(422,localized_message('unknownDateUnit2'))
    dt=datetime.fromtimestamp(now(),timezone.utc); months=duration*(12 if unit=='years' else 1); total=dt.year*12+dt.month-1+months
    year,month=divmod(total,12); month+=1
    if year>9999: fail(422,localized_message('tooLongAPeriod'))
    return int(dt.replace(year=year,month=month,day=min(dt.day,calendar.monthrange(year,month)[1])).timestamp())

@app.post('/api/notes/{nid}/shares')
def create_share(nid:str,data: Share,request: Request):
    s=notes_ready(request)
    if data.mode not in ('public','password','authenticated','users'): fail(422,localized_message('unknownAccessMode'))
    hashed=None; allowed=[]
    if data.mode=='password':
        if not data.password or len(data.password)<8: fail(422,localized_message('linkPasswordMinimum8Characters'))
        hashed=ph.hash(data.password)
    if data.mode=='users':
        allowed=list(set(login_name(x) for x in data.allowed))
        if not allowed: fail(422,localized_message('specifyAtLeastOneUsername'))
    exp=expiry(data.duration,data.unit); token=secrets.token_urlsafe(24)
    with db() as c:
        n=publication_access(c,nid,s['user_id'],fail)
        if data.permission=='edit':
            from app.notion_sync.access import require_writable
            require_writable(c,n,fail)
        if data.permission=='edit' and c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(nid,)).fetchone():fail(422,localized_message('theDatabaseIsOnlyReadOnly'))
        for name in allowed:
            if not c.execute('SELECT 1 FROM users WHERE login=?',(name,)).fetchone(): fail(422,localized_message('theLoginIsNotRegisteredSendAnInvitation', value0=f'{name}'))
        c.execute('INSERT INTO shares(token,note_id,mode,password,allowed,expires,revoked,created,permission) VALUES(?,?,?,?,?,?,0,?,?)',(token,nid,data.mode,hashed,json.dumps(allowed),exp,now(),data.permission))
        from app.workspace_lifecycle import audit
        if n['space_id']:audit(c,n['space_id'],s['user_id'],'publication_created',{'note_id':nid},now())
    return {'token':token,'url':PUBLIC_URL+'/share/'+token,'expires':exp,'permission':data.permission}

@app.get('/api/notes/{nid}/shares')
def list_shares(nid:str,request: Request):
    s=notes_ready(request)
    with db() as c:
        publication_access(c,nid,s['user_id'],fail)
        return [dict(r)|{'url':PUBLIC_URL+'/share/'+r['token']} for r in c.execute('SELECT token,mode,allowed,expires,revoked,created,permission FROM shares WHERE note_id=? ORDER BY created DESC',(nid,))]

@app.delete('/api/shares/{token}')
def revoke_share(token:str,request: Request):
    s=ready(request)
    with db() as c:
        share=c.execute('SELECT * FROM shares WHERE token=?',(token,)).fetchone()
        if not share: fail(404,localized_message('linkNotFound'))
        n=publication_access(c,share['note_id'],s['user_id'],fail)
        from app.workspace_lifecycle import audit
        if n['space_id']:audit(c,n['space_id'],s['user_id'],'publication_revoked',{'note_id':n['id']},now())
        c.execute('UPDATE shares SET revoked=1 WHERE token=?',(token,))
    return {'ok':True}

class SharePermission(BaseModel):
    model_config=ConfigDict(extra='forbid')
    permission:Literal['view','edit']

@app.patch('/api/shares/{token}')
def share_permission(token:str,data:SharePermission,request:Request):
    s=ready(request)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        sh=c.execute('SELECT * FROM shares WHERE token=?',(token,)).fetchone()
        if not sh:fail(404,localized_message('linkNotFound'))
        n=publication_access(c,sh['note_id'],s['user_id'],fail)
        if data.permission=='edit':
            from app.notion_sync.access import require_writable
            require_writable(c,n,fail)
        if data.permission=='edit' and c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(sh['note_id'],)).fetchone():fail(422,localized_message('theDatabaseIsOnlyReadOnly'))
        c.execute('UPDATE shares SET permission=? WHERE token=?',(data.permission,token))
    return {'ok':True,'permission':data.permission}

def active_share(c,token):
    sh=c.execute('SELECT * FROM shares WHERE token=?',(token,)).fetchone()
    from app.workspace_lifecycle import pending
    page=c.execute('SELECT space_id FROM notes WHERE id=?',(sh['note_id'],)).fetchone() if sh else None
    if page and page[0] and pending(c,page[0]):fail(404,localized_message('spaceIsWaitingToBeDeleted'))
    if not sh or sh['revoked'] or (sh['expires'] is not None and sh['expires']<=now()): fail(404,localized_message('theLinkDoesNotExistHasBeenRevoked'))
    return sh

@app.post('/api/shared/{token}/unlock')
def unlock(token:str,data: Password,request: Request,response: Response):
    protection.check(request,'share-password',token); rate(request,'share-password',30,subject=token)
    with db() as c:
        sh=active_share(c,token)
        valid=sh['mode']=='password' and check_password(data.password,sh['password'])
    if not valid:
        protection.failure(request,'share-password',token); fail(403,localized_message('incorrectPassword'))
    protection.success(request,'share-password',token)
    exp=min(now()+3600,sh['expires'] or now()+3600)
    grant=tokens.encode('share',token,exp)
    claims=tokens.decode(grant,'share')
    with db() as c:
        c.execute('DELETE FROM share_sessions WHERE expires<=?',(now(),))
        c.execute('INSERT INTO share_sessions VALUES(?,?,?)',(digest(claims['jti']),token,exp))
    response.set_cookie('drg_share_'+token,grant,max_age=exp-now(),secure=SECURE,httponly=True,samesite='lax',path='/api/shared/'+token)
    return {'ok':True}

def shared_access(c,token,request):
    sh=active_share(c,token)
    if sh['mode'] in ('authenticated','users'):
        s=ready(request)
        if sh['mode']=='users' and s['login'] not in json.loads(sh['allowed']): fail(403,localized_message('thisAccountDoesNotHaveAccess'))
    if sh['mode']=='password':
        try:claims=tokens.decode(request.cookies.get('drg_share_'+token,''),'share')
        except HTTPError:fail(423,localized_message('aLinkPasswordIsRequiredToView'))
        if claims['sub']!=token or not c.execute('SELECT 1 FROM share_sessions WHERE token=? AND share_token=? AND expires>?',(digest(claims['jti']),token,now())).fetchone():
            fail(423,localized_message('aLinkPasswordIsRequiredToView'))
    return sh

def shared_payload(n,sh,c=None):
    result={k:n[k] for k in ('title','body','updated','blocks','icon','cover','full_width','version')}
    if c is not None:
        from app.modules.note_assets import asset_payload
        result.update(asset_payload(c,n,'/api/shared/'+sh['token']+'/assets'))
    if c is not None and n['id']==sh['note_id']:
        from app.database_publication import database_payload
        database=database_payload(c,n,sh)
        if database is not None:result.update(database=database,kind='database')
    result['id']=n['id']
    result['blocks']=json.loads(result['blocks']) if result['blocks'] is not None else None
    from app.account_lifecycle import pending as pending_account
    waiting=c is not None and not n['space_id'] and bool(pending_account(c,n['user_id']))
    from app.notion_sync.access import readonly as notion_readonly
    source_locked=c is not None and notion_readonly(c,n)
    result.update(expires=sh['expires'],mode=sh['mode'],permission='view' if n['locked'] or waiting or source_locked or 'database' in result else sh['permission'],locked=bool(n['locked']))
    if sh['permission']=='edit' and not n['locked'] and not waiting and not source_locked and 'database' not in result:
        result['edit_token']=tokens.encode('share-edit',sh['token'],min(now()+900,sh['expires'] or now()+900))
    return result

@app.get('/api/shared/{token}')
def shared(token:str,request: Request):
    from app.database_publication import publication_page
    with db() as c:
        sh=shared_access(c,token,request)
        n=publication_page(c,sh,fail,request.args.get('record'))
        if not n:fail(404,localized_message('pageIsUnavailable'))
        return shared_payload(n,sh,c)

@app.get('/api/shared/{token}/assets/{aid}')
def shared_asset(token:str,aid:str,request:Request):
    from app.database_publication import publication_page
    from app.data_crypto import media_store
    from app.modules.note_assets import asset_id
    with db() as c:
        sh=shared_access(c,token,request);n=publication_page(c,sh,fail,request.args.get('record'))
        if aid not in (asset_id(n['icon']),asset_id(n['cover'])):
            asset=c.execute('SELECT a.note_id FROM note_assets a JOIN database_records r ON r.note_id=a.note_id WHERE a.id=? AND r.database_id=?',(aid,n['id'])).fetchone()
            if not asset:fail(404,localized_message('theImageDoesNotBelongToThePublication'))
            n=publication_page(c,sh,fail,asset['note_id'])
            if aid not in (asset_id(n['icon']),asset_id(n['cover'])):fail(404,localized_message('theImageDoesNotBelongToThePublication'))
        row=c.execute('SELECT m.id,m.mime FROM media m JOIN note_assets a ON a.id=m.id WHERE a.id=? AND a.note_id=?',(aid,n['id'])).fetchone()
        if not row:fail(404,localized_message('imageNotFound'))
    return Response(media_store(db,DATA).read(row['id']),media_type=row['mime'])

@app.get('/api/shared/{token}/files/{fid}')
def shared_file(token:str,fid:str,request:Request):
    from app.database_publication import publication_page
    from app.modules.note_files import page_file,file_response
    from app.file_store import file_store
    with db() as c:
        sh=shared_access(c,token,request)
        n=publication_page(c,sh,fail,request.args.get('record'))
        if not n:fail(404,localized_message('pageIsUnavailable'))
        row=page_file(c,n,fid,fail,referenced=True)
    def authorize():
        with db() as c:
            sh=shared_access(c,token,request)
            n=publication_page(c,sh,fail,request.args.get('record'))
            if not n:fail(404,localized_message('pageIsUnavailable'))
            page_file(c,n,fid,fail,referenced=True)
    return file_response(file_store(db,DATA),row,request,authorize)

class SharedEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=200)
    body:str=Field(max_length=200000)
    blocks:list[dict]=Field(max_length=2000)
    version:int=Field(ge=1)

@app.put('/api/shared/{token}')
async def edit_shared(token:str,data:SharedEdit,request:Request):
    from app.modules.notes import validate_blocks
    from app.share_editing import replace_content
    from app.page_links import page_links
    from app.collaboration import load_doc
    event=None
    rate(request,'share-edit',120,60,subject=token)
    with db() as c:
        c.execute('BEGIN IMMEDIATE');sh=shared_access(c,token,request)
        if sh['permission']!='edit':fail(403,localized_message('thisLinkAllowsOnlyReading'))
        try:grant=tokens.decode(request.headers.get('x-share-edit-token',''),'share-edit')
        except HTTPError:fail(403,localized_message('clickTheLinkToConfirmTheEdit'))
        if grant['sub']!=token:fail(403,localized_message('theConfirmationAppliesToADifferentLink'))
        n=c.execute('SELECT * FROM notes WHERE id=? AND deleted IS NULL',(sh['note_id'],)).fetchone()
        if not n:fail(404,localized_message('pageIsUnavailable'))
        from app.notion_sync.access import require_writable
        require_writable(c,n,fail)
        from app.account_lifecycle import pending as pending_account
        if not n['space_id'] and pending_account(c,n['user_id']):fail(423,localized_message('thePageOwnerIsWaitingForDeletionEditing'))
        if n['locked']:fail(423,localized_message('thePageIsLockedByTheAuthor'))
        if n['version']!=data.version:fail(409,localized_message('thePageHasBeenModifiedYourTextIs'))
        if c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(n['id'],)).fetchone():fail(422,localized_message('accessToTheDatabaseIsRequiredToEdit'))
        if not data.title.strip():fail(422,localized_message('enterThePageName'))
        validate_blocks(data.blocks,fail)
        from app.personal_data import NOTE_FILE_LINK
        file_refs=lambda value:{match[2] for match in NOTE_FILE_LINK.finditer(value)}
        if not file_refs(json.dumps(data.blocks)+data.body).issubset(file_refs((n['blocks'] or '')+n['body'])):
            fail(422,localized_message('youCanOnlyUseFilesAlreadyAttachedTo'))
        if not page_links(data.blocks).issubset(page_links(json.loads(n['blocks']) if n['blocks'] else None)):
            fail(422,localized_message('youCanModifyThisPageViaThisLink'))
        # A publication editor may edit this document, but does not acquire
        # authority to trash its separately protected child pages.
        removed=page_links(json.loads(n['blocks'] or '[]'))-page_links(data.blocks)
        if any(c.execute('SELECT 1 FROM notes WHERE id=? AND parent_id=? AND deleted IS NULL',(identity,n['id'])).fetchone() for identity in removed):
            fail(422,localized_message('subpagesAreModifiedInThePersonalWorkspaceNot'))
        title=data.title.strip();body=data.body;blocks=data.blocks
        from app.federation.notes import collaborative
        if collaborative(c,n):
            saved=c.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(n['id'],)).fetchone()
            doc=load_doc(saved['state'] if saved else None);before=doc.get_state()
            try:title,body,blocks=replace_content(doc,title,blocks,fail)
            except (ValueError,TypeError,KeyError,AttributeError,OverflowError):fail(422,localized_message('invalidPageContent'))
            state=doc.get_update();update=doc.get_update(before);epoch=saved['epoch'] if saved else secrets.token_urlsafe(16)
            c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET state=excluded.state,updated=excluded.updated',(n['id'],state,epoch,now()))
            event=(n['id'],state,update,epoch,n['version']+1,not bool(saved))
        from app.note_history import snapshot
        snapshot(c,n,now())
        c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',(title,body,json.dumps(blocks,ensure_ascii=False),now(),n['id']))
        result=shared_payload(c.execute('SELECT * FROM notes WHERE id=?',(n['id'],)).fetchone(),sh,c)
    if event:await realtime_router.publication_update(*event)
    return result

@app.get('/api/health')
def health(): return {'ok':True}

from app.modules.expenses import create_expenses_router
expenses_router=create_expenses_router(db,module_guard("expenses"),lambda:now(),fail,expiry,PUBLIC_URL,protection,DATA)
app.include_router(expenses_router)
from app.modules.registration import create_registration_router
app.include_router(create_registration_router(db,session,owner,tokens,protection,check_password,ph.hash,cipher,lambda:now(),fail,PUBLIC_URL,admin_access))
from app.modules.authenticator import create_authenticator_router
app.include_router(create_authenticator_router(db,module_guard('authenticator'),cipher,protection,lambda:now(),fail))
from app.note_locks import create_lock_router
app.include_router(create_lock_router(db,notes_ready,lambda:now(),fail))

STATIC=Path(__file__).resolve().parent/'static'
if STATIC.exists():
    from app.static_assets import StaticAssets
    packaged_assets=StaticAssets(STATIC,cache=DATA/'public-builds')
    @app.get('/native/passkey-create')
    def native_passkey_create():
        return FileResponse(STATIC/'native-passkey-create.html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.get('/native/passkey')
    def native_passkey():
        return FileResponse(STATIC/'native-passkey.html',headers={'Cache-Control':'no-store'})
    @app.get('/push-sw.js')
    def push_worker():
        return FileResponse(STATIC/'push-sw.js',media_type='text/javascript',headers={'Service-Worker-Allowed':'/'})
    @app.get('/assets/{asset:path}')
    def static_asset(asset:str,request:Request):
        response=packaged_assets.response(asset,request)
        if response is None:fail(404,localized_message('fileNotFound'))
        return response
    @app.get('/')
    @app.get('/f/{token}')
    @app.get('/page/{token}')
    @app.get('/page/{token}/history')
    @app.get('/share/{token}')
    @app.get('/invite/{token}')
    @app.get('/reset-password/{token}')
    @app.get('/expense-share/{token}')
    @app.get('/call/{token}')
    def index(token:str=''): return FileResponse(STATIC/'index.html')

import sys
from app.federation.runtime import FederationRuntime
federation_runtime = FederationRuntime(sys.modules[__name__])
federation_service = federation_runtime.service
