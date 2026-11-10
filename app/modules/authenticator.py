"""External TOTP codes, never return seeds through the normal API."""
from app.i18n import message as localized_message
import hashlib,json,secrets
from urllib.parse import urlparse,parse_qs
import pyotp
from app.http import Router,Request
from pydantic import BaseModel,Field,ConfigDict
from typing import Literal

class AuthenticatorInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    label:str=Field(min_length=1,max_length=100)
    issuer:str=Field(default='',max_length=100)
    secret:str=Field(min_length=8,max_length=4096)
    algorithm:Literal['SHA1','SHA256','SHA512']='SHA1'
    digits:Literal[6,8]=6
    period:int=Field(default=30,ge=15,le=120)

def normalize(data):
    values=data.model_dump();raw=values['secret'].strip()
    if raw.startswith('otpauth://'):
        parsed=urlparse(raw)
        if parsed.netloc!='totp':raise ValueError(localized_message('onlyTotpCodesAreSupported'))
        query=parse_qs(parsed.query)
        raw=query.get('secret',[''])[0]
        values.update(algorithm=query.get('algorithm',['SHA1'])[0].upper(),digits=int(query.get('digits',['6'])[0]),period=int(query.get('period',['30'])[0]))
        if not values['issuer']:values['issuer']=query.get('issuer',[''])[0]
    values['secret']=''.join(raw.split()).upper().rstrip('=')
    data=AuthenticatorInput.model_validate(values)
    generator(data.model_dump()).at(0) # Validates base32 without persisting invalid seeds.
    if not data.label.strip():raise ValueError(localized_message('specifyTheName'))
    return data

def generator(entry):
    return pyotp.TOTP(entry['secret'],digits=entry['digits'],interval=entry['period'],digest=getattr(hashlib,entry['algorithm'].lower()))

def create_authenticator_router(db,guard,cipher,protection,now,fail):
    r=Router()
    def access(request):
        s=guard(request)
        if not s['totp']:fail(403,localized_message('toStoreCodesEnable2FaForYourAccount'))
        return s
    @r.get('/api/authenticators')
    def listing(request:Request):
        s=access(request)
        with db() as c:return [dict(x) for x in c.execute('SELECT id,label,issuer,algorithm,digits,period,created FROM authenticators WHERE user_id=? ORDER BY created,id',(s['user_id'],))]
    @r.post('/api/authenticators')
    def add(data:AuthenticatorInput,request:Request):
        s=access(request);protection.rate(request,'totp-vault-add',30,subject=s['user_id'])
        try:data=normalize(data)
        except (ValueError,TypeError,KeyError):fail(422,localized_message('checkTheBase32KeyOrTheOtpauthTotp'))
        eid=secrets.token_urlsafe(12)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT COUNT(*) FROM authenticators WHERE user_id=?',(s['user_id'],)).fetchone()[0]>=100:fail(422,localized_message('theLimitIs100Services'))
            c.execute('INSERT INTO authenticators VALUES(?,?,?,?,?,?,?,?,?)',(eid,s['user_id'],data.label.strip(),data.issuer,cipher.encrypt(data.secret.encode()).decode(),data.algorithm,data.digits,data.period,now()))
        return {'id':eid}
    @r.post('/api/authenticators/{eid}/reveal')
    def reveal(eid:str,request:Request):
        s=access(request);protection.rate(request,'totp-vault-reveal',180,subject=s['user_id'])
        with db() as c:entry=c.execute('SELECT * FROM authenticators WHERE id=? AND user_id=?',(eid,s['user_id'])).fetchone()
        if not entry:fail(404,localized_message('serviceNotFound'))
        values=dict(entry);values['secret']=cipher.decrypt(values['secret'].encode()).decode()
        stamp=now();return {'code':generator(values).at(stamp),'valid_until':(stamp//values['period']+1)*values['period'],'server_time':stamp}
    @r.delete('/api/authenticators/{eid}')
    def remove(eid:str,request:Request):
        s=access(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('DELETE FROM authenticators WHERE id=? AND user_id=?',(eid,s['user_id'])).rowcount:fail(404,localized_message('serviceNotFound'))
            row=c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone();settings=json.loads(row[0]);layout=settings.get('dashboard');changed=False
            for widget in (layout or {}).get('instances',[]):
                if widget['module_id']=='authenticator' and eid in widget['config'].get('entry_ids',[]):
                    widget['config']['entry_ids'].remove(eid);changed=True
                    if not widget['config']['entry_ids']:widget['hidden']=True
            if changed:
                layout['version']+=1;c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
        return {'ok':True}
    return r
