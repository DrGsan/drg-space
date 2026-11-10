from tests.test_security import m,owner,member,client,reset


def uid(c):return c.get('/api/me').json()['id']
def edit(c,target):
    response=c.get('/api/admin/users/'+target+'/friends');assert response.status_code==200,response.text
    return response.json()
def save(c,target,selected,state,**extra):
    return c.put('/api/admin/users/'+target+'/friends',json={'friend_ids':selected,'version':state['version'],**extra})


def test_admin_can_assign_another_users_mutual_friends_without_invites():
    c,_,_=owner();a,_=member(c,'alena');n,_=member(c,'nikolay');l,_=member(c,'lubov')
    aid,nid,lid=uid(a),uid(n),uid(l)
    state=edit(c,aid);assert aid not in {u['id'] for u in state['users']}
    assert save(c,aid,[nid,lid],state).status_code==200
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM friendships').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM social_requests').fetchone()[0]==0
    for peer in (n,l):assert aid in edit(c,uid(peer))['friend_ids']
    assert len(a.get('/api/chat/rooms').json()['items'])==2
    before=edit(c,aid)
    assert save(c,aid,[nid,lid],before).json()=={'friend_ids':sorted([nid,lid]),'added':0,'removed':0}
    with m.db() as db:
        assert all(row['owner_id']==aid for row in db.execute("SELECT owner_id FROM spaces WHERE kind='friends'"))


def test_removal_requires_confirmation_preserves_other_relationships_and_rejects_stale_editor():
    c,_,_=owner();a,_=member(c,'alena');n,_=member(c,'nikolay');l,_=member(c,'lubov')
    aid,nid,lid=uid(a),uid(n),uid(l)
    assert save(c,aid,[nid,lid],edit(c,aid)).status_code==200
    state=edit(c,aid);room=next(s['id'] for s in a.get('/api/social').json()['spaces'] if any(u['id']==nid for u in s['members']))
    shared=a.post('/api/notes',json={'title':'Shared','body':'Private','space_id':room}).json()['id']
    assert a.post('/api/chat/'+room+'/messages',json={'body':'hello','client_id':'admin-friend-removal-test'}).status_code==200
    assert save(c,aid,[lid],state).status_code==422
    assert n.get('/api/notes/'+shared).status_code==200
    assert save(c,aid,[lid],state,confirm_removals=True).status_code==200
    assert n.get('/api/notes/'+shared).status_code==404
    assert len(a.get('/api/chat/rooms').json()['items'])==1
    assert a.get('/api/chat/'+room+'/messages').status_code==404
    assert save(c,aid,[nid,lid],state).status_code==409
    assert edit(c,lid)['friend_ids']==[aid]


def test_invalid_selection_is_atomic_and_members_cannot_edit_any_friends():
    c,_,_=owner();a,_=member(c,'alena');n,_=member(c,'nikolay');aid,nid=uid(a),uid(n)
    state=edit(c,aid)
    for ids,status in [([aid],422),([nid,nid],422),([nid,'f'*32],404)]:
        assert save(c,aid,ids,state).status_code==status
        assert edit(c,aid)['friend_ids']==[]
    assert save(a,aid,[nid],state).status_code==403
    assert save(client(),aid,[nid],state).status_code==401
    c.post('/api/admin/access/lock');assert save(c,aid,[nid],state).status_code==423


def test_recipient_friend_limit_rolls_back_removals_and_all_new_links():
    import secrets
    c,_,_=owner();a,_=member(c,'alena');n,_=member(c,'nikolay');l,_=member(c,'lubov')
    aid,nid,lid=uid(a),uid(n),uid(l)
    assert save(c,aid,[lid],edit(c,aid)).status_code==200
    with m.db() as db:
        for i in range(200):
            peer=secrets.token_hex(16);sid=secrets.token_urlsafe(12);first,second=sorted((nid,peer))
            db.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',(peer,'limit'+str(i),'unused','member','{}',m.now()))
            db.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',(sid,'friends','Друзья',nid,m.now()))
            for identity in (first,second):db.execute('INSERT INTO space_members VALUES(?,?,?)',(sid,identity,m.now()))
            db.execute('INSERT INTO friendships VALUES(?,?,?)',(first,second,sid))
    state=edit(c,aid)
    assert save(c,aid,[nid],state,confirm_removals=True).status_code==422
    assert edit(c,aid)['friend_ids']==[lid]
