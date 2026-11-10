"""A bounded private ledger: integer kopecks, explicit allocations and bilateral debts."""
from app.i18n import message as localized_message
import asyncio,json,secrets,re
import zipstream
from io import BytesIO
from functools import lru_cache
from pathlib import Path
from typing import Literal
from app.http import Router,Request
from app.http import Response,StreamingResponse
from app.data_crypto import media_store
from app.modules.media import inspect_image
from PIL import Image,ImageOps,UnidentifiedImageError
from pydantic import BaseModel,ConfigDict,Field,StrictInt

CATEGORY_CATALOG=json.loads((Path(__file__).resolve().parents[1]/'expense_categories.json').read_text())
CURRENCIES=('RUB','USD','EUR')
LEGACY_CATEGORIES={'food':'Еда','beer':'Пиво','whisky':'Виски','tinctures':'Настойки','cocktails':'Коктейли','mixed':'Всё подряд','taxi':'Такси','shop':'Магазин'}
CATEGORIES={x['id']:x['ru'] for x in CATEGORY_CATALOG if x['group']!='transfers'}|LEGACY_CATEGORIES
def currency(payload):return payload.get('currency') or 'RUB'
def validate_photos(photos,asset_exists):
    if not isinstance(photos,list) or len(photos)>50 or any(not isinstance(x,str) or not re.fullmatch(r'[a-f0-9]{32}',x) for x in photos) or len(set(photos))!=len(photos) or any(not asset_exists(x) for x in photos):raise ValueError(localized_message('invalidEveningPhotos'))

def referenced_photos(c,uid):
    return {aid for e in c.execute('SELECT payload FROM expense_events WHERE user_id=?',(uid,)) for aid in json.loads(e[0]).get('photos',[])}

def retire_photos(c,event,stamp,photos=None):
    from app.modules.widget_models import dashboard
    settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(event['user_id'],)).fetchone()[0])
    refs={settings.get('profile',{}).get('avatar_id'),settings.get('appearance',{}).get('wallpaper')}|{a.get('asset_id') for w in dashboard(settings)['instances'] if w['module_id']=='media' for a in w['config'].get('items',[])}
    others={aid for e in c.execute('SELECT payload FROM expense_events WHERE id<>? AND user_id=?',(event['id'],event['user_id'])) for aid in json.loads(e[0]).get('photos',[])}
    for aid in photos if photos is not None else json.loads(event['payload']).get('photos',[]):
        if aid in refs or aid in others or c.execute('SELECT 1 FROM note_assets WHERE id=?',(aid,)).fetchone():continue
        if c.execute('DELETE FROM media WHERE id=? AND user_id=?',(aid,event['user_id'])).rowcount:c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(aid,'media',stamp))

def purge_expenses(c,stamp):
    for e in c.execute('SELECT * FROM expense_events WHERE purge_at<=?',(stamp,)).fetchall():
        retire_photos(c,e,stamp);c.execute('DELETE FROM expense_events WHERE id=?',(e['id'],))

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Participant(Strict):
    id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    name:str=Field(min_length=1,max_length=80)
class ExpenseGroup(Strict):
    id:str=Field(pattern=r'^pair_[a-zA-Z0-9_-]{1,32}$')
    members:list[str]=Field(min_length=2,max_length=2)

class Event(Strict):
    title:str=Field(min_length=1,max_length=150)
    participants:list[Participant]=Field(min_length=1,max_length=30)
    retention_days:Literal[7,30,90]=30
    currency:Literal['RUB','USD','EUR']|None=None
    groups:list[ExpenseGroup]|None=Field(default=None,max_length=15)
class EventUpdate(Event):
    version:int=Field(ge=1)
    finish:bool=False
class Entry(Strict):
    kind:Literal['expense','debt','repayment']='expense'
    category:str='mixed'
    payer:str=Field(max_length=40)
    amount:StrictInt=Field(ge=1,le=100000000)
    allocations:dict[str,StrictInt]=Field(min_length=1,max_length=30)
    note:str=Field(default='',max_length=400)
    event_version:int=Field(ge=1)
    repaid_for:str|None=Field(default=None,max_length=40)
class Share(Strict):
    duration:int=Field(default=2,ge=1,le=10000)
    unit:Literal['minutes','hours','days','weeks','months','years']='days'

def validate_event(data):
    ids=[p.id for p in data.participants]
    if len(set(ids))!=len(ids) or 'me' not in ids or not data.title.strip() or any(not p.name.strip() for p in data.participants):raise ValueError(localized_message('specifyYourselfAnUnknownParticipantAndUniqueParticipant'))

def validate_groups(groups,participants):
    ids={p['id'] for p in participants};used=set();group_ids=set()
    for group in groups:
        members=set(group['members'])
        if len(members)!=2 or not members.issubset(ids) or members&used or group['id'] in ids or group['id'] in group_ids:raise ValueError(localized_message('expenseGroupsInvalid'))
        used.update(members);group_ids.add(group['id'])

def validate_entry(data,participants,groups=None):
    ids={p['id'] for p in participants};groups=groups or [];targets=ids|{g['id'] for g in groups}
    if data.category not in CATEGORIES or data.payer not in ids or set(data.allocations)-targets or any(v<0 or v>100000000 for v in data.allocations.values()) or sum(data.allocations.values())!=data.amount:raise ValueError(localized_message('theAmountOfSharesMustMatchThePayment'))
    for group in groups:
        if group['id'] in data.allocations and set(group['members'])&data.allocations.keys():raise ValueError(localized_message('expenseGroupOverlap'))
    if data.kind in ('debt','repayment') and (len(data.allocations)!=1 or data.payer in data.allocations or (data.kind=='repayment' and not set(data.allocations).issubset(ids))):raise ValueError(localized_message('forADebtSpecifyOneDebtorAndOne'))
    if data.repaid_for:
        allowed={data.payer}
        for group in groups:
            if data.payer in group['members']:allowed.update([group['id'],*group['members']])
        if data.kind!='repayment' or data.repaid_for not in allowed:raise ValueError(localized_message('expenseGroupsInvalid'))

def report(event,entries,stamp):
    payload=json.loads(event['payload']);people=payload['participants'];groups=payload.get('groups') or []
    group_by_person={p:g['id'] for g in groups for p in g['members']};group_members={g['id']:g['members'] for g in groups}
    names={p['id']:p['name'] for p in people};targets=[*names,*group_members]
    paid={p:0 for p in names};cost={p:0 for p in targets};net={p:0 for p in targets};claims={};categories={k:0 for k in CATEGORIES};items=[];repayments=[]
    my_group=group_by_person.get('me');my_scope={my_group,*group_members.get(my_group,[])} if my_group else {'me'}
    def internal(debtor,creditor):
        return debtor==creditor or (group_by_person.get(creditor) is not None and (debtor==group_by_person[creditor] or group_by_person.get(debtor)==group_by_person[creditor]))
    for row in entries:
        d=json.loads(row['payload']);items.append({'id':row['id'],'created':row['created'],**d});payer=d['payer'];amount=d['amount']
        if d['kind']=='repayment':repayments.append(d);continue
        net[payer]+=amount
        if d['kind']=='expense':paid[payer]+=amount
        for pid,value in d['allocations'].items():
            net[pid]-=value
            if not internal(pid,payer):claims[(pid,payer)]=claims.get((pid,payer),0)+value
            if d['kind']=='expense':
                cost[pid]+=value
                if pid in my_scope:categories[d['category']]+=value
    # Settle purchases before repayments so same-second entries do not depend on random IDs.
    for d in repayments:
        sender=d['payer'];receiver=next(iter(d['allocations']));scope=d.get('repaid_for') or sender;remaining=d['amount']
        candidates=[scope,*group_members.get(scope,[])]
        for debtor in candidates:
            key=(debtor,receiver);used=min(remaining,claims.get(key,0));claims[key]=claims.get(key,0)-used;net[debtor]+=used;net[receiver]-=used;remaining-=used
        if remaining:
            net[sender]+=remaining;net[receiver]-=remaining
            if not internal(receiver,sender):claims[(receiver,sender)]=claims.get((receiver,sender),0)+remaining
    debts=[];seen=set()
    for (debtor,creditor),value in claims.items():
        key=tuple(sorted((debtor,creditor)))
        if key in seen:continue
        seen.add(key);delta=value-claims.get((creditor,debtor),0)
        if delta:debts.append({'from':debtor if delta>0 else creditor,'to':creditor if delta>0 else debtor,'amount':abs(delta)})
    group_cost={g['id']:sum(cost[p] for p in [g['id'],*g['members']]) for g in groups}
    my_paid=paid['me'];my_cost=sum(cost[p] for p in my_scope);my_balance=sum(net[p] for p in my_scope)
    my_incoming=sum(d['amount'] for d in debts if d['to'] in my_scope);my_outgoing=sum(d['amount'] for d in debts if d['from'] in my_scope)
    return {'id':event['id'],'title':event['title'],'created':event['created'],'ends':event['ends'],'purge_at':event['purge_at'],'version':event['version'],'active':event['ends']>stamp,'retention_days':payload['retention_days'],'participants':people,'groups':groups,'group_cost':group_cost,'my_group':my_group,'my_cost':my_cost,'my_paid':my_paid,'my_balance':my_balance,'my_incoming':my_incoming,'my_outgoing':my_outgoing,'entries':items,'paid':paid,'cost':cost,'balance':net,'debts':debts,'categories':categories,'currency':currency(payload),'photos':payload.get('photos',[])}

def create_expenses_router(db,guard,now,fail,expiry,public_url,protection,data_dir):
    r=Router();store=media_store(db,data_dir);photo_lock=asyncio.Semaphore(2)
    def erase(c,e):
        retire_photos(c,e,now());c.execute('DELETE FROM expense_events WHERE id=?',(e['id'],))
    def purge(c):
        purge_expenses(c,now())
    def owned(c,eid,uid):
        e=c.execute('SELECT * FROM expense_events WHERE id=? AND user_id=? AND purge_at>?',(eid,uid,now())).fetchone()
        if not e:fail(404,localized_message('theEventWasNotFoundOrTheExpiration'))
        return e
    def result(c,event):
        value=report(event,c.execute('SELECT * FROM expense_entries WHERE event_id=? ORDER BY created,id',(event['id'],)).fetchall(),now())
        value['photo_types']={}
        if value['photos']:
            markers=','.join('?' for _ in value['photos'])
            value['photo_types']={row['id']:row['mime'] for row in c.execute('SELECT id,mime FROM media WHERE user_id=? AND id IN ('+markers+')',(event['user_id'],*value['photos']))}
        return value
    @r.get('/api/expenses')
    def listing(request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge(c);return [result(c,e) for e in c.execute('SELECT * FROM expense_events WHERE user_id=? ORDER BY created DESC',(s['user_id'],)).fetchall()]
    @r.get('/api/expenses/options')
    def options(request:Request):
        s=guard(request)
        with db() as c:settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
        return {'categories':CATEGORY_CATALOG,'currencies':CURRENCIES,'default_currency':settings.get('expense_currency','RUB'),'legacy_participant_defaults':['Володя','Volodya']}
    @r.post('/api/expenses')
    def start(data:Event,request:Request):
        s=guard(request);protection.rate(request,'expense-create',30,subject=s['user_id'])
        try:
            validate_event(data)
            if data.groups is not None:validate_groups([g.model_dump() for g in data.groups],[p.model_dump() for p in data.participants])
        except ValueError as e:fail(422,str(e))
        eid=secrets.token_urlsafe(12);ends=now()+20*3600
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge(c)
            if c.execute('SELECT COUNT(*) FROM expense_events WHERE user_id=?',(s['user_id'],)).fetchone()[0]>=100:fail(422,localized_message('theLimitIs100Events'))
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0])
            chosen=data.currency or settings.get('expense_currency','RUB')
            if chosen not in CURRENCIES:chosen='RUB'
            c.execute('INSERT INTO expense_events(id,user_id,title,payload,created,ends,purge_at) VALUES(?,?,?,?,?,?,?)',(eid,s['user_id'],data.title.strip(),json.dumps({'participants':[p.model_dump() for p in data.participants],'retention_days':data.retention_days,'currency':chosen,'groups':[g.model_dump() for g in data.groups or []]}),now(),ends,ends+data.retention_days*86400))
            settings['expense_currency']=chosen
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
            return result(c,owned(c,eid,s['user_id']))
    @r.get('/api/expenses/{eid}')
    def get(eid:str,request:Request):
        s=guard(request)
        with db() as c:return result(c,owned(c,eid,s['user_id']))
    @r.put('/api/expenses/{eid}')
    def update(eid:str,data:EventUpdate,request:Request):
        s=guard(request)
        try:
            validate_event(data)
            if data.groups is not None:validate_groups([g.model_dump() for g in data.groups],[p.model_dump() for p in data.participants])
        except ValueError as e:fail(422,str(e))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');e=owned(c,eid,s['user_id'])
            if e['version']!=data.version:fail(409,localized_message('theEventHasBeenModifiedUpdateThePage'))
            if set(p['id'] for p in json.loads(e['payload'])['participants'])-set(p.id for p in data.participants):fail(422,localized_message('youCanRenameAParticipantButYouCannot'))
            previous=json.loads(e['payload']);groups=[g.model_dump() for g in data.groups] if data.groups is not None else previous.get('groups',[])
            try:validate_groups(groups,[p.model_dump() for p in data.participants])
            except ValueError as err:fail(422,str(err))
            if groups!=previous.get('groups',[]):
                used=set()
                for row in c.execute('SELECT payload FROM expense_entries WHERE event_id=?',(eid,)):
                    entry=json.loads(row[0]);used.update(entry['allocations']);used.add(entry.get('repaid_for'))
                new={g['id']:g for g in groups}
                if any(g['id'] in used and new.get(g['id'])!=g for g in previous.get('groups',[])):fail(409,localized_message('expenseGroupInUse'))
            current_currency=currency(previous)
            if data.currency and data.currency!=current_currency and c.execute('SELECT 1 FROM expense_entries WHERE event_id=?',(eid,)).fetchone():fail(409,localized_message('expenseCurrencyLocked'))
            ends=min(e['ends'],now()) if data.finish else e['ends'];purge_at=ends+data.retention_days*86400
            c.execute('UPDATE expense_events SET title=?,payload=?,ends=?,purge_at=?,version=version+1 WHERE id=?',(data.title.strip(),json.dumps({**json.loads(e['payload']),'currency':data.currency or current_currency,'groups':groups,'participants':[p.model_dump() for p in data.participants],'retention_days':data.retention_days}),ends,purge_at,eid))
            if purge_at<=now():
                erase(c,e);return {'deleted':True}
            c.execute('UPDATE expense_shares SET expires=? WHERE event_id=? AND expires>?',(purge_at,eid,purge_at))
            return result(c,owned(c,eid,s['user_id']))
    @r.delete('/api/expenses/{eid}')
    def remove(eid:str,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');erase(c,owned(c,eid,s['user_id']))
        return {'ok':True}
    def save_entry(c,eid,data,s,entry_id=None):
        e=owned(c,eid,s['user_id'])
        if e['version']!=data.event_version:fail(409,localized_message('theEventHasBeenModifiedUpdateThePage'))
        if not entry_id and e['ends']<=now():fail(409,localized_message('theEveningIsOverYouCanCorrectThe'))
        try:validate_entry(data,json.loads(e['payload'])['participants'],json.loads(e['payload']).get('groups'))
        except ValueError as err:fail(422,str(err))
        values=data.model_dump(exclude={'event_version'});body=json.dumps(values)
        if entry_id:
            if not c.execute('UPDATE expense_entries SET payload=? WHERE id=? AND event_id=?',(body,entry_id,eid)).rowcount:fail(404,localized_message('recordNotFound'))
        else:
            if c.execute('SELECT COUNT(*) FROM expense_entries WHERE event_id=?',(eid,)).fetchone()[0]>=2000:fail(422,localized_message('theLimitIs2000Records'))
            c.execute('INSERT INTO expense_entries VALUES(?,?,?,?)',(secrets.token_urlsafe(12),eid,body,now()))
        c.execute('UPDATE expense_events SET version=version+1 WHERE id=?',(eid,));return result(c,owned(c,eid,s['user_id']))
    @r.post('/api/expenses/{eid}/entries')
    def add(eid:str,data:Entry,request:Request):
        s=guard(request)
        with db() as c:c.execute('BEGIN IMMEDIATE');return save_entry(c,eid,data,s)
    @r.put('/api/expenses/{eid}/entries/{entry_id}')
    def edit(eid:str,entry_id:str,data:Entry,request:Request):
        s=guard(request)
        with db() as c:c.execute('BEGIN IMMEDIATE');return save_entry(c,eid,data,s,entry_id)
    @r.delete('/api/expenses/{eid}/entries/{entry_id}')
    def delete_entry(eid:str,entry_id:str,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');owned(c,eid,s['user_id'])
            if not c.execute('DELETE FROM expense_entries WHERE id=? AND event_id=?',(entry_id,eid)).rowcount:fail(404,localized_message('recordNotFound'))
            c.execute('UPDATE expense_events SET version=version+1 WHERE id=?',(eid,));return result(c,owned(c,eid,s['user_id']))
    @r.post('/api/expenses/{eid}/photos')
    async def upload_photo(eid:str,request:Request):
        s=guard(request);protection.rate(request,'expense-photo',120,subject=s['user_id'])
        limit=10*1024*1024
        if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>limit:fail(413,localized_message('thePhotoShouldNotExceed10Mb'))
        async with photo_lock:
            with db() as c:
                e=owned(c,eid,s['user_id'])
                if len(json.loads(e['payload']).get('photos',[]))>=50:fail(422,localized_message('expensePhotosLimit'))
            chunks=[];size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>limit:fail(413,localized_message('thePhotoShouldNotExceed10Mb'))
                chunks.append(chunk)
            body=b''.join(chunks)
            try:mime,width,height,frames=await asyncio.to_thread(inspect_image,body)
            except (ValueError,EOFError,UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):fail(422,localized_message('theImageCouldNotBeReadAPng2'))
            aid=secrets.token_hex(16)
            try:
                with db() as c:
                    c.execute('BEGIN IMMEDIATE');e=owned(c,eid,s['user_id']);payload=json.loads(e['payload'])
                    photos=payload.setdefault('photos',[])
                    if len(photos)>=50:fail(422,localized_message('expensePhotosLimit'))
                    store.write(aid,body)
                    c.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)',(aid,s['user_id'],mime,size,width,height,frames,now()))
                    photos.append(aid);c.execute('UPDATE expense_events SET payload=?,version=version+1 WHERE id=?',(json.dumps(payload),eid))
                    saved=result(c,owned(c,eid,s['user_id']))
            except BaseException:store.delete(aid);raise
        return saved
    @r.delete('/api/expenses/{eid}/photos/{aid}')
    def remove_photo(eid:str,aid:str,request:Request):
        s=guard(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');e=owned(c,eid,s['user_id']);payload=json.loads(e['payload'])
            if aid not in payload.get('photos',[]):fail(404,localized_message('thePhotoWasNotFound'))
            retire_photos(c,e,now(),[aid]);payload['photos'].remove(aid)
            c.execute('UPDATE expense_events SET payload=?,version=version+1 WHERE id=?',(json.dumps(payload),eid))
            return result(c,owned(c,eid,s['user_id']))
    @lru_cache(maxsize=128)
    def photo_preview(aid):
        with Image.open(BytesIO(store.read(aid))) as image:
            image.seek(0);image=ImageOps.exif_transpose(image);image.thumbnail((640,640));output=BytesIO();image.convert('RGB').save(output,format='WEBP',quality=76);return output.getvalue()
    def photo_response(c,e,aid,download,preview=False):
        if aid not in json.loads(e['payload']).get('photos',[]):fail(404,localized_message('thePhotoWasNotFound'))
        row=c.execute('SELECT mime FROM media WHERE id=? AND user_id=?',(aid,e['user_id'])).fetchone()
        if not row:fail(404,localized_message('thePhotoWasNotFound'))
        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}
        if preview and not download:return Response(photo_preview(aid),media_type='image/webp',headers=headers)
        if download:headers['Content-Disposition']='attachment; filename="evening-photo.'+{'image/png':'png','image/jpeg':'jpg','image/webp':'webp','image/gif':'gif'}[row['mime']]+'"'
        return Response(store.read(aid),media_type=row['mime'],headers=headers)
    def photos_zip(c,e,token=None):
        photos=json.loads(e['payload']).get('photos',[]);archive=zipstream.ZipStream(compress_type=zipstream.ZIP_STORED)
        for i,aid in enumerate(photos):
            row=c.execute('SELECT mime FROM media WHERE id=? AND user_id=?',(aid,e['user_id'])).fetchone()
            if not row:fail(404,localized_message('thePhotoWasNotFound'))
            suffix={'image/png':'png','image/jpeg':'jpg','image/webp':'webp','image/gif':'gif'}[row['mime']]
            def content(identity=aid):
                if token:
                    with db() as check:publication(check,token)
                yield store.read(identity)
            archive.add(content(),f'photo-{i+1:02d}.{suffix}')
        return StreamingResponse(iter(archive),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="expense-photos.zip"','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
    @r.get('/api/expenses/{eid}/photos.zip')
    def own_photos_zip(eid:str,request:Request):
        s=guard(request);protection.rate(request,'expense-photos-zip',10,subject=s['user_id'])
        with db() as c:return photos_zip(c,owned(c,eid,s['user_id']))
    @r.get('/api/expenses/{eid}/photos/{aid}')
    def own_photo(eid:str,aid:str,request:Request,download:bool=False,preview:bool=False):
        s=guard(request)
        with db() as c:return photo_response(c,owned(c,eid,s['user_id']),aid,download,preview)
    def publication(c,token):
        sh=c.execute('SELECT * FROM expense_shares WHERE token=? AND expires>?',(token,now())).fetchone()
        if not sh:fail(404,localized_message('theLinkHasExpiredOrBeenRevoked'))
        e=c.execute('SELECT * FROM expense_events WHERE id=? AND purge_at>?',(sh['event_id'],now())).fetchone()
        if not e:fail(404,localized_message('theEventIsNoLongerStored'))
        return sh,e
    @r.get('/api/shared-expenses/{token}/photos.zip')
    def public_photos_zip(token:str,request:Request):
        protection.rate(request,'expense-public-zip',10)
        with db() as c:
            _,e=publication(c,token);return photos_zip(c,e,token)
    @r.get('/api/shared-expenses/{token}/photos/{aid}')
    def public_photo(token:str,aid:str,request:Request,download:bool=False,preview:bool=False):
        protection.rate(request,'expense-public-photo',240)
        with db() as c:
            _,e=publication(c,token);return photo_response(c,e,aid,download,preview)
    @r.post('/api/expenses/{eid}/shares')
    def share(eid:str,data:Share,request:Request):
        s=guard(request);protection.rate(request,'expense-share',30,subject=s['user_id']);token=secrets.token_urlsafe(24)
        with db() as c:
            e=owned(c,eid,s['user_id']);expires=min(expiry(data.duration,data.unit),e['purge_at']);c.execute('INSERT INTO expense_shares VALUES(?,?,?,?)',(token,eid,expires,now()))
        return {'url':public_url+'/expense-share/'+token,'expires':expires,'token':token}
    @r.get('/api/expenses/{eid}/shares')
    def shares(eid:str,request:Request):
        s=guard(request)
        with db() as c:
            owned(c,eid,s['user_id']);return [dict(x)|{'url':public_url+'/expense-share/'+x['token']} for x in c.execute('SELECT * FROM expense_shares WHERE event_id=? AND expires>?',(eid,now()))]
    @r.delete('/api/expense-shares/{token}')
    def revoke(token:str,request:Request):
        s=guard(request)
        with db() as c:
            sh=c.execute('SELECT event_id FROM expense_shares WHERE token=?',(token,)).fetchone()
            if not sh:fail(404,localized_message('linkNotFound'))
            owned(c,sh[0],s['user_id']);c.execute('DELETE FROM expense_shares WHERE token=?',(token,))
        return {'ok':True}
    @r.get('/api/shared-expenses/{token}')
    def public(token:str,request:Request):
        protection.rate(request,'expense-public',120)
        with db() as c:
            sh,e=publication(c,token)
            return result(c,e)|{'share_expires':sh['expires']}
    def cleanup():
        with db() as c:c.execute('BEGIN IMMEDIATE');purge(c)
    r.cleanup=cleanup
    return r
