"""MET forecasts and city search with a shared, encrypted Photon cache."""
from app.i18n import message as localized_message
import asyncio
import json
import logging
import math
import os
import ssl
import tempfile
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from app.data_crypto import DataCipher, StorageError
from app.http import Router, Request
from app.modules.widget_models import owned_widget
from app.process_locks import async_file_lock
from app.version import APP_VERSION

logger = logging.getLogger(__name__)
USER_AGENT = os.getenv('WEATHER_USER_AGENT', '').strip() or ('DrG-Space/' + APP_VERSION + ' (' + os.getenv('PUBLIC_URL', 'http://localhost:8000').rstrip('/') + ')')
FORECAST_URL = 'https://api.met.no/weatherapi/locationforecast/2.0/compact'
SEARCH_URL = 'https://photon.komoot.io/api/'
CITY_SEARCH_URL = 'https://geocoding-api.open-meteo.com/v1/search'
CACHE_LIMIT = 128
SEARCH_CACHE_BYTES = 2 * 1024 * 1024


def service_error(error, search=False):
    """Explain failures without exposing proxy addresses or secrets."""
    label = 'Поиск города' if search else 'Погода'
    if isinstance(error, httpx.TimeoutException):
        return label + ': сервис не ответил вовремя. Попробуйте ещё раз.'
    cause = error
    while cause:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return label + ': сервер не доверяет HTTPS-сертификату. Проверьте CA-сертификаты и настройки прокси на сервере.'
        cause = cause.__cause__
    return label + ': сервис временно недоступен. Попробуйте ещё раз.'


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Missing forecast value')
    return value


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Missing forecast time')
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('Forecast time requires a timezone')
    return int(date.timestamp())


def weather_code(symbol):
    if not isinstance(symbol, str):
        raise ValueError('Missing forecast symbol')
    base = symbol.split('_', 1)[0]
    codes = {'clearsky': 0, 'fair': 1, 'partlycloudy': 2, 'cloudy': 3, 'fog': 45,
             'lightrain': 61, 'rain': 63, 'heavyrain': 65,
             'lightsleet': 68, 'sleet': 69, 'heavysleet': 69,
             'lightsnow': 71, 'snow': 73, 'heavysnow': 75,
             'lightrainshowers': 80, 'rainshowers': 81, 'heavyrainshowers': 82,
             'lightsleetshowers': 68, 'sleetshowers': 69, 'heavysleetshowers': 69,
             'lightsnowshowers': 85, 'snowshowers': 86, 'heavysnowshowers': 86}
    # These two spellings are part of the provider's documented 2.0 format.
    base = base.replace('lightssleet', 'lightsleet').replace('lightssnow', 'lightsnow')
    if base.endswith('andthunder') and base[:-10] in codes:
        return 95
    if base not in codes:
        raise ValueError('Unknown forecast symbol')
    return codes[base]


FORECAST_PERIODS = (('next_1_hours', 1), ('next_6_hours', 6), ('next_12_hours', 12))


def optional_measurement(details, units, name, expected_unit, minimum=None, maximum=None):
    """Ignore unavailable optional metrics rather than inventing their units."""
    if not isinstance(details, dict) or units.get(name) != expected_unit:
        return None
    try:
        value = number(details[name])
    except (KeyError, ValueError, OverflowError):
        return None
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        return None
    return value


def point_summary(data, stamp=None, end=None):
    """Read the shortest available symbol, retaining its period boundaries."""
    for name, hours in FORECAST_PERIODS:
        if name in data and (end is None or stamp + hours * 3600 <= end):
            return weather_code(data[name]['summary']['symbol_code'])
    raise ValueError('Missing forecast summary')


def precipitation(data, units, stamp, end):
    for name, hours in FORECAST_PERIODS:
        period = data.get(name)
        if not isinstance(period, dict) or stamp + hours * 3600 > end:
            continue
        amount = optional_measurement(period.get('details'), units, 'precipitation_amount', 'mm', minimum=0)
        if amount is not None:
            return {'precipitation_mm': amount, 'precipitation_hours': hours}
    return {'precipitation_mm': None, 'precipitation_hours': None}


def future_forecasts(points, start, units, first_extrema):
    """Bound optional forecasts and never interpolate missing model points.

    Period temperature extrema use only instants inside the interval and six-hour
    aggregates wholly contained in it. Wind maxima use valid instantaneous model
    values inside that same interval, rather than gusts or interpolated values.
    Bracketing samples must cover each complete interval at the provider's
    supported one- or six-hour resolution.
    """
    hourly, periods = [], []
    end = points[-1][0]
    for stamp, data in points:
        if not start <= stamp <= start + 15 * 3600 or hourly and stamp < hourly[-1]['time'] + 3 * 3600:
            continue
        try:
            details = data['instant']['details']
            temperature, wind = number(details['air_temperature']), number(details['wind_speed'])
            if wind < 0:
                continue
            code = point_summary(data, stamp, end)
            wind_kmh = round(number(wind * 3.6), 2)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        hourly.append({'time': stamp, 'temperature_2m': temperature, 'weather_code': code,
                       'wind_speed_10m': wind_kmh, **precipitation(data, units, stamp, end)})
        if len(hourly) == 6:
            break
    for index in range(5):
        lower, upper = start + index * 86400, start + (index + 1) * 86400
        if end < upper:
            break
        before = next((i for i in range(len(points) - 1, -1, -1) if points[i][0] <= lower), None)
        after = next((i for i, point in enumerate(points) if point[0] >= upper), None)
        covered = points[before:after + 1] if before is not None and after is not None else []
        if not covered or any(right[0] - left[0] > 21600 for left, right in zip(covered, covered[1:])):
            break
        temperatures, winds, summaries = [], [], []
        try:
            for stamp, data in covered:
                if not lower <= stamp <= upper:
                    continue
                details = data['instant']['details']
                temperatures.append(number(details['air_temperature']))
                wind = optional_measurement(details, units, 'wind_speed', 'm/s', minimum=0)
                if wind is not None:
                    try:
                        winds.append(round(number(wind * 3.6), 2))
                    except (ValueError, OverflowError):
                        pass
                aggregate = data.get('next_6_hours')
                if stamp + 21600 <= upper and isinstance(aggregate, dict):
                    for name in ('air_temperature_min', 'air_temperature_max'):
                        value = optional_measurement(aggregate.get('details'), units, name, 'celsius')
                        if value is not None:
                            temperatures.append(value)
                try:
                    code = point_summary(data, stamp, upper)
                except (KeyError, TypeError, ValueError):
                    continue
                summaries.append((abs(stamp - (lower + 43200)), stamp, code))
        except (KeyError, TypeError, ValueError, OverflowError):
            break
        if not temperatures:
            break
        minimum, maximum = first_extrema if index == 0 else (min(temperatures), max(temperatures))
        periods.append({'start': lower, 'end': upper, 'temperature_2m_min': minimum,
                        'temperature_2m_max': maximum,
                        'wind_speed_10m_max': max(winds) if winds else None,
                        'weather_code': min(summaries)[2] if summaries else None})
    return hourly, periods


def validated_forecast(payload, at=None):
    """Select the current forecast point and extrema over the next 24 hours.

    These are model forecasts, not observations or calendar-day extrema.
    Re-selecting the point on cached documents keeps 304 responses useful as
    time advances without pretending that the model itself has been updated.
    """
    at = int(time.time()) if at is None else at
    properties = payload['properties']
    meta = properties['meta']
    units = meta['units']
    if units['air_temperature'] != 'celsius' or units['wind_speed'] != 'm/s':
        raise ValueError('Unexpected forecast units')
    model_updated = timestamp(meta['updated_at'])
    series = properties['timeseries']
    if not isinstance(series, list) or not 1 <= len(series) <= 500:
        raise ValueError('Missing forecast series')
    points = sorted(((timestamp(point['time']), point['data']) for point in series), key=lambda point: point[0])
    if len({stamp for stamp, _ in points}) != len(points):
        raise ValueError('Duplicate forecast points')
    current = next((point for point in reversed(points) if point[0] <= at), points[0])
    start, data = current
    if start < at - 21600 or start > at + 21600:
        raise ValueError('Forecast does not cover the current time')
    details = data['instant']['details']
    temperature, wind = number(details['air_temperature']), number(details['wind_speed'])
    if wind < 0:
        raise ValueError('Negative wind speed')
    code = point_summary(data)
    temperatures = []
    end = start + 86400
    for stamp, point in points:
        if not start <= stamp <= end:
            continue
        temperatures.append(number(point['instant']['details']['air_temperature']))
        if stamp + 21600 <= end and 'next_6_hours' in point:
            period = point['next_6_hours']
            if not isinstance(period, dict):
                raise ValueError('Invalid forecast period')
            aggregate = period.get('details', {})
            if not isinstance(aggregate, dict):
                raise ValueError('Invalid forecast period details')
            for key in ('air_temperature_min', 'air_temperature_max'):
                if key in aggregate:
                    temperatures.append(number(aggregate[key]))
    if points[-1][0] < end:
        raise ValueError('Forecast does not cover the next 24 hours')
    hourly, periods = future_forecasts(points, start, units, (min(temperatures), max(temperatures)))
    pressure = optional_measurement(details, units, 'air_pressure_at_sea_level', 'hPa', minimum=0)
    direction = optional_measurement(details, units, 'wind_from_direction', 'degrees', minimum=0, maximum=360)
    return {'provider': 'met-no', 'forecast_range': 'next_24h', 'updated': model_updated,
            'forecast_time': start, 'model_updated': model_updated,
            'current': {'temperature_2m': temperature, 'apparent_temperature': None,
                        'weather_code': code, 'wind_speed_10m': round(number(wind * 3.6), 2),
                        'relative_humidity': optional_measurement(details, units, 'relative_humidity', '%', minimum=0, maximum=100),
                        'pressure_hpa': pressure if pressure else None,
                        'wind_direction': direction % 360 if direction is not None else None,
                        **precipitation(data, units, start, points[-1][0])},
            'daily': {'temperature_2m_min': [min(temperatures)], 'temperature_2m_max': [max(temperatures)]},
            'hourly': hourly, 'forecast_periods': periods}


def validated_cities(payload):
    features = payload['features']
    if not isinstance(features, list):
        raise ValueError('Missing city results')
    result, seen = [], set()
    for feature in features[:100]:
        try:
            properties, geometry = feature['properties'], feature['geometry']
            if not isinstance(properties, dict) or not isinstance(geometry, dict):
                continue
            kind = properties.get('type') or properties.get('layer') or properties.get('osm_value')
            if kind not in ('city', 'locality', 'town', 'village', 'hamlet', 'isolated_dwelling') or geometry['type'] != 'Point':
                continue
            if kind == 'locality' and properties.get('osm_value') not in ('city', 'locality', 'town', 'village', 'hamlet', 'isolated_dwelling'):
                continue
            longitude, latitude = geometry['coordinates'][:2]
            longitude, latitude = number(longitude), number(latitude)
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                continue
            name, country, region = properties['name'], properties.get('country', ''), properties.get('state', '')
            if not all(isinstance(value, str) and len(value) <= 200 for value in (name, country, region)) or not name.strip():
                continue
            city = {'name': name.strip(), 'country': country, 'region': region, 'latitude': latitude, 'longitude': longitude}
            key = (city['name'], latitude, longitude)
            if key in seen:
                continue
            seen.add(key); result.append(city)
            if len(result) == 5:
                break
        except (ValueError, KeyError, TypeError, IndexError):
            continue
    if features and not result:
        raise ValueError('No valid city results')
    return result


def expires_at(headers, checked):
    try:
        expiry = int(parsedate_to_datetime(headers.get('expires', '')).timestamp())
    except (ValueError, TypeError, OverflowError):
        expiry = checked + 600
    return min(checked + 86400, max(checked + 600, expiry))


class GeocodingFailure(Exception):
    """A safe provider message, including messages served from the backoff cache."""


class PhotonSearch:
    """Serialize searches across local workers, including the response body.

    The public demo's request spacing applies to the server, not to each worker.
    A shared cache also avoids fetching the same query on different workers.
    The monotonic clock is shared by processes on one host; a previous host boot
    cannot cause an unbounded wait because the spacing is capped at one second.
    """
    CONTEXT = 'weather:photon:v1'

    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / 'provider-cache'
        self.path = self.directory / 'photon-v1.cache'
        self._cipher = None

    @property
    def cipher(self):
        # Importing the module or building routes must not read unconfigured keys.
        if self._cipher is None:
            self._cipher = DataCipher.from_env()
        return self._cipher

    def _directory(self):
        if self.directory.is_symlink():
            raise StorageError('Provider cache directory must not be a symbolic link.')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _read(self):
        self._directory()
        try:
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except FileNotFoundError:
            return {'last_search': None, 'entries': OrderedDict()}
        with os.fdopen(descriptor, 'rb') as source:
            encrypted = source.read(SEARCH_CACHE_BYTES + 128)
            if len(encrypted) >= SEARCH_CACHE_BYTES + 128:
                raise StorageError('Provider cache is too large.')
        try:
            payload = json.loads(self.cipher.open(encrypted, self.CONTEXT))
            if not isinstance(payload, dict) or payload.get('version') != 1:
                raise ValueError('Invalid cache format')
            last = payload['last_search']
            if last is not None and number(last) < 0:
                raise ValueError('Invalid search clock')
            entries = payload['entries']
            if not isinstance(entries, list) or len(entries) > CACHE_LIMIT:
                raise ValueError('Invalid cache entries')
            result = OrderedDict()
            for key, entry in entries:
                if not isinstance(key, str) or not 2 <= len(key) <= 300 or not isinstance(entry, dict):
                    raise ValueError('Invalid search entry')
                number(entry['expires'])
                if 'error' in entry:
                    if not isinstance(entry['error'], str) or len(entry['error']) > 400 or 'cities' in entry:
                        raise ValueError('Invalid cached failure')
                else:
                    cities = entry['cities']
                    if not isinstance(cities, list) or len(cities) > 5:
                        raise ValueError('Invalid cached cities')
                    for city in cities:
                        if not all(isinstance(city[name], str) and len(city[name]) <= 200 for name in ('name', 'country', 'region')):
                            raise ValueError('Invalid cached city name')
                        if not city['name'].strip() or not -90 <= number(city['latitude']) <= 90 or not -180 <= number(city['longitude']) <= 180:
                            raise ValueError('Invalid cached coordinates')
                if key in result:
                    raise ValueError('Duplicate cached search')
                result[key] = entry
            return {'last_search': last, 'entries': result}
        except (ValueError, KeyError, TypeError, IndexError) as error:
            raise StorageError('Provider cache contains invalid data.') from error

    def _write(self, state):
        body = json.dumps({'version': 1, 'last_search': state['last_search'],
                           'entries': list(state['entries'].items())}, ensure_ascii=False, allow_nan=False).encode()
        if len(body) > SEARCH_CACHE_BYTES:
            raise StorageError('Provider cache is too large.')
        encrypted = self.cipher.seal(body, self.CONTEXT)
        descriptor, name = tempfile.mkstemp(prefix='.photon-', dir=self.directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as target:
                target.write(encrypted)
                target.flush(); os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _remember(self, state, key, value, checked):
        entries = state['entries']
        for old_key in list(entries):
            if entries[old_key]['expires'] <= checked:
                del entries[old_key]
        entries[key] = value
        entries.move_to_end(key)
        while len(entries) > CACHE_LIMIT:
            entries.popitem(last=False)
        self._write(state)

    async def search(self, q, now, language=None):
        key = (language + ':' if language else '') + q.casefold()
        async with async_file_lock(self.root, 'weather-photon'):
            state = self._read()
            cached = state['entries'].get(key)
            if cached and cached['expires'] > now():
                state['entries'].move_to_end(key)
                self._write(state)
                if cached.get('error'):
                    raise GeocodingFailure(cached['error'])
                return cached['cities']
            last = state['last_search']
            delay = 0 if last is None else min(1, max(0, 1 - (time.monotonic() - last)))
            if delay:
                await asyncio.sleep(delay)
            state['last_search'] = time.monotonic()
            # Persist before sending, so a worker exit cannot reset the interval.
            self._write(state)
            try:
                async with httpx.AsyncClient(timeout=12, trust_env=True, follow_redirects=True, max_redirects=3) as client:
                    params = [('q', q), ('limit', 5), ('layer', 'city'), ('layer', 'locality')]
                    if language: params.append(('lang',language))
                    response = await client.get(SEARCH_URL, params=params, headers={'User-Agent': USER_AGENT})
                    response.raise_for_status()
                    cities = validated_cities(response.json())
                checked = now()
                self._remember(state, key, {'cities': cities, 'expires': checked + 86400}, checked)
                return cities
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as error:
                logger.warning('Weather geocoding failed (%s)', type(error).__name__)
                message = service_error(error, search=True)
                checked = now()
                self._remember(state, key, {'error': message, 'expires': checked + 60}, checked)
                raise GeocodingFailure(message) from None


def named_cities(payload, limit=5):
    """Validate GeoNames settlements and real IANA zones, never fixed offsets."""
    if not isinstance(payload, dict) or payload.get('error'):
        raise ValueError('Invalid geocoding response')
    rows = payload.get('results', [])
    if not isinstance(rows, list):
        raise ValueError('Invalid city results')
    result, seen = [], set()
    for row in rows[:100]:
        try:
            name, country, region, zone = row['name'], row.get('country', ''), row.get('admin1', ''), row['timezone']
            if not str(row.get('feature_code', '')).startswith('PPL'):
                continue
            if not all(isinstance(value, str) and len(value) <= 200 for value in (name, country, region, zone)) or not name.strip():
                continue
            if not zone or zone.startswith('/') or '..' in zone.split('/'):
                continue
            ZoneInfo(zone)
            lat, lon = number(row['latitude']), number(row['longitude'])
            if not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat, lon) in seen:
                continue
            seen.add((lat, lon))
            result.append({'name': name, 'country': country, 'region': region, 'latitude': lat, 'longitude': lon, 'timezone': zone})
            if len(result) == limit:
                break
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            continue
    if rows and not result:
        raise ValueError('No valid settlements')
    return result


class CitySearch(PhotonSearch):
    """Multilingual names + zones; Photon remains a fallback for local names.

    A separate encrypted cache avoids replaying incomplete old Photon results.
    Locale is part of the key, so parallel ru/en clients cannot mix names.
    """
    CONTEXT = 'weather:cities:v2'

    def __init__(self, root):
        super().__init__(root)
        self.path = self.directory / 'cities-v2.cache'
        self.fallback = PhotonSearch(root)
        from app.world_cities import WorldCities
        self.reference = WorldCities(root)

    async def search(self, q, now, language='ru'):
        language = 'en' if language == 'en' else 'ru'
        local = await asyncio.to_thread(self.reference.search, q, language)
        if local:
            return local
        key = language + ':' + q.casefold()
        async with async_file_lock(self.root, 'weather-cities'):
            state = self._read()
            cached = state['entries'].get(key)
            if cached and cached['expires'] > now():
                if cached.get('error'):
                    raise GeocodingFailure(cached['error'])
                return cached['cities']
            last = state['last_search']
            if last is not None:
                await asyncio.sleep(min(1, max(0, 1 - (time.monotonic() - last))))
            state['last_search'] = time.monotonic()
            self._write(state)
            cities, failed = [], False
            try:
                async with httpx.AsyncClient(timeout=12, trust_env=True, follow_redirects=True, max_redirects=3) as client:
                    response = await client.get(CITY_SEARCH_URL, params={'name': q, 'count': 5, 'language': language}, headers={'User-Agent': USER_AGENT})
                    response.raise_for_status()
                    cities = named_cities(response.json())
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
                failed = True
            if not cities:
                try:
                    cities = await self.fallback.search(q, now, 'en')
                    # Local-script aliases may exist only in OSM. Resolve the
                    # returned English settlement name to a zone, checking the
                    # coordinates so namesakes never assign a different zone.
                    for city in cities:
                        await asyncio.sleep(1)
                        try:
                            async with httpx.AsyncClient(timeout=8, trust_env=True, follow_redirects=True, max_redirects=3) as client:
                                response = await client.get(CITY_SEARCH_URL, params={'name': city['name'], 'count': 20, 'language': language}, headers={'User-Agent': USER_AGENT})
                                response.raise_for_status()
                                candidates = named_cities(response.json(),limit=20)
                            match = next((item for item in candidates if abs(item['latitude']-city['latitude'])<0.15 and abs(item['longitude']-city['longitude'])<0.15), None)
                            if match: city.update(match)
                        except (httpx.HTTPError, ValueError, KeyError, TypeError):
                            pass
                except GeocodingFailure as error:
                    self._remember(state, key, {'error': str(error), 'expires': now() + 60}, now())
                    raise
            # Do not cache an outage as a successful empty search for a day.
            self._remember(state, key, {'cities': cities, 'expires': now() + (60 if failed else 86400)}, now())
            return cities


def create_weather_router(db, ready, rate, now, fail):
    router = Router()
    cache, locks = OrderedDict(), {}
    searches = CitySearch(os.getenv('DATA_DIR', './data'))

    def remember(store, key, value):
        store[key] = value; store.move_to_end(key)
        while len(store) > CACHE_LIMIT:
            store.popitem(last=False)

    @asynccontextmanager
    async def forecast_lock(key):
        entry = locks.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if not entry[1]:
                locks.pop(key, None)

    @router.get('/api/weather/search')
    async def search_city(q: str, request: Request):
        ready(request); rate(request, 'weather-search', 60, 60)
        q = q.strip()
        if not 2 <= len(q) <= 100:
            fail(422, localized_message('enterBetween2And100Characters'))
        try:
            from app.i18n import current_language
            return await searches.search(q, now, current_language.get())
        except GeocodingFailure as error:
            fail(503, str(error))

    @router.get('/api/widgets/{widget_id}/weather')
    async def weather(widget_id: str, request: Request):
        session = ready(request); rate(request, 'weather', 60, 60)
        with db() as connection:
            config = owned_widget(connection, session['user_id'], widget_id, 'weather', fail)['config']
        if config.get('latitude') is None:
            return {'configured': False}
        key = (round(config['latitude'], 4), round(config['longitude'], 4))
        city = config.get('city', '')
        async with forecast_lock(key):
            cached = cache.get(key)
            if cached and cached['expires'] > now():
                cache.move_to_end(key)
                if cached.get('error'):
                    fail(503, cached['error'])
                if not cached.get('stale') or cached['checked'] >= now() - 21600:
                    try:
                        forecast = validated_forecast(cached['payload'], now())
                    except (ValueError, KeyError, TypeError, IndexError):
                        pass
                    else:
                        return {'configured': True, **forecast, 'checked_at': cached['checked'], 'city': city, 'stale': cached.get('stale', False)}
            try:
                headers = {'User-Agent': USER_AGENT}
                if cached and cached.get('last_modified'):
                    headers['If-Modified-Since'] = cached['last_modified']
                async with httpx.AsyncClient(timeout=12, trust_env=True, follow_redirects=True, max_redirects=3) as client:
                    response = await client.get(FORECAST_URL, params={'lat': f'{key[0]:.4f}', 'lon': f'{key[1]:.4f}'}, headers=headers)
                    if response.status_code == 304:
                        if not cached or 'payload' not in cached:
                            raise ValueError('Provider returned 304 without a cached forecast')
                        payload = cached['payload']
                    else:
                        response.raise_for_status()
                        payload = response.json()
                    forecast = validated_forecast(payload, now())
                    checked = now()
                    entry = {'payload': payload, 'checked': checked, 'expires': expires_at(response.headers, checked),
                             'last_modified': response.headers.get('last-modified') or (cached or {}).get('last_modified')}
                remember(cache, key, entry)
                return {'configured': True, **forecast, 'checked_at': checked, 'city': city, 'stale': False}
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as error:
                logger.warning('Weather forecast failed (%s)', type(error).__name__)
                if cached and 'payload' in cached and cached['checked'] >= now() - 21600:
                    try:
                        forecast = validated_forecast(cached['payload'], now())
                    except (ValueError, KeyError, TypeError, IndexError):
                        pass
                    else:
                        remember(cache, key, cached | {'expires': now() + 60, 'stale': True})
                        return {'configured': True, **forecast, 'checked_at': cached['checked'], 'city': city, 'stale': True}
                message = service_error(error)
                remember(cache, key, {'error': message, 'expires': now() + 60})
                fail(503, message)

    return router
