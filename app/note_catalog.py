"""Request-scoped metadata batches; no authorization or response cache."""
import hashlib
import json


def preload(c,notes,uid,session,stamp):
    from app.notion_sync.presentation import index
    shown=index(c,uid)
    ids=list(dict.fromkeys([n['id'] for n in notes]+[n['id'] for n in shown['containers'].values()]))
    c.note_catalog_metadata={'databases':set(),'records':{},'favorites':set(),'unlocks':{},'ids':set(ids)}
    c.note_catalog_assets={}
    images={n[kind][6:] for n in [*notes,*shown['containers'].values()] for kind in ('icon','cover') if n[kind].startswith('image:')}
    for start in range(0,len(ids),200):
        batch=ids[start:start+200];marks=','.join('?' for _ in batch);cache=c.note_catalog_metadata
        cache['databases'].update(r[0] for r in c.execute('SELECT note_id FROM note_databases WHERE note_id IN ('+marks+')',tuple(batch)))
        cache['records'].update({r['note_id']:r['database_id'] for r in c.execute('SELECT note_id,database_id FROM database_records WHERE note_id IN ('+marks+')',tuple(batch))})
        cache['favorites'].update(r[0] for r in c.execute('SELECT note_id FROM note_favorites WHERE user_id=? AND note_id IN ('+marks+')',(uid,*batch)))
    locked=[n['id'] for n in notes if n['locked']]
    if session:
        for start in range(0,len(locked),200):
            batch=locked[start:start+200]
            c.note_catalog_metadata['unlocks'].update({r['note_id']:r['expires'] for r in c.execute('SELECT note_id,expires FROM note_unlocks WHERE session_token=? AND expires>? AND note_id IN ('+','.join('?' for _ in batch)+')',(session['token'],stamp,*batch))})
    images=list(images)
    for start in range(0,len(images),200):
        batch=images[start:start+200]
        for r in c.execute('SELECT id,note_id,kind,payload FROM note_assets WHERE id IN ('+','.join('?' for _ in batch)+')',tuple(batch)):
            c.note_catalog_assets[(r['id'],r['note_id'],r['kind'])]=r
    # Shared-page profiles are also request-scoped, not one query per note.
    from app.note_access import participants
    from app.notifications import actors
    people=set()
    for n in notes:
        if n['space_id']:people.update(participants(c,n));people.add(n['user_id'])
    c.note_profiles={person['id']:person for person in actors(c,sorted(people),stamp)}


def page_metadata(c,n,uid,aliases=()):
    cache=getattr(c,'note_catalog_metadata',None)
    if cache is not None and n['id'] in cache['ids']:
        return {'kind':'database' if n['id'] in cache['databases'] else 'page','database_id':cache['records'].get(n['id']),
                'favorite':int(any(identity in cache['favorites'] for identity in (n['id'],*aliases)))}
    database=c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(n['id'],)).fetchone()
    record=c.execute('SELECT database_id FROM database_records WHERE note_id=?',(n['id'],)).fetchone()
    ids=[n['id'],*aliases]
    favorite=c.execute('SELECT 1 FROM note_favorites WHERE note_id IN ('+','.join('?' for _ in ids)+') AND user_id=?',(*ids,uid)).fetchone()
    return {'kind':'database' if database else 'page','database_id':record[0] if record else None,'favorite':int(bool(favorite))}


def conditional(notes,session,previous):
    # Hash only the freshly authorized metadata projection. No content bodies,
    # provider requests, cross-account cache, TTL or stored secret is involved.
    encoded=json.dumps(notes,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    signature=hashlib.sha256(session['user_id'].encode()+b'\0'+session['token'].encode()+b'\0'+encoded).hexdigest()
    result={'revision':signature,'unchanged':previous==signature}
    if not result['unchanged']:result['notes']=notes
    return result
