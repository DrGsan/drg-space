"""Durable portal votes feed the archive bot's complete, versioned snapshots."""
import json
import time
from app.reactions import PREFIX,key,read

RECONCILE_SECONDS = 60


def signature(value):
    return json.dumps([value.get('revision',0),value['users']],sort_keys=True)


class FamilyReactionSync:
    def sync_reaction_votes(self):
        if not self.status().get('bot_reactions'):return
        cursor=getattr(self,'reaction_cursor','')
        confirmed=getattr(self,'reaction_confirmed',{})
        self.reaction_confirmed=confirmed
        stamp=time.monotonic()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=c.execute('SELECT key,value FROM app_settings WHERE key LIKE ? AND key>? ORDER BY key LIMIT 50',(PREFIX+'telegram:%',cursor)).fetchall()
            if not rows and cursor:
                rows=c.execute('SELECT key,value FROM app_settings WHERE key LIKE ? ORDER BY key LIMIT 50',(PREFIX+'telegram:%',)).fetchall()
            if not rows:return
            self.reaction_cursor=rows[-1]['key']
            pending=[]
            for row in rows:
                value=json.loads(row['value']);ack=confirmed.get(row['key'])
                if ack and ack[0]==signature(value) and stamp-ack[1]<RECONCILE_SECONDS:continue
                pending.append((row['key'],value))
            if not pending:return
            # The archive accepts one revision per batch. Reuse it on retries;
            # only actual vote changes or a mixed batch require a newer value.
            revision=max(value.get('revision',0) for _,value in pending) or time.time_ns()//1000
            batch=[]
            sent={}
            for ident,value in pending:
                if value.get('revision')!=revision:
                    value['revision']=revision
                    c.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(value),ident))
                batch.append(dict(group_id=value['space'][len('telegram:'):],message_id=value['message'],votes=value['users']))
                sent[ident]=signature(value)
        result=self.bot_fetch('v1/site-reactions','PUT',{'revision':revision,'items':batch})
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            for item in result['items']:
                space='telegram:'+item['group_id'];mid=item['message_id']
                current=read(c,space,mid)
                ident=key(space,mid)
                if signature(current)!=sent.get(ident):continue
                if current.get('revision')!=item.get('revision'):
                    # A restored site may lag an archive's durable revision.
                    # Rebase the local votes for the next retry, never replace
                    # a concurrent local change with the archive response.
                    remote=item.get('revision')
                    if type(remote) is int and current.get('revision',0)<remote<2**53-1:
                        current['revision']=max(time.time_ns()//1000,remote+1)
                        c.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(current),ident))
                    continue
                if item['state']=='gone' or item['state']=='delivered' and not current['users']:
                    c.execute('DELETE FROM app_settings WHERE key=?',(ident,))
                    confirmed.pop(ident,None)
                elif item['state']=='delivered':
                    confirmed.pop(ident,None)
                    confirmed[ident]=(signature(current),stamp)
            # This cache is disposable: restarts/leader changes resend state.
            while len(confirmed)>2048:confirmed.pop(next(iter(confirmed)))
