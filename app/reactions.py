"""Viewer-authorized local reactions; Telegram authors remain a separate identity."""
from app.i18n import message as localized_message
import json
from pydantic import BaseModel,ConfigDict,Field
from app.notifications import actor
PREFIX='message-reactions:'
EMOJIS=('👍','❤️','😂','🔥','👏','😢','😮','🎉','🤔','👀','✅','🙏','😁','😍','💯','🤝','🥰','💔','😎','😡','👎','👌','😱','🫡')
class Reaction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    emoji:str=Field(min_length=1,max_length=16)

def key(space,mid):return PREFIX+space+':'+str(mid)
def read(c,space,mid):
    row=c.execute('SELECT value FROM app_settings WHERE key=?',(key(space,mid),)).fetchone()
    return json.loads(row[0]) if row else {'version':1,'space':space,'message':mid,'users':{}}
def eligible(c,space,uid):
    if space.startswith('telegram:'):
        row=c.execute('SELECT role,settings FROM users WHERE id=?',(uid,)).fetchone()
        if not row:return False
        from app.modules.family_photo_events import has_access
        settings=json.loads(row['settings']);gid=space[len('telegram:'):]
        return has_access(settings,row['role'],gid) and (row['role']=='owner' or gid in settings.get('family_photo_access',{}).get('write_groups',[]))
    return bool(c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?',(space,uid)).fetchone())
def payload(c,space,mid,stamp):
    value=read(c,space,mid)
    profiles={uid:actor(c,uid,stamp) for uid in value['users'] if eligible(c,space,uid)}
    return serialize(value,profiles)

def serialize(value,profiles):
    result=[]
    for emoji in EMOJIS:
        identities=[uid for uid,values in value['users'].items() if emoji in values and uid in profiles]
        if identities:result.append({'emoji':emoji,'count':len(identities),'people':[profiles[uid] for uid in sorted(identities)],'source':'site','complete':True})
    return result

def toggle(c,space,mid,uid,emoji,stamp,fail,revision=None):
    if emoji not in EMOJIS:fail(422,localized_message('chooseAnEmojiFromTheListOfReactions'))
    value=read(c,space,mid);selected=value['users'].get(uid,[])
    if emoji in selected:selected.remove(emoji)
    else:
        if len(selected)>=3:fail(422,localized_message('youCanSelectUpToThreeReactionsTo'))
        selected.append(emoji)
    if selected:value['users'][uid]=selected
    else:value['users'].pop(uid,None)
    if revision is not None:value['revision']=revision
    if value['users'] or 'revision' in value:c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key(space,mid),json.dumps(value)))
    else:c.execute('DELETE FROM app_settings WHERE key=?',(key(space,mid),))
    return payload(c,space,mid,stamp)

def purge(c):
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)).fetchall():
        value=json.loads(row['value']);space=value['space']
        exists=space.startswith('telegram:') or c.execute('SELECT 1 FROM messages WHERE space_id=? AND id=?',(space,value['message'])).fetchone()
        users={uid:entries for uid,entries in value['users'].items() if eligible(c,space,uid)} if exists else {}
        if not users and (not exists or space.startswith('telegram:')) and not (space.startswith('telegram:') and 'revision' in value):c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],))
        elif users!=value['users']:value['users']=users;c.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(value),row['key']))

def validate_archive(c,fail):
    try:
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)):
            value=json.loads(row['value'])
            if set(value)-{'version','space','message','users','revision'} or not {'version','space','message','users'}<=set(value) or type(value['version']) is not int or value['version']!=1 or not isinstance(value['space'],str) or len(value['space'])>100 or type(value['message']) is not int or not 0<value['message']<=2**53-1 or not isinstance(value['users'],dict) or len(value['users'])>500:raise ValueError()
            if row['key']!=key(value['space'],value['message']):raise ValueError()
            if 'revision' in value and (type(value['revision']) is not int or not 0<value['revision']<=2**53-1):raise ValueError()
            import re
            for uid,entries in value['users'].items():
                if not re.fullmatch(r'[a-f0-9]{32}',uid) or not isinstance(entries,list) or not 1<=len(entries)<=3 or len(set(entries))!=len(entries) or any(emoji not in EMOJIS for emoji in entries):raise ValueError()
    except (ValueError,TypeError,KeyError):fail()


def changed(c,space,after):
    return {value['message']:value['revision'] for row in c.execute('SELECT value FROM app_settings WHERE key LIKE ?',(PREFIX+space+':%',)) if (value:=json.loads(row[0])).get('revision',0)>after}
