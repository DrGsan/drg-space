"""Durable private inbox, session alerts and relationship-scoped presence."""
from app.i18n import message as localized_message
import asyncio
import hashlib
import json
import secrets
import time
from contextlib import suppress
from types import SimpleNamespace
from app.http import Router,Request,WebSocket,WebSocketDisconnect,HTTPError,Query
from pydantic import BaseModel,ConfigDict
from app.redis_cache import CACHE_SECONDS

RETENTION=7*86400
CHAT_ACTIVITY_TTL=30
class NotificationPreferences(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sound:bool=True
    popups:bool=True

def notify(c,uid,kind,payload,stamp):
    if not c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():return
    from app.chat_notifications import muted
    silent=kind in ('message','family_message') and muted(c,uid,payload.get('space_id'),stamp)
    if silent:payload={**payload,'silent':True}
    cursor=c.execute('INSERT INTO notifications(user_id,kind,payload,created) VALUES(?,?,?,?)',(uid,kind,json.dumps(payload,ensure_ascii=False),stamp))
    if kind in ('message','family_message','call','security','family_export_ready') and not silent:
        from app.web_push import enqueue
        enqueue(c,uid,cursor.lastrowid,stamp)
    c.execute('DELETE FROM notifications WHERE user_id=? AND id NOT IN (SELECT id FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 1000)',(uid,uid))

def connected(c,a,b,stamp=None):
    stamp=stamp if stamp is not None else int(time.time())
    return a==b or bool(c.execute('SELECT 1 FROM space_members a JOIN space_members b ON b.space_id=a.space_id WHERE a.user_id=? AND b.user_id=?',(a,b)).fetchone()) or bool(c.execute('SELECT 1 FROM social_requests WHERE expires>? AND ((sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?))',(stamp,a,b,b,a)).fetchone())

def presence(c,uid,stamp,*,include_history=False):
    row=c.execute('SELECT last_seen FROM user_presence WHERE user_id=?',(uid,)).fetchone()
    age=max(0,stamp-row[0]) if row else None
    active = c.execute("SELECT 1 FROM session_presence p JOIN sessions s ON s.token=p.session_token WHERE s.user_id=? AND s.stage='full' AND s.expires>? AND p.last_seen>? LIMIT 1",(uid,stamp,stamp-90)).fetchone() if age is not None and age<90 else None
    return {'online':bool(active),'last_seen':row[0] if row and (include_history or age<86400) else None}

def actor(c,uid,stamp,*,include_history=False):
    row=c.execute('SELECT id,login,settings FROM users WHERE id=?',(uid,)).fetchone()
    if not row:return {'id':uid,'login':'Удалённый пользователь','avatar_url':None,'online':False,'last_seen':None}
    profile=json.loads(row['settings']).get('profile',{});aid=profile.get('avatar_id') or profile.get('avatar_preset')
    return {'id':uid,'login':row['login'],'display_name':profile.get('display_name') or '', 'avatar_preset':profile.get('avatar_preset'), 'avatar_url':f'/api/users/{uid}/avatar?v={aid}' if aid else None,**presence(c,uid,stamp,include_history=include_history)}

def actors(c,uids,stamp,*,include_history=False):
    """Same private payload as actor(), with bounded batches instead of N+1 SQL."""
    uids=list(uids);result={}
    for start in range(0,len(uids),200):
        batch=uids[start:start+200];marks=','.join('?' for _ in batch)
        rows=c.execute('SELECT u.id,u.login,u.settings,p.last_seen FROM users u LEFT JOIN user_presence p ON p.user_id=u.id WHERE u.id IN ('+marks+')',tuple(batch)).fetchall()
        active={row[0] for row in c.execute("SELECT DISTINCT s.user_id FROM session_presence p JOIN sessions s ON s.token=p.session_token WHERE s.user_id IN ("+marks+") AND s.stage='full' AND s.expires>? AND p.last_seen>?",(*batch,stamp,stamp-90))}
        for row in rows:
            uid=row['id'];seen=row['last_seen'];age=max(0,stamp-seen) if seen is not None else None
            profile=json.loads(row['settings']).get('profile',{});aid=profile.get('avatar_id') or profile.get('avatar_preset')
            result[uid]={'id':uid,'login':row['login'],'display_name':profile.get('display_name') or '',
                         'avatar_preset':profile.get('avatar_preset'),'avatar_url':f'/api/users/{uid}/avatar?v={aid}' if aid else None,
                         'online':age is not None and age<90 and uid in active,
                         'last_seen':seen if seen is not None and (include_history or age<86400) else None}
    return [result.get(uid,{'id':uid,'login':'Удалённый пользователь','avatar_url':None,'online':False,'last_seen':None}) for uid in uids]

class InboxView:
    """One transaction's access snapshot; counting never builds UI previews."""
    def __init__(self,c,uid,stamp,members=None):
        self.c,self.uid,self.stamp=c,uid,stamp
        self.members=members if members is not None else {r[0] for r in c.execute('SELECT space_id FROM space_members WHERE user_id=?',(uid,))}
        self.user=c.execute('SELECT role,settings FROM users WHERE id=?',(uid,)).fetchone()
        self.settings=json.loads(self.user['settings']) if self.user else {}
        self.messages={};self.pending={};self.notes={};self.actors={}
        self.next_expiry=stamp+30

    def prime(self,rows):
        from app.chat_storage import cutoff
        ids={json.loads(r['payload']).get('message_id') for r in rows if r['kind']=='message'}
        ids={mid for mid in ids if type(mid) is int and mid not in self.messages}
        for mid in ids:self.messages[mid]=None
        ids=list(ids)
        for start in range(0,len(ids),256):
            batch=ids[start:start+256]
            for r in self.c.execute('SELECT id,space_id,created,expires FROM messages WHERE id IN ('+','.join('?' for _ in batch)+') AND created>? AND (expires=0 OR expires>?)',(*batch,cutoff(self.stamp),self.stamp)):
                self.messages[r['id']]=dict(r)
                if r['created']<=cutoff(self.stamp+CACHE_SECONDS):self.next_expiry=min(self.next_expiry,self.stamp+1)

    def permitted(self,row,data):
        c,uid,stamp=self.c,self.uid,self.stamp
        kind=row['kind'];sid=data.get('space_id')
        if kind in ('tracker','tracker_invitation'):
            from app.tracker import role,project_row
            try:
                project=project_row(c,data.get('project_id',''))
                if not role(c,project,uid,pending=kind=='tracker_invitation'):return False
                if kind=='tracker' and not c.execute('SELECT 1 FROM tracker_issues WHERE id=? AND project_id=?',(data.get('issue_id',''),project['id'])).fetchone():return False
            except HTTPError:return False
        if kind=='security' and (not self.user or self.user['role']!='owner'):return False
        if kind=='family_export_ready':
            from app.modules.family_export_events import notice_access
            if not self.user or not notice_access(c,uid,data,stamp,user=self.user):return False
            self.next_expiry=min(self.next_expiry,data['expires'])
        if kind=='family_message':
            from app.modules.family_photo_events import notice_access
            if not self.user or not notice_access(c,uid,data,user=self.user,settings=self.settings):return False
        if kind in ('message','call'):
            if sid not in self.members:return False
            if sid not in self.pending:
                from app.workspace_lifecycle import pending
                self.pending[sid]=pending(c,sid)
            if self.pending[sid]:return False
        if kind=='message':
            mid=data.get('message_id')
            if type(mid) is not int:return False
            if mid not in self.messages:self.prime([row])
            message=self.messages.get(mid)
            if not message or message['space_id']!=sid:return False
            if message['expires']:self.next_expiry=min(self.next_expiry,message['expires'])
        if kind=='mention':
            nid=data.get('note_id')
            if nid not in self.notes:
                from app.modules.social import note_access
                try:
                    note_access(c,nid,uid,lambda code,msg:(_ for _ in ()).throw(HTTPError(code,msg)))
                    self.notes[nid]=True
                except HTTPError:self.notes[nid]=False
            if not self.notes[nid]:return False
        self.next_expiry=min(self.next_expiry,row['created']+RETENTION)
        return True

    def render(self,row):
        data=json.loads(row['payload'])
        if not self.permitted(row,data):return None
        item=dict(row);item['payload']=data
        if data.get('preview') and data.get('space_id') and '@' in data['preview']:
            from app.people_names import mention_text,names
            if names(self.settings):data['preview']=mention_text(self.c,self.uid,data['space_id'],data['preview'])
        if data.get('actor_id'):
            aid=data['actor_id']
            if aid not in self.actors:self.actors[aid]=actor(self.c,aid,self.stamp)
            data['actor_profile']=self.actors[aid]
        if row['kind']=='message':
            expiry=self.messages[data['message_id']]['expires']
            if expiry:item['expires']=expiry
        if row['kind']=='invitation':
            data['pending']=bool(self.c.execute('SELECT 1 FROM social_requests WHERE id=? AND recipient_id=?',(data.get('request_id'),self.uid)).fetchone())
        if row['kind']=='task_invitation':
            from app.task_invitations import get
            invitation=get(self.c,data.get('list_id',''),data.get('invitation_id',''),self.stamp)
            data['pending']=bool(invitation and invitation['recipient_id']==self.uid)
        if row['kind']=='tracker_invitation':
            data['pending']=bool(self.c.execute("SELECT 1 FROM tracker_members WHERE project_id=? AND actor_id=? AND peer_id='' AND state='pending'",(data.get('project_id',''),self.uid)).fetchone())
        from app.chat_notifications import mute_until
        sid=data.get('space_id')
        item['silent']=row['kind'] in ('message','family_message') and (data.get('silent') is True or isinstance(sid,str) and bool(mute_until(self.settings,sid,self.stamp)))
        item['read']=bool(item['read'])
        return item


def visible(c,row,uid,members=None,stamp=None):
    return InboxView(c,uid,stamp if stamp is not None else int(time.time()),members).render(row)


def unread_count(c,uid,stamp,members=None,*,view=None):
    view=view or InboxView(c,uid,stamp,members)
    cache=getattr(getattr(c,'backend',None),'redis_cache',None)
    ticket=None
    if cache and not c.realtime_dirty:
        scope=hashlib.sha256(json.dumps([view.settings,view.user['role'] if view.user else None,sorted(view.members)],sort_keys=True).encode()).hexdigest()
        ticket,hit=cache.lookup('inbox-unread',uid,stamp,scope=scope)
        if hit is not None:
            count,deadline=hit
            view.next_expiry=min(view.next_expiry,deadline)
            return count
    rows=c.execute('SELECT kind,payload,created FROM notifications WHERE user_id=? AND read=0 AND created>?',(uid,stamp-RETENTION)).fetchall()
    view.prime(rows)
    count=sum(view.permitted(row,json.loads(row['payload'])) for row in rows)
    if cache and not c.realtime_dirty:cache.put(ticket,count,view.next_expiry,stamp)
    return count

def chat_is_foreground(c,uid,stamp,coordinator):
    if coordinator is None:return False
    for peer in coordinator.peers_sync('inbox',('inbox',uid)):
        data=peer['metadata']
        if data.get('chat_active') is True and stamp-CHAT_ACTIVITY_TTL<data.get('seen',0)<=stamp:
            if c.execute("SELECT 1 FROM sessions WHERE token=? AND user_id=? AND stage='full' AND expires>?",(data.get('session_token'),uid,stamp)).fetchone():return True
    return False

def create_notifications_router(db,ready,tokens,rate,gate,now,fail,public_url,coordinator=None):
    r=Router();live_counts={};last_purge=[0]
    def inbox(uid,after=None,before=None,*,deadline=False):
        with db() as c:
            if now()-last_purge[0]>=60:
                c.execute('DELETE FROM notifications WHERE created<=?',(now()-RETENTION,));last_purge[0]=now()
            members={row[0] for row in c.execute('SELECT space_id FROM space_members WHERE user_id=?',(uid,))}
            rows=c.execute('SELECT * FROM notifications WHERE user_id=? AND (? IS NULL OR id>?) AND (? IS NULL OR id<?) ORDER BY id '+('ASC' if after is not None else 'DESC')+' LIMIT 100',(uid,after,after,before,before)).fetchall()
            view=InboxView(c,uid,now(),members);view.prime(rows)
            items=[item for row in rows if (item:=view.render(row)) is not None]
            unread=unread_count(c,uid,view.stamp,members,view=view)
            cursor=max((row['id'] for row in rows),default=after or 0) if after is not None else c.execute('SELECT COALESCE(MAX(id),0) FROM notifications WHERE user_id=?',(uid,)).fetchone()[0]
            cleared={row['id']:row['chat_cleared'] for row in c.execute('SELECT s.id,s.chat_cleared FROM spaces s JOIN space_members m ON m.space_id=s.id WHERE m.user_id=? AND s.chat_cleared>0',(uid,))}
            reads={row['space_id']:row['message_id'] for row in c.execute('SELECT r.space_id,r.message_id FROM chat_reads r JOIN space_members m ON m.space_id=r.space_id AND m.user_id=r.user_id WHERE r.user_id=?',(uid,))}
            for sid,mid in cleared.items():reads[sid]=max(reads.get(sid,0),mid)
            from app.modules.family_photo_events import read_map,has_access
            reads.update({'telegram:'+gid:mid for gid,mid in read_map(view.settings).items() if view.user and has_access(view.settings,view.user['role'],gid)})
        result={'items':items,'unread':unread,'cursor':cursor,'more':len(rows)==100,'chat_cleared':cleared,'message_reads':reads}
        return (result,view.next_expiry) if deadline else result
    @r.put('/api/notifications/preferences')
    def preferences(data:NotificationPreferences,request:Request):
        s=ready(request)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            settings=json.loads(c.execute('SELECT settings FROM users WHERE id=?',(s['user_id'],)).fetchone()[0]);settings['notifications']=data.model_dump()
            c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),s['user_id']))
        return data.model_dump()
    @r.get('/api/notifications')
    def listing(request:Request,after:int|None=Query(default=None,ge=0),before:int|None=Query(default=None,ge=1)):
        s=ready(request);return inbox(s['user_id'],after,before)
    @r.post('/api/notifications/read-all')
    def read_all(request:Request):
        s=ready(request)
        with db() as c:c.execute('UPDATE notifications SET read=1 WHERE user_id=?',(s['user_id'],))
        return {'ok':True}
    @r.patch('/api/notifications/{nid}')
    def read(nid:int,request:Request):
        s=ready(request)
        with db() as c:
            changed=c.execute('UPDATE notifications SET read=1 WHERE id=? AND user_id=?',(nid,s['user_id'])).rowcount
            if not changed:fail(404,localized_message('notificationNotFound'))
        return {'ok':True}
    @r.delete('/api/notifications/{nid}')
    def remove(nid:int,request:Request):
        s=ready(request)
        with db() as c:c.execute('DELETE FROM notifications WHERE id=? AND user_id=?',(nid,s['user_id']))
        return {'ok':True}
    @r.post('/api/presence')
    def heartbeat(request:Request):
        s=ready(request);rate(request,'heartbeat',120,period=60,subject=s['user_id'])
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            # A racing heartbeat must not resurrect activity after logout/revocation.
            if not c.execute("SELECT 1 FROM sessions WHERE token=? AND expires>? AND stage='full'",(s['token'],now())).fetchone():fail(401,localized_message('sessionTerminated'))
            c.execute('INSERT INTO session_presence VALUES(?,?) ON CONFLICT(session_token) DO UPDATE SET last_seen=excluded.last_seen',(s['token'],now()))
            c.execute('INSERT INTO user_presence VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen',(s['user_id'],now()))
        return {'ok':True}
    @r.get('/api/presence')
    def contacts(request:Request,scope:str=Query(default='contacts')):
        s=ready(request)
        if scope not in ('contacts','admin'):fail(422,localized_message('unknownUserList'))
        if scope=='admin' and s['role'] not in ('owner','admin'):fail(403,localized_message('availableToAdministrator'))
        with db() as c:
            ids={row[0] for row in c.execute('SELECT id FROM users')} if scope=='admin' else {row[0] for row in c.execute('SELECT DISTINCT b.user_id FROM space_members a JOIN space_members b ON b.space_id=a.space_id WHERE a.user_id=?',(s['user_id'],))}
            from app.people_names import names
            return {'users':actors(c,ids,now(),include_history=scope=='admin'),'people_names':names(json.loads(s['settings'])),'account_deletion':s.get('account_deletion')}
    @r.websocket('/api/live/inbox')
    async def live(ws:WebSocket):
        accepted=False;counted=None;lease=None;chat_active=False
        dirty=asyncio.Event();receiver=waker=None
        async def invalidate():dirty.set()
        async def activity(data,s):
            nonlocal chat_active
            if 'chat_active' not in data:return
            if type(data['chat_active']) is not bool:fail(422,localized_message('invalidMessageSectionStatus'))
            chat_active=data['chat_active']
            if lease:await lease.update({'session_token':s['token'],'chat_active':chat_active,'seen':now()})
        def message_cursor(uid):
            with db() as c:
                return c.execute('SELECT COALESCE(MAX(m.id),0) FROM messages m JOIN space_members sm ON sm.space_id=m.space_id WHERE sm.user_id=?',(uid,)).fetchone()[0]
        def auth():
            req=SimpleNamespace(cookies=ws.cookies,headers=ws.headers,method='GET',client=ws.client)
            return ready(req),req
        try:
            if ws.headers.get('origin','').rstrip('/')!=public_url:await ws.close(code=4403);return
            async with gate.shared():s,req=auth();rate(req,'inbox-connect',120,subject=s['user_id'])
            if coordinator:
                from app.multiprocess import PeerLimit
                try:lease=await coordinator.register_peer('inbox',s['user_id'],('inbox',s['user_id']),secrets.token_urlsafe(18))
                except PeerLimit:await ws.close(code=4429);return
            else:
                if live_counts.get(s['user_id'],0)>=12:await ws.close(code=4429);return
                counted=s['user_id'];live_counts[counted]=live_counts.get(counted,0)+1
            await ws.accept();accepted=True
            hello=json.loads(await asyncio.wait_for(ws.receive_text(),10))
            if not isinstance(hello,dict) or hello.get('type')!='auth':fail(403,localized_message('connectionConfirmationRequired'))
            tokens.check_csrf(SimpleNamespace(headers={'x-csrf-token':str(hello.get('csrf',''))}),s['csrf'])
            if coordinator:coordinator.subscribe_changes(invalidate)
            async with gate.shared_wait():
                s,req=auth()
                await activity(hello,s)
                dirty.clear()
                initial,next_refresh=inbox(s['user_id'],deadline=True)
                chat_cursor=message_cursor(s['user_id'])
            cursor=initial['cursor'];unread=initial['unread'];cleared=initial['chat_cleared'];reads=initial['message_reads'];await ws.send_json({'type':'ready',**initial})
            receiver=asyncio.create_task(ws.receive_text())
            waker=asyncio.create_task(dirty.wait())
            while True:
                done,_=await asyncio.wait((receiver,waker),timeout=1,return_when=asyncio.FIRST_COMPLETED)
                data=None
                if receiver in done:
                    raw=receiver.result()
                    if len(raw)>2000:fail(413,localized_message('tooLargeRequest'))
                    data=json.loads(raw)
                    if not isinstance(data,dict) or data.get('type') not in ('ping','activity'):fail(422,localized_message('unknownAction'))
                    receiver=asyncio.create_task(ws.receive_text())
                changed=dirty.is_set()
                if changed:
                    waker.cancel()
                    await asyncio.gather(waker,return_exceptions=True)
                    dirty.clear()
                    waker=asyncio.create_task(dirty.wait())
                # Keep the independent 1-second security watchdog. An idle data
                # subscription must never delay session revocation or expiry.
                async with gate.shared_wait():
                    current,_=auth()
                    if data is not None:
                        if data.get('type')=='activity' and 'chat_active' not in data:fail(422,localized_message('messageSectionStatusRequired'))
                        await activity(data,current)
                    interval=coordinator.recovery_interval if coordinator else 1
                    refresh=changed or now()>=next_refresh or interval==1
                    if refresh:
                        updates,next_refresh=inbox(current['user_id'],cursor,deadline=True)
                        next_refresh=min(next_refresh,now()+interval)
                        latest=message_cursor(current['user_id']) if chat_active else chat_cursor
                if data and data.get('type')=='activity':await ws.send_json({'type':'activity','chat_active':chat_active})
                if not refresh:continue
                if latest!=chat_cursor:
                    await ws.send_json({'type':'chat_changed'})
                    chat_cursor=latest
                cursor=updates['cursor']
                if updates['items'] or updates['unread']!=unread or updates['chat_cleared']!=cleared or updates['message_reads']!=reads:
                    await ws.send_json({'type':'notifications',**updates})
                unread=updates['unread'];cleared=updates['chat_cleared'];reads=updates['message_reads']
                if updates['more']:dirty.set()
        except (HTTPError,ValueError,TypeError,asyncio.TimeoutError):
            with suppress(RuntimeError,WebSocketDisconnect):await ws.close(code=4401 if accepted else 4403)
        except WebSocketDisconnect:pass
        finally:
            if coordinator:coordinator.unsubscribe_changes(invalidate)
            tasks=[t for t in (receiver,waker) if t is not None]
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            if lease:await lease.close()
            if counted:
                live_counts[counted]-=1
                if not live_counts[counted]:live_counts.pop(counted,None)
    live.origin=public_url
    live.authorize=ready
    return r
