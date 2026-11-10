"""Account-scoped commit receipts for retrying an interrupted archive request."""
import hashlib
import json

LIFETIME = 7 * 86400
SOURCES=('notion','outline','notion-sync-copy','notion-recovery')

def prefix(uid, source):
    return source+'-import-receipt:'+hashlib.sha256(uid.encode()).hexdigest()[:32]+':'

def key(uid, source, operation):
    return prefix(uid, source)+hashlib.sha256(operation.encode()).hexdigest()

def previous(c, uid, source, operation, digest, stamp):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (key(uid, source, operation),)).fetchone()
    if not row:
        return None
    value = json.loads(row[0])
    if stamp-value['created'] > LIFETIME:
        c.execute('DELETE FROM app_settings WHERE key=?', (key(uid, source, operation),))
        return None
    if value['sha256'] != digest:
        raise ValueError('Archive operation was already committed for another digest')
    return value['summary']

def committed(c, uid, source, operation, digest, summary, stamp):
    rows = c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (prefix(uid, source)+'%',)).fetchall()
    rows = sorted(rows, key=lambda row:json.loads(row['value'])['created'], reverse=True)
    for index, row in enumerate(rows):
        if index >= 19 or stamp-json.loads(row['value'])['created'] > LIFETIME:
            c.execute('DELETE FROM app_settings WHERE key=?', (row['key'],))
    value = {'created':stamp, 'sha256':digest, 'summary':summary}
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', (key(uid, source, operation), json.dumps(value, ensure_ascii=False)))

def clear(c, uid):
    for source in SOURCES:
        c.execute('DELETE FROM app_settings WHERE key LIKE ?', (prefix(uid, source)+'%',))

def purge(c, stamp):
    for source in SOURCES:
        for row in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?', (source+'-import-receipt:%',)).fetchall():
            if stamp-json.loads(row['value'])['created'] > LIFETIME:
                c.execute('DELETE FROM app_settings WHERE key=?', (row['key'],))
