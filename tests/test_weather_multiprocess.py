"""Provider limits belong to the host, even when workers have separate caches."""
import asyncio
import base64
import multiprocessing
import os
import time
from types import SimpleNamespace

import httpx
import pytest

from app.data_crypto import DataCipher, StorageError
from app.modules import weather as w


KEY = base64.urlsafe_b64encode(b'w' * 32).decode()


def city(name):
    return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [37.6, 55.7]},
            'properties': {'name': name, 'country': 'Россия', 'state': '', 'osm_value': 'city'}}


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv('DATA_ENCRYPTION_ENABLED', 'true')
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', KEY)


def worker_search(root, query, start, ready, events):
    """Each spawned child builds its own service and HTTP client."""
    os.environ['DATA_ENCRYPTION_ENABLED'] = 'true'
    os.environ['DATA_ENCRYPTION_KEY'] = KEY
    original = httpx.AsyncClient

    async def provider(request):
        assert request.url.host == 'photon.komoot.io'
        assert request.url.params.get_list('layer') == ['city', 'locality']
        assert request.headers['User-Agent'] == w.USER_AGENT
        events.put(('start', os.getpid(), time.monotonic()))
        # Longer than the rate interval: the next worker must also await the body.
        await asyncio.sleep(1.1)
        events.put(('finish', os.getpid(), time.monotonic()))
        return httpx.Response(200, json={'features': [city(request.url.params['q'])]})

    def factory(**kwargs):
        assert kwargs['trust_env'] is True and kwargs.get('verify', True) is True
        return original(transport=httpx.MockTransport(provider), **kwargs)

    httpx.AsyncClient = factory
    ready.put(os.getpid())
    if not start.wait(10):
        raise RuntimeError('Workers did not start')
    try:
        result = asyncio.run(w.PhotonSearch(root).search(query, lambda: int(time.time())))
        events.put(('result', query, result))
    except BaseException as error:
        events.put(('error', query, type(error).__name__))
        raise


def test_photon_workers_share_casefolded_cache_interval_and_response_lock(tmp_path):
    context = multiprocessing.get_context('spawn')
    start, ready, events = context.Event(), context.Queue(), context.Queue()
    queries = ['Москва', 'москва', 'Владивосток']
    processes = [context.Process(target=worker_search, args=(str(tmp_path), query, start, ready, events)) for query in queries]
    records = []
    try:
        for process in processes:
            process.start()
        pids = {ready.get(timeout=10) for _ in processes}
        assert len(pids) == 3
        start.set()
        while sum(record[0] in ('result', 'error') for record in records) < len(processes):
            records.append(events.get(timeout=10))
        for process in processes:
            process.join(timeout=10)
        assert all(process.exitcode == 0 for process in processes), records
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        ready.close(); events.close()
    starts = sorted((record for record in records if record[0] == 'start'), key=lambda record: record[2])
    finishes = {record[1]: record[2] for record in records if record[0] == 'finish'}
    assert len(starts) == 2 and starts[0][1] != starts[1][1]
    assert starts[1][2] - starts[0][2] >= 1
    assert starts[1][2] >= finishes[starts[0][1]]
    results = {record[1]: record[2] for record in records if record[0] == 'result'}
    assert results['Москва'] == results['москва']
    assert results['Владивосток'][0]['name'] == 'Владивосток'
    cache = tmp_path / 'provider-cache' / 'photon-v1.cache'
    assert cache.read_bytes().startswith(DataCipher.MAGIC)
    assert 'Москва'.encode() not in cache.read_bytes()
    assert cache.stat().st_mode & 0o777 == 0o600
    assert list(cache.parent.glob('.photon-*')) == []


def mock_provider(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_shared_photon_failure_backoff_and_recovery(monkeypatch, tmp_path):
    calls, checked = [], [1791878400]
    monotonic = [10.0]
    monkeypatch.setattr(w, 'time', SimpleNamespace(monotonic=lambda: monotonic[0]))

    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError('do not expose proxy credentials')
        return httpx.Response(200, json={'features': [city('Москва')]})

    mock_provider(monkeypatch, provider)
    first, second = w.PhotonSearch(tmp_path), w.PhotonSearch(tmp_path)
    for service in (first, second):
        with pytest.raises(w.GeocodingFailure) as error:
            asyncio.run(service.search('Москва', lambda: checked[0]))
        assert 'credentials' not in str(error.value)
    assert len(calls) == 1
    checked[0] += 61; monotonic[0] += 61
    assert asyncio.run(second.search('москва', lambda: checked[0]))[0]['name'] == 'Москва'
    assert len(calls) == 2
    assert asyncio.run(first.search('Москва', lambda: checked[0]))[0]['name'] == 'Москва'
    assert len(calls) == 2


def test_shared_photon_cache_is_bounded_and_evicts_old_queries(monkeypatch, tmp_path):
    calls, tick = [], [10.0]

    def monotonic():
        tick[0] += 2
        return tick[0]

    monkeypatch.setattr(w, 'time', SimpleNamespace(monotonic=monotonic))

    def provider(request):
        calls.append(request.url.params['q'])
        return httpx.Response(200, json={'features': [city(request.url.params['q'])]})

    mock_provider(monkeypatch, provider)
    service = w.PhotonSearch(tmp_path)

    async def populate():
        for index in range(w.CACHE_LIMIT + 2):
            await service.search(f'Город {index}', lambda: 1791878400)
        # A fresh instance sees the bounded cache and must fetch evicted query 0.
        second = w.PhotonSearch(tmp_path)
        await second.search('Город 129', lambda: 1791878400)
        await second.search('Город 0', lambda: 1791878400)

    asyncio.run(populate())
    assert len(calls) == w.CACHE_LIMIT + 3
    assert calls[-1] == 'Город 0'
    assert len(w.PhotonSearch(tmp_path)._read()['entries']) == w.CACHE_LIMIT


def test_photon_invalid_encryption_key_cannot_send_or_store_plaintext(monkeypatch, tmp_path):
    calls = []
    mock_provider(monkeypatch, lambda request: calls.append(request))
    service = w.PhotonSearch(tmp_path)  # Construction remains possible before env setup.
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', 'invalid')
    with pytest.raises(StorageError, match='DATA_ENCRYPTION_KEY'):
        asyncio.run(service.search('Москва', lambda: 1791878400))
    assert not calls
    assert not service.path.exists()


def test_photon_encrypted_cache_rejects_a_different_key(monkeypatch, tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={'features': [city('Москва')]})

    mock_provider(monkeypatch, provider)
    asyncio.run(w.PhotonSearch(tmp_path).search('Москва', lambda: 1791878400))
    monkeypatch.setenv('DATA_ENCRYPTION_KEY', base64.urlsafe_b64encode(b'x' * 32).decode())
    with pytest.raises(StorageError, match='authentication failed'):
        asyncio.run(w.PhotonSearch(tmp_path).search('Москва', lambda: 1791878400))
    assert len(calls) == 1
