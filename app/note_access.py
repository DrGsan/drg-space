"""Page-scoped guest access, stored in the existing encrypted settings store.

Workspace membership is the default. Explicit page grants/revocations apply to
that page and its descendants without changing family or friendship membership.
"""
from app.i18n import message as localized_message
import json
import re
import copy
from app import workspace_management as management

PREFIX='note-access:'
ROLES={'viewer','commenter','editor','none'}

def validate(value):
    if not isinstance(value,dict) or set(value)!={'version','members'} or type(value['version']) is not int or value['version']!=1 or not isinstance(value['members'],dict) or len(value['members'])>500:
        raise ValueError(localized_message('invalidPageAccessSettings'))
    if any(not isinstance(uid,str) or not re.fullmatch(r'[a-f0-9]{32}',uid) or not isinstance(role,str) or role not in ROLES for uid,role in value['members'].items()):
        raise ValueError(localized_message('invalidPageParticipants'))
    return value

class AccessIndex:
    def __init__(self,c):
        self.pages={n['id']:dict(n) for n in c.execute('SELECT id,user_id,space_id,parent_id,deleted FROM notes')}
        spaces=list(c.execute('SELECT id,owner_id,kind FROM spaces'))
        self.spaces={s['id']:s['owner_id'] for s in spaces};self.kinds={s['id']:s['kind'] for s in spaces}
        self.members={}
        for m in c.execute('SELECT space_id,user_id FROM space_members'):self.members.setdefault(m['space_id'],set()).add(m['user_id'])
        self.rules={}
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)):
            self.rules[row['key'][len(PREFIX):]]=validate(json.loads(row['value']))['members']
        self.permissions={(p['note_id'],p['user_id']):p['role'] for p in c.execute('SELECT * FROM note_permissions')}
        self.users={r[0] for r in c.execute('SELECT id FROM users')}
        self.management=management.configs(c)
        from app.space_profiles import templates
        self.templates=templates(c)
        self.audiences=management.audiences(c)
        from app.workspace_lifecycle import deletions
        self.deletions=deletions(c)
        self.scopes={}
    def chain(self,n):
        seen=set()
        while n and n['id'] not in seen:
            seen.add(n['id']);yield n
            n=self.pages.get(n['parent_id']) if n['parent_id'] else None
    def visible(self,n,uid):
        if n['space_id'] in self.deletions:return False
        # Private documents retain their original ownership boundary.
        if not n['space_id']:return n['user_id']==uid
        if self.workspace_manager(n['space_id'],uid):return True
        scope,policy=self.scope(n)
        if policy and policy['mode']=='restricted':return bool(self.sources(n,uid))
        for ancestor in scope:
            rule=self.rules.get(ancestor['id'],{}).get(uid)
            if rule:return rule!='none'
        return uid in self.members.get(n['space_id'],set())
    def personal(self,n,uid):
        return not n['space_id'] or self.kinds.get(n['space_id'])=='friends' or uid not in self.members.get(n['space_id'],set())
    def role(self,n,uid):
        if (n['user_id']==uid and not self.work_space(n['space_id'])) or self.spaces.get(n['space_id'])==uid or self.workspace_manager(n['space_id'],uid):return 'editor'
        scope,policy=self.scope(n)
        if policy and policy['mode']=='restricted':
            sources=self.sources(n,uid)
            return max((s['role'] for s in sources),key=lambda r:management.ROLES[r],default='viewer')
        for ancestor in scope:
            role=self.rules.get(ancestor['id'],{}).get(uid) or self.permissions.get((ancestor['id'],uid))
            if role:return role if role!='none' else 'viewer'
        return 'editor'
    def work_space(self,sid):
        return self.kinds.get(sid)=='family' and self.templates.get(sid,'family') in {'project','organization'}
    def workspace_manager(self,sid,uid):
        return (self.kinds.get(sid)=='family' and uid in self.members.get(sid,set())
                and (self.spaces.get(sid)==uid or uid in self.management.get(sid,management.default())['admins']))
    def scope(self,n):
        if n['id'] not in self.scopes:
            chain=[];policy=None
            for ancestor in self.chain(n):
                chain.append(ancestor)
                candidate=self.audiences.get(ancestor['id'])
                if candidate and candidate['mode']!='inherit':policy=candidate;break
            self.scopes[n['id']]=(chain,policy)
        return self.scopes[n['id']]
    def sources(self,n,uid):
        if not n['space_id']:return [{'kind':'personal','label':'Личная страница','role':'editor'}] if n['user_id']==uid else []
        sid=n['space_id'];member=uid in self.members.get(sid,set())
        if self.workspace_manager(sid,uid):return [{'kind':'manager','label':'Владелец пространства' if self.spaces.get(sid)==uid else 'Администратор пространства','role':'editor'}]
        scope,policy=self.scope(n)
        if not policy or policy['mode']!='restricted':
            if not self.visible(n,uid):return []
            return [{'kind':'person' if not member else 'workspace','label':'Личное приглашение' if not member else 'Участник пространства','role':self.role(n,uid)}]
        if n['user_id']==uid and member and not self.work_space(sid):return [{'kind':'creator','label':'Создатель страницы','role':'editor'}]
        sources=[];config=self.management.get(sid,management.default())
        if member:
            for tid,role in policy['teams'].items():
                team=config['teams'].get(tid)
                if team and uid in team['members']:sources.append({'kind':'team','id':tid,'label':team['name'],'role':role})
        direct=None;override=None
        for ancestor in scope:
            if direct is None:direct=self.rules.get(ancestor['id'],{}).get(uid)
            if override is None:override=self.permissions.get((ancestor['id'],uid))
        if direct!='none':
            personal=direct or (policy['members'].get(uid) if member else None)
            if personal:sources.append({'kind':'person','label':'Личное разрешение','role':override or personal})
        return sources
    def participants(self,n):
        users=set(self.members.get(n['space_id'],set())) if n['space_id'] else {n['user_id']}
        scope,policy=self.scope(n)
        for ancestor in scope:users.update(self.rules.get(ancestor['id'],{}))
        return {uid for uid in users if uid in self.users and self.visible(n,uid)}

def access_index(c):
    if not hasattr(c,'__dict__'):return AccessIndex(c)
    if getattr(c,'note_access_index',None) is None:c.note_access_index=AccessIndex(c)
    return c.note_access_index

def can_view(c,n,uid):return access_index(c).visible(n,uid)
def participants(c,n):return access_index(c).participants(n)
def visible_ids(c,uid):
    index=access_index(c)
    return {nid for nid,n in index.pages.items() if index.visible(n,uid)}

def save_rule(c,nid,uid,role):
    row=c.execute('SELECT value FROM app_settings WHERE key=?',(PREFIX+nid,)).fetchone()
    value=validate(json.loads(row['value'])) if row else {'version':1,'members':{}}
    if role is None:value['members'].pop(uid,None)
    else:value['members'][uid]=role
    validate(value)
    if value['members']:c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(PREFIX+nid,json.dumps(value,separators=(',',':'))))
    else:c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+nid,))
    management.invalidate(c,dirty=True)

def remove_page_settings(c,ids):
    for nid in ids:
        from app.notion_sync.pending_assets import published
        published(c,nid)
        from app.note_trash import forget
        forget(c,[nid])
        from app.note_lifecycle import PREFIX as DELETION_PREFIX
        c.execute('DELETE FROM app_settings WHERE key=?',(DELETION_PREFIX+nid,))
        c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+nid,))
        c.execute('DELETE FROM app_settings WHERE key=?',(management.AUDIENCE_PREFIX+nid,))
    management.invalidate(c)

def purge_orphans(c):
    pages={r[0] for r in c.execute('SELECT id FROM notes')};users={r[0] for r in c.execute('SELECT id FROM users')}
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)).fetchall():
        if row['key'][len(PREFIX):] not in pages:c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],));continue
        value=validate(json.loads(row['value']));kept={uid:role for uid,role in value['members'].items() if uid in users}
        if kept!=value['members']:
            value['members']=kept
            if kept:c.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(value),row['key']))
            else:c.execute('DELETE FROM app_settings WHERE key=?',(row['key'],))
    management.purge(c)

def validate_archive(c,fail):
    try:
        index=AccessIndex(c);users={r[0] for r in c.execute('SELECT id FROM users')}
        for nid,rules in index.rules.items():
            n=index.pages.get(nid)
            if not n or not n['space_id']:raise ValueError('Missing shared page')
            for uid,role in rules.items():
                if uid not in users or (role=='none' and (uid==index.spaces.get(n['space_id']) or uid==n['user_id'] and not index.work_space(n['space_id']))):raise ValueError('Invalid participant')
    except (ValueError,TypeError):fail()

def revoke_workspace_grants(c,sid,uid):
    index=access_index(c)
    for nid,page in list(index.pages.items()):
        if page['space_id']==sid and index.rules.get(nid,{}).get(uid) in ('viewer','commenter','editor'):save_rule(c,nid,uid,None)

def copy_access(c,source,target):
    """Pin the effective explicit boundary when moving/copying a branch."""
    if not source['space_id']:return
    index=access_index(c);scope,policy=index.scope(source)
    if not policy:return
    value=copy.deepcopy(policy);value['revision']=management.get_audience(c,target)['revision']+1
    direct={};permissions={}
    for ancestor in scope:
        for uid,role in index.rules.get(ancestor['id'],{}).items():direct.setdefault(uid,role)
        for (nid,uid),role in index.permissions.items():
            if nid==ancestor['id']:permissions.setdefault(uid,role)
    target_page=index.pages[target]
    for uid,role in direct.items():
        if role=='none' and uid in (target_page['user_id'],index.spaces.get(source['space_id'])):continue
        save_rule(c,target,uid,role)
    for uid,role in permissions.items():
        c.execute('INSERT INTO note_permissions VALUES(?,?,?) ON CONFLICT(note_id,user_id) DO UPDATE SET role=excluded.role',(target,uid,role))
    management.save_audience(c,target,value)

def pin_before_move(c,n,new_parent):
    if n['parent_id']!=new_parent:copy_access(c,n,n['id'])

def profile(c,uid,stamp):
    from app.notifications import actor
    if not hasattr(c,'note_profiles'):c.note_profiles={}
    if uid not in c.note_profiles:c.note_profiles[uid]=actor(c,uid,stamp)
    return c.note_profiles[uid]

def visible_rows(c,uid,columns='*',include_deleted=False):
    identities=sorted(visible_ids(c,uid));rows=[]
    for start in range(0,len(identities),400):
        batch=identities[start:start+400]
        rows.extend(c.execute('SELECT '+columns+' FROM notes WHERE id IN ('+','.join('?' for _ in batch)+')'+('' if include_deleted else ' AND deleted IS NULL'),tuple(batch)).fetchall())
    return rows

def connected_by_page(c,a,b):
    index=access_index(c)
    return any(not n['deleted'] and index.visible(n,a) and index.visible(n,b) for n in index.pages.values())
