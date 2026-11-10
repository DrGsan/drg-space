import copy,json,base64
import pytest
from app.http import WebSocketDisconnect
from tests.test_security import owner,member,client,reset,note
from tests.test_social import family,join,shared,initialize,hello,next_event
from tests.test_admin import backup,headers,proof,uid
from app import main as m
from app.modules.notion import DEFAULT_PROPERTIES,DEFAULT_VIEWS


def database(c,scope=None,parent=None):
    r=c.post('/api/databases',json={'title':'Проекты','space_id':scope,'parent_id':parent});assert r.status_code==200,r.text;return r.json()['id']
def record(c,nid,title='Проект',properties=None):
    r=c.post('/api/databases/'+nid+'/records',json={'title':title,'properties':properties or {}});assert r.status_code==200,r.text;return r.json()['id']
def permissions(c,nid,g,role):
    r=c.put('/api/notes/'+nid+'/permissions',json={'user_id':g.get('/api/me').json()['id'],'role':role});assert r.status_code==200,r.text

def test_database_records_are_nested_searchable_block_pages_and_share_views():
    c,_,_=owner();nid=database(c);rid=record(c,nid,'Книга для отпуска',{'status':'В работе','date':{'start':'2026-10-11','end':'2026-10-15'},'tags':['Личное']})
    d=c.get('/api/databases/'+nid).json();assert {v['type'] for v in d['views']}=={'table','board','calendar','gallery','timeline'}
    page=c.get('/api/notes/'+rid).json();assert page['parent_id']==nid and page['database_id']==nid
    assert c.put('/api/notes/'+rid,json={'title':page['title'],'body':'Внутри записи есть текст','version':page['version']}).status_code==200
    assert c.get('/api/notes/search?q=внутри').json()['items'][0]['id']==rid
    assert c.patch('/api/database-records/'+rid,json={'properties':{'status':'Готово'},'version':1}).status_code==200
    assert c.get('/api/databases/'+nid).json()['records'][0]['properties']['status']=='Готово'
    assert c.patch('/api/database-records/'+rid,json={'properties':{},'version':1}).status_code==409
    assert c.patch('/api/notes/'+rid,json={'parent_id':None}).status_code==422
    assert c.post('/api/notes/'+nid+'/shares',json={'mode':'public'}).status_code==200
    assert c.get('/api/notes/'+nid).json()['kind']=='database'

@pytest.mark.parametrize('properties',[{'unknown':'x'},{'status':'Неизвестно'},{'tags':['Работа','Работа']},{'date':{'start':'2026-10-20','end':'2026-10-10'}},{'date':{'start':'not-a-date'}}])
def test_typed_record_values_reject_invalid_input(properties):
    c,_,_=owner();nid=database(c)
    assert c.post('/api/databases/'+nid+'/records',json={'title':'Bad','properties':properties}).status_code==422
    assert c.get('/api/databases/'+nid).json()['records']==[]

def test_custom_properties_filters_and_views_validate_and_are_atomic():
    c,_,_=owner();nid=database(c);rid=record(c,nid,properties={'status':'В работе'});d=c.get('/api/databases/'+nid).json()
    props=d['properties']+[{'id':'cost','name':'Бюджет','type':'number'},{'id':'done','name':'Сделано','type':'checkbox'},{'id':'site','name':'Сайт','type':'url'}]
    views=copy.deepcopy(d['views']);views[0]['filter']={'property':'status','operator':'equals','value':'В работе'};views[0]['sort']={'property':'cost','direction':'desc'}
    assert c.put('/api/databases/'+nid,json={'properties':props,'views':views,'version':1}).status_code==200
    assert c.put('/api/databases/'+nid,json={'properties':props,'views':views,'version':1}).status_code==409
    bad=copy.deepcopy(props);bad[0]['options']=['Готово']
    assert c.put('/api/databases/'+nid,json={'properties':bad,'views':views,'version':2}).status_code==422
    assert c.get('/api/databases/'+nid).json()['version']==2
    for values in [{'cost':True},{'done':'yes'},{'site':'javascript:alert(1)'}]:assert c.patch('/api/database-records/'+rid,json={'properties':values,'version':1}).status_code==422
    assert c.patch('/api/database-records/'+rid,json={'properties':{'cost':120.5,'done':True,'site':'https://example.com'},'version':1}).status_code==200


def test_database_copy_trash_restore_preserves_properties_and_records():
    c,_,_=owner();nid=database(c);rid=record(c,nid,properties={'status':'Готово'})
    copied=c.post('/api/notes/'+nid+'/duplicate').json()['id'];d=c.get('/api/databases/'+copied).json();assert d['records'][0]['note_id']!=rid and d['records'][0]['properties']=={'status':'Готово'}
    assert c.delete('/api/notes/'+nid).status_code==200
    assert c.get('/api/databases/'+nid).status_code==404 and c.get('/api/notes/'+rid).status_code==404
    assert c.post('/api/notes/'+nid+'/restore').status_code==200
    assert c.get('/api/databases/'+nid).json()['records'][0]['note_id']==rid
    c.delete('/api/notes/'+nid);assert c.delete('/api/notes/'+nid+'/permanent').status_code==200
    with m.db() as db:assert not db.execute('SELECT 1 FROM database_records WHERE database_id=?',(nid,)).fetchone()


def test_page_roles_apply_to_rest_websocket_and_inherited_database_mutations():
    c,_,_=owner();g,_=member(c);o,_=member(c,'outsider');sid=family(c);join(c,g,sid);nid=database(c,sid);rid=record(c,nid);root=shared(c,sid);child=shared(c,sid,root['id']);initialize(c,child['id'])
    permissions(c,root['id'],g,'viewer');permissions(c,nid,g,'commenter')
    assert g.get('/api/notes/'+child['id']).json()['role']=='viewer'
    for path,method,data in [(f'/notes/{child["id"]}','PATCH',{'icon':'x'}),(f'/notes/{root["id"]}/duplicate','POST',{}),(f'/notes/{root["id"]}','DELETE',None),(f'/notes','POST',{'title':'Bypass','parent_id':root['id']}),(f'/databases/{nid}/records','POST',{'title':'Bypass'}),(f'/database-records/{rid}','PATCH',{'properties':{},'version':1})]:
        assert getattr(g,method.lower())('/api'+path,**({'json':data} if data is not None else {})).status_code==403,path
    with pytest.raises(WebSocketDisconnect):
        with g.websocket_connect('/api/live/notes/'+child['id']) as ws:hello(ws,g);ws.receive_json()
    assert g.post('/api/notes/'+child['id']+'/comments',json={'body':'Нет прав'}).status_code==403
    assert g.post('/api/notes/'+nid+'/comments',json={'body':'Есть комментарий'}).status_code==200
    assert o.get('/api/databases/'+nid).status_code==404
    assert o.get('/api/notes/'+root['id']+'/comments').status_code==404
    assert g.put('/api/notes/'+root['id']+'/permissions',json={'user_id':c.get('/api/me').json()['id'],'role':'viewer'}).status_code==403
    permissions(c,root['id'],g,None);assert g.get('/api/notes/'+child['id']).json()['role']=='editor'


def test_comments_threads_block_context_mentions_and_revocation():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);n=shared(c,sid);initialize(c,n['id'])
    assert c.post('/api/notes/'+n['id']+'/comments',json={'body':'@friend Посмотри этот абзац','block_id':'paragraph-one'}).status_code==200
    root=c.get('/api/notes/'+n['id']+'/comments').json()[0];assert root['mentions']==[g.get('/api/me').json()['id']]
    assert g.get('/api/note-mentions').json()[0]['note_id']==n['id']
    assert g.post('/api/notes/'+n['id']+'/comments',json={'body':'Готово','parent_id':root['id']}).status_code==200
    reply=c.get('/api/notes/'+n['id']+'/comments').json()[1];assert reply['block_id']=='paragraph-one'
    assert g.post('/api/notes/'+n['id']+'/comments',json={'body':'Вложенный ответ','parent_id':reply['id']}).status_code==422
    assert g.patch('/api/notes/'+n['id']+'/comments/'+root['id'],json={'resolved':True}).status_code==403
    assert c.patch('/api/notes/'+n['id']+'/comments/'+root['id'],json={'resolved':True}).status_code==200
    assert g.post('/api/notes/'+n['id']+'/comments',json={'body':'Поздний ответ','parent_id':root['id']}).status_code==409
    assert c.post('/api/notes/'+n['id']+'/comments',json={'body':'Потерянный блок','block_id':'missing'}).status_code==422
    assert c.delete(f'/api/social/families/{sid}/members/{g.get("/api/me").json()["id"]}').status_code==200
    assert g.get('/api/note-mentions').json()==[] and g.get('/api/notes/'+n['id']+'/comments').status_code==404


def test_database_permissions_comments_are_portable_and_user_deletion_cascades():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);nid=database(c,sid);rid=record(c,nid,properties={'status':'В работе'});permissions(c,nid,g,'commenter');g.post('/api/notes/'+rid+'/comments',json={'body':'@owner Проверь'})
    saved=backup(c);c.delete('/api/notes/'+nid)
    assert c.post('/api/admin/backups/restore',content=saved,headers=headers()).status_code==200
    with m.db() as db:
        assert json.loads(db.execute('SELECT properties FROM database_records WHERE note_id=?',(rid,)).fetchone()[0])=={'status':'В работе'}
        assert db.execute('SELECT COUNT(*) FROM note_permissions').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM note_comments').fetchone()[0]==1
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None

@pytest.mark.parametrize('bad',[{'type':[]},{'group_by':{}},{'visible':[{}]},{'filter':{'property':[],'operator':'contains','value':''}},{'sort':{'property':{},'direction':'asc'}}])
def test_malformed_view_shapes_return_validation_errors(bad):
    c,_,_=owner();nid=database(c);d=c.get('/api/databases/'+nid).json();views=copy.deepcopy(d['views']);views[0].update(bad)
    assert c.put('/api/databases/'+nid,json={'properties':d['properties'],'views':views,'version':1}).status_code==422


def test_record_restore_requires_its_database_instead_of_orphaning_properties():
    c,_,_=owner();nid=database(c);rid=record(c,nid,properties={'status':'В работе'});c.delete('/api/notes/'+rid);c.delete('/api/notes/'+nid)
    assert c.post('/api/notes/'+rid+'/restore').status_code==409
    assert c.post('/api/notes/'+nid+'/restore').status_code==200
    assert c.post('/api/notes/'+rid+'/restore').status_code==200
    assert c.get('/api/databases/'+nid).json()['records'][0]['properties']=={'status':'В работе'}


def test_shared_database_titles_use_versions_instead_of_overwriting_concurrent_edits():
    c,_,_=owner();nid=database(c);p=c.get('/api/notes/'+nid).json()
    r=c.patch('/api/notes/'+nid+'/database-title',json={'title':'Новый заголовок','version':p['version']});assert r.status_code==200,r.text
    assert c.patch('/api/notes/'+nid+'/database-title',json={'title':'Потерянная правка','version':p['version']}).status_code==409
    assert c.get('/api/notes/'+nid).json()['title']=='Новый заголовок'


def test_permission_revocation_rejects_an_update_from_an_already_open_socket():
    from tests.test_social import load_doc,content
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);n=shared(c,sid);initialize(c,n['id'])
    with g.websocket_connect('/api/live/notes/'+n['id']) as ws:
        hello(ws,g,99);event=next_event(ws,'sync');doc=load_doc(base64.b64decode(event['state']));content(doc).insert(0,'Запрещённая правка ')
        permissions(c,n['id'],g,'viewer')
        ws.send_json({'type':'update','seq':1,'epoch':event['epoch'],'update':base64.b64encode(doc.get_update()).decode()})
        with pytest.raises(WebSocketDisconnect):next_event(ws,'ack')
    assert 'Запрещённая' not in c.get('/api/notes/'+n['id']).json()['body']


def test_database_schema_cannot_erase_values_of_a_record_with_readonly_permission():
    c,_,_=owner();g,_=member(c);sid=family(c);join(c,g,sid);nid=database(c,sid);rid=record(c,nid,properties={'tags':['Работа']});permissions(c,rid,g,'viewer')
    d=g.get('/api/databases/'+nid).json();props=[p for p in d['properties'] if p['id']!='tags'];views=[{**v,'visible':[]} for v in d['views']]
    assert g.put('/api/databases/'+nid,json={'properties':props,'views':views,'version':d['version']}).status_code==403
    assert c.get('/api/databases/'+nid).json()['records'][0]['properties']=={'tags':['Работа']}


def test_list_archive_views_validate_persist_and_restore_in_backup():
    c,_,_=owner();nid=database(c);record(c,nid,properties={'tags':['Работа']})
    d=c.get('/api/databases/'+nid).json()
    views=[{'id':'notes','name':'Заметки','type':'list','filter':{'property':'tags','operator':'not_contains','value':'Работа'},'visible':['tags']},{'id':'archive','name':'Архив','type':'list','filter':{'property':'tags','operator':'contains','value':'Работа'}}]
    result=c.put('/api/databases/'+nid,json={'properties':d['properties'],'views':views,'version':d['version']})
    assert result.status_code==200,result.text
    saved=backup(c)
    assert c.post('/api/admin/backups/restore',content=saved,headers=headers()).status_code==200
    with m.db() as conn:assert json.loads(conn.execute('SELECT views FROM note_databases WHERE note_id=?',(nid,)).fetchone()[0])==views


def test_database_publication_scope_preview_revocation_and_readonly():
    import io
    from PIL import Image
    c,_,_=owner();nid=database(c);rid=record(c,nid,'Public guitar');other=note(c)
    secret=record(c,nid,'Locked secret')
    with m.db() as conn:conn.execute('UPDATE notes SET locked=1 WHERE id=?',(secret,))
    stream=io.BytesIO();Image.new('RGB',(32,24),'#776655').save(stream,format='PNG')
    upload=c.post('/api/notes/'+rid+'/files?name=guitar.png',content=stream.getvalue(),headers={'Content-Type':'image/png'})
    assert upload.status_code==200,upload.text
    url=m.PUBLIC_URL+upload.json()['url'];fid=url.rsplit('/',1)[-1]
    page=c.get('/api/notes/'+rid).json()
    assert c.put('/api/notes/'+rid,json={'title':page['title'],'body':'','blocks':[{'id':'guitar','type':'image','props':{'url':url},'children':[]}],'version':page['version']}).status_code==200
    d=c.get('/api/databases/'+nid).json();assert next(r for r in d['records'] if r['note_id']==rid)['preview_url']==upload.json()['url']
    share=c.post('/api/notes/'+nid+'/shares',json={'mode':'public'}).json();token=share['token']
    guest=client()
    public=guest.get('/api/shared/'+token);assert public.status_code==200,public.text
    payload=public.json();assert payload['database']['role']=='viewer'
    assert [r['note_id'] for r in payload['database']['records']]==[rid]
    preview=payload['database']['records'][0]['preview_url'];assert guest.get(preview).content==stream.getvalue()
    assert guest.get('/api/shared/'+token+'?record='+rid).status_code==200
    for blocked in (secret,other):assert guest.get('/api/shared/'+token+'?record='+blocked).status_code==404
    assert guest.get('/api/shared/'+token+'/files/'+fid).status_code==404
    assert guest.get('/api/shared/'+token+'/files/'+fid+'?record='+other).status_code==404
    assert c.post('/api/notes/'+nid+'/shares',json={'mode':'public','permission':'edit'}).status_code==422
    assert c.patch('/api/shares/'+token,json={'permission':'edit'}).status_code==422
    assert c.delete('/api/shares/'+token).status_code==200
    assert guest.get(preview).status_code==404
    assert guest.get('/api/shared/'+token+'?record='+rid).status_code==404


@pytest.mark.parametrize('mode',['password','authenticated','users'])
def test_database_publication_preserves_access_modes_for_records(mode):
    c,_,_=owner();g,_=member(c);outsider,_=member(c,'outsider');nid=database(c);rid=record(c,nid)
    data={'mode':mode}
    if mode=='password':data['password']='publication-password'
    if mode=='users':data['allowed']=['friend']
    response=c.post('/api/notes/'+nid+'/shares',json=data);assert response.status_code==200,response.text
    token=response.json()['token'];url='/api/shared/'+token+'?record='+rid;anonymous=client()
    assert anonymous.get(url).status_code==(423 if mode=='password' else 401)
    if mode=='password':
        assert anonymous.post('/api/shared/'+token+'/unlock',json={'password':'publication-password'}).status_code==200
        assert anonymous.get(url).status_code==200
    else:
        assert g.get(url).status_code==200
        assert outsider.get(url).status_code==(403 if mode=='users' else 200)
    with m.db() as conn:conn.execute('UPDATE shares SET expires=? WHERE token=?',(m.now()-1,token))
    assert g.get(url).status_code==404
