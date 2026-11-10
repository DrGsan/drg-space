"""HTTP through Flask's WSGI app; WebSockets through a real Werkzeug socket."""
import json
import io
import sys
import threading
from contextlib import contextmanager
from weakref import WeakKeyDictionary

import httpx
from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from werkzeug.serving import make_server
from app.http import WebSocketDisconnect

_servers = WeakKeyDictionary()
_server_lock = threading.Lock()


def websocket_server(app):
    with _server_lock:
        if app not in _servers:
            server = make_server('127.0.0.1', 0, app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            _servers[app] = server
        return _servers[app]


class SocketSession:
    def __init__(self, socket):
        self.socket = socket

    def send_json(self, data):
        try:self.socket.send(json.dumps(data))
        except ConnectionClosed as error:raise WebSocketDisconnect(error.rcvd.code if error.rcvd else 1006) from None

    def receive_json(self):
        try:return json.loads(self.socket.recv(timeout=10))
        except ConnectionClosed as error:raise WebSocketDisconnect(error.rcvd.code if error.rcvd else 1006) from None


class FlaskTransport(httpx.BaseTransport):
    def __init__(self,app,remote_addr):self.app,self.remote_addr=app,remote_addr

    def handle_request(self,request):
        request.read()
        environ={'wsgi.version':(1,0),'wsgi.url_scheme':request.url.scheme,
                 'wsgi.input':io.BytesIO(request.content),'wsgi.errors':sys.stderr,
                 'wsgi.multithread':True,'wsgi.multiprocess':False,'wsgi.run_once':False,
                 'REQUEST_METHOD':request.method,'SCRIPT_NAME':'','PATH_INFO':request.url.path,
                 'QUERY_STRING':request.url.query.decode('ascii'),'SERVER_NAME':request.url.host,
                 'SERVER_PORT':str(request.url.port or 80),'SERVER_PROTOCOL':'HTTP/1.1',
                 'REMOTE_ADDR':self.remote_addr}
        for key,value in request.headers.raw:
            key=key.decode('ascii').upper().replace('-','_')
            environ[key if key in ('CONTENT_TYPE','CONTENT_LENGTH') else 'HTTP_'+key]=value.decode('latin-1')
        status=[];headers=[]
        def start_response(code,values,exc_info=None):
            if exc_info:raise exc_info[1].with_traceback(exc_info[2])
            status.append(int(code.split()[0]));headers.extend(values)
        iterable=self.app(environ,start_response)
        try:return httpx.Response(status[0],headers=headers,content=b''.join(iterable))
        finally:
            if hasattr(iterable,'close'):iterable.close()



class TestClient(httpx.Client):
    __test__ = False
    def __init__(self, app, client=('testclient', 50000), **kwargs):
        self.application = app
        super().__init__(transport=FlaskTransport(app, client[0]), base_url='http://testserver', **kwargs)

    @contextmanager
    def websocket_connect(self, path, headers=None):
        server = websocket_server(self.application)
        request = self.build_request('GET', path, headers=headers)
        values = dict(request.headers)
        origin = values.pop('origin', None)
        values.pop('host', None)
        try:
            socket = connect(f'ws://127.0.0.1:{server.server_port}{path}', origin=origin, additional_headers=values, open_timeout=5, close_timeout=2, legacy=True)
        except InvalidStatus:
            raise WebSocketDisconnect(4403) from None
        try:
            # Rejected origin/session is closed before the CSRF hello.
            try:socket.recv(timeout=0.05)
            except TimeoutError:pass
            except ConnectionClosed as error:raise WebSocketDisconnect(error.rcvd.code if error.rcvd else 1006) from None
            yield SocketSession(socket)
        finally:
            socket.close()
