import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import Headers

from app.public_builds import PublicBuilds, TTL
from app.static_assets import StaticAssets


def build(root, version, *, shared=True):
    static = root / version
    assets = static / 'assets'
    assets.mkdir(parents=True)
    files = {f'app-{version}.js': f'import("./lazy-{version}.js");',
             f'lazy-{version}.js': f'export const version="{version}";'}
    if shared:
        files['shared-COMMON.js'] = 'shared'
    for name, body in files.items():
        (assets / name).write_text(body)
    (assets / 'unlisted-private.txt').write_text('do not retain')
    (static / 'index.html').write_text('mutable HTML')
    (static / 'asset-manifest.json').write_text(json.dumps({'immutable': list(files), 'aliases': {'app.js': f'app-{version}.js'}}))
    return static


def request(etag=''):
    return SimpleNamespace(headers=Headers({'If-None-Match': etag}))


def body(response):
    try:
        return response.get_data()
    finally:
        response.close()


def test_old_lazy_graph_survives_replacement_but_mutable_and_private_files_do_not(tmp_path):
    a, b = build(tmp_path, 'A'), build(tmp_path, 'B')
    cache = tmp_path / 'cache'
    StaticAssets(a, cache=cache)
    shutil.rmtree(a)
    server = StaticAssets(b, cache=cache)
    old = server.response('lazy-A.js', request())
    assert old.status_code == 200 and old.public_static_asset
    assert old.headers['Cache-Control'].endswith('immutable')
    assert 'javascript' in old.headers['Content-Type']
    etag = old.headers['ETag']
    assert body(old) == b'export const version="A";'
    assert body(server.response('app.js', request())) == b'import("./lazy-B.js");'
    assert body(server.response('shared-COMMON.js', request())) == b'shared'
    assert server.response('lazy-A.js', request(etag)).status_code == 304
    for name in ['index.html', 'unlisted-private.txt', '../index.json', 'index.json', '.lock']:
        # Current unlisted static files keep their existing behavior; none are retained.
        assert server.cache.open(name) is None


def test_seven_days_start_at_retirement_and_current_build_does_not_expire(tmp_path):
    stamp = [1000]
    store = PublicBuilds(tmp_path / 'cache', now=lambda: stamp[0])
    a, b = build(tmp_path, 'A'), build(tmp_path, 'B')
    store.publish(a)
    stamp[0] += TTL * 10
    store.prune()
    file, _ = store.open('lazy-A.js'); file.close()
    store.publish(b)
    stamp[0] += TTL - 1
    file, _ = store.open('lazy-A.js'); file.close()
    stamp[0] += 1
    assert store.open('lazy-A.js') is None
    store.prune()
    assert not (store.objects / 'lazy-A.js').exists()
    assert (store.objects / 'lazy-B.js').exists()
    assert (store.objects / 'shared-COMMON.js').exists()


def test_budget_evicts_whole_oldest_graph_and_deduplicates_shared_files(tmp_path):
    stamp = [1000]
    roots = [build(tmp_path, v) for v in ['A', 'B', 'C']]
    one_size = sum((roots[0] / 'assets' / n).stat().st_size for n in json.loads((roots[0] / 'asset-manifest.json').read_text())['immutable'])
    store = PublicBuilds(tmp_path / 'cache', now=lambda: stamp[0], limit=one_size * 2)
    for root in roots:
        store.publish(root); stamp[0] += 1
    assert store.open('app-A.js') is None and store.open('lazy-A.js') is None
    for name in ['app-B.js', 'lazy-B.js', 'app-C.js', 'lazy-C.js', 'shared-COMMON.js']:
        file, _ = store.open(name); file.close()
    assert sum(p.stat().st_size for p in store.objects.iterdir()) <= store.limit
    assert len(store.load()['builds']) == 2


def test_seeded_prior_build_does_not_replace_current_or_extend_retirement(tmp_path):
    stamp = [1000]
    a, b = build(tmp_path, 'A'), build(tmp_path, 'B')
    store = PublicBuilds(tmp_path / 'cache', now=lambda: stamp[0])
    store.publish(b); current = store.load()['current']
    store.publish(a, promote=False)
    assert store.load()['current'] == current
    stamp[0] += TTL - 1
    store.publish(a, promote=False); store.publish(b)
    stamp[0] += 1
    store.prune()
    assert store.open('lazy-A.js') is None
    assert store.load()['current'] == current


def test_workers_can_publish_together_and_read_new_registry_without_restart(tmp_path):
    a, b = build(tmp_path, 'A'), build(tmp_path, 'B')
    cache = tmp_path / 'cache'
    observer = PublicBuilds(cache)
    assert observer.open('lazy-A.js') is None
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda _: PublicBuilds(cache).publish(a), range(4)))
    assert len(observer.load()['builds']) == 1
    PublicBuilds(cache).publish(b)
    for name in ['lazy-A.js', 'lazy-B.js']:
        file, _ = observer.open(name); file.close()
    assert len(observer.load()['builds']) == 2
    assert not list(cache.glob('.index-*')) and not list((cache / 'objects').glob('.asset-*'))


def test_active_response_survives_eviction_and_releases_descriptor(tmp_path):
    stamp = [1000]
    a, b = build(tmp_path, 'A'), build(tmp_path, 'B')
    store = PublicBuilds(tmp_path / 'cache', now=lambda: stamp[0])
    store.publish(a); store.publish(b)
    file, _ = store.open('lazy-A.js')
    stamp[0] += TTL; store.prune()
    assert not (store.objects / 'lazy-A.js').exists()
    with file:
        assert file.read() == b'export const version="A";'
    assert file.closed


def test_unregistered_paths_symlinks_and_corrupt_metadata_fail_closed(tmp_path):
    a = build(tmp_path, 'A'); store = PublicBuilds(tmp_path / 'cache'); store.publish(a)
    private = tmp_path / 'private.js'; private.write_text('private')
    (store.objects / 'fake-HASH.js').symlink_to(private)
    assert store.open('fake-HASH.js') is None
    for name in ['../private.js', '/private.js', 'objects/../private.js']:
        assert store.open(name) is None
    (store.objects / 'lazy-A.js').unlink(); (store.objects / 'lazy-A.js').symlink_to(private)
    assert store.open('lazy-A.js') is None
    store.index.write_text('{invalid json')
    assert store.open('app-A.js') is None
    store.prune()
    assert private.read_text() == 'private' and not list(store.objects.iterdir())


def test_manifest_rejects_outside_files_and_symlinks_and_immutable_collisions(tmp_path):
    a = build(tmp_path, 'A'); store = PublicBuilds(tmp_path / 'cache'); store.publish(a)
    (a / 'assets' / 'lazy-A.js').write_text('different content, same immutable name')
    with pytest.raises(ValueError, match='collision'):
        store.publish(a)
    file, _ = store.open('lazy-A.js')
    with file: assert file.read() == b'export const version="A";'
    (a / 'asset-manifest.json').write_text(json.dumps({'immutable': ['../private.js']}))
    with pytest.raises(ValueError, match='manifest'): store.publish(a)
    b = build(tmp_path, 'B'); (b / 'assets' / 'lazy-B.js').unlink(); (b / 'assets' / 'lazy-B.js').symlink_to(a / 'assets' / 'lazy-A.js')
    with pytest.raises(ValueError, match='regular'): store.publish(b)


def test_current_packaged_assets_work_if_cache_is_unavailable(tmp_path, monkeypatch):
    a = build(tmp_path, 'A')
    monkeypatch.setattr(PublicBuilds, 'publish', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    server = StaticAssets(a, cache=tmp_path / 'cache')
    assert body(server.response('lazy-A.js', request())) == b'export const version="A";'
    assert server.response('unknown.js', request()) is None
