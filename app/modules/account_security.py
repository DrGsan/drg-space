"""Account-owned security controls and staged, acknowledged TOTP activation."""
from app.i18n import message as localized_message
import hashlib,hmac,json,secrets
from typing import Literal
import pyotp
from app.http import Router,Request,Response
from pydantic import BaseModel,ConfigDict,Field
from app.passwords import NewPassword,clear_enrollment

class PasswordProof(BaseModel):
    model_config=ConfigDict(extra='forbid')
    password:str=Field(min_length=1,max_length=256)
class FactorProof(PasswordProof):
    code:str=Field(default='',max_length=100)
class ChangePassword(FactorProof):
    new_password:NewPassword
    password_hint:str=Field(default="",max_length=200)
class Code(BaseModel):
    code:str=Field(min_length=1,max_length=100)
class Activate(BaseModel):
    saved_recovery_codes:Literal[True]

def verify_account_factor(c,user,code,cipher,now):
    if not user['totp']:return True
    secret=cipher.decrypt(user['totp'].encode()).decode();counter=now()//30
    match=next((k for k in (counter-1,counter,counter+1) if k>user['last_counter'] and hmac.compare_digest(pyotp.TOTP(secret).at(k*30).encode(),code.strip().encode())),None)
    if match is not None:
        c.execute('UPDATE users SET last_counter=? WHERE id=?',(match,user['id']));return True
    return c.execute('DELETE FROM recovery_codes WHERE user_id=? AND code=?',(user['id'],hashlib.sha256(code.strip().upper().encode()).hexdigest())).rowcount==1

def create_account_security_router(db,session,ready,fail,check_password,hash_password,cipher,now,tokens,protection):
    router=Router()
    def digest(value):return hashlib.sha256(value.encode()).hexdigest()
    def attempt(request,action,s):
        protection.check(request,action,s['user_id']);protection.rate(request,action,30,subject=s['user_id'])
    def reject(request,action,s,message,status=401):
        protection.failure(request,action,s['user_id']);fail(status,message)
    def current(c,s):
        if not c.execute('SELECT 1 FROM sessions WHERE token=? AND expires>?',(s['token'],now())).fetchone():fail(401,localized_message('sessionHasBeenRevokedOrExpired'))
    def factor(c,user,code):return verify_account_factor(c,user,code,cipher,now)

    @router.post('/api/2fa/enroll')
    def enroll(data:PasswordProof,request:Request):
        s=session(request);attempt(request,'enroll',s)
        valid=False
        with db() as c:
            c.execute('BEGIN IMMEDIATE');current(c,s);user=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
            if user['totp']:fail(409,localized_message('label2faIsAlreadyEnabled'))
            valid=check_password(data.password,user['password'])
            if valid:
                settings=json.loads(user['settings']);secret=pyotp.random_base32()
                previous=settings.get('_previous_totp_hash')
                if settings.get('_pending_totp'):previous_pending=digest(cipher.decrypt(settings['_pending_totp'].encode()).decode())
                else:previous_pending=None
                while digest(secret) in (previous,previous_pending):secret=pyotp.random_base32()
                clear_enrollment(settings)
                settings.update(_pending_totp=cipher.encrypt(secret.encode()).decode(),_pending_until=now()+600,_pending_session=s['token'])
                c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),user['id']))
        if not valid:reject(request,'enroll',s,'Неверный пароль.')
        protection.success(request,'enroll',s['user_id'])
        return {'secret':secret,'uri':pyotp.TOTP(secret).provisioning_uri(name=s['login'],issuer_name='DrG Space')}

    @router.post('/api/2fa/confirm')
    def confirm(data:Code,request:Request):
        s=session(request);attempt(request,'confirm',s);invalid=False
        codes=['-'.join([secrets.token_hex(3),secrets.token_hex(3)]).upper() for _ in range(10)]
        with db() as c:
            c.execute('BEGIN IMMEDIATE');current(c,s);user=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
            if user['totp']:fail(409,localized_message('label2faIsAlreadyEnabled'))
            settings=json.loads(user['settings'])
            if settings.get('_pending_session')!=s['token'] or settings.get('_pending_until',0)<now() or not settings.get('_pending_totp'):fail(400,localized_message('setupExpiredPleaseStartAgainInThisBrowser'))
            secret=cipher.decrypt(settings['_pending_totp'].encode()).decode();counter=now()//30
            match=next((k for k in (counter-1,counter,counter+1) if hmac.compare_digest(pyotp.TOTP(secret).at(k*30).encode(),data.code.strip().encode())),None)
            if match is None:invalid=True
            else:
                settings['_pending_codes']=cipher.encrypt(json.dumps(codes).encode()).decode();settings['_pending_counter']=match
                c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),user['id']))
        if invalid:reject(request,'confirm',s,'Неверный код.')
        protection.success(request,'confirm',s['user_id'])
        return {'recovery_codes':codes,'needs_acknowledgement':True}

    @router.post('/api/2fa/activate')
    def activate(data:Activate,request:Request,response:Response):
        s=session(request);attempt(request,'activate',s)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');current(c,s);user=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone();settings=json.loads(user['settings'])
            if user['totp']:fail(409,localized_message('label2faIsAlreadyEnabled'))
            if settings.get('_pending_session')!=s['token'] or settings.get('_pending_until',0)<now() or not settings.get('_pending_codes'):fail(400,localized_message('firstConfirmTheVerificationCodeInThisBrowser'))
            encrypted=settings['_pending_totp'];counter=settings['_pending_counter'];codes=json.loads(cipher.decrypt(settings['_pending_codes'].encode()))
            clear_enrollment(settings);settings.pop('_two_factor_disabled',None)
            c.execute('UPDATE users SET totp=?,last_counter=?,settings=? WHERE id=?',(encrypted,counter,json.dumps(settings),user['id']))
            c.execute('DELETE FROM recovery_codes WHERE user_id=?',(user['id'],))
            c.executemany('INSERT INTO recovery_codes VALUES(?,?)',[(user['id'],digest(code)) for code in codes])
            tokens.require_trusted(c,s)
            c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
            from app.registration_invites import complete
            friendship=complete(c,s['user_id'],now(),fail)
            csrf=tokens.issue(response,s['user_id'],True,connection=c,created=s['created'])
        return {'ok':True,'csrf':csrf,**({'invitation_friendship':friendship} if friendship else {})}

    @router.post('/api/security/2fa/disable')
    def disable(data:FactorProof,request:Request,response:Response):
        s=ready(request);attempt(request,'disable-2fa',s);valid=False
        with db() as c:
            c.execute('BEGIN IMMEDIATE');current(c,s);user=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
            if user['role']=='admin':fail(403,localized_message('forWebsiteAdministrator2FaIsMandatoryTheOwner'))
            if not user['totp']:fail(409,localized_message('label2faIsAlreadyDisabled'))
            from app.secret_vault import prevent_factor_reset
            prevent_factor_reset(c,user['id'],fail)
            valid=check_password(data.password,user['password']) and factor(c,user,data.code)
            if valid:
                settings=json.loads(user['settings']);clear_enrollment(settings)
                settings['_previous_totp_hash']=digest(cipher.decrypt(user['totp'].encode()).decode());settings['_two_factor_disabled']=True
                c.execute('UPDATE users SET totp=NULL,last_counter=-1,settings=? WHERE id=?',(json.dumps(settings),user['id']))
                tokens.require_trusted(c,s)
                c.execute('DELETE FROM recovery_codes WHERE user_id=?',(user['id'],));c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
                csrf=tokens.issue(response,s['user_id'],True,connection=c,created=s['created'])
        if not valid:reject(request,'disable-2fa',s,'Неверный пароль или код 2FA.',403)
        protection.success(request,'disable-2fa',s['user_id'])
        return {'ok':True,'csrf':csrf}

    @router.put('/api/security/password')
    def password(data:ChangePassword,request:Request,response:Response):
        s=ready(request);attempt(request,'change-password',s)
        # Hash work outside the write transaction; session is rechecked within it.
        hashed=hash_password(data.new_password);valid=False
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM sessions WHERE token=?',(s['token'],)).fetchone():fail(401,localized_message('sessionTerminated'))
            user=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
            valid=check_password(data.password,user['password']) and factor(c,user,data.code)
            if valid:
                if check_password(data.new_password,user['password']):fail(422,localized_message('theNewPasswordMustBeDifferentFromThe'))
                settings=json.loads(user['settings']);clear_enrollment(settings);settings['_password_hint']=data.password_hint.strip()
                c.execute('UPDATE users SET password=?,settings=? WHERE id=?',(hashed,json.dumps(settings),user['id']))
                tokens.require_trusted(c,s)
                c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
                csrf=tokens.issue(response,s['user_id'],True,connection=c,created=s['created'])
        if not valid:reject(request,'change-password',s,'Неверный текущий пароль или код 2FA.',403)
        protection.success(request,'change-password',s['user_id'])
        return {'ok':True,'csrf':csrf}
    return router
