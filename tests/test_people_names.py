import json
import pytest
from tests.test_security import owner, member, reset
from tests.test_social import family, join, shared
from tests.test_admin import backup, headers, proof
from tests.test_web_push import push_service, conversation, register, message
from app import main as m
from app.people_names import KEY, mention_text


def test_nicknames_are_private_and_never_rename_profiles_or_shared_content():
    c,_,_=owner();a,_=member(c);b,_=member(c,'another');sid=family(c)
    join(c,a,sid);join(c,b,sid,'another')
    aid=a.get('/api/me').json()['id'];cid=c.get('/api/me').json()['id']
    assert a.put('/api/account/profile',json={'display_name':'Алёна'}).status_code==200
    page=shared(a,sid)
    response=c.put('/api/users/'+aid+'/nickname',json={'nickname':'  Мама  '})
    assert response.status_code==200 and response.json()=={aid:'Мама'}
    assert c.get('/api/me').json()['people_names']=={aid:'Мама'}
    assert c.get('/api/presence').json()['people_names']=={aid:'Мама'}
    for account in (a,b):assert account.get('/api/me').json()['people_names']=={}
    assert b.put('/api/users/'+aid+'/nickname',json={'nickname':'Коллега'}).status_code==200
    assert b.get('/api/account/people-names').json()=={aid:'Коллега'}
    assert a.put('/api/users/'+cid+'/nickname',json={'nickname':'Папа'}).status_code==200
    for account in (c,a,b):
        person=next(p for p in account.get('/api/social').json()['spaces'][0]['members'] if p['id']==aid)
        assert person['display_name']=='Алёна' and 'nickname' not in person
        assert account.get('/api/notes/'+page['id']).json()['title']==page['title']
    assert a.get('/api/me').json()['settings']['profile']['display_name']=='Алёна'
    assert KEY not in c.get('/api/me').json()['settings']
    assert c.delete('/api/users/'+aid+'/nickname').json()=={}
    assert b.get('/api/account/people-names').json()=={aid:'Коллега'}


def test_regular_members_can_name_coworkers_but_not_unrelated_accounts():
    c,_,_=owner();a,_=member(c);b,_=member(c,'another');sid=family(c);join(c,a,sid)
    cid=c.get('/api/me').json()['id'];aid=a.get('/api/me').json()['id'];bid=b.get('/api/me').json()['id']
    assert a.put('/api/users/'+cid+'/nickname',json={'nickname':'Руководитель'}).status_code==200
    assert a.put('/api/users/'+bid+'/nickname',json={'nickname':'Чужой'}).status_code==404
    assert a.put('/api/users/'+aid+'/nickname',json={'nickname':'Я'}).status_code==422
    for value in ('a'*81,'line\nbreak','tab\tname','bad\x00name','bad\x7fname'):
        assert a.put('/api/users/'+cid+'/nickname',json={'nickname':value}).status_code==422
    assert a.put('/api/users/'+cid+'/nickname',json={'nickname':'   '}).json()=={}
    assert a.put('/api/users/'+cid+'/nickname',json={'nickname':'<img src=x onerror=alert(1)>'}).status_code==200
    assert a.get('/api/account/people-names').json()[cid].startswith('<img')
    assert a.put('/api/users/'+'f'*32+'/nickname',json={'nickname':'Unknown'}).status_code==404


def test_full_backup_preserves_private_names_and_deleted_accounts_are_cleaned():
    c,_,_=owner();a,_=member(c);aid=a.get('/api/me').json()['id']
    assert c.put('/api/users/'+aid+'/nickname',json={'nickname':'Мама'}).status_code==200
    saved=backup(c)
    c.delete('/api/users/'+aid+'/nickname')
    assert c.post('/api/admin/backups/restore',content=saved,headers=headers()).status_code==200
    with m.db() as db:
        row=db.execute('SELECT settings FROM users WHERE login=?',('owner',)).fetchone()
        assert json.loads(row[0])[KEY]=={aid:'Мама'}


def test_aliases_do_not_follow_reused_usernames_after_deletion():
    c,_,_=owner();a,_=member(c);aid=a.get('/api/me').json()['id']
    c.put('/api/users/'+aid+'/nickname',json={'nickname':'Мама'})
    assert c.post('/api/admin/users/delete',json=proof(user_id=aid,confirmation='friend')).status_code==200
    from tests.deletion_helpers import finish_account
    finish_account(aid)
    from app.people_names import purge
    with m.db() as db:purge(db)
    other,_=member(c)
    assert other.get('/api/me').json()['id']!=aid
    assert c.get('/api/account/people-names').json()=={}


def test_push_and_inbox_use_only_the_recipients_private_names(push_service):
    sender,recipient,sid=conversation();uid=sender.get('/api/me').json()['id']
    assert recipient.put('/api/users/'+uid+'/nickname',json={'nickname':'Мама'}).status_code==200
    register(recipient)
    source=message(sender,sid,body='Привет @owner — https://example.com/@owner name@owner.com')
    assert m.web_push.deliver_one()
    payload=push_service[0][1]
    assert payload['title']=='Мама'
    assert payload['body']=='Привет @Мама — https://example.com/@owner name@owner.com'
    recipient_id=recipient.get('/api/me').json()['id']
    with m.db() as db:
        assert db.execute('SELECT body FROM messages WHERE id=?',(source['id'],)).fetchone()[0].startswith('Привет @owner')
        notice=json.loads(db.execute("SELECT payload FROM notifications WHERE user_id=? AND kind='message'",(recipient_id,)).fetchone()[0])
        assert '@Мама' not in notice['preview']
    inbox=recipient.get('/api/notifications').json()['items'][0]['payload']
    assert inbox['actor_profile']['login']=='owner' and '@Мама' in inbox['preview']
    assert sender.get('/api/me').json()['people_names']=={}


def test_pending_second_factor_cannot_read_private_names():
    from tests.test_security import client
    c,_,_=owner();a,_=member(c);uid=a.get('/api/me').json()['id']
    c.put('/api/users/'+uid+'/nickname',json={'nickname':'Мама'})
    pending=client();response=pending.post('/api/login',json={'login':'owner','password':'Owner-password-123'})
    assert response.status_code==200 and response.json()['needs_2fa']
    assert pending.get('/api/me').json()['people_names']=={}
    assert pending.get('/api/account/people-names').status_code==401
