"""Server-owned sibling positions, separate from legacy task payloads.

The existing encrypted settings store lets an older server ignore manual order
without making reminders unreadable. Full backups retain these records.
"""
import hashlib
import json
import re

PREFIX = 'task-order:'
MAX_POSITION = 9007199254740991


def positions(c, lid):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (PREFIX+lid,)).fetchone()
    return json.loads(row[0]) if row else {}


def save(c, lid, value):
    ids = {r[0] for r in c.execute('SELECT task_id FROM task_details WHERE list_id=?', (lid,))}
    value = {identity:position for identity,position in value.items() if identity in ids}
    if value:
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (PREFIX+lid,json.dumps(value,separators=(',',':'))))
    else:
        c.execute('DELETE FROM app_settings WHERE key=?', (PREFIX+lid,))


def ordered(row, value):
    return (value.get(row['id'],0),row['created'],row['id'])


def revision(c, lid):
    value=positions(c,lid)
    rows=c.execute('SELECT t.id,d.parent_id,d.version FROM tasks t JOIN task_details d ON d.task_id=t.id WHERE d.list_id=?',(lid,))
    snapshot=[[r['id'],r['parent_id'],value.get(r['id'],0),r['version']] for r in sorted(rows,key=lambda r:r['id'])]
    return hashlib.sha256(json.dumps(snapshot,separators=(',',':')).encode()).hexdigest()


def append(c, lid, parent, tid):
    value=positions(c,lid)
    rows=[dict(r) for r in c.execute('SELECT t.id,t.created,d.parent_id FROM tasks t JOIN task_details d ON d.task_id=t.id WHERE d.list_id=?',(lid,)) if r['parent_id']==parent and r['id']!=tid]
    highest=max((value.get(r['id'],0) for r in rows),default=0)
    if highest==MAX_POSITION:
        for index,row in enumerate(sorted(rows,key=lambda r:ordered(r,value)),1):value[row['id']]=index
        highest=len(rows)
    value[tid]=highest+1;save(c,lid,value)


def transfer(c, source, target, identities):
    old=positions(c,source);new=positions(c,target)
    new.update({identity:old.get(identity,0) for identity in identities})
    save(c,source,old);save(c,target,new)


def purge(c):
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)).fetchall():
        lid=row['key'][len(PREFIX):];value=json.loads(row['value'])
        ids={r[0] for r in c.execute('SELECT task_id FROM task_details WHERE list_id=?',(lid,))}
        kept={identity:position for identity,position in value.items() if identity in ids}
        if kept!=value or not kept:save(c,lid,kept)


def validate_archive(c, fail):
    lists={r['task_id']:r['list_id'] for r in c.execute('SELECT task_id,list_id FROM task_details')}
    for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',)):
        lid=row['key'][len(PREFIX):];value=json.loads(row['value'])
        if not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',lid) or not isinstance(value,dict) or len(value)>5000:fail()
        for identity,position in value.items():
            if not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',identity) or type(position) is not int or not 0<=position<=MAX_POSITION:fail()
            if identity in lists and lists[identity]!=lid:fail()
