"""Capability scope, expiry and real persisted group signaling, without accounts for guests."""
import json
import secrets
import uuid
import pytest
from tests.test_security import owner, member, client, reset
from app import main as m
from app.modules.guest_calls import purge_guest_calls
from tests.test_livekit import sfu

@pytest.fixture(autouse=True)
def transport(monkeypatch,sfu):
    monkeypatch.setattr('app.modules.calls.turn_available',lambda:True)

def invite(a):
    data={'id':uuid.uuid4().hex,'transport':'livekit'};r=a.post('/api/calls/guest-rooms',json=data);assert r.status_code==200,r.text
    return r.json(),data

def joining(name='Гость'):
    return {'id':uuid.uuid4().hex,'nonce':secrets.token_urlsafe(32),'name':name,'transport':'livekit'}

def enter(room,name='Гость'):
    c=client();data=joining(name);r=c.post('/api/guest-calls/'+room['url'].split('/')[-1]+'/join',json=data)
    assert r.status_code==200,r.text
    item=r.json();c.headers['authorization']='Bearer '+item['credential'];return c,item,data

def sync(c,room,signals=None,muted=False):
    return c.post('/api/guest-calls/rooms/'+room['id']+'/sync',json={'muted':muted,'signals':signals or []})

def test_guest_link_joins_by_name_only_and_does_not_grant_an_account():
    a,_,_=owner();room,data=invite(a);token=room['url'].split('/')[-1]
    assert room['expires']-room['created']==43200 and room['limit']==8
    repeated=a.post('/api/calls/guest-rooms',json=data).json()
    assert repeated['server_time']>=room['server_time']
    assert {k:v for k,v in repeated.items() if k!='server_time'}=={k:v for k,v in room.items() if k!='server_time'}
    before=None
    with m.db() as db:before=db.execute('SELECT count(*) FROM users').fetchone()[0]
    c,item,_=enter(room,'<img src=x onerror=alert(1)>')
    assert c.get('/api/me').status_code==401
    assert c.get('/api/chat/rooms').status_code==401
    assert c.get('/api/calls/guest-rooms').status_code==401
    state=sync(c,room).json();assert len(state['participants'])==1
    assert 'credential' not in state and 'token' not in state
    assert state['participants'][0]['name']=='<img src=x onerror=alert(1)>'
    with m.db() as db:assert db.execute('SELECT count(*) FROM users').fetchone()[0]==before
    assert c.get('/api/guest-calls/'+token).json()['count']==1
    assert client().post('/api/guest-calls/rooms/'+room['id']+'/sync',json={'muted':False}).status_code==401
    assert c.post('/api/guest-calls/rooms/'+room['id']+'/leave',json={}).status_code==200
    assert sync(c,room).status_code==401

def test_group_signals_reach_only_the_other_peer_and_are_idempotent():
    a,_,_=owner();room,_=invite(a)
    guests=[enter(room,name)[:2] for name in ['Первый','Второй','Третий']]
    # Simulate an already running pre-upgrade room; new joins must be closed,
    # while its authenticated participants can finish their existing conversation.
    with m.db() as db:
        row=db.execute('SELECT payload FROM guest_call_rooms WHERE id=?',(room['id'],)).fetchone();payload=json.loads(row['payload']);payload['transport']='webrtc'
        db.execute('UPDATE guest_call_rooms SET payload=? WHERE id=?',(json.dumps(payload),room['id']))
        db.execute('DELETE FROM app_settings WHERE key=?',('livekit:room:'+room['id'],))
    assert client().post('/api/guest-calls/'+room['url'].split('/')[-1]+'/join',json=joining()).status_code==409
    ordered=sorted(guests,key=lambda pair:pair[1]['self']);first,f=ordered[0];second,s=ordered[1];third,t=ordered[2]
    description={'type':'offer','sdp':'v=0\r\no=private-offer'}
    ice={'candidate':'candidate:1 1 udp 1 192.0.2.1 10000 typ host','sdpMid':'0','sdpMLineIndex':0}
    signal={'peer_id':s['self'],'description':description,'candidates':[ice]}
    assert sync(first,room,[signal]).status_code==200
    assert sync(first,room,[signal]).status_code==200
    remote=sync(second,room).json();assert remote['peers'][0]['description']==description and len(remote['peers'][0]['candidates'])==1
    assert sync(third,room).json()['peers']==[]
    assert 'private-offer' not in json.dumps(sync(first,room).json())
    answer={'peer_id':f['self'],'description':{'type':'answer','sdp':'v=0\r\no=private-answer'}}
    assert sync(second,room,[answer]).status_code==200
    assert sync(first,room).json()['peers'][0]['description']['type']=='answer'
    assert sync(second,room,[signal|{'peer_id':f['self']}]).status_code==422
    other_room,_=invite(a);other,item,_=enter(other_room)
    assert sync(first,other_room).status_code==401
    assert sync(first,room,[{'peer_id':item['self'],'description':description}]).status_code==200
    assert sync(other,other_room).json()['peers']==[]
    assert first.post('/api/guest-calls/rooms/'+room['id']+'/leave',json={}).status_code==200
    assert sync(second,room).json()['peers']==[]

def test_expiry_at_twelve_hours_closes_existing_sessions_and_purges(monkeypatch):
    a,_,_=owner();room,_=invite(a);c,_,_=enter(room);token=room['url'].split('/')[-1]
    monkeypatch.setattr(m,'now',lambda:room['expires'])
    assert c.get('/api/guest-calls/'+token).status_code==410
    assert c.post('/api/guest-calls/'+token+'/join',json=joining()).status_code==410
    assert sync(c,room).status_code==410
    with m.db() as db:
        purge_guest_calls(db,m.now())
        for table in ['guest_call_rooms','guest_call_participants','guest_call_links']:assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0

def test_capacity_stale_seats_and_owner_controls(monkeypatch):
    a,_,_=owner();b,_=member(a);room,_=invite(a);token=room['url'].split('/')[-1]
    guest_client=client()
    for _ in range(8):assert guest_client.post('/api/guest-calls/'+token+'/join',json=joining()).status_code==200
    assert guest_client.post('/api/guest-calls/'+token+'/join',json=joining()).status_code==409
    assert b.get('/api/calls/guest-rooms').json()['items']==[]
    assert b.post('/api/calls/guest-rooms/'+room['id']+'/join',json=joining()).status_code==404
    before=m.now();monkeypatch.setattr(m,'now',lambda:before+91)
    host=a.post('/api/calls/guest-rooms/'+room['id']+'/join',json=joining());assert host.status_code==200,host.text
    assert host.json()['participants'][0]['host'] is True
    assert a.post('/api/calls/guest-rooms/'+room['id']+'/end',json={}).status_code==200
    assert guest_client.get('/api/guest-calls/'+token).status_code==410

def test_join_retries_names_csrf_and_room_capabilities():
    a,_,_=owner();room,_=invite(a);token=room['url'].split('/')[-1];c=client();data=joining('Тест')
    first=c.post('/api/guest-calls/'+token+'/join',json=data).json();second=c.post('/api/guest-calls/'+token+'/join',json=data).json()
    assert first['self']==second['self'] and first['credential']==second['credential']
    assert len(second['participants'])==1
    assert c.post('/api/guest-calls/'+token+'/join',json=data|{'nonce':secrets.token_urlsafe(32)}).status_code==409
    for name in ['   ','Имя\nстрока','x'*61]:assert c.post('/api/guest-calls/'+token+'/join',json=joining(name)).status_code==422
    c.headers['origin']='https://foreign.invalid'
    assert c.post('/api/guest-calls/'+token+'/join',json=joining()).status_code==403
    del a.headers['x-csrf-token'];assert a.post('/api/calls/guest-rooms',json={'id':uuid.uuid4().hex}).status_code==403

def test_ephemeral_guests_are_not_resurrected_by_a_full_restore(monkeypatch):
    from tests.test_admin import backup,headers
    from app.backups import Backups
    a,_,_=owner();room,_=invite(a);c,joined,_=enter(room)
    assert a.put('/api/calls/guest-rooms/'+room['id'],json={'title':'Private temporary call title'}).status_code==200
    write=Backups.stage_write;seen=[]
    def inspect_snapshot(service,directory,name,body):
        if name=='site.sqlite3':
            # Check deleted pages too, not just the logical table contents.
            assert room['url'].split('/')[-1].encode() not in body
            assert joined['credential'].encode() not in body
            assert b'Private temporary call title' not in body
            seen.append(name)
        return write(service,directory,name,body)
    monkeypatch.setattr(Backups,'stage_write',inspect_snapshot)
    body=backup(a);assert seen

    r=a.post('/api/admin/backups/restore',content=body,headers=headers());assert r.status_code==200,r.text
    with m.db() as db:
        for table in ['guest_call_rooms','guest_call_participants','guest_call_links']:assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
    assert sync(c,room).status_code==410


def test_room_appearance_is_owner_only_preserves_link_expiry_and_reaches_guests(monkeypatch):
    import base64,io
    from PIL import Image
    a,_,_=owner();b,_=member(a);room,_=invite(a);token=room['url'].split('/')[-1]
    guest,_,_=enter(room)
    with m.db() as c:storage_counts=[c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['media','files']]
    image=Image.new('RGB',(600,300),'#5678cc');stream=io.BytesIO();image.save(stream,format='JPEG',exif=b'private-camera-metadata')
    changes={'title':'  Семейный вечер 🐈  ','avatar':base64.b64encode(stream.getvalue()).decode()}
    path='/api/calls/guest-rooms/'+room['id']
    assert b.put(path,json=changes).status_code==404
    assert guest.put(path,json=changes).status_code==401
    saved=a.put(path,json=changes);assert saved.status_code==200,saved.text
    updated=saved.json();assert updated['title']=='Семейный вечер 🐈'
    assert updated['url']==room['url'] and updated['expires']==room['expires'] and updated['created']==room['created']
    assert 'avatar' not in updated and 'token' not in updated
    avatar=guest.get(updated['avatar_url']);assert avatar.status_code==200
    assert avatar.headers['content-type']=='image/webp' and avatar.headers['cache-control']=='no-store'
    thumb=Image.open(io.BytesIO(avatar.content));assert thumb.size==(256,256) and not thumb.getexif()
    assert b'private-camera-metadata' not in avatar.content
    assert guest.get('/api/guest-calls/'+token).json()['title']==updated['title']
    assert sync(guest,room).json()['room']['avatar_url']==updated['avatar_url']
    assert a.get('/api/calls/guest-rooms').json()['items'][0]['title']==updated['title']
    # Renaming alone keeps the photo; removal revokes the same old photo URL.
    renamed=a.put(path,json={'title':'Новое название'}).json();assert renamed['avatar_url']==updated['avatar_url']
    removed=a.put(path,json={'title':'Новое название','remove_avatar':True}).json();assert removed['avatar_url'] is None
    assert guest.get(updated['avatar_url']).status_code==404
    a.put(path,json=changes)
    with m.db() as c:assert [c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['media','files']]==storage_counts
    ended,_=invite(a)
    ended_appearance=a.put('/api/calls/guest-rooms/'+ended['id'],json=changes).json()
    assert a.post('/api/calls/guest-rooms/'+ended['id']+'/end',json={}).status_code==200
    assert guest.get(ended_appearance['avatar_url']).status_code==410
    with m.db() as c:assert c.execute('SELECT 1 FROM guest_call_rooms WHERE id=?',(ended['id'],)).fetchone() is None
    monkeypatch.setattr(m,'now',lambda:room['expires'])
    assert guest.get(updated['avatar_url']).status_code==410
    with m.db() as c:
        purge_guest_calls(c,m.now());assert c.execute('SELECT COUNT(*) FROM guest_call_rooms').fetchone()[0]==0
        assert [c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['media','files']]==storage_counts


def test_room_appearance_rejects_bad_images_names_and_csrf_without_partial_update():
    import base64
    a,_,_=owner();room,_=invite(a);path='/api/calls/guest-rooms/'+room['id']
    for body in [{'title':'   '},{'title':'x'*81},{'title':'Name\nSecond line'},{'title':'bad\x7f'},
                 {'title':'Good','avatar':'not base64'}, {'title':'Good','avatar':base64.b64encode(b'<svg onload="alert(1)"/>').decode()},
                 {'title':'Good','avatar':'','remove_avatar':True}]:
        assert a.put(path,json=body).status_code==422
        assert a.get('/api/calls/guest-rooms').json()['items'][0]['title']=='Гостевой звонок'
    del a.headers['x-csrf-token'];assert a.put(path,json={'title':'Should not save'}).status_code==403
