"""Portable task lists preserve content, never foreign identity or access."""
import copy
import json
import pytest
from tests.test_personal_data import export,unseal,seal,restore,rows
from tests.test_security import owner,member,reset
from tests.test_widgets import add
from tests.test_task_lists import share
from app import main as m


def post(c,path,data):
    response=c.post('/api'+path,json=data)
    assert response.status_code==200,response.text
    return response.json()


def friends(c,g):
    request=post(c,'/social/friends',{'login':'friend'})
    assert g.post('/api/social/requests/'+request['id']+'/accept').status_code==200


def test_pending_deletion_list_contents_remain_in_personal_archive():
    c,_,_=owner()
    listing=post(c,'/task-lists',{'name':'Восстановимый список'})
    task=post(c,'/tasks',{'title':'Сохранить в архиве','list_id':listing['id'],'done':True})
    assert c.delete('/api/task-lists/'+listing['id']).status_code==200
    manifest,_=unseal(export(c));data=manifest['data']
    restored_list=next(x for x in data['task_lists'] if x['name']=='Восстановимый список')
    archived=next(x for x in data['tasks'] if x['title']==task['title'])
    assert archived['list_id']==restored_list['id'] and archived['done']


def test_task_list_snapshots_preserve_hierarchy_metadata_retention_and_map_widget_sources():
    c,_,_=owner();g,_=member(c);friends(c,g)
    default=c.get('/api/task-lists').json()[0]
    assert c.patch('/api/task-lists/'+default['id'],json={'retain_completed':True}).status_code==200
    private=post(c,'/task-lists',{'name':'Мой проект','icon':'briefcase','color':'sage','retain_completed':True})
    share(c,g,private['id'],g.get('/api/me').json()['id'])
    parent=post(c,'/tasks',{'title':'Корневое дело','list_id':private['id'],'tags':['работа'],'flagged':True,'priority':3,'notes':'Описание','url':'https://example.com/docs'})
    child=post(c,'/tasks',{'title':'Поддело с датой','list_id':private['id'],'parent_id':parent['id'],'day':'2026-12-31','time':'14:30','tags':['важно']})
    post(c,'/tasks',{'title':'Основное без даты'})
    shared=post(g,'/task-lists',{'name':'Общий проект','retain_completed':True,'color':'rose'})
    share(g,c,shared['id'],c.get('/api/me').json()['id'])
    shared_task=post(c,'/tasks',{'title':'Задача друга','list_id':shared['id'],'done':True,'tags':['вместе']})
    post(g,'/tasks',{'title':'Недоступная личная задача'})
    add(c,'tasks',{'view':'list','list_id':private['id']},'private_list')
    add(c,'tasks',{'view':'list','list_id':None},'default_list')
    add(c,'tasks',{'view':'today','list_id':None},'today_list')
    add(c,'tasks',{'view':'flagged','list_id':None},'flags_list')
    add(c,'tasks',{'view':'list','list_id':shared['id']},'shared_list')
    manifest,files=unseal(export(c));data=manifest['data'];raw=json.dumps(data)
    for forbidden in [c.get('/api/me').json()['id'],g.get('/api/me').json()['id'],private['id'],shared['id'],'owner_id','members','can_manage','Недоступная личная задача']:
        assert forbidden not in raw
    lists={item['name']:item for item in data['task_lists']}
    assert lists['Общий проект']['source_shared'] and not lists['Общий проект']['is_default']
    assert lists['Мой проект']['source_shared'] and not lists['Мой проект']['is_default']
    assert lists['Напоминания']['is_default']
    assert next(item for item in data['tasks'] if item['id']==parent['id'])['day']==''
    assert restore(g,seal(manifest,files),'Friend-password-123').status_code==200
    restored=g.get('/api/task-lists').json();copied={name:next(item for item in restored if item['name']==name and item['owner'] and not item['shared'] and not item['is_default']) for name in lists}
    assert all(item['retain_completed'] and not item['shared'] and item['owner'] for item in copied.values())
    tasks=g.get('/api/tasks').json()
    new_parent=next(item for item in tasks if item['title']=='Корневое дело' and item['list_id']==copied['Мой проект']['id'])
    new_child=next(item for item in tasks if item['title']=='Поддело с датой' and item['list_id']==copied['Мой проект']['id'])
    assert new_parent['id']!=parent['id'] and new_child['parent_id']==new_parent['id']
    assert new_parent['list_id']==new_child['list_id']==copied['Мой проект']['id']
    assert new_parent['tags']==['работа'] and new_parent['flagged'] and new_parent['priority']==3
    assert new_parent['notes']=='Описание' and new_parent['url']=='https://example.com/docs'
    assert new_child['day']=='2026-12-31' and new_child['time']=='14:30'
    own_copy=next(item for item in tasks if item['title']=='Задача друга' and item['list_id']==copied['Общий проект']['id'])
    assert own_copy['done'] and own_copy['completed_at'] is not None
    widgets={item['id']:item for item in g.get('/api/dashboard/layout').json()['instances']}
    assert widgets['private_list']['config']['list_id']==copied['Мой проект']['id']
    assert widgets['default_list']['config']['list_id']==copied['Напоминания']['id']
    assert widgets['shared_list']['config']['list_id']==copied['Общий проект']['id']
    assert widgets['today_list']['config']=={'view':'today','list_id':None}
    assert widgets['flags_list']['config']=={'view':'flagged','list_id':None}
    friend_uid=g.get('/api/me').json()['id']
    with m.db() as db:
        assert db.execute('SELECT user_id FROM tasks WHERE id=?',(shared_task['id'],)).fetchone()[0]==friend_uid
        assert not db.execute('SELECT 1 FROM task_list_members WHERE list_id=?',(copied['Общий проект']['id'],)).fetchone()
    # Removing friendship does not revoke imported private snapshots.
    sid=c.get('/api/social').json()['spaces'][0]['id']
    assert c.delete('/api/social/spaces/'+sid).status_code==200
    assert any(item['id']==own_copy['id'] for item in g.get('/api/tasks').json())


def test_legacy_personal_archive_imports_dated_tasks_into_current_default_without_replacing_lists(monkeypatch):
    # List payloads include live member presence; keep that clock stable while
    # checking that a legacy import preserves the existing list and membership.
    stamp=m.now();monkeypatch.setattr(m,'now',lambda:stamp)
    c,_,_=owner();g,_=member(c)
    task=post(c,'/tasks',{'title':'Старое дело','day':'2026-12-31'})
    manifest,files=unseal(export(c));data=manifest['data'];data.pop('task_lists')
    data['tasks']=[{key:item[key] for key in ('id','title','day','done','created')} for item in data['tasks']]
    for widget in data['settings'].get('dashboard',{}).get('instances',[]):
        if widget['module_id']=='tasks':widget['config']={}
    # Persist the first authenticated visit before comparing live list payloads.
    assert g.get('/api/me').status_code==200
    target=g.get('/api/task-lists').json()[0]
    result=restore(g,seal(manifest,files),'Friend-password-123');assert result.status_code==200,result.text
    copied=g.get('/api/tasks').json()[0]
    assert copied['title']=='Старое дело' and copied['day']=='2026-12-31'
    assert copied['id']!=task['id'] and copied['list_id']==target['id'] and copied['parent_id'] is None
    # A personal import may replace the viewer's name/avatar, while list
    # definitions and membership must retain their original identities.
    actual=g.get('/api/task-lists').json()
    assert [{k:v for k,v in item.items() if k!='members'} for item in actual]==[{k:v for k,v in target.items() if k!='members'}]
    assert [[(member['id'],member['login'],member['owner'],member['active']) for member in item['members']] for item in actual]==[[(member['id'],member['login'],member['owner'],member['active']) for member in target['members']]]


@pytest.mark.parametrize('attack',['foreign_list','parent_missing','parent_other_list','cycle','depth','time_without_date','bad_time','completed_undone','identity','members','widget_foreign_list','duplicate_default','missing_default','task_link_outside'])
def test_task_archive_invalid_references_and_metadata_rollback_without_live_lookup(attack):
    c,_,_=owner();g,_=member(c)
    first=post(c,'/task-lists',{'name':'Первый'});second=post(c,'/task-lists',{'name':'Второй'})
    post(c,'/tasks',{'title':'Дело','list_id':first['id']})
    foreign=post(g,'/task-lists',{'name':'Чужой'})
    manifest,files=unseal(export(c));data=manifest['data'];task=data['tasks'][0]
    if attack=='foreign_list':task['list_id']=foreign['id']
    elif attack=='parent_missing':task['parent_id']='abcdefghijklmnop'
    elif attack=='parent_other_list':
        parent=copy.deepcopy(task);parent['id']='abcdefghijklmnop';parent['list_id']=next(item['id'] for item in data['task_lists'] if item['name']=='Второй');data['tasks'].append(parent);task['parent_id']=parent['id']
    elif attack=='cycle':task['parent_id']=task['id']
    elif attack=='depth':
        previous=task
        for i in range(5):
            child=copy.deepcopy(task);child['id']='depth'+str(i).zfill(11);child['parent_id']=previous['id'];data['tasks'].append(child);previous=child
    elif attack=='time_without_date':task['time']='14:00'
    elif attack=='bad_time':task['time']='25:80';task['day']='2026-12-31'
    elif attack=='completed_undone':task['completed_at']=1
    elif attack=='identity':data['task_lists'][0]['owner_id']=g.get('/api/me').json()['id']
    elif attack=='members':data['task_lists'][0]['members']=[]
    elif attack=='widget_foreign_list':data['settings']['dashboard']={'version':1,'instances':[{'id':'foreign_task','module_id':'tasks','config':{'view':'list','list_id':foreign['id']}}]}
    elif attack=='task_link_outside':task['url']='https://drg-personal.invalid/page/'+foreign['id']
    elif attack=='duplicate_default':data['task_lists'][1]['is_default']=True
    elif attack=='missing_default':
        for item in data['task_lists']:item['is_default']=False
        data['settings']['dashboard']={'version':1,'instances':[{'id':'default_tasks','module_id':'tasks','config':{'view':'list','list_id':None}}]}
    before=rows();response=restore(c,seal(manifest,files))
    assert response.status_code==422,response.text
    assert rows()==before


def test_revoked_task_widget_is_omitted_only_from_export_and_never_changes_live_layout():
    c,_,_=owner();g,_=member(c);friends(c,g)
    shared=post(g,'/task-lists',{'name':'Отозванный список'})
    uid=c.get('/api/me').json()['id'];share(g,c,shared['id'],uid)
    add(c,'tasks',{'view':'list','list_id':shared['id']},'revoked_list')
    assert g.delete('/api/task-lists/'+shared['id']+'/members/'+uid).status_code==200
    live=c.get('/api/me').json()['settings']['dashboard']
    manifest,_=unseal(export(c))
    assert not any(item['id']=='revoked_list' for item in manifest['data']['settings']['dashboard']['instances'])
    assert c.get('/api/me').json()['settings']['dashboard']==live
    assert shared['id'] not in json.dumps(manifest)


def test_task_local_page_and_media_links_remap_while_external_urls_stay_literal():
    from tests.test_security import note
    from tests.test_widgets import image_bytes,enable
    c,_,_=owner();g,_=member(c)
    nid=note(c);enable(c,'media')
    response=c.post('/api/media',content=image_bytes());assert response.status_code==200
    aid=response.json()['id'];outside='https://external.example/page/'+nid
    nested='https://external.example/nested/page/'+nid
    protocol_relative='//external.example/page/'+nid
    post(c,'/tasks',{'title':'Ссылка на мою страницу','url':m.PUBLIC_URL+'/page/'+nid+'?mode=read#block-one',
                     'notes':m.PUBLIC_URL+'/page/'+nid+' and /api/media/'+aid+' and '+outside+' and '+nested+' and '+protocol_relative})
    post(c,'/tasks',{'title':'Внешняя ссылка','url':outside,'notes':outside})
    post(c,'/tasks',{'title':'Ссылка на изображение','url':m.PUBLIC_URL+'/api/media/'+aid})
    manifest,files=unseal(export(c));data=manifest['data']
    own=next(item for item in data['tasks'] if item['title']=='Ссылка на мою страницу')
    assert own['url']=='https://drg-personal.invalid/page/'+nid+'?mode=read#block-one'
    assert own['notes']=='/page/'+nid+' and /api/media/'+aid+' and '+outside+' and '+nested+' and '+protocol_relative
    result=restore(g,seal(manifest,files),'Friend-password-123');assert result.status_code==200,result.text
    new_page=g.get('/api/notes').json()[0]['id'];new_asset=g.get('/api/media').json()[0]['id'];tasks=g.get('/api/tasks').json()
    own=next(item for item in tasks if item['title']=='Ссылка на мою страницу')
    external=next(item for item in tasks if item['title']=='Внешняя ссылка')
    image=next(item for item in tasks if item['title']=='Ссылка на изображение')
    assert own['url']==m.PUBLIC_URL+'/page/'+new_page+'?mode=read#block-one'
    assert own['notes']=='/page/'+new_page+' and /api/media/'+new_asset+' and '+outside+' and '+nested+' and '+protocol_relative
    assert external['url']==external['notes']==outside
    assert image['url']==m.PUBLIC_URL+'/api/media/'+new_asset
