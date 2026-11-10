"""Reactions are independently synchronized and cannot change edit ownership."""
from tests.test_chat_workspace import conversations
from tests.test_chat_retention import send
from tests.test_next_features import ok
from tests.test_chat_editing import edit
from tests.test_security import client,reset

from app import main as m


def test_reactions_acl_toggle_limits_and_independent_content_revision():
    c,g,o,sid=conversations();message=ok(send(c,sid,body='Исходный текст'))
    path=f'/api/chat/{sid}/messages/{message["id"]}/reactions'
    assert client().post(path,json={'emoji':'👍'}).status_code==401
    assert o.post(path,json={'emoji':'👍'}).status_code==404
    assert g.post(path,json={'emoji':'anything'}).status_code==422
    first=ok(g.post(path,json={'emoji':'👍'}))['message']
    assert first['revision']==message['revision']==0 and first['edited']==0 and first['reaction_revision']==1
    assert first['reactions'][0]['people'][0]['id']==g.get('/api/me').json()['id']
    # A reaction never invalidates an already open author editor.
    changed=ok(edit(c,sid,message,'Правка автора'));assert changed['revision']==2
    ok(g.post(path,json={'emoji':'❤️'}));ok(g.post(path,json={'emoji':'🔥'}))
    assert g.post(path,json={'emoji':'😂'}).status_code==422
    result=ok(g.post(path,json={'emoji':'👍'}));assert len(result['reactions'])==2
    assert result['message']['body']=='Правка автора'
    assert result['message']['revision']==2


def test_removed_reaction_is_delivered_to_reconnecting_clients():
    c,g,o,sid=conversations();message=ok(send(c,sid,body='Старая запись'))
    path=f'/api/chat/{sid}/messages/{message["id"]}/reactions'
    ok(g.post(path,json={'emoji':'👍'}));ok(g.post(path,json={'emoji':'👍'}))
    history=ok(c.get(f'/api/chat/{sid}/messages?after={message["id"]}&edited_after=1'))
    assert history['items']==[] and history['edit_cursor']==2
    assert len(history['edits'])==1 and history['edits'][0]['reactions']==[]
    assert history['edits'][0]['reaction_revision']==2
    assert history['edits'][0]['revision']==0


def test_full_backup_preserves_reactions_and_rejects_invalid_metadata():
    import json
    import pytest
    from tests.test_admin import backup,headers
    from app.reactions import read,key,validate_archive
    from app.backups import BackupError
    c,g,o,sid=conversations();message=ok(send(c,sid,body='Общий документ'))
    ok(g.post(f'/api/chat/{sid}/messages/{message["id"]}/reactions',json={'emoji':'🔥'}))
    archive=backup(c);ok(c.post('/api/admin/backups/restore',content=archive,headers=headers()))
    def invalid():raise BackupError('Invalid reactions')
    with m.db() as db:
        value=read(db,sid,message['id']);assert value['revision']==1 and list(value['users'].values())==[['🔥']]
        validate_archive(db,invalid)
        value['users'][next(iter(value['users']))]=['unexpected']
        db.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(value),key(sid,message['id'])))
        with pytest.raises(BackupError):validate_archive(db,invalid)
