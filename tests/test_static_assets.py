import json
import pytest
from types import SimpleNamespace
from werkzeug.datastructures import Headers

from app.static_assets import StaticAssets
from tests.test_security import client, owner, reset, m


def request(etag=''):
    return SimpleNamespace(headers=Headers({'If-None-Match':etag}))


def test_content_addressed_asset_is_immutable_and_alias_revalidates(tmp_path):
    assets=tmp_path/'assets';assets.mkdir()
    (assets/'app-ABC123.js').write_text('export const version=1;')
    (tmp_path/'asset-manifest.json').write_text(json.dumps({'aliases':{'app.js':'app-ABC123.js'},'immutable':['app-ABC123.js']}))
    server=StaticAssets(tmp_path)
    alias=server.response('app.js',request())
    fresh=server.response('app-ABC123.js',request())
    assert alias.headers['ETag']==fresh.headers['ETag']
    assert alias.headers['Cache-Control']=='public, max-age=0, must-revalidate'
    assert fresh.headers['Cache-Control']=='public, max-age=31536000, immutable'
    assert fresh.public_static_asset
    fresh.close();alias.close()
    same=server.response('app-ABC123.js',request('W/'+fresh.headers['ETag']))
    assert same.status_code==304 and same.get_data()==b''
    assert same.headers['Cache-Control'].endswith('immutable')
    changed=server.response('app-ABC123.js',request('"another-build"'))
    assert changed.status_code==200;changed.close()


def test_public_asset_resolution_rejects_escape_and_does_not_trust_filename_pattern(tmp_path):
    (tmp_path/'assets').mkdir();(tmp_path/'private.js').write_text('private')
    (tmp_path/'assets'/'looks-hashed-ABC123.js').write_text('public')
    (tmp_path/'assets'/'escape.js').symlink_to(tmp_path/'private.js')
    server=StaticAssets(tmp_path)
    assert server.response('../private.js',request()) is None
    assert server.response('escape.js',request()) is None
    assert server.response('missing.js',request()) is None
    response=server.response('looks-hashed-ABC123.js',request())
    assert 'immutable' not in response.headers['Cache-Control'];response.close()


def test_http_boundary_caches_only_packaged_assets_and_never_api_or_html():
    if not (m.STATIC/'asset-manifest.json').is_file():pytest.skip('Run npm run build for packaged HTTP asset checks')
    c=client();response=c.get('/assets/app.js')
    assert response.status_code==200,response.text
    assert response.headers['cache-control']=='public, max-age=0, must-revalidate'
    etag=response.headers['etag']
    again=c.get('/assets/app.js',headers={'If-None-Match':etag})
    assert again.status_code==304 and not again.content
    assert c.head('/assets/app.js').headers['etag']==etag
    for path in ['/','/native/passkey','/push-sw.js','/api/health','/api/me','/assets/not-a-real-file.js']:
        assert c.get(path).headers['cache-control']=='no-store',path
    c,_,_=owner()
    note=c.post('/api/notes',json={'title':'Private'}).json()
    response=c.get('/api/notes/'+note['id']);assert response.status_code==200
    assert response.headers['cache-control']=='no-store'


def test_retained_build_http_boundary_keeps_private_and_error_responses_no_store(tmp_path, monkeypatch):
    from tests.test_public_builds import build
    a, b = build(tmp_path, 'A'), build(tmp_path, 'B')
    cache = tmp_path / 'cache'
    StaticAssets(a, cache=cache)
    import shutil
    shutil.rmtree(a)
    monkeypatch.setattr(m, 'packaged_assets', StaticAssets(b, cache=cache))
    c = client()
    old = c.get('/assets/lazy-A.js')
    assert old.status_code == 200 and old.content == b'export const version="A";'
    assert old.headers['cache-control'].endswith('immutable')
    assert 'javascript' in old.headers['content-type']
    assert c.get('/assets/lazy-A.js', headers={'If-None-Match': old.headers['etag']}).status_code == 304
    for path in ['/assets/index.json', '/assets/not-a-build.js', '/api/me', '/api/health', '/']:
        assert c.get(path).headers['cache-control'] == 'no-store'
