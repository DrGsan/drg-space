import copy
import json
import uuid
from collections import Counter
from pathlib import Path
import pytest
from tests.test_security import reset,owner,member,note
from tests.test_notion_sync_outbound import enabled
from tests.test_social import family,join
from app import main as m
from app.database import Connection


def catalog(c,revision=''):
    r=c.get('/api/notes?catalog=true&catalog_revision='+revision);assert r.status_code==200,r.text
    return r.json()


def test_thousand_note_catalog_is_batched_and_unchanged_transfer_is_small(monkeypatch):
    c,s,p,service,stamp,rid,mapping,patches=enabled()
    with m.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE notion_roots SET mode='pull' WHERE id=?",(rid,))
        source=dict(db.execute('SELECT * FROM notes').fetchone());link=dict(db.execute('SELECT * FROM notion_pages').fetchone())
        for i in range(1,1000):
            nid=f'audit-{i:010d}';remote=str(uuid.uuid5(uuid.NAMESPACE_URL,'audit-notion-'+str(i)))
            db.execute('INSERT INTO notes(id,user_id,title,body,blocks,icon,cover,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',(nid,s['user_id'],'Audit note '+str(i),source['body'],source['blocks'],source['icon'],source['cover'],stamp[0],stamp[0]))
            db.execute("INSERT INTO notion_pages(id,user_id,root_id,notion_id,note_id,status,writable,payload,updated) VALUES(?,?,?,?,?,'active',1,?,?)",('link-'+str(i),s['user_id'],rid,remote,nid,link['payload'],stamp[0]))
    execute=Connection._execute;queries=Counter()
    def count(connection,sql,parameters=()):queries[' '.join(sql.split())]+=1;return execute(connection,sql,parameters)
    monkeypatch.setattr(Connection,'_execute',count)
    first=c.get('/api/notes?catalog=true&catalog_revision=');assert first.status_code==200
    value=first.json();assert len(value['notes'])==1000 and not value['unchanged']
    assert all('body' not in n and 'blocks' not in n for n in value['notes'])
    first_sql=sum(queries.values());assert first_sql<80
    queries.clear();same=c.get('/api/notes?catalog=true&catalog_revision='+value['revision']);assert same.status_code==200
    assert same.json()['unchanged'] and 'notes' not in same.json() and len(same.content)<150
    repeat_sql=sum(queries.values());assert repeat_sql<80
    # The legacy/native list API remains an array with identical metadata.
    assert c.get('/api/notes?catalog=true').json()==value['notes']
    target=Path(__file__).resolve().parents[1]/'artifacts/notes-sync-efficiency';target.mkdir(parents=True,exist_ok=True)
    (target/('catalog-postgres.json' if m.database.kind=='postgres' else 'catalog-sqlcipher.json')).write_text(json.dumps({'notes':1000,'first_sql':first_sql,'repeat_sql':repeat_sql,'first_bytes':len(first.content),'unchanged_bytes':len(same.content),'storage':m.database.kind},indent=2)+'\n')


@pytest.mark.parametrize('change',['favorite','title','move','lock','trash','icon','create'])
def test_revision_follows_visible_metadata_without_changing_legacy_responses(change):
    c,_,_=owner();a=note(c);b=note(c);value=catalog(c)
    page=c.get('/api/notes/'+a).json()
    if change=='favorite':assert c.patch('/api/notes/'+a,json={'favorite':True}).status_code==200
    elif change=='title':assert c.put('/api/notes/'+a,json={'title':'New title','body':'Text','version':page['version']}).status_code==200
    elif change=='move':assert c.post('/api/notes/'+b+'/move',json={'parent_id':None,'before_id':a,'version':c.get('/api/notes/'+b).json()['version']}).status_code==200
    elif change=='lock':assert c.post('/api/notes/'+a+'/lock',json={'mode':'lock'}).status_code==200
    elif change=='trash':assert c.delete('/api/notes/'+a).status_code==200
    elif change=='icon':assert c.patch('/api/notes/'+a,json={'icon':'🌟'}).status_code==200
    else:note(c)
    changed=catalog(c,value['revision']);assert not changed['unchanged'] and changed['revision']!=value['revision']
    assert changed['notes']==c.get('/api/notes?catalog=true').json()


def test_conditional_catalog_rechecks_revoked_access_and_is_account_scoped():
    c,_,_=owner();guest,_=member(c);gid=guest.get('/api/me').json()['id'];sid=family(c);join(c,guest,sid)
    r=c.post('/api/notes',json={'title':'Shared','space_id':sid});assert r.status_code==200;nid=r.json()['id']
    value=catalog(guest);assert any(n['id']==nid for n in value['notes'])
    assert not catalog(c,value['revision'])['unchanged']
    assert c.delete('/api/social/spaces/'+sid+'/members/'+gid).status_code==200
    changed=catalog(guest,value['revision']);assert not changed['unchanged'] and all(n['id']!=nid for n in changed['notes'])


def test_batch_keeps_assets_favorites_and_session_unlock_expiry(monkeypatch):
    from tests.test_widgets import image_bytes
    c,_,_=owner();nid=note(c)
    assert c.post('/api/notes/'+nid+'/assets/icon',content=image_bytes()).status_code==200
    assert c.patch('/api/notes/'+nid,json={'favorite':True}).status_code==200
    assert c.post('/api/notes/'+nid+'/lock',json={'mode':'lock'}).status_code==200
    assert c.post('/api/notes/'+nid+'/lock',json={'mode':'temporary'}).status_code==200
    value=catalog(c);shown=next(n for n in value['notes'] if n['id']==nid)
    assert shown['favorite']==1 and shown['icon_url'] and shown['unlock_until']
    assert c.get(shown['icon_url']).status_code==200
    with m.db() as db:db.execute('UPDATE note_unlocks SET expires=0 WHERE note_id=?',(nid,))
    changed=catalog(c,value['revision']);assert not changed['unchanged']
    assert next(n for n in changed['notes'] if n['id']==nid)['unlock_until'] is None
