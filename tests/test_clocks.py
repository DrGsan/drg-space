import copy
import pytest
from app.modules.clock import ClockConfig
from tests.test_security import owner,member,reset
from tests.test_widgets import add,enable
from tests.test_admin import backup,headers
from app import main as m


def test_new_accounts_share_the_portable_default_layout_and_switches_remain_independent():
    from app.modules.widget_models import default_dashboard
    c,_,_=owner();g,_=member(c)
    for user in (c,g):
        enabled=user.get('/api/modules').json()['enabled']
        assert all(value for key,value in enabled.items() if key!='tracker')
        assert user.get('/api/dashboard/layout').json()==default_dashboard()
        clock=next(w for w in user.get('/api/dashboard/layout').json()['instances'] if w['module_id']=='clock')
        assert clock['config']['timezone']=='local'
        assert 'dashboard' not in user.get('/api/me').json()['settings']
    assert c.put('/api/modules',json={'enabled':{'clock':False}}).status_code==200
    assert g.get('/api/modules').json()['enabled']['clock']


def test_two_clock_instances_have_independent_persisted_options_and_survive_disabling():
    c,_,_=owner();g,_=member(c);enable(c,'clock')
    add(c,'clock',{'timezone':'Europe/Moscow','label':'MSK','show_seconds':False,'show_timezone':False,'theme':'minimal'},'moscow_clock')
    layout=add(c,'clock',{'mode':'digital','timezone':'Asia/Vladivostok','label':'VL','show_seconds':True,'show_date':True,'date_format':'custom','date_template':'ddd · DD.MM.YYYY'},'vladivostok_clock')
    clocks=[w for w in layout['instances'] if w['id'] in ('moscow_clock','vladivostok_clock')]
    assert clocks[0]['config']['mode']=='analog' and not clocks[0]['config']['show_seconds']
    assert not clocks[0]['config']['show_timezone'] and clocks[0]['config']['show_date']
    assert clocks[1]['config']['mode']=='digital' and clocks[1]['config']['date_template']=='ddd · DD.MM.YYYY'
    assert clocks[1]['config']['show_timezone']
    assert [w['id'] for w in g.get('/api/dashboard/layout').json()['instances'] if w['module_id']=='clock']==['home_clock']
    assert c.put('/api/modules',json={'enabled':{'clock':False}}).status_code==200
    assert c.get('/api/dashboard/layout').json()==layout
    enable(c,'clock');assert c.get('/api/dashboard/layout').json()==layout
    assert c.get('/api/me').json()['settings']['dashboard']==layout

@pytest.mark.parametrize('bad',[{'timezone':'Not/AZone'},{'timezone':'../../etc/passwd'},{'timezone':'/etc/passwd'},{'timezone':'localtime'},{'theme':'unknown'},{'mode':'video'},{'show_seconds':'false'},{'show_timezone':'false'},{'show_timezone':0},{'show_timezone':None},{'show_date':1},{'date_format':'unknown'},{'hour_format':'25h'},{'label':'x'*101},{'date_template':''},{'unexpected':True}])
def test_invalid_clock_options_are_rejected_without_changing_layout(bad):
    c,_,_=owner();old=c.get('/api/dashboard/layout').json();draft=copy.deepcopy(old);draft['instances'].append({'id':'bad_clock','module_id':'clock','config':bad})
    assert c.put('/api/dashboard/layout',json=draft).status_code==422
    assert c.get('/api/dashboard/layout').json()==old


def test_real_iana_regions_and_all_themes_validate_including_dst_and_half_hour_zones():
    for zone in ('local','UTC','Europe/Moscow','Asia/Vladivostok','America/New_York','Asia/Kolkata','Australia/Lord_Howe'):
        for theme in ('classic','minimal','station','roman','orbit','bold','segments','flip','accent'):
            assert ClockConfig(timezone=zone,theme=theme).timezone==zone


@pytest.mark.parametrize('bad',[
    {'cities':[{'timezone':'UTC'}]*5}, {'cities':[{'timezone':'Not/AZone'}]},
    {'cities':[{'timezone':'../../etc/passwd'}]}, {'cities':[{'timezone':'UTC','label':'x'*101}]},
    {'cities':[{'timezone':'UTC','unexpected':True}]}, {'cities':'UTC'}, {'cities':None},
])
def test_invalid_world_clocks_preserve_existing_layout(bad):
    c,_,_=owner();old=c.get('/api/dashboard/layout').json();draft=copy.deepcopy(old)
    draft['instances'][0]['config'].update(bad)
    assert c.put('/api/dashboard/layout',json=draft).status_code==422
    assert c.get('/api/dashboard/layout').json()==old


def test_world_clock_cities_survive_size_change_backup_and_old_single_layout():
    c,_,_=owner();enable(c,'clock')
    cities=[{'timezone':zone,'label':label} for zone,label in [('local','Home'),('Asia/Vladivostok','VL'),('America/New_York','NY'),('Australia/Lord_Howe','Island')]]
    layout=add(c,'clock',{'mode':'digital','theme':'segments','cities':cities},'world_clock')
    clock=next(w for w in layout['instances'] if w['id']=='world_clock')
    for size in ('2x2','2x1','1x2','1x1'):
        clock['size']=size
        response=c.put('/api/dashboard/layout',json=layout);assert response.status_code==200
        layout=response.json();clock=next(w for w in layout['instances'] if w['id']=='world_clock')
        assert clock['config']['cities']==cities
    body=backup(c)
    assert c.post('/api/admin/backups/restore',content=body,headers=headers()).status_code==200
    import json
    with m.db() as db:
        restored=json.loads(db.execute('SELECT settings FROM users').fetchone()[0])['dashboard']
        assert restored==layout
    assert ClockConfig(timezone='Europe/Moscow',theme='minimal').cities==[]
    assert 'cities' not in ClockConfig(timezone='Europe/Moscow',theme='minimal').model_dump()


def test_device_timezone_survives_layout_and_module_saves_without_replacing_explicit_regions():
    c,_,_=owner()
    layout=c.get('/api/dashboard/layout').json()
    clock=next(w for w in layout['instances'] if w['id']=='home_clock')
    assert clock['config']['timezone']=='local'
    # Saving unrelated preferences must keep the automatic mode.
    assert c.put('/api/modules',json={'enabled':{'weather':False}}).status_code==200
    assert next(w for w in c.get('/api/dashboard/layout').json()['instances'] if w['id']=='home_clock')['config']['timezone']=='local'
    # An old saved default, even Moscow without a custom label, stays explicit.
    clock['config']['timezone']='Europe/Moscow'
    saved=c.put('/api/dashboard/layout',json=layout)
    assert saved.status_code==200
    assert c.get('/api/dashboard/layout').json()==saved.json()
    assert next(w for w in saved.json()['instances'] if w['id']=='home_clock')['config']['timezone']=='Europe/Moscow'


@pytest.mark.parametrize('zone',['local','Asia/Vladivostok'])
@pytest.mark.parametrize('legacy',[False,True])
def test_clock_settings_are_included_in_encrypted_backup_restore(legacy,zone):
    import json
    c,_,codes=owner();enable(c,'clock');layout=add(c,'clock',{'mode':'digital','timezone':zone,'label':'VL','show_seconds':False,'show_timezone':False,'date_format':'yyyy-mm-dd'})
    if legacy:
        for widget in layout['instances']:
            if widget['module_id']=='clock':widget['config'].pop('show_timezone')
        with m.db() as db:
            user=db.execute('SELECT id,settings FROM users').fetchone()
            settings=json.loads(user['settings']);settings['dashboard']=layout
            db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),user['id']))
    body=backup(c);draft=copy.deepcopy(layout);draft['instances']=draft['instances'][:3];assert c.put('/api/dashboard/layout',json=draft).status_code==200
    assert c.post('/api/admin/backups/restore',content=body,headers=headers()).status_code==200
    with m.db() as db:
        settings=json.loads(db.execute('SELECT settings FROM users').fetchone()[0]);assert settings['dashboard']==layout and 'clock' not in settings.get('modules',{})
    login=c.post('/api/login',json={'login':'owner','password':'Owner-password-123'});assert login.status_code==200
    c.headers['x-csrf-token']=login.json()['csrf']
    verified=c.post('/api/2fa/verify',json={'code':codes[0]});assert verified.status_code==200
    c.headers['x-csrf-token']=verified.json()['csrf']
    restored=c.get('/api/dashboard/layout').json()
    assert restored==layout
    assert c.put('/api/dashboard/layout',json=restored).status_code==200
    clocks=[w for w in c.get('/api/dashboard/layout').json()['instances'] if w['module_id']=='clock']
    assert next(w for w in clocks if w['id']=='extra_clock')['config']['show_timezone'] is legacy
