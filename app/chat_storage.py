"""Chat envelope and private attachments, using the existing encrypted file store.

Reserved file IDs encode their room. For these immutable rows only, version=1
means a draft; version=message_id+1 binds the attachment to its one message.
Normal file IDs remain 16 characters and keep their original version semantics.
"""
import calendar
import json
import mimetypes
import re
from datetime import datetime,timezone

MAX_ATTACHMENT=50*1024*1024
DRAFT_SECONDS=86400
EDIT_SECONDS=86400
CHAT_TIMERS=(0,86400,604800,2592000)

def mentioned_members(c,sid,text,*,view=None):
    if view is not None:return view.mentions(sid,text)
    if '@' not in text:return []
    if not c.execute("SELECT 1 FROM spaces WHERE id=? AND kind='family'",(sid,)).fetchone():return []
    from app.notifications import actor
    import time
    text=re.sub(r'https?://[^\s<>"\']+','',text,flags=re.I)
    members=[]
    for row in c.execute('SELECT u.id,u.login FROM users u JOIN space_members m ON m.user_id=u.id WHERE m.space_id=?',(sid,)):
        # Prefer the entire real username, including leading dots/hyphens.
        # A sentence's trailing period is also a valid separator.
        if re.search(r'(?<![\w@])@'+re.escape(row['login'])+r'(?![\w@-]|\.[\w.-])',text,re.I):members.append(actor(c,row['id'],int(time.time())))
    return members


PREFIX='\x1eDRG-CHAT-1\n'
# Keep the new envelope inside the original reserved namespace. Earlier apps
# escaped any user text starting with PREFIX, including this new header.
REPLY_PREFIX=PREFIX+'v2\n'
ATTACHMENT_PATTERN=r'^chat-[a-zA-Z0-9_-]{16}-[a-zA-Z0-9_-]{16}$'

def attachment_room(fid):
    return fid[5:21] if isinstance(fid,str) and re.fullmatch(ATTACHMENT_PATTERN,fid) else None

def cutoff(stamp):
    value=datetime.fromtimestamp(stamp,timezone.utc);number=value.year*12+value.month-1-3;year,month=divmod(number,12);month+=1
    return int(value.replace(year=year,month=month,day=min(value.day,calendar.monthrange(year,month)[1])).timestamp())

def envelope(body):
    if not isinstance(body,str):raise ValueError('Invalid chat message.')
    prefix=next((value for value in (REPLY_PREFIX,PREFIX) if body.startswith(value)),None)
    if prefix:
        data=json.loads(body[len(prefix):]);keys={'text','attachments'}|({'reply_to'} if prefix==REPLY_PREFIX else set())
        if not isinstance(data,dict) or set(data)!=keys or not isinstance(data['text'],str) or len(data['text'])>4000 or not isinstance(data['attachments'],list) or len(data['attachments'])>10 or any(not attachment_room(x) for x in data['attachments']) or len(set(data['attachments']))!=len(data['attachments']) or not (data['text'].strip() or data['attachments']):raise ValueError('Invalid chat envelope.')
        reply=data.get('reply_to')
        if prefix==REPLY_PREFIX and (type(reply) is not int or not 1<=reply<=2**53-1):raise ValueError('Invalid chat reply.')
        return data['text'],data['attachments'],reply
    if not 1<=len(body)<=4000:raise ValueError('Invalid chat message.')
    return body,[],None

def decode(body):
    text,attachments,_=envelope(body)
    return text,attachments

def encode(text,attachments,reply_to=None):
    if reply_to is not None:return REPLY_PREFIX+json.dumps({'text':text,'attachments':attachments,'reply_to':reply_to},ensure_ascii=False,separators=(',',':'))
    if not attachments and not text.startswith((PREFIX,REPLY_PREFIX)):return text
    return PREFIX+json.dumps({'text':text,'attachments':attachments},ensure_ascii=False,separators=(',',':'))

IMAGE_MIMES=('image/png','image/jpeg','image/gif','image/webp')
VIDEO_MIMES=('video/mp4','video/webm','video/quicktime','video/x-m4v')

def media_kind(mime):
    return 'animation' if mime=='image/gif' else 'image' if mime in IMAGE_MIMES else 'video' if mime in VIDEO_MIMES else 'audio' if mime in ('audio/mp4','audio/ogg','audio/webm') else 'file'

def serial_file(row):
    kind=media_kind(row['mime']);url='/api/chat/'+attachment_room(row['id'])+'/attachments/'+row['id']
    result={k:row[k] for k in ('id','name','mime','size')}|{'kind':kind,'url':url}
    if 'recording_payload' in row.keys() and row['recording_payload']:
        from app.chat_recordings import public_payload
        result.update(public_payload(row['recording_payload']))
    # Older uploads and originals sent as documents need a content check before
    # becoming inline media. An extension alone never changes the response MIME.
    if kind=='file' and media_kind(mimetypes.guess_type(row['name'])[0])!='file':result['preview_url']=url+'/preview'
    return result

def serial_message(c,row,stamp=None,*,view=None):
    from app.reactions import payload,read
    result=dict(row);result['reactions']=view.reactions(row) if view is not None else payload(c,row['space_id'],row['id'],stamp or row['created']);result['reaction_revision']=(view.reaction(row) if view is not None else read(c,row['space_id'],row['id'])).get('revision',0);text,ids,reply_to=envelope(row['body']);result['body']=text;result['attachments']=[];result['reply']=None;result['editable_until']=row['created']+EDIT_SECONDS;result['mentions']=mentioned_members(c,row['space_id'],text,view=view)
    for fid in ids:
        from app.chat_recordings import file_row
        file=view.file(row,fid) if view is not None else file_row(c,fid,sender=row['sender_id'],version=row['id']+1)
        if file and attachment_room(fid)==row['space_id']:result['attachments'].append(serial_file(file))
    if reply_to is not None:
        import time
        reference=view.references.get((row['space_id'],reply_to)) if view is not None else c.execute('SELECT m.id,m.body,m.sender_id,m.expires,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id=? AND m.space_id=? AND m.created>? AND (m.expires=0 OR m.expires>?)',(reply_to,row['space_id'],cutoff(stamp if stamp is not None else int(time.time())),stamp if stamp is not None else int(time.time()))).fetchone()
        from app.notifications import actor
        person=(view.profile(reference['sender_id']) if view is not None else actor(c,reference['sender_id'],stamp if stamp is not None else int(time.time()))) if reference else {}
        result['reply']={'id':reply_to,'sender_profile':{key:person.get(key,'') for key in ('id','login','display_name')} if reference else {},'sender_id':reference['sender_id'] if reference else None,'mentions':[{key:p.get(key,'') for key in ('id','login','display_name')} for p in mentioned_members(c,row['space_id'],decode(reference['body'])[0],view=view)] if reference else [],'sender':(person.get('display_name') or reference['sender']) if reference else None,'body':decode(reference['body'])[0][:240] if reference else '', 'unavailable':reference is None}
        if reference and reference['expires']:result['reply']['expires']=reference['expires']
    return result


class MessageView:
    """One authorized history read; never shared between transactions or users."""
    def __init__(self,c,rows,stamp):
        from app.notifications import actors
        from app.reactions import key
        self.stamp=stamp;self.members={};self.references={};self.files={};self.reaction_values={}
        spaces=sorted({row['space_id'] for row in rows})
        for batch in self.batches(spaces):
            for row in c.execute('SELECT m.space_id,u.id,u.login,s.kind FROM space_members m JOIN users u ON u.id=m.user_id JOIN spaces s ON s.id=m.space_id WHERE m.space_id IN ('+self.marks(batch)+')',tuple(batch)):
                self.members.setdefault(row['space_id'],[]).append(dict(row))
        keys=list({key(row['space_id'],row['id']) for row in rows})
        for batch in self.batches(keys):
            for row in c.execute('SELECT key,value FROM app_settings WHERE key IN ('+self.marks(batch)+')',tuple(batch)):
                self.reaction_values[row['key']]=json.loads(row['value'])
        reply_ids=set();file_ids=set()
        for row in rows:
            _,files,reply=envelope(row['body']);file_ids.update(files)
            if reply is not None:reply_ids.add(reply)
        for batch in self.batches(sorted(reply_ids)):
            for row in c.execute('SELECT m.id,m.space_id,m.body,m.sender_id,m.expires,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id IN ('+self.marks(batch)+') AND m.space_id IN ('+self.marks(spaces)+') AND m.created>? AND (m.expires=0 OR m.expires>?)',(*batch,*spaces,cutoff(stamp),stamp)):
                self.references[(row['space_id'],row['id'])]=row
        versions={}
        for row in rows:versions.setdefault(row['sender_id'],set()).add(row['id']+1)
        owners=' OR '.join('(f.user_id=? AND f.version IN ('+self.marks(values)+'))' for values in versions.values())
        owner_params=tuple(value for uid,values in versions.items() for value in (uid,*sorted(values)))
        for batch in self.batches(sorted(file_ids)):
            for row in c.execute('SELECT f.*,r.payload AS recording_payload FROM files f LEFT JOIN chat_recordings r ON r.file_id=f.id WHERE f.id IN ('+self.marks(batch)+') AND ('+owners+')',(*batch,*owner_params)):
                self.files[row['id']]=row
        identities={row['sender_id'] for row in rows}|{row['sender_id'] for row in self.references.values()}
        identities.update(member['id'] for members in self.members.values() for member in members)
        ordered=sorted(identities)
        self.profiles=dict(zip(ordered,actors(c,ordered,stamp)))

    @staticmethod
    def batches(values):
        for start in range(0,len(values),200):yield values[start:start+200]

    @staticmethod
    def marks(batch):return ','.join('?' for _ in batch)

    def profile(self,uid):return self.profiles[uid]

    def reaction(self,row):
        from app.reactions import key
        return self.reaction_values.get(key(row['space_id'],row['id']),{'version':1,'space':row['space_id'],'message':row['id'],'users':{}})

    def reactions(self,row):
        from app.reactions import serialize
        value=self.reaction(row);members={m['id'] for m in self.members.get(row['space_id'],[])}
        return serialize(value,{uid:self.profile(uid) for uid in value['users'] if uid in members})

    def file(self,row,fid):
        saved=self.files.get(fid)
        return saved if saved and saved['user_id']==row['sender_id'] and saved['version']==row['id']+1 else None

    def mentions(self,sid,text):
        if '@' not in text:return []
        text=re.sub(r'https?://[^\s<>"\']+','',text,flags=re.I)
        return [self.profile(row['id']) for row in self.members.get(sid,[]) if row['kind']=='family' and re.search(r'(?<![\w@])@'+re.escape(row['login'])+r'(?![\w@-]|\.[\w.-])',text,re.I)]


def serial_messages(c,rows,stamp):
    if not rows:return []
    view=MessageView(c,rows,stamp)
    return [serial_message(c,row,stamp,view=view)|{'sender_profile':view.profile(row['sender_id'])} for row in rows]

def retire(c,row,stamp):
    if row['object_id']:c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(row['object_id'],'file',stamp))
    c.execute('DELETE FROM files WHERE id=?',(row['id'],))

HISTORY_VERSION_PREFIX='chat-history-version:'
DELETED_PREFIX='chat-deleted:'

def deletion_key(sid,uid,client_id):
    from hashlib import sha256
    return DELETED_PREFIX+sid+':'+sha256((uid+'\0'+client_id).encode()).hexdigest()

def history_version(c,sid):
    row=c.execute('SELECT value FROM app_settings WHERE key=?',(HISTORY_VERSION_PREFIX+sid,)).fetchone()
    return int(row[0]) if row else 0

def purge(c,stamp):
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(DELETED_PREFIX+'%',)).fetchall():
        sid=row['key'][len(DELETED_PREFIX):].split(':',1)[0]
        if int(row['value'])<=cutoff(stamp) or not c.execute('SELECT 1 FROM spaces WHERE id=?',(sid,)).fetchone():
            c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],))
    for row in c.execute('SELECT key FROM app_settings WHERE key LIKE ?',(HISTORY_VERSION_PREFIX+'%',)).fetchall():
        if not c.execute('SELECT 1 FROM spaces WHERE id=?',(row['key'][len(HISTORY_VERSION_PREFIX):],)).fetchone():
            c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],))
    # Message expiry is checked independently of the periodic physical cleanup.
    expired={(row['space_id'],row['id']) for row in c.execute('SELECT space_id,id FROM messages WHERE created<=? OR (expires>0 AND expires<=?)',(cutoff(stamp),stamp))}
    c.execute('DELETE FROM messages WHERE created<=? OR (expires>0 AND expires<=?)',(cutoff(stamp),stamp))
    if expired:
        for row in c.execute("SELECT id,payload FROM notifications WHERE kind='message'").fetchall():
            data=json.loads(row['payload'])
            if (data.get('space_id'),data.get('message_id')) in expired:c.execute('DELETE FROM notifications WHERE id=?',(row['id'],))
    for row in c.execute("SELECT * FROM files WHERE length(id)=38").fetchall():
        sid=attachment_room(row['id'])
        if not sid:continue
        exists=c.execute('SELECT 1 FROM spaces WHERE id=?',(sid,)).fetchone()
        if row['version']==1:
            if not exists or row['created']<=stamp-DRAFT_SECONDS:retire(c,row,stamp)
            continue
        message=c.execute('SELECT * FROM messages WHERE id=? AND sender_id=? AND space_id=?',(row['version']-1,row['user_id'],sid)).fetchone()
        if not message or row['id'] not in decode(message['body'])[1]:retire(c,row,stamp)

def validate_archive(c,invalid):
    from app.chat_recordings import validate_archive as validate_recordings
    validate_recordings(c,invalid)
    seen=set()
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(DELETED_PREFIX+'%',)):
        import re
        try:stamp=int(row['value'])
        except (ValueError,TypeError):invalid();continue
        if not re.fullmatch(r'chat-deleted:[A-Za-z0-9_-]{1,128}:[a-f0-9]{64}',row['key']) or not 0<stamp<=2**53-1 or str(stamp)!=row['value']:invalid()
    for space in c.execute('SELECT chat_ttl,chat_cleared FROM spaces'):
        if type(space['chat_ttl']) is not int or space['chat_ttl'] not in CHAT_TIMERS or type(space['chat_cleared']) is not int or not 0<=space['chat_cleared']<=2**53-1:invalid()
    cleared={row['id']:row['chat_cleared'] for row in c.execute('SELECT id,chat_cleared FROM spaces')}
    versions={row['id']:row['chat_revision'] for row in c.execute('SELECT id,chat_revision FROM spaces')}
    if any(type(value) is not int or not 0<=value<=2**53-1 for value in versions.values()):invalid()
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(HISTORY_VERSION_PREFIX+'%',)):
        sid=row['key'][len(HISTORY_VERSION_PREFIX):]
        try:version=int(row['value'])
        except (ValueError,TypeError):invalid();continue
        if not 0<version<=versions.get(sid,2**53-1) or str(version)!=row['value']:invalid()
    for row in c.execute('SELECT * FROM messages'):
        if row['id']<=cleared[row['space_id']]:invalid()
        if type(row['expires']) is not int or row['expires']!=0 and row['expires']-row['created'] not in CHAT_TIMERS[1:]:invalid()
        if type(row['edited']) is not int or type(row['revision']) is not int or not 0<=row['revision']<=2**53-1 or (row['revision']==0)!=(row['edited']==0):invalid()
        if row['edited']:
            if not row['created']<=row['edited']<row['created']+EDIT_SECONDS or row['revision']>versions[row['space_id']] or (row['space_id'],row['revision']) in seen:invalid()
            seen.add((row['space_id'],row['revision']))
        try:_,ids,reply=envelope(row['body'])
        except (ValueError,TypeError):invalid()
        if reply is not None:
            referenced=c.execute('SELECT space_id FROM messages WHERE id=?',(reply,)).fetchone()
            if reply>=row['id'] or referenced and referenced['space_id']!=row['space_id']:invalid()
        for fid in ids:
            file=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not file or attachment_room(fid)!=row['space_id'] or file['user_id']!=row['sender_id'] or file['version']!=row['id']+1:invalid()
    for row in c.execute('SELECT * FROM files WHERE length(id)=38'):
        sid=attachment_room(row['id'])
        if not sid or not isinstance(row['version'],int) or row['version']<1 or row['kind']!='file' or row['parent_id'] is not None or row['size']>MAX_ATTACHMENT or not c.execute('SELECT 1 FROM spaces WHERE id=?',(sid,)).fetchone():invalid()
        if row['version']>1:
            message=c.execute('SELECT body FROM messages WHERE id=? AND sender_id=? AND space_id=?',(row['version']-1,row['user_id'],sid)).fetchone()
            if not message or row['id'] not in decode(message['body'])[1]:invalid()
