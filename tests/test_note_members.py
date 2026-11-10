"""Page guest authorization, revocation, branches and portable storage."""
from tests.test_security import owner,member,client,reset
from tests.test_social import family,join
from tests.test_widgets import image_bytes
from tests.test_personal_data import export,restore,unseal
from app import main as m

def create(c,title,scope,parent=None):
    r=c.post('/api/notes',json={'title':title,'space_id':scope,'parent_id':parent});assert r.status_code==200,r.text;return r.json()
def trusted_member(c,name='friend'):
    guest,token=member(c,name)
    request=c.post('/api/social/friends',json={'login':name});assert request.status_code==200,request.text
    assert guest.post('/api/social/requests/'+request.json()['id']+'/accept').status_code==200
    return guest,token

def invite(c,nid,login,role='editor'):
    r=c.post('/api/notes/'+nid+'/members',json={'logins':[login],'role':role});assert r.status_code==200,r.text

def test_add_outside_workspace_grants_only_selected_branch_without_changing_social_membership():
    c,_,_=owner();g,_=trusted_member(c);sid=family(c)
    root=create(c,'Shared root',sid);child=create(c,'Child',sid,root['id']);other=create(c,'Secret sibling',sid)
    before=g.get('/api/social').json()
    invite(c,root['id'],'friend','commenter')
    assert g.get('/api/social').json()==before
    assert {n['id'] for n in g.get('/api/notes').json()}=={root['id'],child['id']}
    assert g.get('/api/notes/'+other['id']).status_code==404
    payload=g.get('/api/notes/'+root['id']).json();assert payload['external'] and payload['role']=='commenter'
    assert len(payload['participants'])==2
    assert g.get('/api/notes/search?q=Child&space_id=personal').json()['total']==1
    assert g.get('/api/notes/search?q=Secret').json()['total']==0
    assert g.post('/api/notes/'+root['id']+'/comments',json={'body':'Allowed comment'}).status_code==200
    assert g.patch('/api/notes/'+root['id'],json={'icon':'📕','version':root['version']}).status_code==403
    uid=g.get('/api/me').json()['id']
    assert c.put('/api/notes/'+root['id']+'/permissions',json={'user_id':uid,'role':'editor'}).status_code==200
    assert g.get('/api/notes/'+child['id']).json()['role']=='editor'
    created=create(g,'Guest child',sid,root['id']);assert created['role']=='editor'
    assert g.post('/api/notes',json={'title':'Outside branch','space_id':sid}).status_code==404
    assert g.post('/api/notes/'+root['id']+'/members',json={'logins':['owner']}).status_code==403
    assert g.delete('/api/notes/'+root['id']+'/members/'+uid).status_code==403

def test_remove_workspace_member_revokes_branch_files_search_comments_and_readding_restores_children():
    c,_,_=owner();g,_=trusted_member(c);sid=family(c);join(c,g,sid);uid=g.get('/api/me').json()['id']
    root=create(c,'Restricted',sid);child=create(c,'Nested',sid,root['id']);other=create(c,'Still accessible',sid)
    attachment=c.post('/api/notes/'+root['id']+'/files?name=image.png',content=image_bytes()).json()
    invite(c,child['id'],'friend','editor')
    assert g.patch('/api/notes/'+child['id'],json={'favorite':True}).status_code==200
    r=c.delete('/api/notes/'+root['id']+'/members/'+uid);assert r.status_code==200,r.text
    assert {n['id'] for n in g.get('/api/notes').json()}=={other['id']}
    assert len(g.get('/api/social').json()['spaces'][0]['members'])==2
    for nid in (root['id'],child['id']):
        for path in ['', '/permissions','/comments','/history']:
            assert g.get('/api/notes/'+nid+path).status_code==404
    assert g.get(attachment['url']).status_code==404
    assert g.get('/api/notes/search?q=Nested').json()['total']==0
    assert not g.get('/api/favorites').json()
    invite(c,root['id'],'friend','viewer')
    assert g.get('/api/notes/'+child['id']).json()['role']=='viewer'
    assert g.get(attachment['url']).status_code==200
    assert g.post('/api/notes/'+root['id']+'/comments',json={'body':'Forbidden'}).status_code==403

def test_owners_are_protected_bulk_invite_atomic_csrf_and_backup():
    c,_,_=owner();g,_=trusted_member(c);sid=family(c);root=create(c,'Protected',sid)
    own=c.get('/api/me').json()['id']
    assert client().post('/api/notes/'+root['id']+'/members',json={'logins':['friend']}).status_code==401
    assert c.post('/api/notes/'+root['id']+'/members',json={'logins':['friend']},headers={'x-csrf-token':'bad'}).status_code==403
    assert c.delete('/api/notes/'+root['id']+'/members/'+own).status_code==422
    assert c.post('/api/notes/'+root['id']+'/members',json={'logins':['friend','missing-user']}).status_code==404
    assert g.get('/api/notes/'+root['id']).status_code==404
    invite(c,root['id'],'friend','viewer')
    from tests.test_admin import backup,headers
    archive=backup(c)
    with m.db() as db:db.execute('DELETE FROM app_settings WHERE key LIKE ?',('note-access:%',))
    r=c.post('/api/admin/backups/restore',content=archive,headers=headers());assert r.status_code==200,r.text
    assert g.get('/api/me').status_code==401
    with m.db() as db:
        from app.note_access import can_view
        n=db.execute('SELECT * FROM notes WHERE id=?',(root['id'],)).fetchone()
        target=db.execute('SELECT id FROM users WHERE login=?',('friend',)).fetchone()[0]
        assert can_view(db,n,target)

def test_personal_export_omits_revoked_content_and_includes_external_shared_pages():
    c,_,_=owner();g,_=trusted_member(c);sid=family(c)
    root=create(c,'Exported guest page',sid);invite(c,root['id'],'friend','viewer')
    archive=export(g,password='Friend-password-123');result=restore(g,archive,password='Friend-password-123');assert result.status_code==200,result.text
    assert any(n['title']==root['title'] and not n['space_id'] for n in g.get('/api/notes').json())
    uid=g.get('/api/me').json()['id'];c.delete('/api/notes/'+root['id']+'/members/'+uid)
    archive=export(g,password='Friend-password-123')
    manifest,_=unseal(archive)
    assert root['id'] not in {n['id'] for n in manifest['data']['notes']}
    # The imported private copy remains, while the revoked source is excluded.
    assert root['id'] not in {n['id'] for n in g.get('/api/notes').json()}

def test_revoked_guest_cannot_bypass_attachment_scope_or_submit_updates_on_open_socket():
    import base64
    import pytest
    from app.http import WebSocketDisconnect
    from tests.test_social import shared,initialize,hello,next_event,load_doc,content
    c,_,_=owner();g,_=trusted_member(c);sid=family(c);uid=g.get('/api/me').json()['id']
    hidden=shared(c,sid);allowed=shared(c,sid);initialize(c,hidden['id'])
    attachment=c.post('/api/notes/'+hidden['id']+'/files?name=private.png',content=image_bytes()).json()
    invite(c,hidden['id'],'friend');invite(c,allowed['id'],'friend')
    with g.websocket_connect('/api/live/notes/'+hidden['id']) as ws:
        hello(ws,g,99);event=next_event(ws,'sync');doc=load_doc(base64.b64decode(event['state']));content(doc).insert(0,'Forbidden change ')
        assert c.delete('/api/notes/'+hidden['id']+'/members/'+uid).status_code==200
        ws.send_json({'type':'update','seq':1,'epoch':event['epoch'],'update':base64.b64encode(doc.get_update()).decode()})
        with pytest.raises(WebSocketDisconnect):next_event(ws,'ack')
    assert 'Forbidden' not in c.get('/api/notes/'+hidden['id']).json()['body']
    assert g.get('/api/notes/'+allowed['id']+'/files/'+attachment['id']).status_code==404
    forged=g.post('/api/notes',json={'title':'Bypass','space_id':sid,'parent_id':allowed['id'],'blocks':[{'type':'image','props':{'url':'http://testserver'+attachment['url']}}]})
    assert forged.status_code==403,forged.text

def test_removing_family_member_clears_individual_grants_and_deleted_pages_leave_no_settings():
    c,_,_=owner();g,_=trusted_member(c);sid=family(c);join(c,g,sid);uid=g.get('/api/me').json()['id']
    root=create(c,'Shared',sid);invite(c,root['id'],'friend')
    assert c.delete('/api/social/families/'+sid+'/members/'+uid).status_code==200
    assert g.get('/api/notes/'+root['id']).status_code==404
    invite(c,root['id'],'friend')
    assert c.delete('/api/notes/'+root['id']).status_code==200
    assert c.delete('/api/notes/'+root['id']+'/permanent').status_code==200
    with m.db() as db:assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',('note-access:'+root['id'],)).fetchone()


def test_legacy_guests_can_see_participant_avatars_until_page_access_is_removed():
    c,_,_=owner();g,_=member(c);sid=family(c);root=create(c,'Avatars',sid)
    uid=c.get('/api/me').json()['id'];guest=g.get('/api/me').json()['id']
    r=c.post('/api/account/avatar',content=image_bytes());assert r.status_code==200,r.text
    assert g.get('/api/users/'+uid+'/avatar').status_code==404
    # A page guest created before friends-only invitations remains authorized.
    from app.note_access import save_rule
    with m.db() as db:save_rule(db,root['id'],guest,'viewer')
    assert g.get('/api/users/'+uid+'/avatar').status_code==200
    assert c.delete('/api/notes/'+root['id']+'/members/'+guest).status_code==200
    assert g.get('/api/users/'+uid+'/avatar').status_code==404


def test_only_trusted_friends_are_offered_and_new_invitations_are_checked_atomically():
    c,_,_=owner();friend,_=trusted_member(c);stranger,_=member(c,'stranger');sid=family(c)
    join(c,stranger,sid,'stranger')
    root=create(c,'Friends only',sid)
    assert [u['login'] for u in c.get('/api/notes/'+root['id']+'/member-options').json()]==['friend']
    other=create(c,'Other branch',family(c))
    denied=c.post('/api/notes/'+other['id']+'/members',json={'logins':['friend','stranger']})
    assert denied.status_code==403,denied.text
    assert friend.get('/api/notes/'+other['id']).status_code==404
    invite(c,other['id'],'friend')
    # Existing family participants retain their rights without a friendship.
    uid=stranger.get('/api/me').json()['id']
    assert c.put('/api/notes/'+root['id']+'/permissions',json={'user_id':uid,'role':'viewer'}).status_code==200
    assert stranger.get('/api/notes/'+root['id']).json()['role']=='viewer'


def test_catalog_metadata_uses_current_invites_without_document_payload():
    c,_,_=owner();g,_=trusted_member(c);sid=family(c)
    root=create(c,'Shared catalog',sid);child=create(c,'Nested catalog',sid,root['id']);hidden=create(c,'Hidden sibling',sid)
    assert g.get('/api/notes?catalog=true').json()==[]
    invite(c,root['id'],'friend','viewer')
    catalog=g.get('/api/notes?catalog=true').json()
    assert {n['id'] for n in catalog}=={root['id'],child['id']}
    assert all('body' not in n and 'blocks' not in n and n['role']=='viewer' for n in catalog)
    assert all(n['external'] for n in catalog)
    assert 'blocks' in g.get('/api/notes/'+root['id']).json()
    uid=g.get('/api/me').json()['id'];c.delete('/api/notes/'+root['id']+'/members/'+uid)
    assert g.get('/api/notes?catalog=true').json()==[]
