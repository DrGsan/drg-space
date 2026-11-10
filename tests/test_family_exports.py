import json
import httpx
import pytest
from tests.test_security import owner,member,reset
from tests.test_family_photos import enable,grant,G1,G2
from app import main as m


@pytest.fixture
def exports(monkeypatch):
    bridge=m.family_photos
    for key,value in {'url':'http://archive.internal:8090','token':'service-secret-'*4,'cached':None,'checked_at':0,'ever_available':False}.items():monkeypatch.setattr(bridge,key,value)
    jobs={};calls=[]
    groups=[{'id':g,'title':g,'latest_message_id':0,'kind':'supergroup','can_send':True} for g in (G1,G2)]
    def handle(request):
        path=request.url.path;actor=request.url.params.get('actor_id');calls.append((request.method,path,actor))
        if path=='/v1/status':return httpx.Response(200,json={'service':'family-photo','api_version':1,'group_management':1,'group_copies':1,'photo_exports':1,'export_notifications':1})
        if path=='/v1/groups':return httpx.Response(200,json={'items':groups})
        if path=='/v1/admin/groups':return httpx.Response(200,json={'items':groups,'purged':{},'server_time':m.now()})
        if path=='/v1/admin/exports':return httpx.Response(200,json={'items':[{**j,'actor_id':j['actor'],'group_titles':['Семейная группа']} for jid,j in jobs.items() if j['state']=='ready']})
        if path.endswith('/unread'):return httpx.Response(200,json={'unread':0})
        if path.endswith('/copy'):return httpx.Response(201,json={'id':'export:'+'a'*24})
        if path=='/v1/exports':
            if request.method=='POST':
                payload=json.loads(request.content);jobs['a'*32]=payload|{'actor':actor,'state':'ready','expires':m.now()+604800,'id':'a'*32}
                return httpx.Response(202,json=jobs['a'*32])
            return httpx.Response(200,json={'items':[j for j in jobs.values() if j['actor']==actor],'server_time':m.now()})
        if path.startswith('/v1/exports/'):
            jid=path.split('/')[3];job=jobs.get(jid)
            if not job or job['actor']!=actor:return httpx.Response(404,json={})
            if path.endswith('/cancel'):job['state']='cleaning';return httpx.Response(200,json={'ok':True})
            if path.endswith('/download'):
                if request.headers.get('range')=='bytes=1-3':return httpx.Response(206,content=b'zip',headers={'Content-Range':'bytes 1-3/10','Accept-Ranges':'bytes','Content-Disposition':'attachment; filename="photos.zip"'})
                return httpx.Response(200,content=b'zip')
            return httpx.Response(200,json=job)
        return httpx.Response(200,json={'ok':True})
    monkeypatch.setattr(bridge,'client',lambda timeout=5:httpx.Client(base_url=bridge.url+'/',transport=httpx.MockTransport(handle),headers={'Authorization':'Bearer '+bridge.token}))
    return bridge,jobs,calls,groups


def test_copies_are_owner_only_and_do_not_inherit_access(exports):
    bridge,jobs,calls,groups=exports
    c,_,_=owner();g,_=member(c);uid=g.get('/api/me').json()['id']
    grant(c,uid,[G1]);enable(c)
    body={'request_id':'b'*32};path='/api/family-photos/groups/'+G1+'/copy'
    assert g.post(path,json=body).status_code==403
    assert c.post(path,json=body).status_code==200
    with m.db() as db:assert json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])['family_photo_access']['groups']==[G1]
    assert c.post(path,json={'request_id':'../x'}).status_code==422


def test_exports_are_owner_only_even_with_group_grants_and_old_urls(exports):
    bridge,jobs,calls,groups=exports
    c,_,_=owner();g,_=member(c);uid=g.get('/api/me').json()['id']
    grant(c,uid,[G1,G2])
    body={'request_id':'b'*32,'groups':[G1,G2],'videos':False}
    for base in ('/api/family-photos/exports','/api/family-photos/admin/exports'):
        assert g.post(base,json=body).status_code==403
        assert g.get(base).status_code==403
        assert g.get(base+'/'+'a'*32+'/download').status_code==403
        assert g.post(base+'/'+'a'*32+'/cancel',json={}).status_code==403
    assert not any(path=='/v1/exports' and method=='POST' for method,path,_ in calls)
    with m.db() as db:db.execute("UPDATE users SET role='admin' WHERE id=?",(uid,))
    assert g.post('/api/family-photos/admin/exports',json=body).status_code==403


def test_owner_exports_work_without_gallery_module_and_preserve_ranges(exports):
    bridge,jobs,calls,groups=exports
    c,_,_=owner()
    body={'request_id':'b'*32,'groups':[G1,G2],'videos':False}
    assert c.post('/api/family-photos/admin/exports',json=body).status_code==200
    path='/api/family-photos/admin/exports/'+'a'*32+'/download'
    download=c.get(path,headers={'Range':'bytes=1-3'})
    assert download.status_code==206 and download.content==b'zip'
    assert download.headers['Content-Range']=='bytes 1-3/10'
    assert c.get('/api/family-photos/admin/exports').json()['items'][0]['download_url']==path
    groups.pop()
    assert c.get(path).status_code==404
    assert c.post('/api/family-photos/admin/exports/'+'a'*32+'/cancel',json={}).status_code==200


def test_locked_management_blocks_exports_through_new_and_old_urls(exports):
    c,_,_=owner()
    c.post('/api/admin/access/lock',json={})
    for base in ('/api/family-photos/exports','/api/family-photos/admin/exports'):
        assert c.get(base).status_code==423
        assert c.post(base,json={'request_id':'b'*32,'groups':[G1],'videos':False}).status_code==423
        assert c.get(base+'/'+'a'*32+'/download').status_code==423
        assert c.post(base+'/'+'a'*32+'/cancel',json={}).status_code==423


def test_copy_of_copy_is_rejected_on_both_routes(exports):
    bridge,jobs,calls,groups=exports
    c,_,_=owner()
    copied='export:'+'c'*24
    groups.append({'id':copied,'title':'Копия','copy_source':G1,'copy_created':m.now(),'kind':'export','latest_message_id':0})
    for base in ('/api/family-photos/groups/','/api/family-photos/admin/groups/'):
        assert c.post(base+copied+'/copy',json={'request_id':'d'*32}).status_code==409
    assert not any(path.endswith('/copy') for _,path,_ in calls)
    assert c.post('/api/family-photos/admin/groups/'+G1+'/copy',json={'request_id':'e'*32}).status_code==200


def test_background_retires_exports_created_by_previous_nonowner_access(exports):
    bridge,jobs,calls,groups=exports
    c,_,_=owner();g,_=member(c);uid=g.get('/api/me').json()['id']
    grant(c,uid,[G1])
    jobs['a'*32]={'id':'a'*32,'actor':uid,'groups':[G1],'state':'ready','expires':m.now()+100}
    bridge.poll_messages(m.now())
    assert jobs['a'*32]['state']=='cleaning'


def test_admin_row_exposes_ready_and_preparing_archive_state(exports):
    bridge,jobs,calls,groups=exports
    c,_,_=owner();uid=c.get('/api/me').json()['id']
    jobs['a'*32]={'id':'a'*32,'actor':uid,'groups':[G1],'state':'ready','expires':m.now()+100,'ready_at':m.now()}
    jobs['b'*32]={'id':'b'*32,'actor':uid,'groups':[G2],'state':'running','expires':None}
    data=c.get('/api/family-photos/admin').json()
    assert data['groups'][0]['export_ready']['download_url']=='/api/family-photos/admin/exports/'+'a'*32+'/download'
    assert data['groups'][1]['export_preparing']['id']=='b'*32
