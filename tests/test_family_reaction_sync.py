import json
import httpx
import pytest
from tests.test_family_photos import connected,enable,grant,G1
from tests.test_security import owner,member,reset
from app import main as m
from app.reactions import key,read,purge,validate_archive
from types import SimpleNamespace
import time
from app.modules import family_photo_reactions as sync


def setup(connected,monkeypatch):
    bridge,_=connected;c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    grant(c,uid,[G1]);enable(guest)
    assert c.put('/api/family-photos/admin/users/'+uid,json={'enabled':True,'groups':[G1],'write_groups':[G1]}).status_code==200
    bridge.status();bridge.cached['bot_reactions']=True
    original=bridge.fetch
    monkeypatch.setattr(bridge,'fetch',lambda path,method='GET',data=None:{'id':1,'media':[],'reactions':[]} if path==f'v1/groups/{G1}/messages/1' else original(path,method,data))
    return bridge,c,guest,uid


def test_votes_and_empty_removal_survive_outage_purge_and_retry(connected,monkeypatch):
    bridge,c,guest,uid=setup(connected,monkeypatch)
    path=f'/api/family-photos/groups/{G1}/messages/1/reactions'
    assert guest.post(path,json={'emoji':'👍'}).json()['bot_reaction_sync']['state']=='queued'
    saved=[]
    def failed(*args,**kwargs):raise RuntimeError('archive down')
    monkeypatch.setattr(bridge,'bot_fetch',failed)
    with pytest.raises(RuntimeError):bridge.sync_reaction_votes()
    with m.db() as db:assert read(db,'telegram:'+G1,1)['users']=={uid:['👍']}
    def forward(path,method,data):
        assert path=='v1/site-reactions' and method=='PUT';saved.append(data)
        return {'items':[item|{'revision':data['revision'],'state':'queued'} for item in data['items']]}
    monkeypatch.setattr(bridge,'bot_fetch',forward);bridge.sync_reaction_votes()
    assert saved[-1]['items'][0]['votes']=={uid:['👍']}
    assert guest.post(path,json={'emoji':'👍'}).status_code==200
    with m.db() as db:
        purge(db);validate_archive(db,lambda:pytest.fail('invalid backup metadata'))
        assert read(db,'telegram:'+G1,1)['users']=={} and read(db,'telegram:'+G1,1)['revision']>0
    bridge.sync_reaction_votes();assert saved[-1]['items'][0]['votes']=={}
    monkeypatch.setattr(bridge,'bot_fetch',lambda path,method,data:{'items':[item|{'revision':data['revision'],'state':'delivered'} for item in data['items']]})
    bridge.sync_reaction_votes()
    with m.db() as db:assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',(key('telegram:'+G1,1),)).fetchone()


def test_late_ack_never_deletes_newer_votes_and_view_only_cannot_react(connected,monkeypatch):
    bridge,c,guest,uid=setup(connected,monkeypatch);path=f'/api/family-photos/groups/{G1}/messages/1/reactions'
    guest.post(path,json={'emoji':'👍'})
    def concurrent(path,method,data):
        with m.db() as db:
            value=read(db,'telegram:'+G1,1);value['revision']=data['revision']+1;value['users']={uid:['❤️']}
            db.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(value),key('telegram:'+G1,1)))
        return {'items':[item|{'revision':data['revision'],'state':'gone'} for item in data['items']]}
    monkeypatch.setattr(bridge,'bot_fetch',concurrent);bridge.sync_reaction_votes()
    with m.db() as db:assert read(db,'telegram:'+G1,1)['users']=={uid:['❤️']}
    grant(c,uid,[G1]);assert guest.post(path,json={'emoji':'🔥'}).status_code==403
    with m.db() as db:
        purge(db);assert read(db,'telegram:'+G1,1)['users']=={}


def test_confirmed_votes_skip_idle_writes_but_reconcile_and_send_changes(connected,monkeypatch):
    bridge,c,guest,uid=setup(connected,monkeypatch)
    stamp=[100.0]
    monkeypatch.setattr(sync,'time',SimpleNamespace(monotonic=lambda:stamp[0],time_ns=time.time_ns))
    path=f'/api/family-photos/groups/{G1}/messages/1/reactions'
    guest.post(path,json={'emoji':'👍'})
    sent=[]
    def delivered(path,method,data):
        if path=='v1/site-permissions':return {'ok':True}
        sent.append(data)
        return {'items':[item|{'revision':data['revision'],'state':'delivered'} for item in data['items']]}
    monkeypatch.setattr(bridge,'bot_fetch',delivered)
    bridge.sync_reaction_votes()
    with m.db() as db:before=read(db,'telegram:'+G1,1)
    for _ in range(3):bridge.sync_reaction_votes()
    with m.db() as db:assert read(db,'telegram:'+G1,1)==before
    assert len(sent)==1
    stamp[0]+=61;bridge.sync_reaction_votes()
    assert len(sent)==2 and sent[-1]['revision']==before['revision']
    guest.post(path,json={'emoji':'❤️'});bridge.sync_reaction_votes()
    assert len(sent)==3 and sent[-1]['items'][0]['votes']=={uid:['👍','❤️']}
    # Purge can remove an ineligible actor without changing the vote revision.
    assert grant(c,uid,[G1]).status_code==200
    with m.db() as db:purge(db)
    bridge.sync_reaction_votes()
    assert len(sent)==4 and sent[-1]['items'][0]['votes']=={}
    with m.db() as db:assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',(key('telegram:'+G1,1),)).fetchone()


def test_queued_votes_retry_same_revision_and_restart_resends(connected,monkeypatch):
    bridge,c,guest,uid=setup(connected,monkeypatch)
    guest.post(f'/api/family-photos/groups/{G1}/messages/1/reactions',json={'emoji':'👍'})
    sent=[]
    def forward(path,method,data):
        sent.append(data)
        return {'items':[item|{'revision':data['revision'],'state':'queued' if len(sent)==1 else 'delivered'} for item in data['items']]}
    monkeypatch.setattr(bridge,'bot_fetch',forward)
    bridge.sync_reaction_votes();bridge.sync_reaction_votes();bridge.sync_reaction_votes()
    assert len(sent)==2 and sent[0]['revision']==sent[1]['revision']
    bridge.reaction_confirmed={};bridge.sync_reaction_votes()
    assert len(sent)==3


def test_permissions_refresh_before_expiry_and_revoke_without_delay(connected,monkeypatch):
    from app.modules import family_photo_messaging as permissions
    bridge,c,guest,uid=setup(connected,monkeypatch)
    stamp=[100.0];sent=[]
    monkeypatch.setattr(permissions,'time',SimpleNamespace(monotonic=lambda:stamp[0],time_ns=time.time_ns))
    def forward(path,method,data):
        sent.append(data);return {'revision':data['revision']}
    monkeypatch.setattr(bridge,'bot_fetch',forward)
    bridge.permission_confirmed=None
    bridge.sync_send_permissions();bridge.sync_send_permissions()
    assert len(sent)==1 and sent[-1]['actors'][uid]==[G1]
    stamp[0]+=61;bridge.sync_send_permissions()
    assert len(sent)==2
    grant(c,uid,[G1]);bridge.sync_send_permissions()
    assert len(sent)==3 and sent[-1]['actors'].get(uid)==[]
    # An old archive or an ignored out-of-order snapshot is retried.
    for response in ({'ok':True},{'ok':True,'revision':None}):
        bridge.permission_confirmed=None
        monkeypatch.setattr(bridge,'bot_fetch',lambda path,method,data:response)
        bridge.sync_send_permissions()
        assert bridge.permission_confirmed is None


def test_restored_older_revision_recovers_without_overwriting_votes(connected,monkeypatch):
    bridge,c,guest,uid=setup(connected,monkeypatch)
    guest.post(f'/api/family-photos/groups/{G1}/messages/1/reactions',json={'emoji':'👍'})
    with m.db() as db:remote=read(db,'telegram:'+G1,1)['revision']+100000000
    sent=[]
    def forward(path,method,data):
        sent.append(data)
        revision=remote if len(sent)==1 else data['revision']
        return {'items':[item|{'revision':revision,'state':'delivered'} for item in data['items']]}
    monkeypatch.setattr(bridge,'bot_fetch',forward)
    bridge.sync_reaction_votes();bridge.sync_reaction_votes();bridge.sync_reaction_votes()
    assert len(sent)==2 and sent[1]['revision']>remote
    assert sent[1]['items'][0]['votes']=={uid:['👍']}


def test_gallery_version_requires_current_group_grant(connected,monkeypatch):
    bridge,c,guest,uid=setup(connected,monkeypatch)
    original=bridge.fetch
    monkeypatch.setattr(bridge,'fetch',lambda path,method='GET',data=None:{'version':'abc'} if path.endswith('/media-version?through=1') else original(path,method,data))
    path=f'/api/family-photos/groups/{G1}/media-version'
    assert guest.get(path+'?through=1').json()=={'version':'abc'}
    assert guest.get(path+'?through=bad').status_code==422
    assert guest.get(path+'?through=9999999999999999').status_code==422
    assert grant(c,uid,[],enabled=False).status_code==200
    assert guest.get(path+'?through=1').status_code==403
