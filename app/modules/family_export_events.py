"""Exactly-once archive readiness events in the existing inbox/push transaction."""
import json
import re
import time

KEY='_family_photo_export_events_v1'


def notice_access(c,uid,payload,stamp=None,*,user=None):
    from .family_photo_events import GROUP_ID
    return (isinstance(payload.get('export_id'),str) and re.fullmatch(r'[a-f0-9]{32}',payload['export_id'])
        and isinstance(payload.get('family_group_id'),str) and GROUP_ID.fullmatch(payload['family_group_id'])
        and type(payload.get('expires')) is int and payload['expires']>(int(time.time()) if stamp is None else stamp)
        and (user['role']=='owner' if user is not None else bool(c.execute("SELECT 1 FROM users WHERE id=? AND role='owner'",(uid,)).fetchone())))


class FamilyExportEvents:
    def export_events(self,jobs,groups,stamp):
        active={group['id'] for group in groups}
        ready={job['id']:job for job in jobs if job.get('state')=='ready' and type(job.get('expires')) is int
            and job['expires']>stamp and job.get('groups') and all(gid in active for gid in job['groups'])}
        from app.notifications import notify
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT value FROM app_settings WHERE key=?',(KEY,)).fetchone()
            previous=json.loads(row[0]) if row else {}
            seen={key:expires for key,expires in previous.items() if expires>stamp}
            owners={row[0] for row in c.execute("SELECT id FROM users WHERE role='owner'")}
            for jid,job in ready.items():
                if jid in seen or job['actor_id'] not in owners:continue
                payload={'export_id':jid,'family_group_id':job['groups'][0],
                    'group_title':', '.join(job.get('group_titles',[]))[:240] or 'Семейная группа',
                    'expires':job['expires']}
                notify(c,job['actor_id'],'family_export_ready',payload,stamp)
                seen[jid]=job['expires']
            for row in c.execute("SELECT id,user_id,payload FROM notifications WHERE kind='family_export_ready'").fetchall():
                payload=json.loads(row['payload'])
                if payload.get('export_id') not in ready or not notice_access(c,row['user_id'],payload,stamp):
                    c.execute('DELETE FROM notifications WHERE id=?',(row['id'],))
            if seen!=previous:
                c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(KEY,json.dumps(seen)))

    def push_export(self,payload,uid):
        from app.http import HTTPError
        jid=payload.get('export_id')
        if not isinstance(jid,str) or not re.fullmatch(r'[a-f0-9]{32}',jid):return None
        try:
            job=self.fetch('v1/exports/'+jid+'?actor_id='+uid)
            if job.get('state')!='ready' or job.get('expires',0)<=time.time():return None
            active={group['id'] for group in self.fetch('v1/groups')['items']}
            return job if all(gid in active for gid in job['groups']) else None
        except HTTPError as error:
            if error.status_code in {404,410}:return None
            raise
