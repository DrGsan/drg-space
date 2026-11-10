import httpx
import io
from datetime import datetime, timezone
from PIL import Image
from tests.test_security import owner, member, client, reset
from app import main as m
from app.modules import rss


def add(c, kind, config=None, widget_id=None):
    layout=c.get('/api/dashboard/layout').json()
    layout['instances'].append({'id':widget_id or 'extra_'+kind,'module_id':kind,'config':config or {}})
    r=c.put('/api/dashboard/layout',json=layout)
    assert r.status_code==200,r.text
    return r.json()


def enable(c, *kinds):
    assert c.put('/api/modules',json={'enabled':{kind:True for kind in kinds}}).status_code==200


def image_bytes(animated=False):
    buffer=io.BytesIO();image=Image.new('RGB',(12,8),'red')
    if animated:image.save(buffer,format='GIF',save_all=True,append_images=[Image.new('RGB',(12,8),'blue')],duration=100,loop=0)
    else:image.save(buffer,format='PNG')
    return buffer.getvalue()


def test_instances_have_separate_weather_and_account_settings(monkeypatch):
    c,_,_=owner();g,_=member(c)
    enable(c,'weather');enable(g,'weather');add(c,'weather',{},'weather')
    add(c,'weather',{'city':'Москва','latitude':55.75,'longitude':37.6},'moscow')
    add(c,'weather',{'city':'Владивосток','latitude':43.11,'longitude':131.88},'vladivostok')
    coords=[]
    original_client=httpx.AsyncClient
    def provider(request):
        coords.append(float(request.url.params['lat']))
        base=m.now()//3600*3600
        iso=lambda stamp:datetime.fromtimestamp(stamp,timezone.utc).isoformat().replace('+00:00','Z')
        data={'instant':{'details':{'air_temperature':10,'wind_speed':2}},'next_1_hours':{'summary':{'symbol_code':'clearsky_day'}}}
        payload={'properties':{'meta':{'updated_at':iso(base),'units':{'air_temperature':'celsius','wind_speed':'m/s'}},'timeseries':[{'time':iso(base+hour*3600),'data':data} for hour in range(31)]}}
        return httpx.Response(200,json=payload)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original_client(transport=httpx.MockTransport(provider),**kwargs))
    assert c.get('/api/widgets/moscow/weather').json()['city']=='Москва'
    assert c.get('/api/widgets/vladivostok/weather').json()['city']=='Владивосток'
    assert coords==[55.75,43.11]
    assert g.get('/api/widgets/moscow/weather').status_code==404
    assert c.get('/api/widgets/weather/weather').json()=={'configured':False}
    assert c.put('/api/modules',json={'enabled':{'weather':False}}).status_code==200
    assert c.get('/api/widgets/moscow/weather').status_code==200


def test_layout_validation_conflicts_and_independent_preferences():
    c,_,_=owner()
    old=c.get('/api/dashboard/layout').json()
    saved=add(c,'bookmarks',{'links':[{'title':'GitHub','url':'https://github.com','icon':'🧑‍💻'}]})
    assert c.put('/api/dashboard/layout',json=old).status_code==409
    assert c.put('/api/appearance',json={'theme':'lilac','mode':'dark'}).status_code==200
    enable(c,'bookmarks')
    assert c.get('/api/dashboard/layout').json()==saved
    assert c.get('/api/me').json()['settings']['appearance']['theme']=='lilac'
    for config in [{'links':[{'title':'Bad','url':'javascript:alert(1)'}]}, {'links':[{'title':'Bad','url':'https://user:password@example.com'}]}, {'links':[{'title':'Bad','url':'https://example.com:8080'}]}, {'links':[{'title':'Bad','url':'https://example.com','color':'invalid'}]}]:
        payload=c.get('/api/dashboard/layout').json();payload['instances'][-1]['config']=config
        assert c.put('/api/dashboard/layout',json=payload).status_code==422
    payload=c.get('/api/dashboard/layout').json();payload['instances'][-1]['module_id']='unknown'
    assert c.put('/api/dashboard/layout',json=payload).status_code==422
    assert c.put('/api/dashboard/layout',json={'version':saved['version'],'instances':[]}).status_code==200
    assert c.get('/api/dashboard/layout').json()['instances']==[]


def test_six_themes_schedule_validation_and_account_isolation():
    c,_,_=owner();g,_=member(c)
    assert len(c.get('/api/appearance').json()['themes'])==6
    for theme in c.get('/api/appearance').json()['themes']:
        assert c.put('/api/appearance',json={'theme':theme,'mode':'schedule','day_start':'08:30','night_start':'20:45'}).status_code==200
    for payload in [{'theme':'unknown'}, {'mode':'invalid'}, {'day_start':'25:00'}, {'day_start':'22:00','night_start':'22:00'}]:
        assert c.put('/api/appearance',json=payload).status_code==422
    assert g.get('/api/appearance').json()['preferences']['theme']=='ocean'
    assert client().get('/api/appearance').status_code==401


def test_media_private_upload_animation_validation_and_references():
    c,_,_=owner();g,_=member(c)
    assert c.put('/api/modules',json={'enabled':{'media':False}}).status_code==200
    assert c.get('/api/modules').json()['enabled']['media']
    enable(c,'media');enable(g,'media')
    uploads=[]
    for animated in (False,True):
        r=c.post('/api/media',content=image_bytes(animated));assert r.status_code==200,r.text
        asset=r.json();uploads.append(asset)
        assert asset['frames']==(2 if animated else 1)
        assert 'user_id' not in asset
        assert c.get(asset['url']).content==image_bytes(animated)
        assert client().get(asset['url']).status_code==401
        assert g.get(asset['url']).status_code==404
    assert g.get('/api/media').json()==[]
    for content in (b'<svg onload="alert(1)"></svg>',b'not an image',b'GIF89a'):
        assert c.post('/api/media',content=content).status_code==422
    assert c.post('/api/media',content=b'x',headers={'content-length':str(51*1024*1024)}).status_code==413
    asset=uploads[0]
    config={'items':[{'id':'picture','asset_id':asset['id']}],'selected':['picture'],'mode':'slideshow','interval':2}
    add(c,'media',config)
    assert c.delete(asset['url']).status_code==409
    payload=g.get('/api/dashboard/layout').json();payload['instances'].append({'id':'foreign','module_id':'media','config':config})
    assert g.put('/api/dashboard/layout',json=payload).status_code==404
    payload=c.get('/api/dashboard/layout').json();payload['instances'].pop()
    assert c.put('/api/dashboard/layout',json=payload).status_code==200
    assert c.delete(asset['url']).status_code==200
    assert c.get(asset['url']).status_code==404
    csrf=c.headers.pop('x-csrf-token')
    assert c.get('/api/modules').json()['enabled']['media']
    c.headers['x-csrf-token']=csrf
    assert c.put('/api/modules',json={'enabled':{'media':False}}).status_code==200
    assert c.get(uploads[1]['url']).status_code==200


def test_media_selection_and_external_url_validation():
    c,_,_=owner();layout=c.get('/api/dashboard/layout').json()
    item={'id':'external','url':'https://example.com/picture.png'}
    for config in [{'items':[item],'selected':['missing']}, {'items':[item,item]}, {'items':[item],'selected':['external','external']}, {'items':[{'id':'external','url':'http://example.com/picture.png'}]}, {'items':[item],'interval':1}, {'items':[item],'mode':'invalid'}]:
        payload=layout|{'instances':layout['instances']+[{'id':'gallery','module_id':'media','config':config}]}
        assert c.put('/api/dashboard/layout',json=payload).status_code==422


def test_rss_per_instance_sources_cache_and_failures(monkeypatch):
    c,_,_=owner();g,_=member(c);enable(c,'rss');enable(g,'rss')
    feeds=[{'url':'https://news.example/feed','label':'Новости'},{'url':'https://bad.example/feed','label':'Ошибка'}]
    add(c,'rss',{'feeds':feeds,'limit':1},'news')
    calls=[]
    async def load(url,label):
        calls.append((url,label))
        if 'bad.' in url:raise ValueError('unavailable')
        return [{'title':'Первый','url':'https://example.com/1','source':label,'published':1},{'title':'Второй','url':'https://example.com/2','source':label,'published':2}]
    monkeypatch.setattr(rss,'load_feed',load)
    for _ in range(2):
        r=c.get('/api/widgets/news/rss');assert r.status_code==200,r.text
        assert [item['title'] for item in r.json()['items']]==['Второй']
        assert r.json()['errors'][0]['source']=='Ошибка'
    assert len(calls)==2
    assert g.get('/api/widgets/news/rss').status_code==404
    assert c.put('/api/modules',json={'enabled':{'rss':False}}).status_code==200
    assert c.get('/api/widgets/news/rss').status_code==200
    assert len(calls)==2


def test_rss_manual_refresh_bypasses_cache_and_automatic_refresh_expires(monkeypatch):
    c,_,_=owner();g,_=member(c)
    enable(c,'rss');enable(g,'rss')
    add(c,'rss',{'feeds':[{'url':'https://refresh.example/feed'}]},'refresh_news')
    clock=[m.now()];monkeypatch.setattr(m,'now',lambda:clock[0])
    calls=[]
    async def load(url,label):
        calls.append(url)
        return [{'title':str(len(calls)),'url':'https://refresh.example/story','source':'News','published':clock[0]}]
    monkeypatch.setattr(rss,'load_feed',load)
    assert c.get('/api/widgets/refresh_news/rss').json()['items'][0]['title']=='1'
    assert c.get('/api/widgets/refresh_news/rss').json()['items'][0]['title']=='1'
    assert c.get('/api/widgets/refresh_news/rss?refresh=true').json()['items'][0]['title']=='2'
    assert c.get('/api/widgets/refresh_news/rss').json()['items'][0]['title']=='2'
    clock[0]+=601
    assert c.get('/api/widgets/refresh_news/rss').json()['items'][0]['title']=='3'
    assert g.get('/api/widgets/refresh_news/rss?refresh=true').status_code==404
    assert client().get('/api/widgets/refresh_news/rss?refresh=true').status_code==401
    assert c.get('/api/widgets/refresh_news/rss?refresh=invalid').status_code==422
    assert c.put('/api/modules',json={'enabled':{'rss':False}}).status_code==200
    assert c.get('/api/widgets/refresh_news/rss?refresh=true').status_code==200
    assert len(calls)==4


def test_rss_failed_refresh_retains_previous_headlines_until_recovery(monkeypatch):
    c,_,_=owner();enable(c,'rss')
    add(c,'rss',{'feeds':[{'url':'https://retry.example/feed'}]},'retry_news')
    calls=[];unavailable=[False]
    async def load(url,label):
        calls.append(url)
        if unavailable[0]:raise ValueError('unavailable')
        return [{'title':str(len(calls)),'url':'https://retry.example/story','source':'News','published':1}]
    monkeypatch.setattr(rss,'load_feed',load)
    assert c.get('/api/widgets/retry_news/rss').json()['items'][0]['title']=='1'
    unavailable[0]=True
    failed=c.get('/api/widgets/retry_news/rss?refresh=true').json()
    assert failed['items'][0]['title']=='1';assert len(failed['errors'])==1
    cached=c.get('/api/widgets/retry_news/rss').json()
    assert cached['items']==failed['items'];assert cached['errors']==failed['errors']
    assert len(calls)==2
    unavailable[0]=False
    recovered=c.get('/api/widgets/retry_news/rss?refresh=true').json()
    assert recovered['items'][0]['title']=='3';assert recovered['errors']==[]
