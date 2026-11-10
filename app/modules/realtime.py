"""Authenticated, bounded WebSocket rooms and optional durable chat."""
from app.i18n import message as localized_message
from app.modules.family_avatars import avatar_url
from app.space_profiles import templates
from app.workspace_management import is_manager
import asyncio, base64, json, secrets
from contextlib import suppress
from types import SimpleNamespace
from typing import Annotated
from app.http import Router, Request, WebSocket, WebSocketDisconnect, HTTPError, Query
from pydantic import BaseModel, Field, ConfigDict, StrictBool
from app.notifications import notify,actor,chat_is_foreground,RETENTION as NOTIFICATION_RETENTION
from app.chat_storage import cutoff,encode,decode,envelope,serial_message,attachment_room,MAX_ATTACHMENT,DRAFT_SECONDS,EDIT_SECONDS,ATTACHMENT_PATTERN,CHAT_TIMERS,retire,history_version,HISTORY_VERSION_PREFIX,deletion_key
from app.collaboration import MAX_STATE, load_doc, project, links_ok
from app.modules.social import note_access, space_access
from app.modules.notion import require_role
from app.multiprocess import PeerCollision, PeerLimit, PeerSuperseded
from app.redis_cache import CACHE_SECONDS

class Message(BaseModel):
    model_config=ConfigDict(extra='forbid')
    body:str=Field(default='',max_length=4000)
    attachments:list[Annotated[str,Field(pattern=ATTACHMENT_PATTERN,max_length=38)]]=Field(default_factory=list,max_length=10)
    client_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{8,80}$')
    reply_to:Annotated[int,Field(strict=True,ge=1,le=2**53-1)]|None=None

class ReadCursor(BaseModel):
    model_config=ConfigDict(extra='forbid')
    message_id:int=Field(strict=True,ge=1,le=2**53-1)

class MessageEdit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    body:str=Field(max_length=4000)
    revision:int=Field(strict=True,ge=0,le=2**53-1)

class ChatTimer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    seconds:int=Field(strict=True)

class ChatNotificationPreferences(BaseModel):
    model_config=ConfigDict(extra='forbid')
    seconds:int=Field(strict=True)

class ChatPreferences(BaseModel):
    model_config=ConfigDict(extra='forbid')
    favorite:StrictBool|None=None
    pinned:StrictBool|None=None

def create_realtime_router(db,ready,guards,tokens,protection,gate,now,fail,public_url,*,coordinator=None):
    router=Router();rooms={}
    def auth(ws,kind,target):
        req=SimpleNamespace(cookies=ws.cookies,headers=ws.headers,method='GET',client=ws.client)
        protection.check(req)
        s=guards('notes' if kind=='note' else 'chat')(req)
        with db() as c:
            if kind=='note':
                n=note_access(c,target,s['user_id'],fail);require_role(c,n,s['user_id'],fail,session=s,now=now)
                if c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(target,)).fetchone():fail(403,localized_message('basAreEditedViaApiRecords'))
                from app.federation.notes import collaborative
                if not collaborative(c,n):fail(403,localized_message('collaborativeEditingIsAvailableForSharedPages'))
            else:space_access(c,target,s['user_id'],fail)
        return s,req
    def message(c,sid,uid,data):
        if c.execute('SELECT 1 FROM app_settings WHERE key=?',(deletion_key(sid,uid,data.client_id),)).fetchone():fail(409,localized_message('thisMessageHasBeenDeletedCreateANew'))
        existing=c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.space_id=? AND m.sender_id=? AND m.client_id=?',(sid,uid,data.client_id)).fetchone()
        if existing:
            if existing['created']<=cutoff(now()) or existing['expires'] and existing['expires']<=now():fail(409,localized_message('theMessageHasExpiredCreateANewMessage'))
            return serial_message(c,existing,now())|{'sender_profile':actor(c,uid,now())}
        if not data.body.strip() and not data.attachments:fail(422,localized_message('enterAMessageOrAttachAFile'))
        if len(set(data.attachments))!=len(data.attachments):fail(422,localized_message('reAttachment'))
        if data.reply_to is not None and not c.execute('SELECT 1 FROM messages WHERE id=? AND space_id=? AND created>? AND (expires=0 OR expires>?)',(data.reply_to,sid,cutoff(now()),now())).fetchone():fail(404,localized_message('theMessageIsNotAvailableForReply'))
        unavailable=[]
        for fid in data.attachments:
            file=c.execute('SELECT * FROM files WHERE id=? AND user_id=?',(fid,uid)).fetchone()
            if attachment_room(fid)!=sid or not file or file['version']!=1 or file['created']<=now()-DRAFT_SECONDS:unavailable.append(fid)
        if unavailable:fail(404,{'message':'Вложение недоступно или уже отправлено. Прикрепите его заново или отправьте текст без него.','unavailable_attachments':unavailable})
        stamp=now();ttl=c.execute('SELECT chat_ttl FROM spaces WHERE id=?',(sid,)).fetchone()[0]
        cursor=c.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created,expires) VALUES(?,?,?,?,?,?)',(sid,uid,data.client_id,encode(data.body.strip(),data.attachments,data.reply_to),stamp,stamp+ttl if ttl else 0))
        for fid in data.attachments:c.execute('UPDATE files SET version=?,updated=? WHERE id=?',(cursor.lastrowid+1,now(),fid))
        saved=serial_message(c,c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id=?',(cursor.lastrowid,)).fetchone(),now())
        for member in c.execute('SELECT user_id FROM space_members WHERE space_id=? AND user_id!=?',(sid,uid)).fetchall():
            if chat_is_foreground(c,member[0],stamp,coordinator):continue
            notify(c,member[0],'message',{'space_id':sid,'message_id':saved['id'],'actor_id':uid,'actor':saved['sender'],'mentioned':any(m['id']==member[0] for m in saved['mentions']),'preview':data.body.strip()[:180] or 'Вложение'},now())
        saved['sender_profile']=actor(c,uid,now())
        return saved
    def enqueue(peer,event):
        peer['queue'].put_nowait(event)
        # A delta or ACK at version N does not prove the peer received edits
        # committed on another worker at N-1. Only a full canonical snapshot
        # advances the reconciliation cursor; UI version numbers are separate.
        if event['type']=='sync' and event.get('version'):
            peer['canonical_version']=max(peer['canonical_version'],event['version'])
    def receipts(c,sid):
        return {r['user_id']:r['message_id'] for r in c.execute('SELECT r.user_id,r.message_id FROM chat_reads r JOIN space_members m ON m.space_id=r.space_id AND m.user_id=r.user_id WHERE r.space_id=?',(sid,))}
    def live_presence(state,kind):
        return state if kind!='chat' or state and state.get('until',0)>now() else None
    def chat_catchup(peer,target):
        with db() as c:
            state=dict(c.execute('SELECT chat_ttl,chat_cleared FROM spaces WHERE id=?',(target,)).fetchone())
            state['history_version']=history_version(c,target)
            if state!=peer.get('chat_state'):
                peer['chat_state']=state;enqueue(peer,{'type':'chat_state',**state})
            reads=receipts(c,target)
            if reads!=peer.get('reads'):
                peer['reads']=reads;enqueue(peer,{'type':'reads','reads':reads})
            rows=c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.space_id=? AND m.created>? AND (m.expires=0 OR m.expires>?) AND m.id>? ORDER BY m.id ASC LIMIT 128',(target,cutoff(now()),now(),peer['chat_cursor'])).fetchall()
            for row in rows:
                enqueue(peer,{'type':'message','message':serial_message(c,row,now())|{'sender_profile':actor(c,row['sender_id'],now())}})
                peer['chat_cursor']=row['id']
            # Revisions are allocated under the same transaction lock as writes.
            # They form a room-wide edit cursor, independent of message IDs and
            # wall-clock seconds, including multiple edits in the same second.
            rows=c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.space_id=? AND m.created>? AND (m.expires=0 OR m.expires>?) AND m.revision>? ORDER BY m.revision ASC LIMIT 128',(target,cutoff(now()),now(),peer['edit_cursor'])).fetchall()
            for row in rows:
                enqueue(peer,{'type':'message_edited','message':serial_message(c,row,now())|{'sender_profile':actor(c,row['sender_id'],now())}})
                peer['edit_cursor']=row['revision']
    async def publish(room,event,exclude=None):
        for peer in tuple(rooms.get(room,())):
            if peer is exclude:continue
            try:
                auth(peer['ws'],*room)
                if room[0]=='note' and event.get('epoch'):
                    if peer['epoch'] and peer['epoch']!=event['epoch']:
                        await peer['ws'].close(code=4409);peer['stop'].set();continue
                    peer['epoch']=event['epoch']
                if coordinator and room[0]=='chat' and event['type']=='message':
                    # A later commit may be published first by another worker.
                    # Catch up canonically instead of skipping a message gap.
                    previous=peer['chat_cursor'];chat_catchup(peer,room[1])
                    # Preserve an explicit idempotent retry acknowledgement.
                    # Browser consumers merge messages by their durable ID.
                    if event['message']['id']<=previous:enqueue(peer,event)
                else:
                    if coordinator and event['type']=='presence':
                        if event['state'] is None:peer['presence_seen'].pop(event['client_id'],None)
                        else:peer['presence_seen'][event['client_id']]=event['state']
                    enqueue(peer,event)
            except (HTTPError,asyncio.QueueFull):
                # Do not leak even one event to a removed member or revoked session.
                peer['stop'].set()
    async def reconcile():
        if not rooms:return
        registered=await coordinator.peers('realtime')
        live={r['token'] for r in registered}
        try:
            async with gate.shared():
                for room,peers in tuple(rooms.items()):
                    kind,target=room
                    remote=[r for r in registered if r['room']==room]
                    document=None
                    if kind=='note':
                        # Idle sockets poll only the small generation/version
                        # tuple, not a potentially 1.5-MB encrypted document.
                        with db() as c:current=c.execute('SELECT d.epoch,n.version FROM collaborative_docs d JOIN notes n ON n.id=d.note_id WHERE d.note_id=?',(target,)).fetchone()
                    for peer in tuple(peers):
                        if peer['stop'].is_set():continue
                        try:
                            auth(peer['ws'],kind,target)
                            if peer['lease'].token not in live:
                                peer['stop'].set();continue
                            if kind=='note':
                                if peer['epoch'] and (not current or current['epoch']!=peer['epoch']):
                                    await peer['ws'].close(code=4409);peer['stop'].set();continue
                                if current and (peer['epoch'] is None or current['version']>peer['canonical_version']):
                                    if document is None:
                                        with db() as c:document=c.execute('SELECT d.state,d.epoch,n.version FROM collaborative_docs d JOIN notes n ON n.id=d.note_id WHERE d.note_id=?',(target,)).fetchone()
                                    if not document or (peer['epoch'] and document['epoch']!=peer['epoch']):
                                        await peer['ws'].close(code=4409);peer['stop'].set();continue
                                    peer['epoch']=document['epoch']
                                    enqueue(peer,{'type':'sync','state':base64.b64encode(document['state']).decode(),'epoch':document['epoch'],'version':document['version']})
                            else:chat_catchup(peer,target)
                            presence={r['cid']:state for r in remote if r['token']!=peer['lease'].token and (state:=live_presence(r['metadata'].get('presence'),kind)) is not None}
                            old=peer['presence_seen']
                            for cid in old.keys()-presence.keys():enqueue(peer,{'type':'presence','client_id':cid,'state':None})
                            for cid,state in presence.items():
                                if old.get(cid)!=state:enqueue(peer,{'type':'presence','client_id':cid,'state':state})
                            peer['presence_seen']=presence
                        except HTTPError as e:
                            if e.status_code!=503:peer['stop'].set()
                        except asyncio.QueueFull:peer['stop'].set()
        except HTTPError as e:
            if e.status_code!=503:raise
    if coordinator:coordinator.subscribe(reconcile)
    def saved_preferences(settings,members):
        value=settings.get('chat',{});value=value if isinstance(value,dict) else {}
        return {key:list(dict.fromkeys(sid for sid in value.get(key,[]) if isinstance(sid,str) and sid in members)) if isinstance(value.get(key,[]),list) else [] for key in ('favorites','pinned')}
    def unread_messages(c,uid):
        result=[]
        for row in c.execute("SELECT id,payload FROM notifications WHERE user_id=? AND kind='message' AND read=0 AND created>?",(uid,now()-NOTIFICATION_RETENTION)):
            payload=json.loads(row['payload'])
            if isinstance(payload,dict) and isinstance(payload.get('space_id'),str) and type(payload.get('message_id')) is int and c.execute('SELECT 1 FROM messages WHERE id=? AND space_id=? AND created>? AND (expires=0 OR expires>?)',(payload['message_id'],payload['space_id'],cutoff(now()),now())).fetchone():result.append((row['id'],payload['space_id'],payload['message_id']))
        return result
    @router.get('/api/chat/typing')
    async def typing(request:Request):
        s=guards('chat')(request);uid=s['user_id']
        with db() as c:
            allowed={r[0] for r in c.execute('SELECT space_id FROM space_members WHERE user_id=?',(uid,))}
            members={sid:{r[0] for r in c.execute('SELECT user_id FROM space_members WHERE space_id=?',(sid,))} for sid in allowed}
        peers=await coordinator.peers('realtime') if coordinator else [{'room':room,'metadata':{'presence':p['presence']}} for room,group in rooms.items() for p in group]
        result={}
        for p in peers:
            kind,sid=p['room'];state=live_presence(p['metadata'].get('presence'),'chat') if kind=='chat' else None
            if sid in allowed and state and state.get('user',{}).get('id') in members[sid]-{uid}:result.setdefault(sid,[]).append(state)
        return {'rooms':result,'server_time':now()}
    @router.get('/api/chat/rooms')
    async def chats(request:Request):
        s=guards('chat')(request);uid=s['user_id'];stamp=now()
        with db() as c:
            spaces=c.execute('SELECT s.*,u.login AS owner_login FROM spaces s JOIN space_members m ON m.space_id=s.id JOIN users u ON u.id=s.owner_id WHERE m.user_id=?',(uid,)).fetchall()
            member_ids={row['id'] for row in spaces};settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]);preferences=saved_preferences(settings,member_ids)
            counts={sid:0 for sid in member_ids}
            # Chat reads belong to the conversation, not to the bell. Suppressed
            # alerts and "read all notifications" must not read another room.
            for row in c.execute('SELECT m.space_id,COUNT(*) AS unread FROM messages m JOIN space_members sm ON sm.space_id=m.space_id LEFT JOIN chat_reads r ON r.space_id=m.space_id AND r.user_id=sm.user_id WHERE sm.user_id=? AND m.sender_id!=? AND m.id>COALESCE(r.message_id,0) AND m.created>=sm.created AND m.created>? AND (m.expires=0 OR m.expires>?) GROUP BY m.space_id',(uid,uid,cutoff(stamp),stamp)):
                counts[row['space_id']]=row['unread']
            items=[];profiles=templates(c)
            for row in spaces:
                item=dict(row);sid=item['id'];item['owner']=item.pop('owner_id')==uid
                from app.workspace_lifecycle import pending
                item['deletion']=pending(c,sid)
                item['manage']=is_manager(c,sid,uid)
                item['template']=profiles.get(sid,'family') if item['kind']=='family' else None
                item['avatar_url']=avatar_url(c,sid) if item['kind']=='family' else None
                item['members']=sorted([actor(c,member[0],stamp) for member in c.execute('SELECT user_id FROM space_members WHERE space_id=?',(sid,))],key=lambda member:member['login'])
                if item['kind']=='friends':item['name']=next(((member.get('display_name') or member['login']) for member in item['members'] if member['id']!=uid),'Друг')
                latest=c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.space_id=? AND m.created>? AND (m.expires=0 OR m.expires>?) ORDER BY m.id DESC LIMIT 1',(sid,cutoff(stamp),stamp)).fetchone()
                item['last_message']=serial_message(c,latest,stamp)|{'sender_profile':actor(c,latest['sender_id'],stamp)} if latest else None
                from app.chat_notifications import mute_until
                item['mute_until']=mute_until(settings,sid,stamp)
                item.update(unread=counts[sid],favorite=sid in preferences['favorites'],pinned=sid in preferences['pinned'])
                if item.get('deletion'):item['last_message']=None;item['unread']=0;counts[sid]=0
                items.append(item)
        items.sort(key=lambda item:(not item['pinned'],-(item['last_message']['created'] if item['last_message'] else item['created']),-(item['last_message']['id'] if item['last_message'] else 0),item['id']))
        # Loading ordinary conversations must never wait for the external archive.
        return {'items':items,'unread':sum(counts.values())}
    @router.get('/api/chat/unread')
    def unread(request:Request):
        s=guards('chat')(request);uid=s['user_id'];stamp=now()
        cache=getattr(getattr(db,'__self__',None),'redis_cache',None)
        ticket=None
        if cache:
            ticket,hit=cache.lookup('chat-unread',uid,stamp)
            if hit is not None:return {'unread':hit[0]}
        with db() as c:
            from app.workspace_lifecycle import deletions
            removed=deletions(c)
            counts=[dict(row) for row in c.execute('SELECT m.space_id,COUNT(*) AS unread,MIN(m.created) AS oldest,MIN(NULLIF(m.expires,0)) AS expiry FROM messages m JOIN space_members sm ON sm.space_id=m.space_id LEFT JOIN chat_reads r ON r.space_id=m.space_id AND r.user_id=sm.user_id WHERE sm.user_id=? AND m.sender_id!=? AND m.id>COALESCE(r.message_id,0) AND m.created>=sm.created AND m.created>? AND (m.expires=0 OR m.expires>?) GROUP BY m.space_id',(uid,uid,cutoff(stamp),stamp)) if row['space_id'] not in removed]
            count=sum(row['unread'] for row in counts)
        deadline=min((row['expiry'] for row in counts if row['expiry']),default=stamp+CACHE_SECONDS)
        if any(row['oldest']<=cutoff(stamp+CACHE_SECONDS) for row in counts):deadline=min(deadline,stamp+1)
        if cache:cache.put(ticket,count,deadline,stamp)
        return {'unread':count}
    @router.post('/api/chat/{sid}/read')
    async def read_chat(sid:str,data:ReadCursor,request:Request):
        s=guards('chat')(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');space_access(c,sid,s['user_id'],fail)
            if not c.execute('SELECT 1 FROM messages WHERE space_id=? AND id=? AND created>? AND (expires=0 OR expires>?)',(sid,data.message_id,cutoff(now()),now())).fetchone():fail(404,localized_message('theMessageIsNotAvailable'))
            c.execute('INSERT INTO chat_reads(space_id,user_id,message_id) VALUES(?,?,?) ON CONFLICT(space_id,user_id) DO UPDATE SET message_id=CASE WHEN excluded.message_id>chat_reads.message_id THEN excluded.message_id ELSE chat_reads.message_id END',(sid,s['user_id'],data.message_id))
            reads=receipts(c,sid)
            changed=0
            for nid,space_id,mid in unread_messages(c,s['user_id']):
                if space_id==sid and mid<=reads[s['user_id']]:changed+=c.execute('UPDATE notifications SET read=1 WHERE id=? AND user_id=?',(nid,s['user_id'])).rowcount
        await publish(('chat',sid),{'type':'reads','reads':reads})
        return {'ok':True,'read':changed}
    @router.put('/api/chat/{sid}/notifications')
    def notification_preferences(sid:str,data:ChatNotificationPreferences,request:Request):
        from app.chat_notifications import KEY,MUTE_DURATIONS,mute_until
        s=guards('chat')(request);uid=s['user_id'];stamp=now()
        family=sid.startswith('telegram:')
        if data.seconds not in (0,*MUTE_DURATIONS,*((-1,) if family else ())):fail(422,localized_message('selectTheAvailableNotificationDisablingPeriod'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT role,settings FROM users WHERE id=?',(uid,)).fetchone()
            settings=json.loads(row['settings'])
            if family:
                from app.modules.family_photo_events import has_access
                if not has_access(settings,row['role'],sid[9:]):fail(403,localized_message('accessToTheFamilyConversationIsClosed'))
            else:space_access(c,sid,uid,fail)
            previous=settings.get(KEY,{})
            values={key:mute_until(settings,key,stamp) for key in previous if isinstance(key,str) and mute_until(settings,key,stamp)} if isinstance(previous,dict) else {}
            values.pop(sid,None)
            if data.seconds:
                if len(values)>=512:fail(422,localized_message('noMoreThan512ConversationsWithDisabledNotifications'))
                values[sid]=-1 if data.seconds==-1 else stamp+data.seconds
            settings[KEY]=values
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
            # Cancel queued alerts too, so unmuting never sends old messages.
            for notice in c.execute("SELECT id,payload FROM notifications WHERE user_id=? AND kind IN ('message','family_message')",(uid,)).fetchall():
                if json.loads(notice['payload']).get('space_id')==sid:
                    c.execute('DELETE FROM push_deliveries WHERE notification_id=?',(notice['id'],))
        return {'mute_until':values.get(sid,0)}
    @router.put('/api/chat/{sid}/preferences')
    def preferences(sid:str,data:ChatPreferences,request:Request):
        s=guards('chat')(request)
        if any(value is None for value in data.model_dump(exclude_unset=True).values()):fail(422,localized_message('specifyWhetherToEnableOrDisableTheSetting'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');space_access(c,sid,s['user_id'],fail)
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0]);members={row[0] for row in c.execute('SELECT space_id FROM space_members WHERE user_id=?',(s['user_id'],))};value=saved_preferences(settings,members)
            for field,key in (('favorite','favorites'),('pinned','pinned')):
                selected=getattr(data,field)
                if selected is not None:
                    if selected and sid not in value[key] and len(value[key])>=256:fail(422,localized_message('inFavoritesAndPinnedThereCanBeNo'))
                    value[key]=[item for item in value[key] if item!=sid]+([sid] if selected else [])
            settings['chat']=value;c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
        return {'favorite':sid in value['favorites'],'pinned':sid in value['pinned']}
    def manage_history(c,sid,uid):
        space=space_access(c,sid,uid,fail)
        if space['kind']=='family' and not is_manager(c,sid,uid):fail(403,localized_message('theConversationHistoryIsManagedByTheWorkspace'))
        return space
    @router.put('/api/chat/{sid}/timer')
    async def timer(sid:str,data:ChatTimer,request:Request):
        s=guards('chat')(request)
        if data.seconds not in CHAT_TIMERS:fail(422,localized_message('selectTheAutoDeletionPeriod'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');manage_history(c,sid,s['user_id'])
            c.execute('UPDATE spaces SET chat_ttl=? WHERE id=?',(data.seconds,sid))
            state=dict(c.execute('SELECT chat_ttl,chat_cleared FROM spaces WHERE id=?',(sid,)).fetchone())
        await publish(('chat',sid),{'type':'chat_state',**state})
        return state
    @router.post('/api/chat/{sid}/clear')
    async def clear(sid:str,request:Request):
        s=guards('chat')(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');manage_history(c,sid,s['user_id'])
            cleared=c.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE space_id=?',(sid,)).fetchone()[0]
            c.execute('UPDATE spaces SET chat_cleared=CASE WHEN chat_cleared>? THEN chat_cleared ELSE ? END WHERE id=?',(cleared,cleared,sid))
            # Retire only sent attachments from this room; leave drafts, notes,
            # regular files, friendships and call history intact.
            for row in c.execute("SELECT * FROM files WHERE id LIKE ? AND version>1",('chat-'+sid+'-%',)).fetchall():
                if attachment_room(row['id'])==sid:retire(c,row,now())
            c.execute('DELETE FROM messages WHERE space_id=?',(sid,))
            for row in c.execute("SELECT id,payload FROM notifications WHERE kind='message'").fetchall():
                if json.loads(row['payload']).get('space_id')==sid:c.execute('DELETE FROM notifications WHERE id=?',(row['id'],))
            state=dict(c.execute('SELECT chat_ttl,chat_cleared FROM spaces WHERE id=?',(sid,)).fetchone())
        await publish(('chat',sid),{'type':'chat_state',**state})
        return state
    @router.get('/api/chat/{sid}/search')
    def search(sid:str,request:Request,q:str=Query(min_length=1,max_length=120),before:int|None=Query(default=None,ge=1,le=2**53-1)):
        s=guards('chat')(request);query=q.strip().casefold();stamp=now()
        if not query:fail(422,localized_message('enterTheTextToSearchFor'))
        protection.rate(request,'chat-search',60,subject=s['user_id'])
        with db() as c:
            space_access(c,sid,s['user_id'],fail)
            # PostgreSQL encrypts message bodies at rest; search plaintext only
            # after the storage adapter decrypts this bounded, authorized batch.
            rows=c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.space_id=? AND m.created>? AND (m.expires=0 OR m.expires>?) AND (? IS NULL OR m.id<?) ORDER BY m.id DESC LIMIT 1001',(sid,cutoff(stamp),stamp,before,before)).fetchall()
            items=[];next_before=None;scanned=0
            for row in rows[:1000]:
                scanned+=1;next_before=row['id']
                if query in decode(row['body'])[0].casefold():items.append(serial_message(c,row,stamp)|{'sender_profile':actor(c,row['sender_id'],stamp)})
                if len(items)==100:break
            more=scanned<len(rows)
        items.reverse()
        return {'items':items,'more':more,'next_before':next_before if more else None,'retention_cutoff':cutoff(stamp),'retention_months':3}
    @router.get('/api/chat/{sid}/messages')
    def history(sid:str,request:Request,before:int|None=Query(default=None,ge=1),after:int|None=Query(default=None,ge=0),edited_after:int|None=Query(default=None,ge=0,le=2**53-1)):
        s=guards('chat')(request)
        if before is not None and after is not None:fail(422,localized_message('selectOneHistoryCursor'))
        with db() as c:
            space_access(c,sid,s['user_id'],fail)
            # Capture the cursor before reading bodies: an edit committed between
            # these queries must remain eligible for the next catch-up request.
            reads=receipts(c,sid)
            state=dict(c.execute('SELECT chat_revision,chat_ttl,chat_cleared FROM spaces WHERE id=?',(sid,)).fetchone());edit_cursor=state.pop('chat_revision')
            state['history_version']=history_version(c,sid)
            rows=c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.space_id=? AND m.created>? AND (m.expires=0 OR m.expires>?) AND (? IS NULL OR m.id<?) AND (? IS NULL OR m.id>?) ORDER BY m.id '+('ASC' if after is not None else 'DESC')+' LIMIT 101',(sid,cutoff(now()),now(),before,before,after,after)).fetchall()
            items=[serial_message(c,row,now())|{'sender_profile':actor(c,row['sender_id'],now())} for row in rows[:100]]
            edits=[];edit_more=False
            if edited_after is not None:
                changed=c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.space_id=? AND m.created>? AND (m.expires=0 OR m.expires>?) AND m.revision>? ORDER BY m.revision ASC LIMIT 101',(sid,cutoff(now()),now(),edited_after)).fetchall()
                from app.reactions import changed as changed_reactions
                reactions=changed_reactions(c,sid,edited_after);combined={row['id']:row for row in changed}
                for mid in reactions:
                    row=c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.space_id=? AND m.id=? AND m.created>? AND (expires=0 OR expires>?)',(sid,mid,cutoff(now()),now())).fetchone()
                    if row:combined[mid]=row
                ordered=sorted(combined.values(),key=lambda row:max(row['revision'],reactions.get(row['id'],0)))
                edits=[serial_message(c,row,now())|{'sender_profile':actor(c,row['sender_id'],now())} for row in ordered[:100]]
                edit_more=len(ordered)>100;edit_cursor=max(edits[-1]['revision'],edits[-1]['reaction_revision']) if edits else edited_after

        if after is None:items.reverse()
        return {**state,'reads':reads,'items':items,'more':len(rows)>100,'edits':edits,'edit_more':edit_more,'edit_cursor':edit_cursor,'server_time':now(),'retention_cutoff':cutoff(now()),'retention_months':3,'max_attachment_bytes':MAX_ATTACHMENT}
    @router.post('/api/chat/{sid}/messages')
    async def send(sid:str,data:Message,request:Request):
        s=guards('chat')(request);protection.rate(request,'chat-send',60,subject=s['user_id'])
        with db() as c:
            c.execute('BEGIN IMMEDIATE');space_access(c,sid,s['user_id'],fail);saved=message(c,sid,s['user_id'],data)
        await publish(('chat',sid),{'type':'message','message':saved})
        return saved
    from app.reactions import Reaction
    @router.post('/api/chat/{sid}/messages/{mid}/reactions')
    async def react(sid:str,mid:int,data:Reaction,request:Request):
        s=guards('chat')(request);protection.rate(request,'chat-reaction',60,subject=s['user_id']);stamp=now()
        with db() as c:
            c.execute('BEGIN IMMEDIATE');space_access(c,sid,s['user_id'],fail)
            row=c.execute('SELECT * FROM messages WHERE space_id=? AND id=? AND created>? AND (expires=0 OR expires>?)',(sid,mid,cutoff(stamp),stamp)).fetchone()
            if not row:fail(404,localized_message('theMessageIsNotAvailable'))
            from app.reactions import toggle
            c.execute('UPDATE spaces SET chat_revision=chat_revision+1 WHERE id=?',(sid,))
            revision=c.execute('SELECT chat_revision FROM spaces WHERE id=?',(sid,)).fetchone()[0]
            reactions=toggle(c,sid,mid,s['user_id'],data.emoji,stamp,fail,revision=revision)
            saved=serial_message(c,c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id=?',(mid,)).fetchone(),stamp)
            saved['sender_profile']=actor(c,row['sender_id'],stamp)
        await publish(('chat',sid),{'type':'message_edited','message':saved})
        return {'reactions':reactions,'message':saved}

    @router.delete('/api/chat/{sid}/messages/{mid}')
    async def delete_message(sid:str,mid:int,request:Request):
        s=guards('chat')(request);protection.rate(request,'chat-delete',60,subject=s['user_id'])
        if not 1<=mid<=2**53-1:fail(404,localized_message('theMessageIsNotAvailable'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE');space_access(c,sid,s['user_id'],fail);stamp=now()
            row=c.execute('SELECT * FROM messages WHERE space_id=? AND id=?',(sid,mid)).fetchone()
            # Safe retry after a lost response; never reveal messages in another room.
            if not row:return {'ok':True}
            if row['sender_id']!=s['user_id']:fail(403,localized_message('youCanOnlyDeleteYourOwnMessages'))
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(deletion_key(sid,s['user_id'],row['client_id']),str(stamp)))
            _,ids,_=envelope(row['body'])
            for fid in ids:
                file=c.execute('SELECT * FROM files WHERE id=? AND user_id=? AND version=?',(fid,s['user_id'],mid+1)).fetchone()
                if file and attachment_room(fid)==sid:retire(c,file,stamp)
            from app.reactions import key as reaction_key
            c.execute('DELETE FROM app_settings WHERE key=?',(reaction_key(sid,mid),))
            c.execute('DELETE FROM messages WHERE space_id=? AND id=?',(sid,mid))
            for notice in c.execute("SELECT id,payload FROM notifications WHERE kind='message'").fetchall():
                payload=json.loads(notice['payload'])
                if payload.get('space_id')==sid and payload.get('message_id')==mid:c.execute('DELETE FROM notifications WHERE id=?',(notice['id'],))
            c.execute('UPDATE spaces SET chat_revision=chat_revision+1 WHERE id=?',(sid,))
            version=c.execute('SELECT chat_revision FROM spaces WHERE id=?',(sid,)).fetchone()[0]
            # One scalar per room invalidates loaded history, including old pages
            # and quotes, without a tombstone log or a schema change.
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(HISTORY_VERSION_PREFIX+sid,str(version)))
            state=dict(c.execute('SELECT chat_ttl,chat_cleared FROM spaces WHERE id=?',(sid,)).fetchone())
        await publish(('chat',sid),{'type':'chat_state',**state,'history_version':version})
        return {'ok':True}

    @router.put('/api/chat/{sid}/messages/{mid}')
    async def edit_message(sid:str,mid:int,data:MessageEdit,request:Request):
        s=guards('chat')(request);protection.rate(request,'chat-edit',60,subject=s['user_id'])
        with db() as c:
            c.execute('BEGIN IMMEDIATE');space_access(c,sid,s['user_id'],fail);stamp=now()
            row=c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.space_id=? AND m.id=? AND m.created>? AND (m.expires=0 OR m.expires>?)',(sid,mid,cutoff(stamp),stamp)).fetchone()
            if not row:fail(404,localized_message('theMessageIsNotAvailable'))
            if row['sender_id']!=s['user_id']:fail(403,localized_message('youCanOnlyModifyYourOwnMessages'))
            if stamp>=row['created']+EDIT_SECONDS:fail(403,localized_message('youCanOnlyModifyTheMessageWithin24'))
            text,attachments,reply=envelope(row['body']);updated=data.body.strip()
            if data.revision!=row['revision'] and updated!=text:fail(409,localized_message('theMessageHasAlreadyBeenModifiedOnAnother'))
            if not updated and not attachments:fail(422,localized_message('theMessageTextCannotBeEmpty'))
            changed=updated!=text
            if changed:
                c.execute('UPDATE spaces SET chat_revision=chat_revision+1 WHERE id=?',(sid,))
                revision=c.execute('SELECT chat_revision FROM spaces WHERE id=?',(sid,)).fetchone()[0]
                c.execute('UPDATE messages SET body=?,edited=?,revision=? WHERE id=?',(encode(updated,attachments,reply),stamp,revision,mid))
                # Refresh existing inbox previews without creating another unread
                # notification or enqueueing another push for the same message.
                for member in c.execute('SELECT user_id FROM space_members WHERE space_id=?',(sid,)):
                    for notification in c.execute("SELECT id,payload FROM notifications WHERE user_id=? AND kind='message' AND created>=?",(member[0],row['created'])):
                        payload=json.loads(notification['payload'])
                        if payload.get('space_id')==sid and payload.get('message_id')==mid:
                            payload['preview']=updated[:180] or 'Вложение';c.execute('UPDATE notifications SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),notification['id']))
            saved=serial_message(c,c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id=?',(mid,)).fetchone(),stamp)|{'sender_profile':actor(c,s['user_id'],stamp)}
        if changed:await publish(('chat',sid),{'type':'message_edited','message':saved})
        return saved
    async def socket(ws,kind,target):
        room=(kind,target);peer=None;writer=None;monitor=None;lease=None;receive=None;stop=None
        try:
            # Cookies alone are insufficient: strict Origin plus a CSRF handshake.
            if ws.headers.get('origin','').rstrip('/')!=public_url:await ws.close(code=4403);return
            async with gate.shared():
                s,req=auth(ws,kind,target)
                protection.rate(req,'socket-connect',120,subject=s['user_id'])
            if not coordinator and sum(p['uid']==s['user_id'] for peers in rooms.values() for p in peers)>=12:await ws.close(code=4429);return
            await ws.accept()
            hello=await asyncio.wait_for(ws.receive_text(),10)
            if len(hello)>2000:fail(413,localized_message('tooLargeRequest'))
            hello=json.loads(hello)
            if not isinstance(hello,dict) or hello.get('type')!='auth':fail(403,localized_message('connectionConfirmationRequired'))
            tokens.check_csrf(SimpleNamespace(headers={'x-csrf-token':str(hello.get('csrf',''))}),s['csrf'])
            cid=hello.get('client_id')
            if type(cid) is not int or not 0<=cid<=2**53:fail(422,localized_message('invalidClient'))
            if coordinator:
                try:lease=await coordinator.register_peer('realtime',s['user_id'],room,cid,{'presence':None})
                except PeerLimit:await ws.close(code=4429);return
                except PeerCollision:fail(403,localized_message('clientIdentifierIsAlreadyInUse'))
            for old in tuple(rooms.get(room,())):
                if old['cid']==cid:
                    if old['uid']!=s['user_id']:fail(403,localized_message('clientIdentifierIsAlreadyInUse'))
                    # A half-open old TCP connection must not prevent reconnecting
                    # the same authenticated Yjs client with unsent local edits.
                    old['stop'].set();rooms[room].remove(old)
            peer={'ws':ws,'uid':s['user_id'],'login':s['login'],'cid':cid,'presence':None,'queue':asyncio.Queue(256),'stop':asyncio.Event(),'epoch':None,'canonical_version':0,'chat_cursor':0,'edit_cursor':0,'presence_seen':{},'lease':lease}
            rooms.setdefault(room,[]).append(peer)
            async def write():
                while not peer['stop'].is_set():
                    event=await peer['queue'].get()
                    async with gate.shared_wait():
                        auth(ws,kind,target)
                        event_epoch=event.get('epoch') or (peer['epoch'] if event['type']=='ack' else None)
                        if kind=='note' and event_epoch:
                            with db() as c:current=c.execute('SELECT epoch FROM collaborative_docs WHERE note_id=?',(target,)).fetchone()
                            if not current or current[0]!=event_epoch:
                                await ws.close(code=4409);peer['stop'].set();return
                    try:await asyncio.wait_for(ws.send_json(event),5)
                    finally:peer['queue'].task_done()
            async def watch():
                while not peer['stop'].is_set():
                    await asyncio.sleep(2)
                    async with gate.shared_wait():
                        auth(ws,kind,target)
                        if kind=='chat' and not coordinator:chat_catchup(peer,target)
                        if kind=='note' and peer['epoch']:
                            with db() as c:current=c.execute('SELECT epoch FROM collaborative_docs WHERE note_id=?',(target,)).fetchone()
                            if not current or current[0]!=peer['epoch']:await ws.close(code=4409);peer['stop'].set();return
            writer=asyncio.create_task(write());monitor=asyncio.create_task(watch())
            async with gate.shared_wait():
                auth(ws,kind,target)
                if kind=='note':
                    with db() as c:
                        n=note_access(c,target,s['user_id'],fail);doc=c.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(target,)).fetchone()
                    if doc:
                        peer['epoch']=doc['epoch'];enqueue(peer,{'type':'sync','state':base64.b64encode(doc['state']).decode(),'epoch':doc['epoch'],'version':n['version']})
                    else:
                        page=dict(n);page['blocks']=json.loads(page['blocks']) if page['blocks'] else None
                        peer['queue'].put_nowait({'type':'init','page':{k:page[k] for k in ('title','body','blocks','version')}})
                else:
                    with db() as c:peer['reads']=receipts(c,target)
                    enqueue(peer,{'type':'reads','reads':peer['reads']})
                    if coordinator:
                        with db() as c:
                            peer['chat_cursor']=c.execute('SELECT COALESCE(MAX(id),0) FROM messages WHERE space_id=?',(target,)).fetchone()[0]
                            peer['edit_cursor']=c.execute('SELECT chat_revision FROM spaces WHERE id=?',(target,)).fetchone()[0]
                    peer['queue'].put_nowait({'type':'ready'})
                if coordinator:
                    others=await coordinator.peers('realtime',room)
                    for other in others:
                        state=live_presence(other['metadata'].get('presence'),kind)
                        if other['token']!=lease.token and state is not None:
                            peer['presence_seen'][other['cid']]=state
                            enqueue(peer,{'type':'presence','client_id':other['cid'],'state':state})
                else:
                    for other in rooms[room]:
                        if other is not peer and (state:=live_presence(other['presence'],kind)):peer['queue'].put_nowait({'type':'presence','client_id':other['cid'],'state':state})
            async def process(raw):
                if len(raw)>2100000:fail(413,localized_message('tooLargeRequest'))
                data=json.loads(raw)
                if not isinstance(data,dict):fail(422,localized_message('invalidMessage'))
                async with gate.shared_wait():
                    current,request=auth(ws,kind,target)
                    if data.get('type')=='presence':
                        protection.rate(request,'presence',1500,subject=current['user_id'])
                        state=data.get('state')
                        if state is not None and (not isinstance(state,dict) or len(json.dumps(state))>8000):fail(422,localized_message('invalidCursor'))
                        if kind=='chat':
                            if state is not None and (set(state)!={'typing'} or type(state['typing']) is not bool):fail(422,localized_message('invalidInputField'))
                            with db() as c:person=actor(c,current['user_id'],now())
                            state={'user':{'id':current['user_id'],'name':person.get('display_name') or person['login']},'until':now()+6} if state and state['typing'] else None
                        elif state is not None:
                            cursor=state.get('cursor')
                            if cursor is not None:
                                if not isinstance(cursor,dict) or set(cursor)!={'anchor','head'}:fail(422,localized_message('invalidCursor'))
                                for pos in cursor.values():
                                    if not isinstance(pos,dict) or set(pos)-{'type','tname','item','assoc'}:fail(422,localized_message('invalidCursorPosition'))
                                    if 'assoc' in pos and (type(pos['assoc']) is not int or not -1<=pos['assoc']<=1):fail(422,localized_message('invalidCursorPosition'))
                                    if pos.get('tname') is not None and (not isinstance(pos['tname'],str) or len(pos['tname'])>100):fail(422,localized_message('invalidCursorPosition'))
                                    for key in ('type','item'):
                                        value=pos.get(key)
                                        if value is not None and (not isinstance(value,dict) or set(value)!={'client','clock'} or any(type(n) is not int or not 0<=n<=2**53 for n in value.values())):fail(422,localized_message('invalidCursorPosition'))
                            state={'cursor':cursor} if cursor is not None else {}
                            state['user']={'id':current['user_id'],'name':current['login'],'color':'#'+current['user_id'][:6]}
                        peer['presence']=state
                        if lease:await lease.update({'presence':state})
                        await publish(room,{'type':'presence','client_id':cid,'state':state},peer);return
                    if data.get('type')=='ping':peer['queue'].put_nowait({'type':'pong'});return
                    if kind=='chat':
                        if data.get('type')!='message':fail(422,localized_message('unknownAction'))
                        parsed=Message.model_validate({k:data[k] for k in ('body','client_id','attachments','reply_to') if k in data});protection.rate(request,'chat-send',60,subject=current['user_id'])
                        with db() as c:
                            c.execute('BEGIN IMMEDIATE');space_access(c,target,current['user_id'],fail);saved=message(c,target,current['user_id'],parsed)
                        await publish(room,{'type':'message','message':saved});return
                    if data.get('type') not in ('init','update'):fail(422,localized_message('unknownAction'))
                    protection.rate(request,'collaboration',1500,subject=current['user_id'])
                    encoded=data.get('update')
                    if not isinstance(encoded,str) or len(encoded)>MAX_STATE*4//3+10:fail(413,localized_message('pageIsTooLarge'))
                    update=base64.b64decode(encoded,validate=True)
                    with db() as c:
                        c.execute('BEGIN IMMEDIATE');n=note_access(c,target,current['user_id'],fail);require_role(c,n,current['user_id'],fail,session=current,now=now);saved=c.execute('SELECT * FROM collaborative_docs WHERE note_id=?',(target,)).fetchone()
                        if data['type']=='init' and saved:
                            peer['epoch']=saved['epoch'];enqueue(peer,{'type':'sync','state':base64.b64encode(saved['state']).decode(),'epoch':saved['epoch'],'version':n['version']});return
                        if data['type']=='init' and data.get('version')!=n['version']:fail(409,localized_message('thePageHasBeenChangedOpenItAgain'))
                        if data['type']=='update' and (not saved or data.get('epoch')!=saved['epoch']):fail(409,localized_message('theVersionOfTheSharedDocumentHasBeen'))
                        doc=load_doc(saved['state'] if saved else None);doc.apply_update(update);state=doc.get_update()
                        if len(state)>MAX_STATE:fail(413,localized_message('pageIsTooLarge'))
                        title,body,blocks=project(doc,fail)
                        from app.note_subpages import reconcile_links
                        reconcile_links(c,n,blocks,current,now(),fail)
                        links_ok(c,blocks,n['space_id'],fail,json.loads(n['blocks']) if n['blocks'] else [])
                        from app.modules.note_files import validate_file_links
                        validate_file_links(c,target,body,blocks,current['user_id'],fail,(n['blocks'] or '')+n['body'])
                        epoch=saved['epoch'] if saved else secrets.token_urlsafe(16)
                        # One history checkpoint per minute rather than per keystroke.
                        if (title,body,json.dumps(blocks,ensure_ascii=False))!=(n['title'],n['body'],n['blocks']):
                            from app.note_history import snapshot
                            snapshot(c,n,now(),interval=60)
                        c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?) ON CONFLICT(note_id) DO UPDATE SET state=excluded.state,updated=excluded.updated',(target,state,epoch,now()))
                        c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',(title,body,json.dumps(blocks,ensure_ascii=False),now(),target))
                        version=c.execute('SELECT version FROM notes WHERE id=?',(target,)).fetchone()[0]
                    peer['epoch']=epoch
                    if data['type']=='init':
                        # Every waiting initializer receives this exact canonical document.
                        for p in rooms.get(room,()):
                            if p['epoch'] is None:p['epoch']=epoch
                        await publish(room,{'type':'sync','state':base64.b64encode(state).decode(),'epoch':epoch,'version':version})
                    else:
                        await publish(room,{'type':'update','update':encoded,'epoch':epoch,'version':version},peer)
                        enqueue(peer,{'type':'ack','seq':data.get('seq'),'version':version})
            while not peer['stop'].is_set():
                receive=asyncio.create_task(ws.receive_text());stop=asyncio.create_task(peer['stop'].wait())
                done,_=await asyncio.wait([receive,stop,writer,monitor],return_when=asyncio.FIRST_COMPLETED)
                stop.cancel()
                if receive not in done:
                    receive.cancel()
                    with suppress(asyncio.CancelledError):await receive
                    break
                try:await process(receive.result())
                except HTTPError as e:
                    peer['queue'].put_nowait({'type':'error','message':e.detail,'status':e.status_code})
                    if e.status_code in (401,403,404,409):
                        with suppress(asyncio.TimeoutError):await asyncio.wait_for(peer['queue'].join(),1)
                        if e.status_code==409:await ws.close(code=4409)
                        break
                except (ValueError,TypeError,KeyError,OverflowError):
                    peer['queue'].put_nowait({'type':'error','message':'Некорректный документ или сообщение.','status':422})
        except (HTTPError,WebSocketDisconnect,asyncio.TimeoutError,ValueError,RuntimeError,KeyError,TypeError,PeerSuperseded):pass
        finally:
            for task in (writer,monitor,receive,stop):
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError,Exception):await task
            if lease:await lease.close()
            if peer:
                peers=rooms.get(room,[])
                if peer in peers:peers.remove(peer)
                if not peers:rooms.pop(room,None)
                current=await coordinator.peers('realtime',room) if coordinator else []
                if not any(p['cid']==peer['cid'] for p in rooms.get(room,())) and not any(p['cid']==peer['cid'] for p in current):
                    await publish(room,{'type':'presence','client_id':peer['cid'],'state':None})
            with suppress(RuntimeError,WebSocketDisconnect):await ws.close(code=4401)
    @router.websocket('/api/live/notes/{nid}')
    async def note_socket(ws:WebSocket,nid:str):await socket(ws,'note',nid)
    @router.websocket('/api/live/chat/{sid}')
    async def chat_socket(ws:WebSocket,sid:str):await socket(ws,'chat',sid)
    note_socket.origin=chat_socket.origin=public_url
    note_socket.authorize=lambda request,nid:auth(request,'note',nid)
    chat_socket.authorize=lambda request,sid:auth(request,'chat',sid)
    async def publication_update(nid,state,update,epoch,version,initialized):
        room=('note',nid)
        if initialized:
            for peer in tuple(rooms.get(room,())):
                if peer['epoch'] is None:peer['epoch']=epoch
            event={'type':'sync','state':base64.b64encode(state).decode(),'epoch':epoch,'version':version}
        else:event={'type':'update','update':base64.b64encode(update).decode(),'epoch':epoch,'version':version}
        await publish(room,event)
    router.publication_update=publication_update
    return router
