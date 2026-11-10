"""Account/session-bound OAuth, encrypted grants and shared request budgets.

All HTTP is outside SQL transactions. OAuth states are single-use even after an
ambiguous exchange; retrying a consumed code is never used as token recovery.
"""
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

from app.notion_sync.client import NotionClient,NotionError,exchange
from app.notion_sync.state import dump
from app.process_locks import try_file_lock


class Connections:
    def __init__(self,db,config,data_dir,*,clock=time.time,transport=None,wait=time.sleep):
        self.db,self.config,self.data_dir=db,config,data_dir
        self.clock,self.transport,self.wait=clock,transport,wait

    def start(self,session,kind='read',platform='web'):
        client_id,_=self.config.credentials(kind)
        kind='read' # Older clients may still request "write": one OAuth grant.
        if platform not in ('web','ios'):raise NotionError('notionInvalidRequest',422)
        token=secrets.token_urlsafe(32);stamp=int(self.clock())
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            owner=c.execute("SELECT 1 FROM users WHERE id=? AND role='owner'",(session['user_id'],)).fetchone()
            if not owner:raise NotionError('notionAccessDenied',403)
            c.execute('DELETE FROM notion_oauth_states WHERE expires<=?',(stamp,))
            c.execute('DELETE FROM notion_oauth_states WHERE session_token=? AND kind=?',(session['token'],kind))
            c.execute('INSERT INTO notion_oauth_states(id,user_id,session_token,kind,platform,expires) VALUES(?,?,?,?,?,?)',
                      (hashlib.sha256(token.encode()).hexdigest(),session['user_id'],session['token'],kind,platform,stamp+600))
        return 'https://api.notion.com/v1/oauth/authorize?'+urlencode({'client_id':client_id,'response_type':'code','owner':'user','redirect_uri':self.config.redirect_uri,'state':token})

    def complete(self,state,code):
        if not isinstance(state,str) or not 32<=len(state)<=100:raise NotionError('notionAuthorizationFailed',400)
        stamp=int(self.clock())
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT o.*,s.expires AS session_expires,s.stage FROM notion_oauth_states o JOIN sessions s ON s.token=o.session_token JOIN users u ON u.id=o.user_id WHERE o.id=? AND u.role='owner'",
                          (hashlib.sha256(state.encode()).hexdigest(),)).fetchone()
            if not row or row['expires']<=stamp or row['session_expires']<=stamp or row['stage']!='full':raise NotionError('notionAuthorizationFailed',400)
            saved=dict(row)
            c.execute('DELETE FROM notion_oauth_states WHERE id=?',(row['id'],))
        if not isinstance(code,str) or not 1<=len(code)<=2000:raise NotionError('notionAuthorizationFailed',400)
        if saved['kind']!='read':raise NotionError('notionAuthorizationFailed',400)
        grant=exchange(self.config,saved['kind'],{'grant_type':'authorization_code','code':code,'redirect_uri':self.config.redirect_uri},self.transport)
        workspace=grant.get('workspace_id');bot=grant.get('bot_id')
        if not isinstance(workspace,str) or not workspace or not isinstance(bot,str) or not bot:raise NotionError('notionAuthorizationFailed',400)
        payload=self._payload(grant)
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            # Logging out or disabling the account during provider consent must
            # not install a token into a now-invalid local session.
            valid=c.execute("SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.user_id=? AND s.stage='full' AND s.expires>? AND u.role='owner'",(saved['session_token'],saved['user_id'],int(self.clock()))).fetchone()
            if not valid:raise NotionError('notionAuthorizationFailed',400)
            c.execute("INSERT INTO notion_connections(id,user_id,kind,workspace_id,bot_id,payload,created,updated) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id,kind,workspace_id) DO UPDATE SET bot_id=excluded.bot_id,payload=excluded.payload,status='active',cooldown=0,updated=excluded.updated",
                      (secrets.token_urlsafe(12),saved['user_id'],saved['kind'],workspace,bot,dump(payload),stamp,stamp))
            # Reauthorization preserves primary IDs, roots and their explicit
            # modes. Only obsolete separate-app tokens are retired afterwards.
            for root in c.execute("SELECT r.id,r.payload FROM notion_roots r JOIN notion_connections n ON n.id=r.connection_id WHERE r.user_id=? AND n.kind='read' AND n.workspace_id=?",(saved['user_id'],workspace)):
                value=json.loads(root['payload'])
                if value.pop('write_connection_id',None) is not None:c.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(value),root['id']))
            c.execute("DELETE FROM notion_connections WHERE user_id=? AND workspace_id=? AND kind='write' AND id NOT IN (SELECT connection_id FROM notion_roots)",(saved['user_id'],workspace))
        return saved['platform']

    def _payload(self,grant):
        result={key:grant[key] for key in ('access_token','refresh_token','workspace_name','workspace_icon') if isinstance(grant.get(key),str)}
        expires=grant.get('expires_in')
        if type(expires) in (int,float) and expires>0:result['expires']=int(self.clock()+expires)
        return result

    def client(self,identity,*,mutation_check=None,request_check=None):
        """Refreshing a rotating token is serialized across all local workers."""
        with self.db() as c:row=c.execute('SELECT n.*,u.role AS owner_role FROM notion_connections n JOIN users u ON u.id=n.user_id WHERE n.id=?',(identity,)).fetchone()
        if row and row['owner_role']!='owner':raise NotionError('notionAccessDenied',403)
        if not self.config.configured or not row or row['status']!='active' or row['kind']!='read':raise NotionError('notionConnectionPaused',409)
        row=dict(row);payload=json.loads(row['payload'])
        if payload.get('expires',float('inf'))<=self.clock()+60:
            lease=try_file_lock(self.data_dir,'notion-refresh-'+identity+'.lock')
            if lease is None:raise NotionError('notionRetryLater',503,5)
            with lease:
                with self.db() as c:latest=c.execute('SELECT payload,status FROM notion_connections WHERE id=?',(identity,)).fetchone()
                if not latest or latest['status']!='active':raise NotionError('notionConnectionPaused',409)
                payload=json.loads(latest['payload'])
                if payload.get('expires',float('inf'))<=self.clock()+60:
                    try:
                        if not payload.get('refresh_token'):raise NotionError('notionAuthorizationExpired',401)
                        grant=exchange(self.config,row['kind'],{'grant_type':'refresh_token','refresh_token':payload['refresh_token']},self.transport)
                    except NotionError:
                        self.pause(identity,'reauthorize')
                        raise NotionError('notionAuthorizationExpired',401) from None
                    payload={**payload,**self._payload(grant)}
                    with self.db() as c:c.execute('UPDATE notion_connections SET payload=?,updated=? WHERE id=?',(dump(payload),int(self.clock()),identity))
        def before(mutation):
            if mutation:
                if mutation_check is None:raise NotionError('notionWriteForbidden',403)
                mutation_check()
            self.reserve(identity,row['workspace_id'])
            if mutation:mutation_check()
            if request_check:request_check()
        return NotionClient(payload['access_token'],write=mutation_check is not None,transport=self.transport,before_request=before)

    def reserve(self,identity,workspace):
        # One common budget for all grants/users of a workspace. This
        # stays conservative when Notion's actual limits are higher.
        stamp=self.clock()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            connection=c.execute('SELECT n.status,n.cooldown,u.role FROM notion_connections n JOIN users u ON u.id=n.user_id WHERE n.id=?',(identity,)).fetchone()
            if connection and connection['role']!='owner':raise NotionError('notionAccessDenied',403)
            if not connection or connection['status']!='active':raise NotionError('notionConnectionPaused',409)
            if connection['cooldown']>stamp:raise NotionError('notionRateLimited',429,int(connection['cooldown']-stamp)+1)
            c.execute('INSERT INTO notion_budgets(workspace_id,next_request) VALUES(?,0) ON CONFLICT(workspace_id) DO NOTHING',(workspace,))
            budget=c.execute('SELECT next_request FROM notion_budgets WHERE workspace_id=?',(workspace,)).fetchone()[0]
            # Integer milliseconds avoid PostgreSQL REAL's loss of fractional
            # seconds at epoch-sized values (which would defeat rate limiting).
            next_request=max(int(stamp*1000),budget)
            delay=(next_request-int(stamp*1000))/1000
            if delay>10:raise NotionError('notionRetryLater',503,10)
            c.execute('UPDATE notion_budgets SET next_request=? WHERE workspace_id=?',(next_request+360,workspace))
        if delay>0:self.wait(delay)
        # Pause/revocation while waiting must prevent sending the request.
        with self.db() as c:
            current=c.execute('SELECT n.status,n.cooldown,u.role FROM notion_connections n JOIN users u ON u.id=n.user_id WHERE n.id=?',(identity,)).fetchone()
            if current and current['role']!='owner':raise NotionError('notionAccessDenied',403)
            if not current or current['status']!='active':raise NotionError('notionConnectionPaused',409)
            if current['cooldown']>self.clock():raise NotionError('notionRateLimited',429,int(current['cooldown']-self.clock())+1)

    def pause(self,identity,status='paused'):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('UPDATE notion_connections SET status=?,updated=? WHERE id=?',(status,int(self.clock()),identity))
            c.execute("UPDATE notion_roots SET status='paused',version=version+1 WHERE connection_id=? AND status!='detached'",(identity,))
            c.execute("UPDATE notes SET version=version+1,updated=? WHERE id IN (SELECT p.note_id FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id WHERE r.connection_id=? AND r.status!='detached')",(int(self.clock()),identity))
