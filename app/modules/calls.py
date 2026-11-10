"""Private two-party voice calls. Media stays in WebRTC; only bounded signaling is stored."""
from app.i18n import message as localized_message
import base64
import hashlib
import hmac
import json
import os
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from app.http import Router, Request, Query
from app.modules.social import space_access
from app.modules.preferences import module_enabled
from app.notifications import actor, notify
from app.chat_storage import cutoff
from app.turn_health import turn_available

SCHEMA = '''
CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,caller_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,recipient_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,status TEXT NOT NULL,created INTEGER NOT NULL,answered INTEGER NOT NULL DEFAULT 0,connected INTEGER NOT NULL DEFAULT 0,ended INTEGER NOT NULL DEFAULT 0,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS call_caller_idx ON calls(caller_id,created);
CREATE INDEX IF NOT EXISTS call_recipient_idx ON calls(recipient_id,created);
CREATE INDEX IF NOT EXISTS call_active_idx ON calls(status,created);
'''
LIVE = ('ringing', 'connecting', 'active')
DEVICE = r'^[a-zA-Z0-9_-]{16,80}$'
class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')
class Device(Model):
    device: str = Field(pattern=DEVICE)
class Start(Device):
    id: str = Field(pattern=DEVICE)
    space_id: str = Field(min_length=1,max_length=128)
    sdp: str = Field(min_length=1,max_length=32000)
class Candidate(Model):
    candidate: str = Field(max_length=1800)
    sdpMid: str | None = Field(default=None,max_length=80)
    sdpMLineIndex: Annotated[int,Field(strict=True,ge=0,le=10)] | None = None
    usernameFragment: str | None = Field(default=None,max_length=256)
class MediaState(Model):
    muted: StrictBool = False
    camera: StrictBool = False
    screen: StrictBool = False
class Signal(Device):
    media: MediaState | None = None
    sdp: str | None = Field(default=None,min_length=1,max_length=32000)
    candidates: list[Candidate] = Field(default_factory=list,max_length=100)
    connected: bool = False
class Finish(Model):
    reason: Literal['hangup','decline','failed'] = 'hangup'


def finish(c,row,status,stamp):
    # Delete IP addresses, SDP and device handles as soon as the call ends.
    c.execute('UPDATE calls SET status=?,ended=?,payload=? WHERE id=?',(status,stamp,'{}',row['id']))
    for notice in c.execute("SELECT id,payload FROM notifications WHERE kind='call' AND user_id=? AND read=0",(row['recipient_id'],)).fetchall():
        if json.loads(notice['payload']).get('call_id')==row['id']:c.execute('UPDATE notifications SET read=1 WHERE id=?',(notice['id'],))


def purge_calls(c,stamp):
    for row in c.execute("SELECT * FROM calls WHERE status IN ('ringing','connecting','active')").fetchall():
        data=json.loads(row['payload'])
        if row['status']=='ringing' and row['created']<=stamp-60:finish(c,row,'missed',stamp)
        elif row['status']!='ringing' and (min(data.get('caller_seen',0),data.get('recipient_seen',0))<=stamp-45 or (row['status']=='connecting' and row['answered']<=stamp-40)):finish(c,row,'failed',stamp)
    c.execute('DELETE FROM calls WHERE created<=?',(cutoff(stamp),))


def ice_config(subject, stamp, fail, lifetime=14400):
    stun=[v.strip() for v in os.getenv('CALL_STUN_URLS','stun:stun.cloudflare.com:3478').split(',') if v.strip()]
    turns=[v.strip() for v in os.getenv('CALL_TURN_URLS','').split(',') if v.strip()]
    if any(not re.fullmatch(r'stuns?:[^\s/@]+',v) for v in stun) or any(not re.fullmatch(r'turns?:[^\s/@]+(?:\?transport=(?:udp|tcp))?',v) for v in turns):fail(503,localized_message('theAdministratorNeedsToVerifyStunTurnAddresses'))
    servers=[{'urls':stun}] if stun else []
    secret=os.getenv('CALL_TURN_SECRET','')
    if turns:
        if len(secret)<16:fail(503,localized_message('theAdministratorNeedsToConfigureTheTurnSecret'))
        username=f'{stamp+max(1,min(lifetime,43200))}:{subject}'
        credential=base64.b64encode(hmac.new(secret.encode(),username.encode(),hashlib.sha1).digest()).decode()
        servers.append({'urls':turns,'username':username,'credential':credential})
    return {'iceServers':servers,'relay_configured':bool(turns),'available':turn_available()}


def create_calls_router(db,ready,rate,now,fail,*,federation_busy=None):
    r=Router()
    def access(c,cid,uid):
        row=c.execute('SELECT * FROM calls WHERE id=? AND (caller_id=? OR recipient_id=?)',(cid,uid,uid)).fetchone()
        if not row:fail(404,localized_message('callNotFound'))
        space_access(c,row['space_id'],uid,fail)
        return row
    def serial(c,row,uid,device=None):
        other=row['recipient_id'] if row['caller_id']==uid else row['caller_id']
        item={k:row[k] for k in ('id','space_id','status','created','answered','connected','ended')}
        side='caller' if row['caller_id']==uid else 'recipient';data=json.loads(row['payload']);remote='recipient' if side=='caller' else 'caller'
        item.update(outgoing=side=='caller',person=actor(c,other,now()),owned=data.get(side+'_device')==device)
        if item['owned'] and row['status'] in LIVE:item.update(sdp=data.get(remote+'_sdp'),candidates=data.get(remote+'_ice',[]),media=data.get(remote+'_media',{}))
        return item
    @r.get('/api/calls/config')
    def config(request:Request):
        s=ready(request)
        from app import livekit
        result=ice_config(s['user_id'],now(),fail)
        sfu=livekit.available()
        return result | {'guest_available':sfu,
                         'livekit_available':sfu,'guest_transports':['livekit']}
    @r.get('/api/calls/history')
    def history(request:Request,before:str|None=Query(default=None,pattern=DEVICE)):
        s=ready(request);uid=s['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge_calls(c,now())
            cursor=access(c,before,uid) if before else None
            before_stamp=cursor['created'] if cursor else None
            rows=c.execute('SELECT id,space_id,caller_id,recipient_id,status,created,answered,connected,ended FROM calls WHERE (caller_id=? OR recipient_id=?) AND space_id IN (SELECT space_id FROM space_members WHERE user_id=?) AND (? IS NULL OR created<? OR (created=? AND id<?)) ORDER BY created DESC,id DESC LIMIT 101',(uid,uid,uid,before_stamp,before_stamp,before_stamp,before)).fetchall()
            return {'items':[serial(c,dict(row)|{'payload':'{}'},uid) for row in rows[:100]],'more':len(rows)>100}
    @r.get('/api/calls/current')
    def current(request:Request,device:str=Query(pattern=DEVICE),call_id:str|None=Query(default=None,pattern=DEVICE)):
        s=ready(request);uid=s['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge_calls(c,now())
            row=access(c,call_id,uid) if call_id else c.execute("SELECT * FROM calls WHERE (caller_id=? OR recipient_id=?) AND status IN ('ringing','connecting','active') ORDER BY created DESC LIMIT 1",(uid,uid)).fetchone()
            if row:
                space_access(c,row['space_id'],uid,fail)
                return {'call':serial(c,row,uid,device)}
            return {'call':None}
    @r.post('/api/calls')
    def start(data:Start,request:Request):
        s=ready(request);uid=s['user_id']
        if not turn_available():fail(503,localized_message('theCallServerIsUnavailablePleaseTryAgain'))
        rate(request,'call-start',12,period=60,subject=uid)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge_calls(c,now());space=space_access(c,data.space_id,uid,fail)
            existing=c.execute('SELECT * FROM calls WHERE id=?',(data.id,)).fetchone()
            if existing:
                if existing['caller_id']!=uid or existing['space_id']!=data.space_id:fail(409,localized_message('theCallIdentifierIsAlreadyInUse'))
                return serial(c,existing,uid,data.device)
            members=[v[0] for v in c.execute('SELECT user_id FROM space_members WHERE space_id=? AND user_id!=?',(data.space_id,uid))]
            if space['kind']!='friends' or len(members)!=1:fail(422,localized_message('aVoiceCallIsAvailableInAPrivate'))
            peer=members[0];settings=c.execute('SELECT settings FROM users WHERE id=?',(peer,)).fetchone()[0]
            from app.modules.guest_calls import purge_guest_calls
            purge_guest_calls(c,now())
            if federation_busy and any(federation_busy(c,person,now()) for person in (uid,peer)):
                fail(409,localized_message('youOrTheOtherPartyAreAlreadyParticipating'))
            if c.execute('SELECT 1 FROM guest_call_participants WHERE user_id IN (?,?)',(uid,peer)).fetchone():fail(409,localized_message('youOrTheOtherPartyAreAlreadyParticipating2'))
            if not module_enabled(json.loads(settings),'chat'):fail(409,localized_message('theOtherPartySMessagesAndCallsAre'))
            if c.execute("SELECT 1 FROM calls WHERE (caller_id IN (?,?) OR recipient_id IN (?,?)) AND status IN ('ringing','connecting','active')",(uid,peer,uid,peer)).fetchone():fail(409,localized_message('youOrTheOtherPartyAreAlreadyParticipating2'))
            payload={'caller_device':data.device,'caller_sdp':data.sdp,'caller_seen':now(),'recipient_seen':now(),'caller_ice':[],'recipient_ice':[]}
            c.execute('INSERT INTO calls(id,space_id,caller_id,recipient_id,status,created,payload) VALUES(?,?,?,?,?,?,?)',(data.id,data.space_id,uid,peer,'ringing',now(),json.dumps(payload)))
            notify(c,peer,'call',{'call_id':data.id,'space_id':data.space_id,'actor_id':uid,'actor':actor(c,uid,now())['login']},now())
            return serial(c,access(c,data.id,uid),uid,data.device)
    @r.post('/api/calls/{cid}/accept')
    def accept(cid:str,data:Device,request:Request):
        s=ready(request);uid=s['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge_calls(c,now());row=access(c,cid,uid);payload=json.loads(row['payload'])
            if row['recipient_id']!=uid:fail(403,localized_message('onlyTheRecipientCanAnswerTheCall'))
            if row['status']!='ringing':
                if row['status'] in LIVE and payload.get('recipient_device')==data.device:return serial(c,row,uid,data.device)
                fail(409,localized_message('theCallHasAlreadyBeenEndedOrAccepted'))
            payload.update(recipient_device=data.device,recipient_seen=now())
            c.execute('UPDATE calls SET status=?,answered=?,payload=? WHERE id=?',('connecting',now(),json.dumps(payload),cid))
            return serial(c,access(c,cid,uid),uid,data.device)
    @r.post('/api/calls/{cid}/signal')
    def signal(cid:str,data:Signal,request:Request):
        s=ready(request);uid=s['user_id'];rate(request,'call-signal',240,period=60,subject=uid)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');purge_calls(c,now());row=access(c,cid,uid);payload=json.loads(row['payload']);side='caller' if row['caller_id']==uid else 'recipient'
            if row['status'] not in LIVE:fail(409,localized_message('callEnded'))
            if payload.get(side+'_device')!=data.device:fail(409,localized_message('theCallIsBeingUsedOnAnotherDevice'))
            if data.sdp:
                old=payload.get(side+'_sdp')
                if old and old!=data.sdp:fail(409,localized_message('connectionParametersHaveAlreadyBeenSet'))
                payload[side+'_sdp']=data.sdp
            candidates=payload.get(side+'_ice',[])
            for candidate in data.candidates:
                value=candidate.model_dump()
                if value not in candidates:candidates.append(value)
            if len(candidates)>100:fail(422,localized_message('tooManyCallNetworkAddresses'))
            payload[side+'_ice']=candidates;payload[side+'_seen']=now()
            if data.media is not None:payload[side+'_media']=data.media.model_dump()
            connected=row['connected'];status=row['status']
            if data.connected and row['answered'] and payload.get('recipient_sdp'):status='active';connected=connected or now()
            c.execute('UPDATE calls SET status=?,connected=?,payload=? WHERE id=?',(status,connected,json.dumps(payload),cid))
            return serial(c,access(c,cid,uid),uid,data.device)
    @r.post('/api/calls/{cid}/end')
    def end(cid:str,data:Finish,request:Request):
        s=ready(request);uid=s['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE');row=access(c,cid,uid)
            if row['status'] in LIVE:
                status='failed' if data.reason=='failed' else 'declined' if row['status']=='ringing' and uid==row['recipient_id'] else 'cancelled' if row['status']=='ringing' else 'ended'
                finish(c,row,status,now())
            return serial(c,access(c,cid,uid),uid)
    return r


def validate_call_archive(c,fail):
    for row in c.execute('SELECT * FROM calls'):
        if not re.fullmatch(DEVICE,row['id']) or row['caller_id']==row['recipient_id'] or row['status'] not in (*LIVE,'ended','declined','cancelled','missed','failed'):fail()
        if any(type(row[k]) is not int or row[k]<0 for k in ('created','answered','connected','ended')):fail()
        space=c.execute('SELECT kind FROM spaces WHERE id=?',(row['space_id'],)).fetchone()
        if not space or space[0]!='friends':fail()
        members={r[0] for r in c.execute('SELECT user_id FROM space_members WHERE space_id=?',(row['space_id'],))}
        if members!={row['caller_id'],row['recipient_id']}:fail()
        try:
            payload=json.loads(row['payload'])
            if not isinstance(payload,dict) or len(row['payload'])>500000:fail()
            if row['status'] not in LIVE:
                if payload or not row['ended']:fail()
                continue
            allowed={side+suffix for side in ('caller','recipient') for suffix in ('_device','_sdp','_seen','_ice','_media')}
            if set(payload)-allowed:fail()
            for side in ('caller','recipient'):
                if side+'_device' in payload and not re.fullmatch(DEVICE,payload[side+'_device']):fail()
                if side+'_sdp' in payload and (not isinstance(payload[side+'_sdp'],str) or not 1<=len(payload[side+'_sdp'])<=32000):fail()
                if type(payload.get(side+'_seen')) is not int or payload[side+'_seen']<0:fail()
                if side+'_media' in payload:MediaState.model_validate(payload[side+'_media'])
                candidates=payload.get(side+'_ice',[])
                if not isinstance(candidates,list) or len(candidates)>100:fail()
                for item in candidates:Candidate.model_validate(item)
            if not payload.get('caller_device') or not payload.get('caller_sdp'):fail()
            if row['status']!='ringing' and not payload.get('recipient_device'):fail()
        except (ValueError,TypeError):fail()
