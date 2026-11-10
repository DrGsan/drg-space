"""The shipped reference works worldwide without any provider connectivity."""
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import pytest
from app.world_cities import WorldCities


def test_multilingual_world_cities_and_namesake_ordering_without_network(tmp_path):
    reference=WorldCities(tmp_path)
    for query in ('Лимасол','Лимассол','Limassol','Λεμεσός'):
        city=reference.search(query)[0]
        assert city['country']=='Кипр' and city['timezone']=='Asia/Nicosia'
        assert 34.6<city['latitude']<34.8
    assert reference.search('東京')[0]['timezone']=='Asia/Tokyo'
    assert reference.search('New York',language='en')[0]['country']=='United States'
    assert reference.search('Берлин')[0]['country']=='Германия'
    assert reference.search('Paris')[0]['timezone']=='Europe/Paris'
    assert reference.search('Москва')[0]['timezone']=='Europe/Moscow'
    assert reference.search('a')==[] and reference.search('" OR 1=1 --')==[]
    for city in reference.search('Лима'):ZoneInfo(city['timezone'])


def test_workers_share_atomic_readonly_reference_and_no_query_history(tmp_path):
    refs=[WorldCities(tmp_path),WorldCities(tmp_path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda ref:ref.search('Limassol'),refs))
    assert results[0]==results[1] and results[0][0]['timezone']=='Asia/Nicosia'
    assert len(list((tmp_path/'public-reference').iterdir()))==1
    assert not list((tmp_path/'public-reference').glob('.cities-*'))
    before=refs[0].path.read_bytes()
    refs[1].search('Город-которого-не-существует')
    assert refs[0].path.read_bytes()==before


def test_reference_refuses_symlink_cache(tmp_path):
    ref=WorldCities(tmp_path);other=tmp_path/'other';other.mkdir()
    ref.directory.symlink_to(other)
    with pytest.raises(OSError):ref.search('Лимасол')
