"""Bounded attachment failures: per-page state, ordinary queue, no extra timer."""
import json
import secrets
from app.notion_sync.state import dump,enqueue

MAX_ATTEMPTS=5


def failed(c,root,job,error,stamp,worker):
    if error.code!='notionAssetUnavailable' or not error.params.get('block_id'):return False
    current=c.execute('SELECT j.generation,r.status FROM notion_jobs j JOIN notion_roots r ON r.id=j.root_id WHERE j.id=? AND j.lease_owner=?',(job['id'],worker)).fetchone()
    if not current or current['status'] not in ('active','importing'):return True
    if current['generation']!=job['generation']:
        c.execute("UPDATE notion_jobs SET lease_until=0,lease_owner='',attempts=0 WHERE id=? AND lease_owner=?",(job['id'],worker))
        return True
    page=c.execute('SELECT n.title FROM notion_pages p JOIN notes n ON n.id=p.note_id WHERE p.root_id=? AND p.notion_id=? AND p.user_id=? AND n.user_id=?',(root['id'],job['entity_id'],root['user_id'],root['user_id'])).fetchone()
    if not page:return False
    payload=json.loads(c.execute('SELECT payload FROM notion_roots WHERE id=?',(root['id'],)).fetchone()[0])
    failures=payload.setdefault('asset_failures',{});old=failures.get(job['entity_id'],{})
    fingerprint=error.params.get('fingerprint','');block_id=error.params['block_id']
    same=old.get('fingerprint')==fingerprint and old.get('block_id')==block_id
    # A new queue entry after a source event starts a fresh bounded series.
    attempts=(old.get('attempts',0) if same and not (old.get('blocked') and not job['attempts']) else 0)+1
    reason=error.params.get('reason','notionAssetUnavailable')
    value={'page':page['title'],'file':error.params.get('file','')[:200],'host':error.params.get('host','')[:253],'reason':reason,
           'block_id':block_id,'fingerprint':fingerprint,'attempts':attempts,'blocked':attempts>=MAX_ATTEMPTS,'direction':'import' if job['direction']=='import' else 'pull'}
    failures[job['entity_id']]=value
    payload['error']='notionAssetUnavailable';payload['error_params']={}
    c.execute('UPDATE notion_roots SET payload=?,updated=?,version=version+1 WHERE id=?',(dump(payload),stamp,root['id']))
    if value['blocked']:
        c.execute('DELETE FROM notion_jobs WHERE id=? AND lease_owner=? AND generation=?',(job['id'],worker,job['generation']))
    else:
        delay=max(error.delay,min(3600,30*2**(attempts-1)))+secrets.randbelow(5)
        c.execute("UPDATE notion_jobs SET lease_until=0,lease_owner='',due=?,attempts=?,error=? WHERE id=? AND lease_owner=?",(stamp+delay,attempts,error.code,job['id'],worker))
    return True


def retry(c,root,stamp,*,decorations=True):
    payload=json.loads(root['payload']);failures=payload.pop('asset_failures',{})
    entries={(key,item['direction']) for key,item in failures.items()}
    entries.update((row['entity_id'],row['direction']) for row in c.execute("SELECT entity_id,direction FROM notion_jobs WHERE root_id=? AND error='notionAssetUnavailable' AND direction IN ('import','pull')",(root['id'],)))
    file_pages={entity for entity,direction in entries}
    decoration_ids={item['notion_id'] for kind in ('cover','icon') for item in payload.get(kind+'_warnings',{}).values()} if decorations else set()
    for entity in decoration_ids:
        row=c.execute('SELECT id,payload FROM notion_pages WHERE root_id=? AND notion_id=?',(root['id'],entity)).fetchone()
        if not row:continue
        value=json.loads(row['payload'])
        for kind in ('cover','icon'):
            if value.get(kind+'_pending'):
                value[kind+'_attempts']=0;value[kind+'_blocked']=False;value[kind+'_retry_at']=stamp
        c.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(value),row['id']))
        if entity not in file_pages:enqueue(c,root['id'],entity,'cover',stamp)
    for entity,direction in entries:
        if direction not in ('import','pull'):continue
        enqueue(c,root['id'],entity,direction,stamp)
        c.execute("UPDATE notion_jobs SET due=?,attempts=0,error='' WHERE root_id=? AND entity_id=? AND direction=?",(stamp,root['id'],entity,direction))
    if payload.get('error')=='notionAssetUnavailable':payload.pop('error',None);payload.pop('error_params',None)
    c.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump(payload),root['id']))


def expose(payload):
    return [{key:value for key,value in item.items() if key in ('page','file','host','reason','attempts','blocked')} for item in payload.get('asset_failures',{}).values()]
