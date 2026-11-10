from tests.test_security import owner,member,reset
from tests.test_pages import page

from app import main as m


def test_trash_newest_batch_first_even_same_second_and_restore_forgets_order(monkeypatch):
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,_,_=owner();first=page(c,'Первая');second=page(c,'Вторая');third=page(c,'Третья')
    for note in [second,third,first]:assert c.delete('/api/notes/'+note['id']).status_code==200
    assert [n['id'] for n in c.get('/api/notes?trash=true').json()]==[first['id'],third['id'],second['id']]
    assert c.post('/api/notes/'+third['id']+'/restore').status_code==200
    with m.db() as db:assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',('note-trash-order:'+third['id'],)).fetchone()
    from app.retention import purge_trash
    with m.db() as db:
        purge_trash(db,stamp+29*86400)
        assert db.execute('SELECT COUNT(*) FROM notes WHERE deleted IS NOT NULL').fetchone()[0]==2
        purge_trash(db,stamp+30*86400)
        assert db.execute('SELECT COUNT(*) FROM notes WHERE deleted IS NOT NULL').fetchone()[0]==0


def test_trash_history_is_read_only_and_private():
    c,_,_=owner();g,_=member(c);n=page(c)
    c.put('/api/notes/'+n['id'],json={'title':'Изменено','body':'Новый текст'})
    c.delete('/api/notes/'+n['id'])
    path='/api/notes/'+n['id']+'/history'
    response=c.get(path+'?trash=true');assert response.status_code==200
    revision=response.json()[0]['id']
    assert c.get(path+'/'+revision+'?trash=true').status_code==200
    assert g.get(path+'?trash=true').status_code==404
    assert c.post(path+'/'+revision+'/restore').status_code==404


def test_trashed_attachments_are_readable_only_explicitly_by_editor():
    c,_,_=owner();g,_=member(c);n=page(c)
    attachment=c.post('/api/notes/'+n['id']+'/files?name=keep.txt',content=b'preserved').json()
    c.put('/api/notes/'+n['id'],json={'body':'[keep]('+attachment['url']+')'})
    c.delete('/api/notes/'+n['id'])
    assert c.get(attachment['url']).status_code==404
    assert c.get(attachment['url']+'?trash=true').content==b'preserved'
    assert g.get(attachment['url']+'?trash=true').status_code==404
    assert c.post('/api/notes/'+n['id']+'/files?name=new.txt',content=b'blocked').status_code==404
