"""Disposable HTTP fixture: replace a public build while a browser stays open."""
import json
import shutil
import sys
from pathlib import Path

from werkzeug.serving import run_simple
from werkzeug.wrappers import Request, Response

from app.static_assets import StaticAssets


root = Path(sys.argv[1])
for version in ['A', 'B']:
    static = root / version
    assets = static / 'assets'; assets.mkdir(parents=True)
    (assets / f'app-{version}.js').write_text(f'''window.version='{version}';
document.querySelector('button').onclick=async()=>{{
try{{const m=await import('./lazy-{version}.js');document.querySelector('output').textContent=m.answer}}
catch(e){{document.querySelector('output').textContent='ERROR: '+e.message}}
}};''')
    (assets / f'lazy-{version}.js').write_text(f"import {{base}} from './shared-{version}.js';export const answer=base+'-{version}';")
    (assets / f'shared-{version}.js').write_text("export const base='loaded';")
    (static / 'asset-manifest.json').write_text(json.dumps({'aliases': {'app.js': f'app-{version}.js'}, 'immutable': [p.name for p in assets.iterdir()]}))
state = {'version': 'A', 'server': StaticAssets(root / 'A', cache=root / 'cache')}


@Request.application
def app(request):
    if request.path == '/replace' and request.method == 'POST':
        state.update(version='B', server=StaticAssets(root / 'B', cache=root / 'cache'))
        shutil.rmtree(root / 'A')
        return Response(status=204)
    if request.path.startswith('/assets/'):
        return state['server'].response(request.path.removeprefix('/assets/'), request) or Response(status=404)
    if request.path == '/':
        return Response(f'<button>Open lazy feature</button><output></output><script type="module" src="/assets/app-{state["version"]}.js"></script>', content_type='text/html', headers={'Cache-Control': 'no-store'})
    return Response(status=404)


run_simple('127.0.0.1', 8049, app, threaded=True, use_reloader=False)
