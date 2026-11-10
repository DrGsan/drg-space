"""Small durable queue and safe restore policy. Never perform network I/O here."""
import json
import secrets


def dump(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def enqueue(c,root,entity,direction,stamp,delay=0):
    c.execute('INSERT INTO notion_jobs(id,root_id,direction,entity_id,due,created,updated) VALUES(?,?,?,?,?,?,?) ON CONFLICT(root_id,direction,entity_id) DO UPDATE SET generation=notion_jobs.generation+1,due=MIN(notion_jobs.due,excluded.due),updated=excluded.updated',
              (secrets.token_urlsafe(12),root,direction,entity,stamp+delay,stamp,stamp))


def refresh_import(c,root,entity,stamp):
    """Remember a source edit without publishing an unfinished snapshot."""
    row=c.execute("SELECT p.id,p.payload FROM notion_pages p JOIN notion_roots r ON r.id=p.root_id JOIN notion_connections n ON n.id=r.connection_id WHERE p.root_id=? AND p.notion_id=? AND p.status='importing' AND r.status IN ('active','importing') AND n.status='active'",(root,entity)).fetchone()
    if not row:return
    value=json.loads(row['payload'])
    if value.get('refresh_required'):
        if not c.execute("SELECT 1 FROM notion_jobs WHERE root_id=? AND entity_id=? AND direction='import'",(root,entity)).fetchone():enqueue(c,root,entity,'import',stamp)
        return
    value['refresh_required']=True
    c.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(value),row['id']))
    enqueue(c,root,entity,'import',stamp)


def pause_after_restore(c):
    for row in c.execute('SELECT id,payload FROM notion_connections').fetchall():
        payload=json.loads(row['payload']);payload.pop('refresh_lease',None)
        c.execute("UPDATE notion_connections SET payload=?,status='paused',cooldown=0 WHERE id=?",(dump(payload),row['id']))
    c.execute("UPDATE notion_roots SET mode='pull',status='paused',version=version+1 WHERE status!='detached'")
    c.execute('DELETE FROM notion_jobs')
    c.execute('DELETE FROM notion_oauth_states')


def claim(c,stamp,owner):
    row=c.execute("SELECT j.* FROM notion_jobs j JOIN notion_roots r ON r.id=j.root_id JOIN notion_connections n ON n.id=r.connection_id WHERE j.due<=? AND j.lease_until<=? AND n.status='active' AND n.cooldown<=? AND r.status IN ('scanning','importing','active') AND (j.direction IN ('scan','import') OR NOT EXISTS (SELECT 1 FROM notion_pages target WHERE target.root_id=j.root_id AND target.notion_id=j.entity_id AND target.status='importing')) AND (j.direction!='import' OR NOT EXISTS (SELECT 1 FROM notion_pages child JOIN notion_pages parent ON parent.root_id=child.root_id AND parent.notion_id=child.parent_id WHERE child.root_id=j.root_id AND child.notion_id=j.entity_id AND parent.status='importing')) ORDER BY CASE WHEN j.direction IN ('scan','pull','push') THEN 0 WHEN j.direction='import' THEN 1 ELSE 2 END,j.due,j.created LIMIT 1",(stamp,stamp,stamp)).fetchone()
    if not row:return None
    changed=c.execute('UPDATE notion_jobs SET lease_until=?,lease_owner=? WHERE id=? AND lease_until<=?',(stamp+90,owner,row['id'],stamp))
    return dict(row) if changed.rowcount==1 else None
