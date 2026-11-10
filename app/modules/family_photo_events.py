"""Family Telegram messages use the site's durable inbox and Web Push outbox."""
import json
import re
from urllib.parse import urlencode
from .family_export_events import FamilyExportEvents

KEY = '_family_photo_events_v1'
GROUP_ID = re.compile(r'(?:-\d{1,20}|export:[a-f0-9]{8,24})')


def read_map(settings):
    value=settings.get('family_photo_reads',{})
    return {gid:mid for gid,mid in value.items() if isinstance(gid,str) and GROUP_ID.fullmatch(gid)
            and type(mid) is int and 0<=mid<=2**53-1} if isinstance(value,dict) else {}


def has_access(settings, role, gid, *, require_module=True):
    if not isinstance(gid, str) or not GROUP_ID.fullmatch(gid):return False
    access=settings.get('family_photo_access',{})
    allowed=role=='owner' or (isinstance(access,dict) and access.get('enabled') is True and gid in access.get('groups',[]))
    if not allowed:return False
    if not require_module:return True
    from app.modules.preferences import module_enabled
    return settings.get('modules',{}).get('family_photos') is True and module_enabled(settings,'chat')


def notice_access(c, uid, payload, *, user=None, settings=None):
    gid=payload.get('family_group_id')
    if payload.get('space_id')!='telegram:'+str(gid) or type(payload.get('message_id')) is not int or not 0<payload['message_id']<=2**53-1:return False
    row=user if user is not None else c.execute('SELECT role,settings FROM users WHERE id=?',(uid,)).fetchone()
    return bool(row and has_access(settings if settings is not None else json.loads(row['settings']),row['role'],gid))


def state(c):
    row=c.execute('SELECT value FROM app_settings WHERE key=?',(KEY,)).fetchone()
    return json.loads(row[0]) if row else {'cursors':{},'baselines':{}}


def save_state(c,value):
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(KEY,json.dumps(value)))


def preview(message):
    if message.get('text','').strip():return message['text'].strip()[:180]
    if message.get('content_type')=='voice':return 'Аудиосообщение'
    if message.get('content_type')=='sticker':return 'Стикер'
    media=message.get('media',[])
    if len(media)>1:return 'Вложения: '+str(len(media))
    return 'GIF' if media and media[0].get('kind')=='animation' else 'Видео' if media and media[0].get('kind')=='video' else 'Фото' if media else 'Новое сообщение'


class FamilyPhotoEvents(FamilyExportEvents):
    def family_counts(self, session, groups=None):
        settings=json.loads(session['settings'])
        if not settings.get('modules',{}).get('family_photos'):return {}
        groups=groups if groups is not None else self.fetch('v1/groups')['items']
        with self.db() as c:baseline=state(c)['baselines']
        counts={};reads=read_map(settings)
        for group in groups:
            gid=group['id']
            if not has_access(settings,session['role'],gid):continue
            after=reads.get(gid,baseline.get(gid,group.get('latest_message_id',group.get('cursor',0))))
            # Only the authoritative latest-message field proves zero unread.
            # Legacy archives/cursors still use the exact counting endpoint.
            latest=group.get('latest_message_id')
            if type(latest) is int and 0<=latest<=2**53-1 and type(after) is int and latest<=after:
                counts[gid]=0
                continue
            args={'after':after}
            if self.status().get('bot_messaging'):args['exclude_actor']=session['user_id']
            if session['role']=='owner' and getattr(self,'reader_id',None):args['exclude_sender']=str(self.reader_id)
            counts[gid]=self.fetch('v1/groups/'+gid+'/unread?'+urlencode(args))['unread']
        return counts

    def poll_messages(self, stamp):
        if not self.status().get('available'):return
        if self.status().get('bot_messaging'):self.sync_send_permissions()
        if self.status().get('group_management'):
            managed=self.fetch('v1/admin/groups')
            self.cleanup_purged(managed.get('purged',{}))
        if self.status().get('photo_exports'):
            jobs=self.fetch('v1/admin/exports')['items']
            with self.db() as c:
                owners={row['id']:dict(row) for row in c.execute('SELECT id,role,settings FROM users')}
            for job in jobs:
                user=owners.get(job['actor_id'])
                if not user or user['role']!='owner':
                    self.fetch('v1/exports/'+job['id']+'/cancel?actor_id='+job['actor_id'],'POST',{})
        if self.status().get('bot_reactions'):self.sync_reaction_votes()
        groups=self.fetch('v1/groups')['items']
        if self.status().get('export_notifications'):self.export_events(jobs,groups,stamp)
        # The field is versioned capability detection: older archives do not
        # accidentally deliver their whole history when the site is upgraded.
        if any(type(g.get('latest_message_id')) is not int for g in groups):return
        with self.db() as c:old=state(c)
        if old!=getattr(self,'_notice_watermark',None):self._notice_cache=None
        updates={}
        for group in groups:
            gid=group['id'];previous=old['cursors'].get(gid)
            if previous is not None and group['latest_message_id']>previous:
                updates[gid]=self.fetch('v1/groups/'+gid+'/updates?'+urlencode({'after':previous,'limit':100}))['items']
        from app.notifications import notify,chat_is_foreground
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            current=state(c)
            if current!=old:return
            users=[dict(row) for row in c.execute('SELECT id,role,settings FROM users')]
            for group in groups:
                gid=group['id'];latest=group['latest_message_id']
                first=gid not in current['cursors']
                if first:current['baselines'][gid]=latest
                baseline=current['baselines'][gid]
                rows=updates.get(gid,[])
                # One album announcement, while every source ID stays readable.
                announce={}
                for message in rows:
                    if message.get('text','').strip() or message.get('media') or message.get('content_type')=='voice':
                        announce[message.get('album_id') or ('message:'+str(message['id']))]=message
                for user in users:
                    settings=json.loads(user['settings'])
                    if not has_access(settings,user['role'],gid,require_module=False):continue
                    reads=read_map(settings)
                    dirty=reads!=settings.get('family_photo_reads')
                    if gid not in reads:
                        reads[gid]=baseline
                        dirty=True
                    if dirty:
                        settings['family_photo_reads']=reads
                        c.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),user['id']))
                        user['settings']=json.dumps(settings)
                    if not has_access(settings,user['role'],gid):continue
                    if not announce:continue
                    if chat_is_foreground(c,user['id'],stamp,getattr(self,'coordinator',None)):continue
                    for message in announce.values():
                        if message['id']<=reads[gid]:continue
                        if message.get('site_actor_id')==user['id']:continue
                        if user['role']=='owner' and str(message.get('sender_id'))==str(getattr(self,'reader_id',None)):continue
                        notify(c,user['id'],'family_message',{'space_id':'telegram:'+gid,'family_group_id':gid,
                            'message_id':message['id'],'source_version':message.get('version'),
                            'actor':message.get('sender') or group['title'],'space_name':group['title'],'telegram_sender_id':message.get('sender_id'),
                            'preview':preview(message)},stamp)
                current['cursors'][gid]=max(current['cursors'].get(gid,0),max((message['id'] for message in rows),default=latest))
            if current!=old:save_state(c,current)
        if current!=old:self._notice_cache=None
        self._notice_watermark=current
        self.refresh_notices(stamp)

    def refresh_notices(self,stamp):
        # Batch existence/version checks contain no text and do not touch S3.
        # Reuse the bounded seven-day ID set between recovery scans. Source
        # fingerprints are still checked every poll, including read notices:
        # unchanged latest-message IDs do not imply no edits or deletions.
        cached=getattr(self,'_notice_cache',None)
        if cached is None or not 0<=stamp-cached[0]<60:
            with self.db() as c:
                notices=[dict(row) for row in c.execute("SELECT id,user_id,payload FROM notifications WHERE kind='family_message' AND created>?",(stamp-7*86400,))]
            self._notice_cache=(stamp,notices)
        else:notices=cached[1]
        groups={}
        for row in notices:
            payload=json.loads(row['payload']);gid=payload.get('family_group_id')
            if isinstance(gid,str) and GROUP_ID.fullmatch(gid):groups.setdefault(gid,[]).append((row,payload))
        for gid,entries in groups.items():
            ids=sorted({p['message_id'] for _,p in entries})
            existing={}
            for start in range(0,len(ids),1000):
                from app.http import HTTPError
                try:existing.update({r['id']:r for r in self.fetch('v1/groups/'+gid+'/message-states','POST',{'ids':ids[start:start+1000]})['items']})
                except HTTPError as error:
                    if error.status_code!=404:raise
            changed={}
            for mid in {p['message_id'] for _,p in entries if p['message_id'] in existing and (p.get('source_version')!=existing[p['message_id']]['fingerprint'] or 'telegram_sender_id' not in p)}:
                changed[mid]=self.fetch('v1/groups/'+gid+'/messages/'+str(mid))
            affected=[(row,payload) for row,payload in entries if payload['message_id'] not in existing or payload['message_id'] in changed]
            if not affected:continue
            updated=[]
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                for row,payload in affected:
                    # A user may have removed the notice, or restore replaced
                    # rows while this process retained its temporary ID cache.
                    current=c.execute("SELECT payload FROM notifications WHERE id=? AND user_id=? AND kind='family_message'",(row['id'],row['user_id'])).fetchone()
                    if not current or current[0]!=row['payload']:
                        self._notice_cache=None
                        continue
                    mid=payload['message_id']
                    if mid not in existing or changed.get(mid,{}).get('site_actor_id')==row['user_id']:
                        c.execute('DELETE FROM notifications WHERE id=? AND user_id=?',(row['id'],row['user_id']))
                        self._notice_cache=None
                    elif mid in changed:
                        payload.update(source_version=changed[mid].get('version'),preview=preview(changed[mid]),actor=changed[mid].get('sender') or payload['actor'],telegram_sender_id=changed[mid].get('sender_id'))
                        encoded=json.dumps(payload)
                        c.execute('UPDATE notifications SET payload=? WHERE id=? AND user_id=?',(encoded,row['id'],row['user_id']))
                        updated.append((row,encoded))
            # Only acknowledge cache changes after a successful commit.
            for row,encoded in updated:row['payload']=encoded

    def push_message(self,payload):
        gid=payload.get('family_group_id');mid=payload.get('message_id')
        if not isinstance(gid,str) or not GROUP_ID.fullmatch(gid) or type(mid) is not int:return None
        from app.http import HTTPError
        try:return self.fetch('v1/groups/'+gid+'/messages/'+str(mid))
        except HTTPError as error:
            if error.status_code==404:return None
            raise
