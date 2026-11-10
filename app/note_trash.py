"""Presentation order only; retention continues to use notes.deleted + 30 days."""
import json
PREFIX='note-trash-order:'
COUNTER='note-trash-counter'

def ordering(c):
    return {r['key'][len(PREFIX):]:json.loads(r['value']) for r in c.execute('SELECT key,value FROM app_settings WHERE key LIKE ?',(PREFIX+'%',))}

def record(c,ids,stamp):
    row=c.execute('SELECT value FROM app_settings WHERE key=?',(COUNTER,)).fetchone()
    number=int(json.loads(row[0]))+1 if row else 1
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(COUNTER,json.dumps(number)))
    for nid in ids:
        c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(PREFIX+nid,json.dumps({'deleted':stamp,'order':number})))

def forget(c,ids):
    for nid in ids:c.execute('DELETE FROM app_settings WHERE key=?',(PREFIX+nid,))


def validate_archive(c,fail):
    try:
        row=c.execute('SELECT value FROM app_settings WHERE key=?',(COUNTER,)).fetchone()
        maximum=json.loads(row[0]) if row else 0
        if type(maximum) is not int or not 0<=maximum<=2**53-1:raise ValueError()
        for nid,value in ordering(c).items():
            if not isinstance(value,dict) or set(value)!={'deleted','order'} or any(type(value[k]) is not int or value[k]<=0 for k in value) or value['order']>maximum:raise ValueError()
            note=c.execute('SELECT deleted FROM notes WHERE id=?',(nid,)).fetchone()
            if not note or note[0]!=value['deleted']:raise ValueError()
    except (ValueError,TypeError,KeyError):fail()
