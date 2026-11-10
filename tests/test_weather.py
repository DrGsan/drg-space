import asyncio
import copy
import ssl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import formatdate

import httpx
import pytest
from tests.test_security import owner, member, client, reset
from tests.test_widgets import add, enable
from app import main as m
from app.modules import weather as w


def mock_provider(monkeypatch, handler):
    from app.world_cities import WorldCities
    monkeypatch.setattr(WorldCities,'search',lambda *args:[])
    original = httpx.AsyncClient
    def factory(**kwargs):
        assert kwargs['trust_env'] is True
        assert kwargs.get('verify', True) is True
        def providers(request):
            # These existing cases exercise Photon fallback and MET forecasts.
            # The primary multilingual provider has its own regression cases.
            if request.url.host == 'geocoding-api.open-meteo.com':
                return httpx.Response(200,json={'results':[]})
            return handler(request)
        return original(transport=httpx.MockTransport(providers), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', factory)


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace('+00:00', 'Z')


def forecast(stamp=None, symbol='partlycloudy_day'):
    base = (m.now() if stamp is None else stamp) // 3600 * 3600
    series = []
    for hour in range(-1, 31):
        data = {'instant': {'details': {'air_temperature': 12 + hour / 2, 'wind_speed': 2}},
                'next_1_hours': {'summary': {'symbol_code': symbol}}}
        if hour == 0:
            data['next_6_hours'] = {'summary': {'symbol_code': symbol}, 'details': {'air_temperature_min': 7, 'air_temperature_max': 17}}
        if hour == 23:
            # This aggregate extends outside the selected 24-hour window.
            data['next_6_hours'] = {'summary': {'symbol_code': symbol}, 'details': {'air_temperature_min': -99, 'air_temperature_max': 99}}
        series.append({'time': iso(base + hour * 3600), 'data': data})
    return {'properties': {'meta': {'updated_at': iso(base - 300), 'units': {'air_temperature': 'celsius', 'wind_speed': 'm/s'}}, 'timeseries': series}}


def detailed_forecast(base):
    payload = forecast(base)
    properties = payload['properties']
    properties['meta']['units'].update({'relative_humidity': '%', 'air_pressure_at_sea_level': 'hPa',
                                      'wind_from_direction': 'degrees', 'precipitation_amount': 'mm',
                                      'air_temperature_min': 'celsius', 'air_temperature_max': 'celsius'})
    for hour in range(31, 121):
        data = {'instant': {'details': {'air_temperature': 12 + hour / 2, 'wind_speed': 2}},
                'next_1_hours': {'summary': {'symbol_code': 'cloudy'}}}
        properties['timeseries'].append({'time': iso(base + hour * 3600), 'data': data})
    for point in properties['timeseries']:
        hour = (w.timestamp(point['time']) - base) // 3600
        point['data']['instant']['details'].update({'relative_humidity': 75, 'air_pressure_at_sea_level': 1012.3,
                                                  'wind_from_direction': 360})
        point['data']['next_1_hours']['details'] = {'precipitation_amount': 0.2}
        point['data']['next_1_hours']['summary']['symbol_code'] = 'clearsky_day' if hour < 24 else 'rain' if hour < 48 else 'cloudy'
    return payload


def city(name='Москва', longitude=37.61123, latitude=55.75123, kind='city'):
    return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [longitude, latitude]},
            'properties': {'name': name, 'country': 'Россия', 'state': 'Регион', 'osm_key': 'place', 'osm_value': kind}}


def test_weather_city_selection_cache_and_stale_fallback(monkeypatch):
    c, _, _ = owner(); enable(c, 'weather')
    calls = []; unavailable = [False]
    payload = forecast(); clock = [m.now()]
    monkeypatch.setattr(m, 'now', lambda: clock[0])
    def provider(request):
        calls.append(str(request.url))
        assert request.headers['User-Agent'] == w.USER_AGENT
        if unavailable[0]:
            raise httpx.ConnectError('unavailable')
        if request.url.host == 'photon.komoot.io':
            assert request.url.params['q'] == 'Москва'
            assert request.url.params.get_list('layer') == ['city', 'locality']
            assert request.url.params['lang'] == 'en'
            return httpx.Response(200, json={'features': [city()]})
        assert request.url.host == 'api.met.no'
        assert request.url.params['lat'] == '55.7512'
        assert request.url.params['lon'] == '37.6112'
        return httpx.Response(200, json=payload)
    mock_provider(monkeypatch, provider)
    cities = c.get('/api/weather/search', params={'q': ' Москва '}).json()
    assert c.get('/api/weather/search', params={'q': 'москва'}).json() == cities
    add(c, 'weather', {key: cities[0][key] for key in ('latitude', 'longitude')} | {'city': cities[0]['name']}, 'moscow_live')
    first = c.get('/api/widgets/moscow_live/weather')
    assert first.status_code == 200, first.text
    assert first.json()['current']['temperature_2m'] >= 12
    assert first.json()['current']['apparent_temperature'] is None
    assert first.json()['current']['wind_speed_10m'] == 7.2
    assert first.json()['forecast_range'] == 'next_24h'
    assert first.json()['provider'] == 'met-no'
    assert not first.json()['stale']
    add(c, 'weather', {'city': 'Другая подпись', 'latitude': 55.75124, 'longitude': 37.61124}, 'same_coords')
    second = c.get('/api/widgets/same_coords/weather').json()
    assert second['city'] == 'Другая подпись'
    assert len(calls) == 2
    saved = c.get('/api/dashboard/layout').json()
    stored = next(item for item in saved['instances'] if item['id'] == 'same_coords')
    assert stored['config']['latitude'] == 55.75124  # Only network/cache coordinates are rounded.
    clock[0] += 601; unavailable[0] = True
    stale = c.get('/api/widgets/moscow_live/weather').json()
    assert stale['stale'] and stale['updated'] == first.json()['updated']
    assert stale['checked_at'] == first.json()['checked_at']
    assert c.get('/api/widgets/moscow_live/weather').json()['stale']
    assert len(calls) == 3  # Failed coordinates are backed off for a minute.
    clock[0] += 21600
    assert c.post('/api/refresh').status_code == 200
    expired = c.get('/api/widgets/moscow_live/weather')
    assert expired.status_code == 503
    assert c.get('/api/notes').status_code == 200


def test_weather_expiry_conditional_304_keeps_model_time_and_advances_point(monkeypatch):
    c, _, _ = owner(); enable(c, 'weather')
    add(c, 'weather', {'city': 'Срок кэша', 'latitude': 44.12003, 'longitude': 31.13002}, 'conditional_weather')
    clock = [m.now()]; monkeypatch.setattr(m, 'now', lambda: clock[0])
    payload = forecast(clock[0]); calls = []
    modified = formatdate(clock[0] - 300, usegmt=True)
    def provider(request):
        calls.append(request)
        headers = {'Expires': formatdate(clock[0] + 1800, usegmt=True), 'Last-Modified': modified}
        if len(calls) > 1:
            assert request.headers['If-Modified-Since'] == modified
            return httpx.Response(304, headers=headers)
        assert 'If-Modified-Since' not in request.headers
        return httpx.Response(200, json=payload, headers=headers)
    mock_provider(monkeypatch, provider)
    first = c.get('/api/widgets/conditional_weather/weather').json()
    clock[0] += 601
    assert c.get('/api/widgets/conditional_weather/weather').json()['updated'] == first['updated']
    assert len(calls) == 1
    clock[0] += 3000
    assert c.post('/api/refresh').status_code == 200
    second = c.get('/api/widgets/conditional_weather/weather').json()
    assert len(calls) == 2
    assert second['updated'] == first['updated'] == second['model_updated']
    assert second['checked_at'] > first['checked_at']
    assert second['forecast_time'] == first['forecast_time'] + 3600
    assert second['current']['temperature_2m'] == 12.5
    assert second['hourly'][0]['time'] == first['hourly'][0]['time'] + 3600
    assert second['forecast_periods'][0]['start'] == second['forecast_time']
    assert not second['stale']
    assert c.get('/api/widgets/conditional_weather/weather').status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize('kind', ['timeout', 'bad-json', 'missing', 'null', 'wrong-type', 'unknown-symbol', 'wrong-units', '304', 'bad-period', 'bad-period-details'])
def test_weather_provider_errors_return_503_without_breaking_notes(monkeypatch, kind):
    c, _, _ = owner(); enable(c, 'weather')
    index = ['timeout', 'bad-json', 'missing', 'null', 'wrong-type', 'unknown-symbol', 'wrong-units', '304', 'bad-period', 'bad-period-details'].index(kind)
    add(c, 'weather', {'city': 'Проверка', 'latitude': 10 + index, 'longitude': 20}, 'broken_weather')
    calls = []
    def provider(request):
        calls.append(request)
        if kind == 'timeout': raise httpx.ReadTimeout('timeout')
        if kind == 'bad-json': return httpx.Response(200, text='not json')
        if kind == '304': return httpx.Response(304)
        data = forecast()
        if kind == 'missing': data['properties'].pop('timeseries')
        if kind == 'null': data['properties']['timeseries'][1]['data']['instant']['details']['air_temperature'] = None
        if kind == 'wrong-type': data['properties']['timeseries'][1]['data']['instant']['details']['air_temperature'] = 'warm'
        if kind == 'unknown-symbol':
            for point in data['properties']['timeseries']: point['data']['next_1_hours']['summary']['symbol_code'] = 'unknown'
        if kind == 'wrong-units': data['properties']['meta']['units']['wind_speed'] = 'mph'
        if kind == 'bad-period': data['properties']['timeseries'][1]['data']['next_6_hours'] = None
        if kind == 'bad-period-details': data['properties']['timeseries'][1]['data']['next_6_hours']['details'] = None
        return httpx.Response(200, json=data)
    mock_provider(monkeypatch, provider)
    result = c.get('/api/widgets/broken_weather/weather')
    assert result.status_code == 503, result.text
    assert c.get('/api/widgets/broken_weather/weather').status_code == 503
    assert len(calls) == 1
    assert c.get('/api/notes').status_code == 200


@pytest.mark.parametrize(('symbol', 'code'), [('clearsky_day', 0), ('fair_night', 1), ('partlycloudy_polartwilight', 2), ('cloudy', 3), ('fog', 45), ('lightrain', 61), ('rain', 63), ('heavyrain', 65), ('lightsleet', 68), ('sleetshowers_day', 69), ('lightsnow', 71), ('snow', 73), ('heavysnow', 75), ('lightrainshowers_day', 80), ('rainshowers_night', 81), ('heavyrainshowers_day', 82), ('lightsnowshowers_day', 85), ('snowshowers_night', 86), ('rainandthunder', 95), ('lightssnowshowersandthunder_day', 95), ('lightssleetshowersandthunder_night', 95)])
def test_met_symbols_map_to_weather_codes(symbol, code):
    assert w.weather_code(symbol) == code


def test_met_point_selection_24_hour_range_and_validation():
    base = 1791878400
    data = forecast(base)
    result = w.validated_forecast(data, base + 1800)
    assert result['forecast_time'] == base
    assert result['daily'] == {'temperature_2m_min': [7], 'temperature_2m_max': [24]}
    before = w.validated_forecast(data, base - 5400)
    assert before['forecast_time'] == base - 3600
    fallback = copy.deepcopy(data)
    for point in fallback['properties']['timeseries']:
        point['data']['next_6_hours'] = point['data'].pop('next_1_hours')
    assert w.validated_forecast(fallback, base)['current']['weather_code'] == 2
    for value in (None, True, '12', float('nan'), float('inf')):
        broken = copy.deepcopy(data)
        broken['properties']['timeseries'][1]['data']['instant']['details']['air_temperature'] = value
        with pytest.raises(ValueError): w.validated_forecast(broken, base)
    with pytest.raises(ValueError): w.validated_forecast(data, base + 40 * 3600)
    with pytest.raises(ValueError): w.weather_code('unknown')
    with pytest.raises(ValueError): w.timestamp('2026-10-13T10:00:00')


def test_met_extended_forecast_is_bounded_and_uses_actual_periods():
    base = 1791878400
    result = w.validated_forecast(detailed_forecast(base), base + 1800)
    current = result['current']
    assert current['relative_humidity'] == 75
    assert current['pressure_hpa'] == 1012.3
    assert current['wind_direction'] == 0
    assert (current['precipitation_mm'], current['precipitation_hours']) == (0.2, 1)
    assert [point['time'] for point in result['hourly']] == [base + hour * 3600 for hour in (0, 3, 6, 9, 12, 15)]
    assert all(point['wind_speed_10m'] == 7.2 and point['precipitation_hours'] == 1 for point in result['hourly'])
    assert [(period['start'], period['end'], period['weather_code']) for period in result['forecast_periods']] == [
        (base, base + 86400, 0), (base + 86400, base + 172800, 63), (base + 172800, base + 259200, 3),
        (base + 259200, base + 345600, 3), (base + 345600, base + 432000, 3)]
    assert all(period['wind_speed_10m_max'] == 7.2 for period in result['forecast_periods'])
    # The first period exactly preserves the original next-24h extrema.
    assert result['forecast_periods'][0]['temperature_2m_min'] == result['daily']['temperature_2m_min'][0] == 7
    assert result['forecast_periods'][0]['temperature_2m_max'] == result['daily']['temperature_2m_max'][0] == 24
    assert result['forecast_periods'][1]['temperature_2m_min'] == 24
    assert result['forecast_periods'][1]['temperature_2m_max'] == 36


def test_met_forecast_period_boundaries_exclude_external_aggregates_and_partial_days():
    base = 1791878400
    payload = detailed_forecast(base)
    series = payload['properties']['timeseries']
    for hour, minimum, maximum in ((24, 5, 40), (43, -900, 900)):
        series[hour + 1]['data']['next_6_hours'] = {
            'summary': {'symbol_code': 'rain'}, 'details': {'air_temperature_min': minimum, 'air_temperature_max': maximum}}
    payload['properties']['timeseries'] = series[:66]  # Ends at +64h: a third complete period is unavailable.
    result = w.validated_forecast(payload, base)
    assert len(result['forecast_periods']) == 2
    assert (result['forecast_periods'][1]['temperature_2m_min'], result['forecast_periods'][1]['temperature_2m_max']) == (5, 40)
    # Later global model data use actual six-hour samples, including a boundary bracket.
    payload = detailed_forecast(base)
    payload['properties']['timeseries'] = [point for point in payload['properties']['timeseries']
                                          if w.timestamp(point['time']) < base + 48 * 3600 or (w.timestamp(point['time']) - base) % 21600 == 0]
    assert len(w.validated_forecast(payload, base + 3600)['forecast_periods']) == 4
    payload['properties']['timeseries'] = [point for point in payload['properties']['timeseries']
                                          if not base + 30 * 3600 <= w.timestamp(point['time']) <= base + 38 * 3600]
    assert len(w.validated_forecast(payload, base)['forecast_periods']) == 1


def test_met_five_period_horizon_omits_a_partial_fifth_and_bounds_long_models():
    base = 1791878400
    payload = detailed_forecast(base)
    series = payload['properties']['timeseries']
    payload['properties']['timeseries'] = series[:-1]  # Ends at +119h, so the fifth period is incomplete.
    periods = w.validated_forecast(payload, base)['forecast_periods']
    assert len(periods) == 4 and periods[-1]['end'] == base + 96 * 3600
    extra = copy.deepcopy(series[-1])
    for hour in range(121, 145):
        extra['time'] = iso(base + hour * 3600)
        series.append(copy.deepcopy(extra))
    payload['properties']['timeseries'] = series
    periods = w.validated_forecast(payload, base)['forecast_periods']
    assert len(periods) == 5 and periods[-1]['end'] == base + 120 * 3600


def test_met_period_wind_maxima_exclude_boundary_brackets_and_aggregates():
    base = 1791878400
    payload = detailed_forecast(base)
    series = payload['properties']['timeseries']
    for hour, speed in ((24, 90), (48, 12), (54, 90)):
        series[hour + 1]['data']['instant']['details']['wind_speed'] = speed
    series[37]['data']['next_6_hours'] = {'summary': {'symbol_code': 'cloudy'},
                                          'details': {'wind_speed': 200, 'wind_speed_of_gust': 300}}
    # The interval +25h..+49h is bracketed by samples at +24h and +54h.
    # Their wind values, and a period aggregate, cannot supply its maximum.
    payload['properties']['timeseries'] = [point for point in series
                                          if w.timestamp(point['time']) == base + 3600
                                          or (w.timestamp(point['time']) - base) % 21600 == 0]
    periods = w.validated_forecast(payload, base + 3600)['forecast_periods']
    assert periods[1]['start'] == base + 25 * 3600 and periods[1]['end'] == base + 49 * 3600
    assert periods[1]['wind_speed_10m_max'] == 43.2


@pytest.mark.parametrize('value', [None, True, 'strong', -1, float('nan'), float('inf'), 1e308])
def test_met_optional_period_wind_skips_invalid_values_without_losing_forecasts(value):
    base = 1791878400
    payload = detailed_forecast(base)
    for point in payload['properties']['timeseries']:
        stamp = w.timestamp(point['time'])
        if base + 24 * 3600 <= stamp <= base + 48 * 3600:
            point['data']['instant']['details']['wind_speed'] = value
    result = w.validated_forecast(payload, base)
    assert result['current']['wind_speed_10m'] == 7.2
    assert len(result['forecast_periods']) == 5
    assert result['forecast_periods'][1]['wind_speed_10m_max'] is None
    assert result['forecast_periods'][2]['wind_speed_10m_max'] == 7.2


def test_met_optional_period_wind_can_be_absent_from_future_model_points():
    base = 1791878400
    payload = detailed_forecast(base)
    for point in payload['properties']['timeseries']:
        if w.timestamp(point['time']) >= base + 24 * 3600:
            del point['data']['instant']['details']['wind_speed']
    result = w.validated_forecast(payload, base)
    assert len(result['forecast_periods']) == 5
    assert result['forecast_periods'][0]['wind_speed_10m_max'] == 7.2
    assert all(period['wind_speed_10m_max'] is None for period in result['forecast_periods'][1:])


@pytest.mark.parametrize(('field', 'value'), [('relative_humidity', -1), ('relative_humidity', 101),
                                            ('air_pressure_at_sea_level', 0), ('air_pressure_at_sea_level', float('nan')),
                                            ('wind_from_direction', 361), ('wind_from_direction', True)])
def test_met_optional_instant_metrics_are_safely_omitted(field, value):
    base = 1791878400
    payload = detailed_forecast(base)
    payload['properties']['timeseries'][1]['data']['instant']['details'][field] = value
    key = {'relative_humidity': 'relative_humidity', 'air_pressure_at_sea_level': 'pressure_hpa', 'wind_from_direction': 'wind_direction'}[field]
    result = w.validated_forecast(payload, base)
    assert result['current'][key] is None
    assert result['current']['temperature_2m'] == 12 and len(result['hourly']) == 6


def test_met_missing_optional_units_and_bad_future_data_do_not_break_current_forecast():
    base = 1791878400
    minimal = w.validated_forecast(forecast(base), base)
    assert all(minimal['current'][key] is None for key in ('relative_humidity', 'pressure_hpa', 'wind_direction', 'precipitation_mm', 'precipitation_hours'))
    assert len(minimal['hourly']) == 6 and len(minimal['forecast_periods']) == 1
    payload = detailed_forecast(base)
    payload['properties']['meta']['units'].update({'relative_humidity': 'ratio', 'precipitation_amount': 'inches'})
    payload['properties']['timeseries'][4]['data']['next_1_hours']['summary']['symbol_code'] = 'unknown'
    payload['properties']['timeseries'][31]['data']['instant']['details']['air_temperature'] = None
    result = w.validated_forecast(payload, base)
    assert result['current']['relative_humidity'] is None and result['current']['precipitation_mm'] is None
    assert len(result['forecast_periods']) == 1
    assert all(point['time'] != base + 3 * 3600 and point['precipitation_mm'] is None for point in result['hourly'])


def test_met_precipitation_falls_back_to_valid_complete_period():
    base = 1791878400
    payload = detailed_forecast(base)
    data = payload['properties']['timeseries'][1]['data']
    data['next_1_hours']['details']['precipitation_amount'] = -1
    data['next_6_hours']['details']['precipitation_amount'] = 1.7
    result = w.validated_forecast(payload, base)
    assert (result['current']['precipitation_mm'], result['current']['precipitation_hours']) == (1.7, 6)
    payload['properties']['timeseries'] = payload['properties']['timeseries'][:26]
    last = payload['properties']['timeseries'][-1]['data']
    last['next_1_hours']['details']['precipitation_amount'] = 999
    # A final instant cannot claim precipitation beyond the available model.
    assert w.precipitation(last, payload['properties']['meta']['units'], base + 86400, base + 86400) == {'precipitation_mm': None, 'precipitation_hours': None}


def test_met_optional_hourly_uses_sparse_actual_samples_and_skips_invalid_wind():
    base = 1791878400
    payload = detailed_forecast(base)
    payload['properties']['timeseries'][4]['data']['instant']['details']['wind_speed'] = float('inf')
    result = w.validated_forecast(payload, base)
    assert all(point['time'] != base + 3 * 3600 for point in result['hourly'])
    assert len(result['hourly']) <= 6 and result['hourly'][-1]['time'] <= base + 15 * 3600
    payload['properties']['timeseries'] = [point for point in payload['properties']['timeseries']
                                          if (w.timestamp(point['time']) - base) % 21600 == 0]
    result = w.validated_forecast(payload, base)
    assert [point['time'] for point in result['hourly']] == [base + hour * 3600 for hour in (0, 6, 12)]


def test_photon_validation_filters_coordinates_objects_and_duplicates():
    valid = city(); bad = [None, {'properties': None, 'geometry': None}, city(kind='street'), city(longitude=181), city(latitude=float('nan')), city(latitude=True)]
    wrong = city(); wrong['properties']['country'] = {'unexpected': 'object'}; bad.append(wrong)
    features = bad + [valid, copy.deepcopy(valid)] + [city(name=f'Город{index}', latitude=40 + index) for index in range(7)]
    results = w.validated_cities({'features': features})
    assert len(results) == 5 and results[0]['name'] == 'Москва'
    assert results[0]['latitude'] == 55.75123 and results[0]['longitude'] == 37.61123
    assert w.validated_cities({'features': []}) == []
    with pytest.raises(ValueError): w.validated_cities({'features': [None]})
    with pytest.raises(ValueError): w.validated_cities({'features': {}})


def test_photon_queries_are_cached_separately_and_invalid_queries_do_not_fetch(monkeypatch):
    c, _, _ = owner(); calls = []
    def provider(request):
        calls.append(request.url.params['q'])
        return httpx.Response(200, json={'features': [city(name=request.url.params['q'])]})
    mock_provider(monkeypatch, provider)
    assert c.get('/api/weather/search', params={'q': 'ГородОдин'}).json()[0]['name'] == 'ГородОдин'
    assert c.get('/api/weather/search', params={'q': 'горододин'}).json()[0]['name'] == 'ГородОдин'
    assert c.get('/api/weather/search', params={'q': 'ГородДва'}).json()[0]['name'] == 'ГородДва'
    assert calls == ['ГородОдин', 'ГородДва']
    for query in ('x', 'z' * 101, '  '):
        assert c.get('/api/weather/search', params={'q': query}).status_code == 422
    assert client().get('/api/weather/search', params={'q': 'ГородДва'}).status_code == 401
    assert len(calls) == 2


def test_weather_concurrent_requests_share_forecast_and_preserve_account_guard(monkeypatch):
    c, _, _ = owner(); g, _ = member(c); enable(c, 'weather'); enable(g, 'weather')
    add(c, 'weather', {'city': 'Параллельно', 'latitude': 41.12001, 'longitude': 34.14001}, 'parallel_weather')
    calls = []
    async def provider(request):
        calls.append(request)
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=forecast())
    mock_provider(monkeypatch, provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: c.get('/api/widgets/parallel_weather/weather'), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert len(calls) == 1
    assert g.get('/api/widgets/parallel_weather/weather').status_code == 404
    assert c.put('/api/modules', json={'enabled': {'weather': False}}).status_code == 200
    assert c.get('/api/widgets/parallel_weather/weather').status_code == 200
    assert len(calls) == 1


def test_weather_tls_diagnostic_and_bad_geocoding(monkeypatch):
    cause = ssl.SSLCertVerificationError('certificate rejected')
    error = httpx.ConnectError('proxy details must not be disclosed'); error.__cause__ = cause
    message = w.service_error(error)
    assert 'CA-сертификаты' in message and 'proxy details' not in message
    c, _, _ = owner(); enable(c, 'weather')
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json={'features': [None]}))
    assert c.get('/api/weather/search', params={'q': 'ПовреждённыйГород'}).status_code == 503
