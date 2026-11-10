"""Realistic multilingual provider responses, locale isolation and safe zones."""
import asyncio
import httpx
import pytest
from app.modules import weather as w
from tests.test_security import owner,reset


@pytest.fixture(autouse=True)
def external_fallback_only(monkeypatch,request):
    if 'offline' in request.node.name:return
    from app.world_cities import WorldCities
    monkeypatch.setattr(WorldCities,'search',lambda *args:[])


def test_offline_api_city_search_never_contacts_providers(monkeypatch):
    c,_,_=owner()
    def unexpected(**kwargs):raise AssertionError('Indexed city must not contact an external provider')
    monkeypatch.setattr(httpx,'AsyncClient',unexpected)
    response=c.get('/api/weather/search',params={'q':'Лимассол'})
    assert response.status_code==200,response.text
    assert response.json()[0]['timezone']=='Asia/Nicosia'
    assert response.json()[0]['country']=='Кипр'


def settlement(name='Лимасол',zone='Asia/Nicosia',lat=34.6841,lon=33.0379):
    return {'name':name,'country':'Кипр','admin1':'Лимасол','timezone':zone,'latitude':lat,'longitude':lon,'feature_code':'PPLA'}


def test_multilingual_search_returns_world_city_zone_and_isolates_locale(monkeypatch):
    c,_,_=owner();calls=[];original=httpx.AsyncClient
    def provider(request):
        calls.append(request)
        assert request.url.host=='geocoding-api.open-meteo.com'
        assert 'countryCode' not in request.url.params
        assert request.url.params['name'] in ('Лимасол','Limassol','лимасол')
        return httpx.Response(200,json={'results':[settlement('Limassol' if request.url.params['language']=='en' else 'Лимасол')]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(provider),**kw))
    ru=c.get('/api/weather/search',params={'q':'Лимасол'},headers={'X-DrG-Language':'ru'}).json()
    assert ru[0]['timezone']=='Asia/Nicosia' and ru[0]['country']=='Кипр'
    assert c.get('/api/weather/search',params={'q':'лимасол'},headers={'X-DrG-Language':'ru'}).json()==ru
    en=c.get('/api/weather/search',params={'q':'Limassol'},headers={'X-DrG-Language':'en'}).json()
    assert en[0]['name']=='Limassol' and len(calls)==2
    assert c.get('/api/weather/search',params={'q':'Лимасол'},headers={'X-DrG-Language':'en'}).status_code==200
    assert len(calls)==3


def test_local_script_fallback_resolves_zone_only_for_matching_coordinates(monkeypatch):
    c,_,_=owner();original=httpx.AsyncClient
    def provider(request):
        if request.url.host=='photon.komoot.io':
            assert request.url.params['lang']=='en'
            return httpx.Response(200,json={'features':[{'properties':{'name':'Limassol','type':'city','country':'Cyprus'},'geometry':{'type':'Point','coordinates':[33.0379,34.6841]}}]})
        if request.url.params['name']=='Λεμεσός':return httpx.Response(200,json={})
        return httpx.Response(200,json={'results':[settlement('Wrong namesake','Europe/London',51.5,-0.1),settlement()]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(provider),**kw))
    result=c.get('/api/weather/search',params={'q':'Λεμεσός'})
    assert result.status_code==200,result.text
    assert result.json()[0]['timezone']=='Asia/Nicosia'


def test_invalid_provider_coordinates_zones_and_non_settlements_are_not_accepted():
    rows=[settlement(zone='Invented/Zone'),settlement(lat=float('nan')),settlement(lon=300),settlement()|{'feature_code':'AIRP'}]
    with pytest.raises(ValueError):w.named_cities({'results':rows})
    assert w.named_cities({'results':[settlement(),settlement()]})==[{'name':'Лимасол','country':'Кипр','region':'Лимасол','latitude':34.6841,'longitude':33.0379,'timezone':'Asia/Nicosia'}]
