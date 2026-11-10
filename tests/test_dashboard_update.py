import io
import json
from datetime import datetime,timezone
from PIL import Image
import pytest
from tests.test_security import owner,member,client,reset,note
from tests.test_widgets import enable,image_bytes
from tests.test_personal_data import export,restore,unseal,seal
from app import main as m
from app.retention import cleanup,TRASH_SECONDS

def stamp(value):return int(datetime.fromisoformat(value).timestamp())

def test_default_dashboard_matches_shared_arrangement_without_private_references():
    from app.modules.widget_models import default_dashboard, DashboardLayout, validate_layout
    from app.modules.clock import ClockConfig
    layout=default_dashboard()
    assert [(w['module_id'],w['size']) for w in layout['instances']]==[
        ('clock','1x1'),('files','1x1'),('tasks','2x2'),('favorites','2x1'),
        ('calendar','1x1'),('weather','1x1'),('rss','2x2'),('chat','1x1'),
        ('bookmarks','1x1'),('notes','2x1'),('media','2x2'),
        ('authenticator','1x1'),('expenses','1x1'),
    ]
    configs={w['module_id']:w['config'] for w in layout['instances']}
    assert configs['clock']==ClockConfig().model_dump()
    assert configs['rss']=={'feeds':[],'limit':10}
    assert configs['bookmarks']['links']==[] and configs['media']['items']==[]
    assert configs['media']['selected']==[] and configs['authenticator']['entry_ids']==[]
    assert configs['chat']['space_id'] is None
    c,_,_=owner();uid=c.get('/api/me').json()['id']
    with m.db() as db:validate_layout(DashboardLayout.model_validate(layout),db,uid,m.fail)
    # Mutating one response must not change the next account's default.
    layout['instances'][0]['config']['label']='Personal clock'
    assert default_dashboard()['instances'][0]['config']['label']==''

def test_saved_layouts_and_explicit_disabled_modules_are_preserved():
    c,_,_=owner();g,_=member(c)
    layout=c.get('/api/dashboard/layout').json()
    layout['instances']=[{'id':'custom_notes','module_id':'notes','title':'Мои страницы','size':'2x1','config':{}}]
    saved=c.put('/api/dashboard/layout',json=layout).json()
    assert c.put('/api/modules',json={'enabled':{'files':False,'clock':False}}).status_code==200
    assert c.get('/api/dashboard/layout').json()==saved
    assert not c.get('/api/modules').json()['enabled']['files']
    assert c.get('/api/modules').json()['enabled']['clock']
    assert g.get('/api/modules').json()['enabled']['files']
    assert len(g.get('/api/dashboard/layout').json()['instances'])==13

def test_system_appearance_default_and_explicit_preferences():
    c,_,_=owner()
    assert c.get('/api/appearance').json()['preferences']['mode']=='system'
    assert c.put('/api/appearance',json={'mode':'light','theme':'sage'}).status_code==200
    assert c.get('/api/appearance').json()['preferences']['mode']=='light'

@pytest.mark.parametrize('size',['1x1','2x1','1x2','2x2','compact','wide'])
def test_square_sizes_and_legacy_layouts(size):
    c,_,_=owner();layout=c.get('/api/dashboard/layout').json()
    layout['instances'][0]['size']=size
    assert c.put('/api/dashboard/layout',json=layout).status_code==200
    layout=c.get('/api/dashboard/layout').json();layout['instances'][0]['size']='2x3'
    assert c.put('/api/dashboard/layout',json=layout).status_code==422
    layout['instances'][0].update(module_id='rss',config={'feeds':[]})
    assert c.put('/api/dashboard/layout',json=layout).status_code==200
    layout=c.get('/api/dashboard/layout').json();layout['instances'][0].update(module_id='favorites',size='2x1',config={'columns':'2'})
    assert c.put('/api/dashboard/layout',json=layout).status_code==200

def test_completed_tasks_expire_after_24_hours_and_not_midnight_or_task_day(monkeypatch):
    clock=stamp('2026-10-12T13:59:00+00:00');monkeypatch.setattr(m,'now',lambda:clock)
    c,_,_=owner();g,_=member(c)
    c.headers['x-time-zone']='Asia/Vladivostok'
    done=c.post('/api/tasks',json={'title':'Выполнено вчерашнее','day':'2026-10-01'}).json()['id']
    undone=c.post('/api/tasks',json={'title':'Не выполнено','day':'2026-10-01'}).json()['id']
    other=g.post('/api/tasks',json={'title':'Чужое','day':'2026-10-12'}).json()['id']
    assert c.patch('/api/tasks/'+done,json={'done':True}).status_code==200
    assert {t['id'] for t in c.get('/api/tasks').json()}=={done,undone}
    clock+=61
    cleanup(m.db,clock)
    assert {t['id'] for t in c.get('/api/tasks').json()}=={done,undone}
    clock+=86400-61
    from tests.test_task_lists import renew
    renew(c);renew(g)
    cleanup(m.db,clock)
    assert [t['id'] for t in c.get('/api/tasks').json()]==[undone]
    assert [t['id'] for t in g.get('/api/tasks').json()]==[other]
    uid=c.get('/api/me').json()['id']
    with m.db() as d:
        assert not d.execute('SELECT 1 FROM tasks WHERE id=?',(done,)).fetchone()
        settings=json.loads(d.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
        assert not settings['_task_completed_at']

def test_unchecking_task_preserves_it_and_completed_export_retains_stamp(monkeypatch):
    c,_,_=owner();clock=m.now();monkeypatch.setattr(m,'now',lambda:clock)
    tid=c.post('/api/tasks',json={'title':'Сохранить','day':'2026-10-01'}).json()['id']
    c.patch('/api/tasks/'+tid,json={'done':True})
    manifest,_=unseal(export(c));assert manifest['data']['tasks'][0]['completed_at']==clock
    assert c.patch('/api/tasks/'+tid,json={'done':False}).status_code==200
    cleanup(m.db,clock+86400)
    assert c.get('/api/tasks').json()[0]['done']==0

def test_trash_expires_after_30_days_with_revisions_and_shares():
    c,_,_=owner();expired=note(c);keep=note(c)
    assert c.put('/api/notes/'+expired,json={'title':'Версия','body':'Версия'}).status_code==200
    c.delete('/api/notes/'+expired);c.delete('/api/notes/'+keep)
    with m.db() as d:d.execute('UPDATE notes SET deleted=? WHERE id=?',(m.now()-TRASH_SECONDS,expired))
    assert [n['id'] for n in c.get('/api/notes?trash=true').json()]==[keep]
    assert c.post('/api/notes/'+expired+'/restore').status_code==404
    with m.db() as d:assert not d.execute('SELECT 1 FROM note_revisions WHERE note_id=?',(expired,)).fetchone()

def test_trash_does_not_cascade_into_recently_deleted_grandchild():
    c,_,_=owner();parent=note(c)
    child=c.post('/api/notes',json={'title':'Ребёнок','parent_id':parent}).json()['id']
    grandchild=c.post('/api/notes',json={'title':'Внук','parent_id':child}).json()['id']
    c.delete('/api/notes/'+parent)
    with m.db() as d:
        d.execute('UPDATE notes SET deleted=? WHERE id IN (?,?)',(m.now()-TRASH_SECONDS,parent,child))
    rows=c.get('/api/notes?trash=true').json()
    assert [n['id'] for n in rows]==[grandchild]
    assert rows[0]['parent_id'] is None

def test_avatar_is_private_encrypted_independent_of_gallery_and_portable():
    c,_,_=owner();g,_=member(c)
    assert c.put('/api/modules',json={'enabled':{'media':False}}).status_code==200
    assert client().post('/api/account/avatar',content=image_bytes()).status_code==401
    assert c.post('/api/account/avatar',content=b'<svg/>').status_code==422
    assert c.post('/api/account/avatar',content=b'x',headers={'content-length':str(11*1024*1024)}).status_code==413
    uploaded=c.post('/api/account/avatar',content=image_bytes(True));assert uploaded.status_code==200,uploaded.text
    aid=uploaded.json()['avatar_id']
    avatar=c.get('/api/account/avatar');assert avatar.status_code==200
    with Image.open(io.BytesIO(avatar.content)) as image:assert image.size==(256,256) and image.format=='WEBP' and image.n_frames==1
    # v is a cache marker; another account receives its own preset avatar.
    other_avatar=g.get('/api/account/avatar?v='+aid)
    assert other_avatar.status_code==200
    assert other_avatar.content==g.get('/api/account/avatar').content
    assert other_avatar.content!=avatar.content
    assert c.get('/api/media').status_code==200
    assert (m.DATA/'media'/aid).read_bytes()!=avatar.content if m.data_cipher.enabled else True
    body=export(c);manifest,files=unseal(body)
    assert manifest['data']['settings']['profile']['avatar_id']==aid
    assert len(manifest['data']['media'])==1
    result=restore(g,body,'Friend-password-123');assert result.status_code==200,result.text
    new=g.get('/api/me').json()['settings']['profile']['avatar_id'];assert new!=aid
    assert g.get('/api/account/avatar').content==avatar.content
    manifest['data']['settings']['profile']['avatar_id']='f'*32
    assert restore(g,seal(manifest,files),'Friend-password-123').status_code==422
    enable(c,'media');assert c.delete('/api/media/'+aid).status_code==409
    assert c.delete('/api/account/avatar').status_code==200
    assert c.get('/api/account/avatar').status_code==404
    assert g.get('/api/account/avatar').status_code==200

def test_large_gallery_image_above_old_limit_accepted():
    c,_,_=owner();enable(c,'media')
    # Incompressible pixels yield a valid ~12 MiB PNG without exceeding pixel limits.
    import random
    image=Image.frombytes('RGB',(2048,2048),random.Random(1).randbytes(2048*2048*3))
    stream=io.BytesIO();image.save(stream,format='PNG');body=stream.getvalue()
    assert 10*1024*1024<len(body)<50*1024*1024
    result=c.post('/api/media',content=body);assert result.status_code==200,result.text
    assert c.get(result.json()['url']).content==body


def test_full_backup_preserves_avatar_without_schema_change():
    from tests.test_admin import backup,headers
    import pyotp
    c,secret,_=owner()
    uploaded=c.post('/api/account/avatar',content=image_bytes()).json()
    original=c.get('/api/account/avatar').content
    body=backup(c)
    c.delete('/api/account/avatar')
    result=c.post('/api/admin/backups/restore',content=body,headers=headers())
    assert result.status_code==200,result.text
    login=c.post('/api/login',json={'login':'owner','password':'Owner-password-123'})
    c.headers['x-csrf-token']=login.json()['csrf']
    verified=c.post('/api/2fa/verify',json={'code':pyotp.TOTP(secret).at(m.now())})
    assert verified.status_code==200,verified.text
    c.headers['x-csrf-token']=verified.json()['csrf']
    assert c.get('/api/me').json()['settings']['profile']==uploaded
    assert c.get('/api/account/avatar').content==original
