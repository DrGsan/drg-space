"""Real Gunicorn sockets must not occupy Flask's HTTP thread pool."""
from contextlib import ExitStack
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]

def wait_for(probe, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if probe():return
        except (OSError, httpx.HTTPError):pass
        time.sleep(.05)
    pytest.fail('Transport condition did not become true.')

def test_async_sockets_streaming_disconnect_and_revocation(tmp_path):
    root = tmp_path / 'asgi-test-transport';root.mkdir()
    with socket.socket() as bound:
        bound.bind(('127.0.0.1', 0));port = bound.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'DRG_ASGI_TEST':'1', 'DATA_DIR':str(root), 'PUBLIC_URL':origin,
           'GUNICORN_WORKERS':'1', 'GUNICORN_THREADS':'2', 'PYTHONPATH':str(ROOT)}
    log = (root / 'server.log').open('w')
    process = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--no-control-socket',
        '--config', str(ROOT/'gunicorn.conf.py'), '--bind', f'127.0.0.1:{port}',
        'tests.asgi_server:app'], cwd=tmp_path, env=env, stdout=log, stderr=log, start_new_session=True)
    try:
        with httpx.Client(base_url=origin, timeout=5, trust_env=False) as client, ExitStack() as sockets:
            wait_for(lambda: client.get('/api/health').status_code == 200)
            url = origin.replace('http:', 'ws:') + '/probe/room?q=decoded%20query'
            connections = []
            # More long-lived connections than the two HTTP worker threads.
            for _ in range(12):
                ws = sockets.enter_context(connect(url, origin=origin, additional_headers={'Cookie':'proof=valid'}, open_timeout=5, close_timeout=1))
                assert json.loads(ws.recv(timeout=5)) == {'room':'room', 'query':'decoded query'}
                connections.append(ws)
            assert client.get('/api/health').status_code == 200
            assert client.post('/json', json={'title':'Потоковый JSON'}).json() == {'title':'Потоковый JSON'}
            assert client.post('/json', json={}).status_code == 422
            connections[-1].send('text');assert json.loads(connections[-1].recv(timeout=3)) == {'echo':'text'}
            for headers, allowed_origin in [({},origin), ({'Cookie':'proof=valid'},'https://wrong.example')]:
                with pytest.raises(InvalidStatus) as rejected:
                    with connect(url, origin=allowed_origin, additional_headers=headers, open_timeout=3):pass
                assert rejected.value.response.status_code == 403
            with connect(url, origin=origin, additional_headers={'Cookie':'proof=valid'}, close_timeout=1) as large:
                large.recv(timeout=3)
                with pytest.raises(ConnectionClosed) as oversized:
                    large.send('x' * 3000000)
                    large.recv(timeout=5)
                assert oversized.value.rcvd.code == 1009
            data = b'bounded upload\x00' * 200000
            response = client.post('/upload', content=(data[n:n+32768] for n in range(0,len(data),32768)))
            assert response.status_code == 200
            assert response.json() == {'size':len(data), 'sha256':hashlib.sha256(data).hexdigest()}
            raw = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            raw.request('GET','/download');download = raw.getresponse();assert download.status == 200
            assert download.read(16) == b'x'*16
            assert client.get('/api/health').json()['leases'] == 1
            download.close();raw.close()
            wait_for(lambda: (root/'stream-closed').exists())
            wait_for(lambda: client.get('/api/health').json()['leases'] == 0)
            # Navigation can cancel a download while the route still prepares
            # its headers. Its eventual response must release the storage gate.
            early = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            early.request('GET', '/slow-download')
            wait_for(lambda: (root/'slow-started').exists())
            early.close()
            wait_for(lambda: (root/'slow-closed').exists())
            wait_for(lambda: client.get('/api/health').json()['leases'] == 0)
            (root/'revoked').touch()
            with pytest.raises(ConnectionClosed):connections[0].recv(timeout=5)
            (root/'revoked').unlink()
            with connect(url, origin=origin, additional_headers={'Cookie':'proof=valid'}, close_timeout=1) as draining:
                draining.recv(timeout=3)
                os.killpg(process.pid, signal.SIGTERM)
                with pytest.raises(ConnectionClosed) as shutdown:
                    draining.recv(timeout=5)
                assert shutdown.value.rcvd.code == 1001
    finally:
        if process.poll() is None:os.killpg(process.pid, signal.SIGTERM)
        try:process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL);process.wait()
        log.close()
    assert process.returncode == 0, (root/'server.log').read_text()[-4000:]
    assert (root/'lifespan-closed').exists()
