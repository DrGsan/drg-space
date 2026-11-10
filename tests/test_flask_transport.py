"""Flask-specific HTTP and streaming concurrency regressions."""
import asyncio
import threading
from types import SimpleNamespace

from flask import Flask
from pydantic import BaseModel
from app.http import Application, JSONResponse, Query, Request, Response, StreamingResponse
from app.storage import StorageGate, StorageMiddleware
from tests.http_client import TestClient
from tests.test_security import reset
from app import main as m


def test_native_flask_and_security_headers_cover_head_options_and_routing_errors():
    assert isinstance(m.app, Flask)
    client=TestClient(m.app)
    for method,path,expected in [('GET','/api/health',200),('HEAD','/api/health',200),('OPTIONS','/api/health',200),('GET','/api/missing-route',404),('PUT','/api/health',405)]:
        response=client.request(method,path)
        assert response.status_code==expected
        assert response.headers['x-content-type-options']=='nosniff'
        assert response.headers['cache-control']=='no-store'
        assert response.headers['x-frame-options']=='DENY'


def test_flask_query_body_validation_and_response_cookie_binding():
    app=Application('validation-test')
    class Input(BaseModel):value:int
    @app.post('/items/{item}')
    def item(item:int,data:Input,request:Request,response:Response,limit:int=Query(default=3,ge=1,le=5)):
        response.set_cookie('proof','ok',httponly=True,samesite='strict')
        return {'item':item,'value':data.value,'limit':limit,'peer':request.client.host}
    try:
        client=TestClient(app)
        success=client.post('/items/2?limit=4',json={'value':7})
        assert success.json()=={'item':2,'value':7,'limit':4,'peer':'testclient'}
        assert 'HttpOnly' in success.headers['set-cookie']
        for path,data in [('/items/x',{'value':1}),('/items/1?limit=0',{'value':1}),('/items/1?limit=6',{'value':1}),('/items/1',{'value':'bad'})]:
            assert client.post(path,json=data).status_code==422
        assert client.post('/items/1',json={'value':'bad'}).json()['detail'][0]['loc']==['body','value']
        assert client.post('/items/1',content=b'{broken',headers={'content-type':'application/json'}).status_code==422
    finally:app.stop_services()


def test_streaming_download_keeps_snapshot_exclusion_until_closed():
    app=Application('stream-test');gate=StorageGate()
    app.add_middleware(StorageMiddleware,gate=gate)
    @app.get('/download')
    def download():return StreamingResponse(iter((b'first',b'second')))
    native=app.test_client()
    response=native.get('/download',buffered=False)
    entered=threading.Event()
    async def snapshot():
        async with gate.exclusive():entered.set()
    pending=app.runtime.submit(snapshot())
    async def barrier():await asyncio.sleep(0)
    app.runtime.run(barrier())
    try:
        assert gate.active==1 and gate.exclusive_pending and not entered.is_set()
        blocked=native.get('/download')
        assert blocked.status_code==503
        response.close()
        pending.result(timeout=3)
        assert entered.is_set() and gate.active==0 and not gate.exclusive_pending
    finally:
        response.close()
        app.stop_services()


def test_postgres_decimal_aggregates_keep_numeric_json_types():
    from decimal import Decimal
    response=JSONResponse({'bytes':Decimal('1048576'),'average':Decimal('1.25')})
    result=response.get_json()
    assert result=={'bytes':1048576,'average':1.25}
    assert type(result['bytes']) is int and type(result['average']) is float


def test_json_error_declares_utf8_for_direct_browser_navigation():
    message='Сеанс истёк или токен недействителен. Войдите снова.'
    response=JSONResponse({'detail':message},401)
    assert response.headers['Content-Type']=='application/json; charset=utf-8'
    assert message in response.data.decode('utf-8')
