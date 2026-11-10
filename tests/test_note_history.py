import json
from app.note_history import HISTORY_SECONDS, purge_history
from tests.test_security import owner,member,reset
from app import main as m
from tests.test_pages import page


def test_history_pagination_keeps_all_recent_and_expires_boundary():
    c,_,_=owner();p=page(c);stamp=m.now()
    with m.db() as db:
        for i in range(207):
            db.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)',(f'revision-{i}',p['id'],str(i),'body',None,stamp))
        db.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)',('expired',p['id'],'old','secret',None,stamp-HISTORY_SECONDS))
    first=c.get('/api/notes/'+p['id']+'/history').json();assert len(first)==100
    second=c.get('/api/notes/'+p['id']+'/history?before='+first[-1]['id']).json();assert len(second)==100
    third=c.get('/api/notes/'+p['id']+'/history?before='+second[-1]['id']).json();assert len(third)==7
    assert [r['id'] for r in first+second+third]==[f'revision-{i}' for i in reversed(range(207))]
    assert c.get('/api/notes/'+p['id']+'/history/expired').status_code==404
    assert c.post('/api/notes/'+p['id']+'/history/expired/restore').status_code==404
    with m.db() as db:assert not db.execute('SELECT 1 FROM note_revisions WHERE id=?',('expired',)).fetchone()


def test_history_preview_restore_conflict_undo_rich_content_and_isolation():
    c,_,_=owner();other,_=member(c)
    blocks=[{'id':'old','type':'codeBlock','props':{'language':'bash'},'content':'echo original','children':[]}]
    p=page(c,'Original',blocks=blocks);n=page(c,'Other page')
    new=c.put('/api/notes/'+p['id'],json={'title':'New','body':'new','blocks':[{'id':'new','type':'paragraph','content':'new'}]}).json()
    rid=c.get('/api/notes/'+p['id']+'/history').json()[0]['id'];path='/api/notes/'+p['id']+'/history/'+rid
    preview=c.get(path).json();assert preview['blocks']==blocks and preview['current_version']==new['version']
    assert other.get(path).status_code==404
    assert c.get('/api/notes/'+n['id']+'/history/'+rid).status_code==404
    newer=c.put('/api/notes/'+p['id'],json={'title':'Concurrent','body':'concurrent','blocks':None,'version':new['version']}).json()
    assert c.post(path+'/restore?version='+str(new['version'])).status_code==409
    assert c.get('/api/notes/'+p['id']).json()['title']=='Concurrent'
    restored=c.post(path+'/restore?version='+str(newer['version'])).json();assert restored['blocks']==blocks and restored['title']=='Original'
    checkpoint=c.get('/api/notes/'+p['id']+'/history').json()[0]
    assert checkpoint['title']=='Concurrent'
    undo=c.post('/api/notes/'+p['id']+'/history/'+checkpoint['id']+'/restore?version='+str(restored['version'])).json()
    assert undo['title']=='Concurrent' and undo['body']=='concurrent'
    assert c.get('/page/'+p['id']+'/history').status_code==200


def test_global_retention_without_editing_and_unchanged_checkpoints(monkeypatch):
    c,_,_=owner();p=page(c);stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    with m.db() as db:
        db.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)',('expired',p['id'],'old','',None,stamp-HISTORY_SECONDS))
        db.execute('INSERT INTO note_revisions VALUES(?,?,?,?,?,?)',('keep',p['id'],'recent','',None,stamp-HISTORY_SECONDS+1))
        purge_history(db,stamp)
        assert [r[0] for r in db.execute('SELECT id FROM note_revisions')]==['keep']
    c.put('/api/notes/'+p['id'],json={'title':p['title'],'body':p['body'],'blocks':p['blocks']})
    count=len(c.get('/api/notes/'+p['id']+'/history').json())
    c.put('/api/notes/'+p['id'],json={'title':p['title'],'body':p['body'],'blocks':p['blocks']})
    assert len(c.get('/api/notes/'+p['id']+'/history').json())==count
