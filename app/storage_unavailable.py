"""Public, self-contained recovery document while private storage is locked."""
from html import escape
import secrets

from app.http import JSONResponse,Response
from app.i18n import current_language,text


def document_navigation(request):
    return (getattr(request,'method','') in ('GET','HEAD') and not request.url.path.startswith('/api/')
                and (request.headers.get('Sec-Fetch-Dest')=='document'
                     or request.headers.get('Sec-Fetch-Mode')=='navigate'
                     or 'text/html' in request.headers.get('Accept','').lower()))


def unavailable(request,error):
    headers={'Retry-After':'5','Cache-Control':'no-store'}
    if not document_navigation(request):return JSONResponse({'detail':error.detail},error.status_code,headers=headers)
    nonce=secrets.token_urlsafe(24)
    headers['Content-Security-Policy']="default-src 'none'; script-src 'nonce-"+nonce+"'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    # Nothing here reads private data or fetches an asset/API while the gate is
    # closed. A fixed nonce-authorized handler reloads the exact URL, including
    # its fragment. No polling, redirect, cache clearing or session change.
    return Response(f'''<!doctype html>
<html lang="{escape(current_language.get())}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow,noarchive"><meta name="color-scheme" content="light dark">
<title>{escape(text('storageBusyTitle'))} · DrG Space</title>
<style>
:root{{color-scheme:light;--bg:#eef2fa;--panel:#ffffffed;--ink:#252d3b;--muted:#586477;--line:#d8e0ee;--accent:#4764f3;--wash:#e1e8ff}}
@media(prefers-color-scheme:dark){{:root:not([data-scheme=light]){{color-scheme:dark;--bg:#152031;--panel:#202d40ee;--ink:#edf2fc;--muted:#b3c0d5;--line:#3b4b63;--accent:#9caeff;--wash:#2e3c58}}}}
:root[data-scheme=dark]{{color-scheme:dark;--bg:#152031;--panel:#202d40ee;--ink:#edf2fc;--muted:#b3c0d5;--line:#3b4b63;--accent:#9caeff;--wash:#2e3c58}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100svh;display:grid;place-items:center;padding:32px 20px;background:radial-gradient(ellipse at 18% 10%,var(--wash),transparent 62%),var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,sans-serif}}
main{{width:100%;max-width:540px;padding:40px;border:1px solid var(--line);border-radius:24px;background:var(--panel);box-shadow:0 20px 80px #11213b12}}
.brand{{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:700;margin-bottom:36px}}.brand span{{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;color:var(--accent);background:var(--wash);font-size:12px}}
.symbol{{display:grid;place-items:center;width:64px;height:64px;border-radius:20px;background:var(--wash);color:var(--accent);margin-bottom:22px}}svg{{width:32px;height:32px}}
h1{{font-size:clamp(25px,4vw,30px);line-height:1.24;letter-spacing:-.035em;margin:0 0 18px;overflow-wrap:anywhere}}p{{color:var(--muted);margin:0 0 26px}}button{{width:100%;min-height:48px;border:0;border-radius:12px;background:var(--accent);color:#fff;font:600 15px/1.4 system-ui;padding:14px 20px;cursor:pointer;white-space:normal}}:root[data-scheme=dark] button{{color:#172239}}button:focus-visible{{outline:3px solid var(--ink);outline-offset:4px}}.hint{{font-size:13px;margin:16px 0 0}}
@media(prefers-color-scheme:dark){{:root:not([data-scheme=light]) button{{color:#172239}}}}@media(max-width:480px){{main{{padding:28px 24px}}.brand{{margin-bottom:28px}}}}
</style></head><body><main aria-labelledby="busy-title"><div class="brand"><span aria-hidden="true">DrG</span>DrG Space</div>
<div class="symbol" aria-hidden="true"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M11 7h10M11 25h10M12 7c0 5 8 5 8 10s0 8 0 8M20 7c0 5-8 5-8 10v8" stroke-linecap="round"/></svg></div>
<h1 id="busy-title">{escape(text('storageBusyTitle'))}</h1><p>{escape(text('storageBusyHint'))}</p>
<button type="button" id="busy-retry">{escape(text('storageBusyRetry'))}</button>
<p class="hint">{escape(text('storageBusyAddressHint'))}</p></main>
<script nonce="{nonce}">
try{{const mode=JSON.parse(localStorage.getItem('drg-appearance')||'{{}}').mode;if(mode==='dark'||mode==='light')document.documentElement.dataset.scheme=mode;}}catch{{}}
document.getElementById('busy-retry').addEventListener('click',()=>{{document.getElementById('busy-retry').disabled=true;location.reload();}});
</script></body></html>''',status_code=error.status_code,headers=headers,media_type='text/html')
