"""Discoverable, UV-required WebAuthn credentials in encrypted account metadata.

The existing encrypted key/value store provides a lookup index and expiring,
one-use ceremonies. Credentials stay in their owning user's private settings;
no schema conversion or separate unencrypted security database is required.
"""
from app.i18n import message as localized_message
import base64,hashlib,hmac,json,re,secrets
from urllib.parse import urlsplit
from pydantic import BaseModel,ConfigDict,Field,field_validator
from webauthn import generate_registration_options,verify_registration_response,generate_authentication_options,verify_authentication_response,options_to_json
from webauthn.helpers.structs import AuthenticatorSelectionCriteria,ResidentKeyRequirement,UserVerificationRequirement,PublicKeyCredentialDescriptor
from app.http import Router,Request,Response
from app.modules.account_security import verify_account_factor

PREFIX='passkey:challenge:'
INDEX='passkey:index:'
LIMIT=20
TTL=180

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def encode(value):return base64.urlsafe_b64encode(value).decode().rstrip('=')
def decode(value):
    if not isinstance(value,str) or not 1<=len(value)<=16000 or not re.fullmatch(r'[A-Za-z0-9_-]+',value):raise ValueError('Invalid encoding')
    result=base64.urlsafe_b64decode(value+'='*((-len(value))%4))
    if encode(result)!=value:raise ValueError('Non-canonical encoding')
    return result

def index_key(credential_id):return INDEX+digest(credential_id)
def clear_passkeys(c,uid):
    user=c.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()
    if not user:return
    settings=json.loads(user['settings'])
    for key in settings.pop('_passkeys',[]):c.execute('DELETE FROM app_settings WHERE key=?',(index_key(key['credential_id']),))
    c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))

def validate_passkeys(settings):
    if not isinstance(settings,dict):raise ValueError('Invalid account settings')
    keys=settings.get('_passkeys',[])
    if not isinstance(keys,list) or len(keys)>LIMIT:raise ValueError('Invalid passkeys')
    ids=set();credentials=set()
    for key in keys:
        if not isinstance(key,dict) or not isinstance(key.get('id'),str) or not re.fullmatch(r'[a-f0-9]{32}',key['id']) or key['id'] in ids:raise ValueError('Invalid passkey')
        ids.add(key['id']);decode(key['credential_id']);decode(key['public_key'])
        if key['credential_id'] in credentials:raise ValueError('Duplicate passkey')
        credentials.add(key['credential_id'])
        if type(key['sign_count']) is not int or not 0<=key['sign_count']<=2**32-1 or not isinstance(key['name'],str) or not 1<=len(key['name'])<=80 or not isinstance(key['rp_id'],str):raise ValueError('Invalid passkey')
        if type(key['created']) is not int or key['created']<0 or (key['last_used'] is not None and (type(key['last_used']) is not int or key['last_used']<0)) or type(key['backed_up']) is not bool:raise ValueError('Invalid passkey metadata')
    return keys

class Proof(BaseModel):
    model_config=ConfigDict(extra='forbid')
    password:str=Field(min_length=1,max_length=256)
    code:str=Field(default='',max_length=100)
class Register(Proof):name:str=Field(default='Мой passkey',min_length=1,max_length=80)
class Rename(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=80)
class Credential(BaseModel):
    model_config=ConfigDict(extra='forbid')
    credential:dict
    csrf:str=Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    native_flow:str|None=Field(default=None,pattern=r'^[A-Za-z0-9_-]{43}$')
    @field_validator('credential')
    @classmethod
    def bounded(cls,value):
        if len(json.dumps(value))>65536:raise ValueError('Credential too large')
        return value

def create_passkey_router(db,ready,tokens,protection,check_password,cipher,now,fail,public_url):
    router=Router();origin=public_url.rstrip('/');rp_id=urlsplit(origin).hostname;secure=urlsplit(origin).scheme=='https'
    def same_origin(request):
        if request.headers.get('origin','').rstrip('/')!=origin:fail(403,'Origin rejected')
    def attempt(request,action,uid=''):
        same_origin(request);protection.check(request,action,uid);protection.rate(request,action,30,subject=uid)
    def current(c,s):
        if not c.execute("SELECT 1 FROM sessions WHERE token=? AND user_id=? AND stage='full' AND expires>?",(s['token'],s['user_id'],now())).fetchone():fail(401,localized_message('sessionTerminatedPleaseSignInAgain'))
    def prove(data,request,s):
        attempt(request,'passkey-manage',s['user_id']);valid=False
        with db() as c:
            c.execute('BEGIN IMMEDIATE');current(c,s);user=c.execute('SELECT * FROM users WHERE id=?',(s['user_id'],)).fetchone()
            valid=check_password(data.password,user['password']) and verify_account_factor(c,user,data.code,cipher,now)
        if not valid:
            protection.failure(request,'passkey-manage',s['user_id']);fail(403,localized_message('incorrectPasswordOr2FaCode'))
        protection.success(request,'passkey-manage',s['user_id']);return user
    def ceremony(request,response,kind,challenge,**payload):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        value={'kind':kind,'challenge':encode(challenge),'csrf':csrf,'expires':now()+TTL,**payload}
        with db() as c:
            c.execute('BEGIN IMMEDIATE');active=0
            for row in c.execute("SELECT key,value FROM app_settings WHERE key LIKE ?",('passkey:challenge:%',)).fetchall():
                if json.loads(row['value'])['expires']<=now():c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],))
                else:active+=1
            if active>=1000:fail(429,localized_message('tooManyAttemptsTryAgainLater'))
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',(PREFIX+digest(token),json.dumps(value)))
        response.set_cookie('drg_passkey',token,max_age=TTL,secure=secure,httponly=True,samesite='strict',path='/api/')
        return csrf
    def consume(data,request,response,kind,s=None):
        same_origin(request);token=request.cookies.get('drg_passkey','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):fail(401,localized_message('passkeySessionHasExpiredPleaseStartANew'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT value FROM app_settings WHERE key=?',(PREFIX+digest(token),)).fetchone()
            value=json.loads(row['value']) if row else None
            if not value or value['expires']<=now() or value['kind']!=kind:fail(401,localized_message('passkeySessionHasExpiredOrHasAlreadyBeen'))
            if not hmac.compare_digest(data.csrf,value['csrf']):fail(403,localized_message('invalidPasskeyConfirmation'))
            if s and (value.get('user_id')!=s['user_id'] or value.get('session')!=s['token']):fail(403,localized_message('passkeyMustBeAddedInTheSameSession'))
            c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+digest(token),))
        response.delete_cookie('drg_passkey',path='/api/',secure=secure,httponly=True,samesite='strict')
        try:
            client=json.loads(decode(data.credential['response']['clientDataJSON']))
            if not isinstance(client,dict):raise ValueError('Invalid client data')
            if client.get('crossOrigin') is True or client.get('topOrigin') is not None:raise ValueError('Cross-origin WebAuthn')
            if data.credential.get('type')!='public-key' or data.credential.get('id')!=data.credential.get('rawId'):raise ValueError('Invalid credential ID')
            decode(data.credential['id'])
        except (ValueError,KeyError,TypeError):fail(401,localized_message('invalidPasskeyConfirmation2'))
        return value
    def public(key):return {name:key.get(name) for name in ('id','name','created','last_used','backed_up','rp_id')}

    @router.get('/api/security/passkeys')
    def listing(request:Request):
        s=ready(request)
        with db() as c:user=c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()
        return {'items':[public(key) for key in validate_passkeys(json.loads(user['settings']))],'limit':LIMIT,'rp_id':rp_id}

    @router.post('/api/security/passkeys/options')
    def register_options(data:Register,request:Request,response:Response):
        s=ready(request);user=prove(data,request,s);keys=validate_passkeys(json.loads(user['settings']))
        if len(keys)>=LIMIT:fail(409,localized_message('youCanSaveUpTo20PasskeysRemove'))
        options=generate_registration_options(rp_id=rp_id,rp_name='DrG',user_id=s['user_id'].encode(),user_name=s['login'],user_display_name=json.loads(user['settings']).get('profile',{}).get('display_name') or s['login'],timeout=120000,authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,require_resident_key=True,user_verification=UserVerificationRequirement.REQUIRED),exclude_credentials=[PublicKeyCredentialDescriptor(id=decode(key['credential_id'])) for key in keys],supported_pub_key_algs=[-7,-257])
        csrf=ceremony(request,response,'register',options.challenge,user_id=s['user_id'],session=s['token'],password=digest(user['password']),name=data.name.strip() or 'Мой passkey')
        return {'options':json.loads(options_to_json(options)),'ceremony_csrf':csrf}

    @router.post('/api/security/passkeys/verify')
    def register_verify(data:Credential,request:Request,response:Response):
        if data.native_flow is not None:fail(422,localized_message('nativeLoginIsNotUsedWhenCreatingA'))
        s=ready(request);attempt(request,'passkey-register',s['user_id']);pending=consume(data,request,response,'register',s)
        try:verified=verify_registration_response(credential=data.credential,expected_challenge=decode(pending['challenge']),expected_rp_id=rp_id,expected_origin=origin,require_user_presence=True,require_user_verification=True,supported_pub_key_algs=[-7,-257])
        except Exception:
            protection.failure(request,'passkey-register',s['user_id']);fail(401,localized_message('browserDidNotConfirmPasskeyCreationPleaseTry'))
        credential_id=encode(verified.credential_id)
        key={'id':secrets.token_hex(16),'credential_id':credential_id,'public_key':encode(verified.credential_public_key),'sign_count':verified.sign_count,'name':pending['name'],'created':now(),'last_used':None,'backed_up':verified.credential_backed_up,'rp_id':rp_id}
        with db() as c:
            c.execute('BEGIN IMMEDIATE');current(c,s);user=c.execute('SELECT settings,password FROM users WHERE id=?',(s['user_id'],)).fetchone();settings=json.loads(user['settings']);keys=validate_passkeys(settings)
            if digest(user['password'])!=pending['password']:fail(401,localized_message('passwordHasBeenChangedPleaseConfirmTheAddition'))
            if len(keys)>=LIMIT or c.execute('SELECT 1 FROM app_settings WHERE key=?',(index_key(credential_id),)).fetchone():fail(409,localized_message('thisPasskeyHasAlreadyBeenAddedOrThe'))
            settings['_passkeys']=[*keys,key];c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',(index_key(credential_id),json.dumps(s['user_id'])))
        protection.success(request,'passkey-register',s['user_id']);return public(key)

    @router.patch('/api/security/passkeys/{kid}')
    def rename(kid:str,data:Rename,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');current(c,s);user=c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone();settings=json.loads(user['settings']);keys=validate_passkeys(settings);key=next((key for key in keys if key['id']==kid),None)
            if not key:fail(404,localized_message('passkeyNotFound'))
            key['name']=data.name.strip() or 'Мой passkey';c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
        return public(key)

    @router.post('/api/security/passkeys/{kid}/delete')
    def remove(kid:str,data:Proof,request:Request):
        s=ready(request);prove(data,request,s)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');current(c,s);user=c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone();settings=json.loads(user['settings']);keys=validate_passkeys(settings);key=next((key for key in keys if key['id']==kid),None)
            if not key:fail(404,localized_message('passkeyNotFound'))
            settings['_passkeys']=[item for item in keys if item['id']!=kid];c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']));c.execute('DELETE FROM app_settings WHERE key=?',(index_key(key['credential_id']),))
        return {'ok':True}

    @router.post('/api/passkeys/options')
    def authenticate_options(request:Request,response:Response):
        attempt(request,'passkey-login');options=generate_authentication_options(rp_id=rp_id,timeout=120000,user_verification=UserVerificationRequirement.REQUIRED)
        return {'options':json.loads(options_to_json(options)),'ceremony_csrf':ceremony(request,response,'authenticate',options.challenge)}

    @router.post('/api/passkeys/verify')
    def authenticate_verify(data:Credential,request:Request,response:Response):
        attempt(request,'passkey-login');pending=consume(data,request,response,'authenticate');credential_id=data.credential['id'];key=None;uid=None
        with db() as c:
            lookup=c.execute('SELECT value FROM app_settings WHERE key=?',(index_key(credential_id),)).fetchone();uid=json.loads(lookup['value']) if lookup else None
            user=c.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone() if uid else None
            if user:key=next((key for key in validate_passkeys(json.loads(user['settings'])) if key['credential_id']==credential_id and key['rp_id']==rp_id),None)
        try:
            if not key or decode(data.credential['response']['userHandle'])!=uid.encode():raise ValueError('Unknown passkey')
            verified=verify_authentication_response(credential=data.credential,expected_challenge=decode(pending['challenge']),expected_rp_id=rp_id,expected_origin=origin,credential_public_key=decode(key['public_key']),credential_current_sign_count=key['sign_count'],require_user_verification=True)
        except Exception:
            protection.failure(request,'passkey-login');fail(401,localized_message('passkeyDidNotConfirmLoginPleaseTryAgain'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');user=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            if not user:fail(401,localized_message('passkeyIsNoLongerAvailable'))
            settings=json.loads(user['settings']);latest=next((item for item in validate_passkeys(settings) if item['id']==key['id']),None)
            if not latest or latest['public_key']!=key['public_key'] or latest['sign_count']!=key['sign_count']:fail(401,localized_message('passkeyHasBeenChangedPleaseStartANew'))
            latest['sign_count']=verified.new_sign_count;latest['last_used']=now();latest['backed_up']=verified.credential_backed_up
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
            if data.native_flow is not None:
                from app.native_auth import grant
                result={'native':grant(c,data.native_flow,user,latest,now,fail)}
            else:result={'csrf':tokens.issue(response,uid,True,connection=c,request=request)}
        protection.success(request,'passkey-login');return result
    return router
