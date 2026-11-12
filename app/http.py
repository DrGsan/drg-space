"""Flask HTTP boundary with explicit Pydantic binding.

Business modules share one persistent asyncio loop for room state, retention and
storage coordination. Production uses the shared ASGI loop for WebSockets and
bounded Flask threads for HTTP; WSGI tests retain the Flask-Sock compatibility path.
"""
from app.i18n import message as localized_message
import asyncio
import atexit
import inspect
import json
import mimetypes
from decimal import Decimal
from datetime import date, datetime
import re
import threading
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import parse_qsl

from flask import Blueprint, Flask, request as flask_request
from flask import Response as FlaskResponse
from flask_sock import Sock
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo
from simple_websocket import ConnectionClosed
from werkzeug.exceptions import HTTPException as WerkzeugError
from werkzeug.datastructures import Headers, MultiDict
from werkzeug.http import parse_cookie


class HTTPError(Exception):
    def __init__(self, status_code, detail, headers=None):
        super().__init__(detail)
        self.status_code, self.detail, self.headers = status_code, detail, headers or {}


class WebSocketDisconnect(Exception):
    def __init__(self, code=1000):
        self.code = code
        super().__init__(f'WebSocket closed: {code}')


class Request:
    def __init__(self, source):
        if isinstance(source, dict):
            self.headers = Headers([(k.decode(), v.decode('latin-1')) for k, v in source.get('headers', [])])
            self.cookies = parse_cookie(self.headers.get('Cookie', ''))
            self.args = MultiDict(parse_qsl(source.get('query_string', b'').decode('latin-1'), keep_blank_values=True))
            self.method = source.get('method', 'GET')
            self.url = SimpleNamespace(path=source.get('path', '/'))
            peer = source.get('client')
            self.client = SimpleNamespace(host=peer[0], port=peer[1]) if peer else None
            self.source = None
        else:
            self.source = source
            self.headers, self.cookies, self.args = source.headers, source.cookies, source.args
            self.method = source.method
            self.url = SimpleNamespace(path=source.path)
            self.client = SimpleNamespace(host=source.remote_addr or 'unknown', port=source.environ.get('REMOTE_PORT', 0))

    async def stream(self):
        # Read a bounded chunk in an I/O thread; an upload must not block rooms.
        while True:
            chunk = await asyncio.to_thread(self.source.stream.read, 1024 * 1024)
            if not chunk:
                break
            yield chunk


class Response(FlaskResponse):
    def __init__(self, content=b'', status_code=200, headers=None, media_type=None, **kwargs):
        super().__init__(content, status=status_code, headers=headers, mimetype=media_type, **kwargs)


def json_value(value):
    if isinstance(value, Decimal):return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (date, datetime)):return value.isoformat()
    if isinstance(value, BaseModel):return value.model_dump(mode='json')
    raise TypeError('Unsupported JSON response value: '+type(value).__name__)


class JSONResponse(Response):
    def __init__(self, content, status_code=200, headers=None):
        from app.i18n import localize_error
        if status_code >= 400:
            content = localize_error(content, status_code)
        super().__init__(json.dumps(content, ensure_ascii=False, separators=(',', ':'), default=json_value, allow_nan=False), status_code, headers, 'application/json')
        self.headers['Content-Type'] = 'application/json; charset=utf-8'


class BackgroundTask:
    def __init__(self, function, *args, **kwargs):
        self.function, self.args, self.kwargs = function, args, kwargs

    def __call__(self):
        self.function(*self.args, **self.kwargs)


class StreamingResponse(Response):
    def __init__(self, content, status_code=200, headers=None, media_type=None, background=None):
        super().__init__(content, status_code, headers, media_type)
        if background:
            self.call_on_close(background)


class FileResponse(StreamingResponse):
    def __init__(self, path, status_code=200, headers=None, media_type=None, filename=None, background=None):
        path = Path(path)
        if not path.is_file():
            raise HTTPError(404, localized_message('fileNotFound'))
        def chunks():
            with path.open('rb') as file:
                while body := file.read(1024 * 1024):
                    yield body
        super().__init__(chunks(), status_code, headers, media_type or mimetypes.guess_type(path.name)[0] or 'application/octet-stream', background)
        self.headers['Content-Length'] = str(path.stat().st_size)
        if filename:
            from urllib.parse import quote
            self.headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(filename)


def Query(default=..., **constraints):
    return Field(default=default, **constraints)


class AsyncRuntime:
    """Created after worker fork; all async services share its event loop."""
    def __init__(self):
        self.lock = threading.Lock()
        self.loop = None
        self.thread = None
        self.external = False

    def attach(self, loop):
        """Share the server loop; never create a thread per long-lived socket."""
        with self.lock:
            if self.loop is not None and self.loop is not loop:
                raise RuntimeError('Application services already use another event loop.')
            self.loop, self.external = loop, True

    def start(self):
        with self.lock:
            if self.loop is not None:
                return
            loop = asyncio.new_event_loop()
            ready = threading.Event()
            def serve():
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()
            self.loop = loop
            self.thread = threading.Thread(target=serve, name='drg-async-services', daemon=True)
            self.thread.start()
            ready.wait()

    def submit(self, coroutine):
        self.start()
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def run(self, coroutine):
        return self.submit(coroutine).result()

    def stop(self):
        if self.loop is None:
            return
        if self.external:
            # The server owns this loop and its request/lifespan tasks.
            self.loop, self.external = None, False
            return
        async def cancel():
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        self.run(cancel())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        self.loop = None


class WebSocket(Request):
    def __init__(self, source, transport, runtime):
        super().__init__(source)
        self.transport, self.runtime = transport, runtime
        self.security_policy = getattr(source.environ.get('drg.application'), 'security_policy', None)
        self.incoming = asyncio.Queue(maxsize=256)
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self._read, name='drg-websocket-reader', daemon=True)
        self.reader.start()

    def _deliver(self, message):
        if self.incoming.full():
            self.closed.set()
            if self.transport.connected:self.transport.close(reason=4429)
            self.incoming.get_nowait()
            self.incoming.put_nowait(WebSocketDisconnect(4429))
            return
        self.incoming.put_nowait(message)

    def _read(self):
        try:
            while not self.closed.is_set():
                message = self.transport.receive(timeout=0.25)
                if message is not None:
                    self.runtime.loop.call_soon_threadsafe(self._deliver, message)
        except ConnectionClosed as error:
            self.runtime.loop.call_soon_threadsafe(self._deliver, WebSocketDisconnect(error.reason or 1000))
        finally:self.closed.set()

    async def accept(self):
        pass  # Flask-Sock owns the WebSocket handshake.

    async def receive_text(self):
        message = await self.incoming.get()
        if isinstance(message, WebSocketDisconnect):raise message
        if self.security_policy:
            await asyncio.to_thread(self.security_policy.check, self)
        if not isinstance(message, str):raise HTTPError(422, localized_message('aTextMessageIsRequired'))
        return message

    async def send_json(self, value):
        if self.security_policy:
            await asyncio.to_thread(self.security_policy.check, self)
        try:
            await asyncio.to_thread(self.transport.send, json.dumps(value, ensure_ascii=False))
        except ConnectionClosed as error:
            raise WebSocketDisconnect(error.reason or 1000) from None

    async def close(self, code=1000):
        self.closed.set()
        if self.transport.connected:
            await asyncio.to_thread(self.transport.close, reason=code)
        await asyncio.to_thread(self.reader.join, 1)


_endpoint_ids = count()


def flask_path(path):
    return re.sub(r'\{([a-zA-Z_][a-zA-Z0-9_]*)(?::(path))?\}', lambda m: '<'+('path:' if m[2] else '')+m[1]+'>', path)


def bind(function, request, response, path_values):
    values = {}
    errors = []
    for name, parameter in inspect.signature(function).parameters.items():
        annotation, default = parameter.annotation, parameter.default
        if annotation is Request:
            values[name] = request
            continue
        if annotation is Response:
            values[name] = response
            continue
        model = inspect.isclass(annotation) and issubclass(annotation, BaseModel)
        location = 'body' if model else 'path' if name in path_values else 'query'
        try:
            if model:
                raw = request.source.get_json(silent=True)
                if raw is None:
                    raise HTTPError(422, localized_message('invalidJsonBody'))
                values[name] = annotation.model_validate(raw)
                continue
            if name in path_values:
                raw = path_values[name]
            elif name in request.args:
                raw = request.args.getlist(name)[-1]
            elif isinstance(default, FieldInfo):
                if default.is_required():
                    errors.append({'type':'missing','loc':[location,name],'msg':'Field required'})
                    continue
                raw = default.get_default()
            elif default is not inspect.Parameter.empty:
                raw = default
            else:
                errors.append({'type':'missing','loc':[location,name],'msg':'Field required'})
                continue
            field_type = str if annotation is inspect.Parameter.empty else annotation
            if isinstance(default, FieldInfo):
                field_type = Annotated[field_type, default]
            values[name] = TypeAdapter(field_type).validate_python(raw)
        except ValidationError as error:
            for item in error.errors(include_context=False, include_url=False, include_input=False):
                item['loc'] = [location, *item['loc']] if model else [location, name, *item['loc']]
                errors.append(item)
    if errors:
        raise HTTPError(422, errors)
    return values


class TypedRoutes:
    def route(self, rule, **options):
        if options.get('websocket'):
            return Flask.route(self, rule, **options)
        def decorate(function):
            endpoint = 'http_'+str(next(_endpoint_ids))
            def view(**path_values):
                app = self if isinstance(self, Application) else flask_request.environ['drg.application']
                req = Request(flask_request._get_current_object())
                return app.runtime.run(app.handle(req, function, path_values))
            self.add_url_rule(flask_path(rule), endpoint, view, **options)
            return function
        return decorate

    def websocket(self, rule):
        def decorate(function):
            self.socket_routes.append((flask_path(rule), function))
            if isinstance(self, Application):
                self.register_socket(*self.socket_routes[-1])
            return function
        return decorate


class Router(TypedRoutes, Blueprint):
    def __init__(self):
        super().__init__('module_'+str(next(_endpoint_ids)), __name__)
        self.socket_routes = []


class Application(TypedRoutes, Flask):
    def __init__(self, name, lifespan=None):
        super().__init__(name, static_folder=None)
        self.runtime = AsyncRuntime()
        self.socket_routes, self.http_middlewares, self.error_handlers = [], [], {}
        self.storage_middleware = None
        self.service_lifespan, self.service_context = lifespan, None
        self.service_lock = threading.Lock()
        self.config['SOCK_SERVER_OPTIONS'] = {'max_message_size': 2100000, 'ping_interval':25}
        self.sock = Sock(self)
        self._asgi_gateway = None
        atexit.register(self.stop_services)

    def __call__(self, *args):
        if len(args) == 3:
            if self._asgi_gateway is None:
                from app.asgi import Gateway
                self._asgi_gateway = Gateway(self)
            return self._asgi_gateway(*args)
        return super().__call__(*args)

    def wsgi_app(self, environ, start_response):
        environ['drg.application'] = self
        return super().wsgi_app(environ, start_response)

    def dispatch_request(self):
        # Flask route lookup errors also pass through the security headers.
        if flask_request.routing_exception is not None:
            error = flask_request.routing_exception
            def invalid():
                raise error
            req = Request(flask_request._get_current_object())
            return self.runtime.run(self.handle(req, invalid, {}))
        if flask_request.method == 'OPTIONS' and flask_request.url_rule.provide_automatic_options:
            response = self.make_default_options_response()
            req = Request(flask_request._get_current_object())
            return self.runtime.run(self.handle(req, lambda: response, {}))
        return super().dispatch_request()

    def include_router(self, router):
        self.register_blueprint(router)
        for rule, function in router.socket_routes:
            self.register_socket(rule, function)

    def register_socket(self, rule, function):
        if (rule, function) not in self.socket_routes:
            self.socket_routes.append((rule, function))
        endpoint='socket_'+str(next(_endpoint_ids))
        @self.before_request
        def authorize_upgrade():
            if flask_request.endpoint != endpoint:return None
            req=Request(flask_request._get_current_object())
            # Capture route values before crossing the Flask context boundary.
            values=dict(flask_request.view_args or {})
            async def preflight():
                policy = getattr(self, 'security_policy', None)
                if policy:
                    await asyncio.to_thread(policy.check, req)
                if req.headers.get('origin','').rstrip('/') != getattr(function,'origin',None):
                    if policy:
                        await asyncio.to_thread(policy.monitor.record, req, 'origin_rejected', outcome='blocked')
                    raise HTTPError(403,'Origin rejected')
                authorize=getattr(function,'authorize',None)
                if authorize:
                    if self.storage_middleware:
                        async with self.storage_middleware.gate.shared():
                            await asyncio.to_thread(authorize,req,**values)
                    else:await asyncio.to_thread(authorize,req,**values)
            try:self.runtime.run(preflight())
            except Exception as error:return self.runtime.run(self.error_response(req,error))
            return None
        def serve(transport, **values):
            self.runtime.start()
            ws = WebSocket(flask_request._get_current_object(), transport, self.runtime)
            async def run():
                async def watch():
                    while not ws.closed.is_set():
                        await asyncio.sleep(1)
                        if ws.security_policy:
                            try:
                                if self.storage_middleware:
                                    async with self.storage_middleware.gate.shared_wait():
                                        await asyncio.to_thread(ws.security_policy.check, ws)
                                else:
                                    await asyncio.to_thread(ws.security_policy.check, ws)
                            except Exception:
                                await ws.close(code=4503)
                                ws._deliver(WebSocketDisconnect(4503))
                                return
                guard = asyncio.create_task(watch())
                try:
                    await function(ws, **values)
                finally:
                    guard.cancel()
                    await asyncio.gather(guard, return_exceptions=True)
            try:self.runtime.run(run())
            finally:self.runtime.run(ws.close())
        self.sock.route(rule, endpoint=endpoint)(serve)

    def middleware(self, kind):
        assert kind == 'http'
        def decorate(function):
            self.http_middlewares.append(function)
            return function
        return decorate

    def add_middleware(self, middleware, **kwargs):
        self.storage_middleware = middleware(**kwargs)

    def exception_handler(self, exception):
        def decorate(function):
            self.error_handlers[exception] = function
            return function
        return decorate

    async def error_response(self, request, error):
        for kind, handler in self.error_handlers.items():
            if isinstance(error, kind):
                return await handler(request, error)
        if isinstance(error, HTTPError):
            return JSONResponse({'detail':error.detail}, error.status_code, error.headers)
        if isinstance(error, WerkzeugError):
            return JSONResponse({'detail':error.description}, error.code or 500)
        self.logger.error('Request failed', exc_info=(type(error), error, error.__traceback__))
        return JSONResponse({'detail':'Ошибка сервера.'}, 500)

    async def handle(self, request, function, path_values):
        async def invoke(req):
            response = Response()
            try:
                # Flask's JSON/form binding can read the streaming WSGI input.
                # The ASGI loop must remain free to supply those body chunks.
                from app.storage import blocking
                values = await blocking(bind, function, req, response, path_values)
                if inspect.iscoroutinefunction(function):
                    result = await function(**values)
                else:
                    result = await blocking(function, **values)
                if not isinstance(result, FlaskResponse):
                    result = JSONResponse(result)
                for key, value in response.headers:
                    if key.lower() == 'set-cookie':
                        result.headers.add(key, value)
                return result
            except Exception as error:
                return await self.error_response(req, error)
        call = invoke
        if self.storage_middleware:
            async def guarded(req):
                return await self.storage_middleware.handle(req, invoke, self.runtime)
            call = guarded
        for middleware in self.http_middlewares:
            previous = call
            async def wrapped(req, middleware=middleware, previous=previous):
                return await middleware(req, previous)
            call = wrapped
        from app.i18n import current_language, negotiate_language
        from app.i18n import normalize_language
        selected = (normalize_language(getattr(request, 'headers', {}).get('X-DrG-Language'))
                    or normalize_language(getattr(request, 'cookies', {}).get('drg_language'))
                    or negotiate_language(getattr(request, 'headers', {}).get('Accept-Language', '')))
        token = current_language.set(selected)
        try:
            response = await call(request)
            response.headers['Content-Language'] = current_language.get()
            return response
        finally:
            current_language.reset(token)

    def start_services(self):
        with self.service_lock:
            if self.service_context is not None or self.service_lifespan is None:
                return
            self.service_context = self.service_lifespan(self)
            self.runtime.run(self.service_context.__aenter__())

    def stop_services(self):
        with self.service_lock:
            if self.service_context is not None:
                self.runtime.run(self.service_context.__aexit__(None, None, None))
                self.service_context = None
            self.runtime.stop()
