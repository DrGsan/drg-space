"""Explicit registration policy, optional member enrollment and one-use recovery links."""
from app.i18n import message as localized_message
from app.passkeys import clear_passkeys
import hashlib,json,secrets
from app.http import Router,Request,Response
from pydantic import BaseModel,Field,ConfigDict,StrictBool
from app.passwords import NewPassword,clear_enrollment

INVITE_SECONDS=7*86400
RESET_SECONDS=3600

def registration_open(c):
    row=c.execute("SELECT value FROM app_settings WHERE key='registration'").fetchone()
    return bool(row and json.loads(row[0]).get('enabled') is True)

class Policy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:StrictBool
    admin_password:str|None=Field(default=None,min_length=1,max_length=256)
class Proof(BaseModel):
    admin_password:str|None=Field(default=None,min_length=1,max_length=256)
class Reset(BaseModel):
    token:str=Field(min_length=40,max_length=100)
    password:NewPassword
    password_hint:str=Field(default='',max_length=200)

def create_registration_router(db,session,owner,tokens,protection,check_password,hash_password,cipher,now,fail,public_url,admin_access):
    r=Router()
    def proof(request,password):
        return admin_access.proof(request,password)
    @r.get('/api/admin/registration')
    def policy(request:Request):
        admin_access.require(request)
        with db() as c:return {'enabled':registration_open(c),'invite_seconds':INVITE_SECONDS}
    @r.put('/api/admin/registration')
    def change(data:Policy,request:Request):
        proof(request,data.admin_password)
        with db() as c:c.execute("INSERT INTO app_settings VALUES('registration',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps({'enabled':data.enabled}),))
        return {'enabled':data.enabled}
    @r.post('/api/2fa/skip')
    def skip(request:Request,response:Response):
        s=session(request)
        if s['role'] in ('owner','admin'):fail(403,localized_message('initialAdministratorSetupRequires2Fa'))
        if s['stage']!='enrollment' or s['totp']:fail(409,localized_message('thereIsNoSettingThatCanBeSkipped'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');u=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
            settings=json.loads(u['settings']);clear_enrollment(settings);settings['_two_factor_disabled']=True
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
            if not c.execute('DELETE FROM sessions WHERE token=?',(s['token'],)).rowcount:fail(401,localized_message('sessionAlreadyUsed'))
            from app.registration_invites import complete
            friendship=complete(c,s['user_id'],now(),fail)
            csrf=tokens.issue(response,s['user_id'],True,connection=c)
        return {'csrf':csrf,**({'invitation_friendship':friendship} if friendship else {})}
    @r.post('/api/admin/users/{uid}/password-reset')
    def reset_link(uid:str,data:Proof,request:Request):
        s=proof(request,data.admin_password)
        token=secrets.token_urlsafe(32)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            from app.site_roles import managed_user
            managed_user(c,uid,s,now(),fail)
            if uid==s['user_id']:fail(422,localized_message('changeYourPasswordInTheSecuritySection'))
            from app.secret_vault import prevent_factor_reset
            prevent_factor_reset(c,uid,fail)
            c.execute('DELETE FROM password_resets WHERE user_id=? OR expires<=?',(uid,now()))
            c.execute('INSERT INTO password_resets VALUES(?,?,?,?)',(tokens.digest(token),uid,now()+RESET_SECONDS,now()))
        return {'url':public_url+'/reset-password/'+token,'expires':now()+RESET_SECONDS}
    @r.post('/api/password-reset')
    def reset(data:Reset,request:Request,response:Response):
        protection.rate(request,'password-reset',20)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');ticket=c.execute('SELECT * FROM password_resets WHERE token=? AND expires>?',(tokens.digest(data.token),now())).fetchone()
            if not ticket:fail(403,localized_message('linkUsedOrExpired'))
            from app.secret_vault import prevent_factor_reset
            prevent_factor_reset(c,ticket['user_id'],fail)
            clear_passkeys(c,ticket['user_id'])
            u=c.execute('SELECT * FROM users WHERE id=?',(ticket['user_id'],)).fetchone();settings=json.loads(u['settings'])
            if u['totp']:settings['_previous_totp_hash']=hashlib.sha256(cipher.decrypt(u['totp'].encode())).hexdigest()
            clear_enrollment(settings);settings.pop('_two_factor_disabled',None);settings['_password_hint']=data.password_hint.strip()
            c.execute('UPDATE users SET password=?,totp=NULL,last_counter=-1,settings=? WHERE id=?',(hash_password(data.password),json.dumps(settings),u['id']))
            c.execute('DELETE FROM recovery_codes WHERE user_id=?',(u['id'],));c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],));c.execute('DELETE FROM password_resets WHERE user_id=?',(u['id'],))
            csrf=tokens.issue(response,u['id'],True,connection=c)
        return {'csrf':csrf,'needs_enrollment':True}
    @r.get('/api/password-hint')
    def hint(request:Request):
        protection.rate(request,'password-hint',20)
        grant=tokens.decode(request.cookies.get('drg_hint',''),'password-hint');ip=protection.ip(request)
        if grant.get('ip')!=tokens.digest(ip):fail(403,localized_message('requestHelpInTheSameBrowser'))
        target=grant.get('login','')
        with db() as c:
            failures=c.execute('SELECT COUNT(*) FROM auth_failures WHERE key=? AND stamp>?',(protection.key(ip,'login',target),now()-protection.window)).fetchone()[0]
            if failures<3:fail(403,localized_message('hintAvailableAfterThreeIncorrectAttempts'))
            u=c.execute('SELECT settings FROM users WHERE login=?',(target,)).fetchone()
        return {'hint':json.loads(u[0]).get('_password_hint','') if u else ''}
    return r
