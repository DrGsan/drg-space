import copy
import json
from tests.test_security import owner, member, client, note, share, reset
from app import main as m
from app.modules.preferences import MODULES, SECTION_MODULES, module_enabled
from app.modules.widget_models import dashboard


def switches(c, **enabled):
    r = c.put('/api/modules', json={'enabled': enabled})
    assert r.status_code == 200, r.text
    return r.json()


def test_switches_require_authentication_and_validate_the_catalog():
    assert client().get('/api/modules').status_code == 401
    c, _, _ = owner()
    initial = c.get('/api/modules').json()
    assert list(initial['enabled']) == ['tracker','clock','files','tasks','favorites','calendar','weather','rss','chat','bookmarks','notes','media','authenticator','expenses']
    assert [entry['id'] for entry in initial['catalog']] == list(initial['enabled'])
    assert {entry['id'] for entry in initial['catalog'] if entry['kind']=='module'} == {'tracker','files','tasks','chat','notes','expenses'}
    assert next(entry for entry in initial['catalog'] if entry['id']=='favorites')['requires']=='notes'
    assert initial['enabled']['tracker'] is False
    assert all(value for key,value in initial['enabled'].items() if key!='tracker')
    for payload in [{'enabled': {'unknown': False}}, {'enabled': {'notes': 'false'}},
                    {'enabled': {'notes': False}, 'unexpected': True}]:
        assert c.put('/api/modules', json=payload).status_code == 422
    del c.headers['x-csrf-token']
    assert c.put('/api/modules', json={'enabled': {'notes': False}}).status_code == 403


def test_disabling_preserves_data_layout_and_publications_and_is_account_specific():
    c, _, _ = owner()
    guest, _ = member(c)
    nid = note(c)
    token = share(c, nid, 'public')
    task = c.post('/api/tasks', json={'title': 'Keep task', 'day': '2026-10-11'}).json()['id']
    layout = c.get('/api/dashboard/layout').json()
    layout['instances'].append({'id':'weather','module_id':'weather','config':{},'hidden':False,'size':'compact'})
    layout['instances'].reverse()
    weather = next(w for w in layout['instances'] if w['id'] == 'weather')
    weather.update(hidden=True, config={'city': 'Казань', 'latitude': 55.79, 'longitude': 49.12})
    layout = c.put('/api/dashboard/layout', json=layout).json()
    switches(c, notes=False, tasks=False, calendar=False, weather=False)
    assert c.get('/api/me').json()['settings']['dashboard']['instances'][0]['config']['city'] == 'Казань'
    assert c.get('/api/dashboard/layout').json() == layout
    assert guest.get('/api/modules').json()['enabled']['notes']
    assert guest.get('/api/notes').status_code == 200
    assert client().get('/api/shared/' + token).status_code == 200
    # Disable applies to reads and writes, even from a tab opened earlier.
    requests = [
        ('get', '/api/notes', None), ('get', '/api/notes/' + nid, None),
        ('post', '/api/notes', {'title': 'Blocked'}),
        ('put', '/api/notes/' + nid, {'title': 'Blocked'}),
        ('delete', '/api/notes/' + nid, None),
        ('get', '/api/notes/' + nid + '/shares', None),
        ('post', '/api/notes/' + nid + '/shares', {'mode': 'public'}),
        ('get', '/api/tasks', None),
        ('patch', '/api/tasks/' + task, {'done': True}),
        ('delete', '/api/tasks/' + task, None),
    ]
    for method, url, body in requests:
        kwargs = {'json': body} if body is not None else {}
        assert getattr(c, method)(url, **kwargs).status_code == 403, url
    assert c.get('/api/modules').json()['enabled']['weather']
    assert c.get('/api/modules').json()['enabled']['calendar']
    assert not c.get('/api/modules').json()['enabled']['favorites']
    enabled = switches(c, notes=True, tasks=True, calendar=True, weather=True)['enabled']
    assert all(enabled[k] for k in ('notes','tasks','calendar','weather'))
    assert c.get('/api/notes/' + nid).json()['body'] == '# Private body'
    assert c.get('/api/tasks').json()[0]['id'] == task
    assert c.get('/api/tasks').json()[0]['done'] == 0
    assert c.get('/api/dashboard/layout').json() == layout


def test_partial_updates_merge_and_appearance_does_not_reenable_modules():
    c, _, _ = owner()
    switches(c, notes=False)
    switches(c, calendar=False)
    assert c.put('/api/appearance', json={'theme': 'sage', 'mode': 'dark'}).status_code == 200
    enabled = c.get('/api/modules').json()['enabled']
    assert enabled == {key: key not in ('notes', 'favorites', 'tracker') for key in enabled}
    assert c.get('/api/me').json()['settings']['modules']=={'notes':False}


def test_revoke_is_available_even_with_notes_disabled():
    c, _, _ = owner()
    token = share(c, note(c), 'public')
    switches(c, notes=False)
    assert c.delete('/api/shares/' + token).status_code == 200
    assert client().get('/api/shared/' + token).status_code == 404


def legacy_settings(c,flags,layout=None):
    settings=c.get('/api/me').json()['settings']
    settings['modules']=flags
    if layout is not None:settings['dashboard']=layout
    uid=c.get('/api/me').json()['id']
    with m.db() as db:db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
    return settings


def test_old_clients_may_submit_the_full_catalog_but_only_section_switches_persist():
    c,_,_=owner()
    effective=switches(c,**{mid:False for mid in MODULES})['enabled']
    assert {mid for mid,enabled in effective.items() if not enabled}==SECTION_MODULES|{'favorites'}
    assert c.get('/api/me').json()['settings']['modules']=={mid:False for mid in MODULES if mid in SECTION_MODULES}
    assert module_enabled({'modules':{'favorites':True,'notes':False}},'favorites') is False
    assert module_enabled({'modules':{'favorites':False,'notes':True}},'favorites') is True
    assert not module_enabled({},'unregistered')


def test_legacy_widget_flags_adapt_only_existing_instances_without_mutating_saved_settings():
    c,_,_=owner();layout=c.get('/api/dashboard/layout').json()
    layout['instances'].reverse();layout['version']=17
    clock=next(w for w in layout['instances'] if w['module_id']=='clock')
    clock.update(title='Мои часы',config={'timezone':'Asia/Vladivostok','label':'Дом','show_date':False})
    old=legacy_settings(c,{'clock':False,'weather':False,'rss':True,'tasks':False},layout)
    old_copy=copy.deepcopy(old)
    adapted=c.get('/api/dashboard/layout').json()
    assert adapted['version']==17 and [w['id'] for w in adapted['instances']]==[w['id'] for w in layout['instances']]
    assert [w['id'] for w in adapted['instances'] if w['hidden']]==['home_weather','home_clock']
    assert next(w for w in adapted['instances'] if w['module_id']=='clock')['config']==clock['config']
    assert c.get('/api/me').json()['settings']==old_copy
    adapted['instances'][0]['title']='Mutated copy'
    assert dashboard(old)==c.get('/api/dashboard/layout').json() and old==old_copy
    assert c.get('/api/modules').json()['enabled']['clock'] and not c.get('/api/modules').json()['enabled']['tasks']


def test_saving_layout_keeps_adapted_hidden_instances_and_allows_new_ones_without_switches():
    c,_,_=owner();layout=c.get('/api/dashboard/layout').json()
    legacy_settings(c,{'clock':False,'weather':False,'notes':False},layout)
    adapted=c.get('/api/dashboard/layout').json()
    adapted['instances'].append({'id':'new_clock','module_id':'clock','config':{'label':'Новые часы'}})
    saved=c.put('/api/dashboard/layout',json=adapted);assert saved.status_code==200,saved.text
    widgets={w['id']:w for w in saved.json()['instances']}
    assert widgets['home_clock']['hidden'] and widgets['home_weather']['hidden']
    assert not widgets['new_clock']['hidden'] and widgets['new_clock']['config']['label']=='Новые часы'
    assert c.get('/api/me').json()['settings']['modules']=={'notes':False}
    assert c.get('/api/dashboard/layout').json()==saved.json()
    draft=saved.json();next(w for w in draft['instances'] if w['id']=='home_clock')['hidden']=False
    unhidden=c.put('/api/dashboard/layout',json=draft);assert unhidden.status_code==200
    assert not next(w for w in unhidden.json()['instances'] if w['id']=='home_clock')['hidden']


def test_saving_section_preferences_materializes_legacy_hidden_defaults_before_clearing_flags():
    c,_,_=owner()
    old=legacy_settings(c,{'clock':False,'favorites':False,'media':True,'notes':False})
    adapted=dashboard(old)
    adapted['version']+=1
    result=switches(c,notes=True)
    assert result['enabled']['tracker'] is False
    assert all(value for key,value in result['enabled'].items() if key!='tracker')
    settings=c.get('/api/me').json()['settings']
    assert settings['modules']=={'notes':True} and settings['dashboard']==adapted
    assert settings['dashboard']['version']==2
    assert {w['module_id'] for w in settings['dashboard']['instances'] if w['hidden']}=={'clock','favorites'}
    assert c.get('/api/dashboard/layout').json()==adapted
    switches(c,tasks=False)
    assert c.get('/api/dashboard/layout').json()==adapted


def test_materializing_legacy_visibility_rejects_stale_layout_writes_but_true_flags_do_not_bump():
    c,_,_=owner();layout=c.get('/api/dashboard/layout').json()
    legacy_settings(c,{'clock':False},layout)
    stale=c.get('/api/dashboard/layout').json()
    switches(c,tasks=False)
    current=c.get('/api/dashboard/layout').json()
    assert current['version']==stale['version']+1
    assert current['instances']==stale['instances']
    assert c.put('/api/dashboard/layout',json=stale).status_code==409
    legacy_settings(c,{'clock':True,'tasks':False},current)
    switches(c,files=False)
    assert c.get('/api/dashboard/layout').json()==current


def test_favorites_guard_names_the_parent_section_and_keeps_account_scope():
    from app.modules.preferences import create_module_services
    c,_,_=owner();g,_=member(c)
    switches(c,notes=False)
    sessions={'owner':c.get('/api/me').json()['settings'],'friend':g.get('/api/me').json()['settings']}
    def ready(name):return {'settings':json.dumps(sessions[name])}
    _,guard=create_module_services(m.db,ready,m.fail)
    from app.http import HTTPError
    import pytest
    with pytest.raises(HTTPError) as error:guard('favorites')('owner')
    assert error.value.status_code==403 and 'Заметки' in error.value.detail and 'Главные заметки' not in error.value.detail
    assert guard('favorites')('friend')['settings']==json.dumps(sessions['friend'])


def test_legacy_widget_flags_survive_portable_import_and_new_instances_are_enabled():
    from tests.test_personal_data import export,restore
    from tests.test_widgets import add
    c,_,_=owner();g,_=member(c)
    layout=c.get('/api/dashboard/layout').json()
    legacy_settings(c,{'clock':False,'favorites':False,'tasks':False},layout)
    body=export(c)
    result=restore(g,body,'Friend-password-123');assert result.status_code==200,result.text
    imported=g.get('/api/dashboard/layout').json()
    assert {w['module_id'] for w in imported['instances'] if w['hidden']}=={'clock','favorites'}
    assert g.get('/api/modules').json()['enabled']['clock'] and not g.get('/api/modules').json()['enabled']['tasks']
    saved=add(g,'clock',{'label':'Новые часы'},'new_imported_clock')
    assert next(w for w in saved['instances'] if w['id']=='new_imported_clock')['hidden'] is False
    assert {w['module_id'] for w in saved['instances'] if w['hidden']}=={'clock','favorites'}
    assert g.get('/api/me').json()['settings']['modules']=={'tasks':False}
