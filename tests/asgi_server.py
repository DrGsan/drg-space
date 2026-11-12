"""Guarded real-transport fixture, never copied into the application image."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from pydantic import BaseModel

from app.http import Application, HTTPError, Request, Router, StreamingResponse, WebSocket
from app.storage import StorageGate, StorageMiddleware

root = Path(os.environ['DATA_DIR'])
if os.getenv('DRG_ASGI_TEST') != '1' or not root.name.startswith('asgi-test-'):
    raise RuntimeError('Requires disposable ASGI test storage.')

@asynccontextmanager
async def lifespan(app):
    assert app.runtime.loop is asyncio.get_running_loop()
    yield
    (root / 'lifespan-closed').touch()

app = Application('asgi-transport-test', lifespan=lifespan)
gate = StorageGate(root)
app.add_middleware(StorageMiddleware, gate=gate)

class Policy:
    monitor = SimpleNamespace(record=lambda *args, **kwargs: None)
    def check(self, request):
        assert isinstance(request, WebSocket)
        if (root / 'revoked').exists():
            raise HTTPError(403, 'Revoked test access.')
app.security_policy = Policy()

@app.get('/api/health')
def health():
    return {'leases': gate.active}

class Input(BaseModel):
    title: str

@app.post('/json')
def json_body(data: Input):
    return {'title': data.title}

@app.post('/upload')
async def upload(request: Request):
    digest = hashlib.sha256();size = 0
    async for part in request.stream():
        digest.update(part);size += len(part)
    return {'size': size, 'sha256': digest.hexdigest()}

@app.get('/download')
def download():
    def chunks():
        try:
            while True:
                yield b'x' * 65536
        finally:
            (root / 'stream-closed').touch()
    return StreamingResponse(chunks())

router = Router()

@router.websocket('/probe/{room}')
async def probe(ws, room):
    await ws.accept()
    await ws.send_json({'room': room, 'query': ws.args.get('q')})
    while True:
        await ws.send_json({'echo': await ws.receive_text()})

def authorize(request, room):
    if request.cookies.get('proof') != 'valid':
        raise HTTPError(401, 'Missing test proof.')
probe.authorize = authorize
probe.origin = os.environ['PUBLIC_URL']
app.include_router(router)
