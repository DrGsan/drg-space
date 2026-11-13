"""Async sockets and bounded, streaming Flask HTTP on the same port and loop.

No request bodies are spooled to disk. A disconnected response wakes blocked
WSGI producers so their iterators close and release storage snapshot leases.
"""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import os
import threading

from a2wsgi import WSGIMiddleware
from a2wsgi.wsgi import WSGIResponder
from werkzeug.exceptions import HTTPException
from werkzeug.routing import Map, Rule

from app.http import HTTPError, Request, WebSocket, WebSocketDisconnect
from app.i18n import message as localized_message


class ClientGone(OSError):
    pass


class StreamingResponder(WSGIResponder):
    def __init__(self, *args, gone, **kwargs):
        super().__init__(*args, **kwargs)
        self.gone = gone
        self.finished = threading.Event()

    def start_response(self, status, response_headers, exc_info=None):
        try:
            return super().start_response(status, response_headers, exc_info)
        except ClientGone:
            # Flask calls start_response before returning its ClosingIterator.
            # Let it return that iterator so WSGIResponder.wsgi can close the
            # response (and its storage lease) when the first body send fails.
            return lambda chunk: self.send({'type':'http.response.body', 'body':chunk, 'more_body':True})

    def send(self, message):
        if self.gone.is_set() or self.finished.is_set():
            raise ClientGone()
        future = asyncio.run_coroutine_threadsafe(self.send_queue.put(message), self.loop)
        while True:
            try:
                return future.result(timeout=.1)
            except FutureTimeout:
                if self.gone.is_set() or self.finished.is_set():
                    future.cancel()
                    raise ClientGone() from None

    async def sender(self, send):
        try:
            await super().sender(send)
        finally:
            self.finished.set()


class StreamingHTTP(WSGIMiddleware):
    async def __call__(self, scope, receive, send):
        incoming = asyncio.Queue(maxsize=1)
        gone = asyncio.Event()

        async def pump():
            try:
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    await incoming.put(message)
            finally:
                gone.set()

        async def body():
            if gone.is_set():
                return {'type': 'http.disconnect'}
            item = asyncio.create_task(incoming.get())
            disconnected = asyncio.create_task(gone.wait())
            try:
                done, _ = await asyncio.wait((item, disconnected), return_when=asyncio.FIRST_COMPLETED)
                return item.result() if item in done else {'type': 'http.disconnect'}
            finally:
                for task in (item, disconnected):
                    task.cancel()
                await asyncio.gather(item, disconnected, return_exceptions=True)

        reader = asyncio.create_task(pump())
        responder = StreamingResponder(self.app, self.executor, self.send_queue_size, gone=gone)
        try:
            await responder(scope, body, send)
        except (ClientGone, ConnectionError):
            if not gone.is_set():
                raise
        finally:
            responder.finished.set()
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


class Socket(WebSocket):
    def __init__(self, scope, receive, send, policy):
        Request.__init__(self, scope)
        self.receive, self.send = receive, send
        self.security_policy = policy
        self.closed = asyncio.Event()
        self.accepted = False
        self._sending = asyncio.Lock()

    async def accept(self):
        if not self.accepted:
            await self.send({'type': 'websocket.accept'})
            self.accepted = True

    async def receive_text(self):
        message = await self.receive()
        if message['type'] == 'websocket.disconnect':
            self.closed.set()
            raise WebSocketDisconnect(message.get('code', 1000))
        if self.security_policy:
            await asyncio.to_thread(self.security_policy.check, self)
        text = message.get('text')
        if not isinstance(text, str):
            raise HTTPError(422, localized_message('aTextMessageIsRequired'))
        if len(text.encode('utf-8')) > 2100000:
            await self.close(1009)
            raise WebSocketDisconnect(1009)
        return text

    async def send_json(self, value):
        import json
        if self.security_policy:
            await asyncio.to_thread(self.security_policy.check, self)
        async with self._sending:
            try:
                await self.send({'type': 'websocket.send', 'text': json.dumps(value, ensure_ascii=False)})
            except (OSError, RuntimeError):
                self.closed.set()
                raise WebSocketDisconnect() from None

    async def close(self, code=1000):
        if self.closed.is_set():
            return
        self.closed.set()
        async with self._sending:
            try:
                await self.send({'type': 'websocket.close', 'code': code})
            except (OSError, RuntimeError):
                pass


class Gateway:
    def __init__(self, app):
        self.app = app
        raw = os.getenv('GUNICORN_THREADS', '64')
        if not raw.isdecimal() or not 1 <= int(raw) <= 128:
            raise RuntimeError('GUNICORN_THREADS must be between 1 and 128.')
        self.http = StreamingHTTP(self.wsgi, workers=int(raw), send_queue_size=1)
        self.routes = Map([Rule(rule, endpoint=function) for rule, function in app.socket_routes])
        self.sockets, self.socket_tasks = set(), set()
        self.draining = False

    async def drain(self):
        """Let clients reconnect and release room leases before services stop."""
        self.draining = True
        await asyncio.gather(*(ws.close(1001) for ws in tuple(self.sockets)), return_exceptions=True)
        if self.socket_tasks:
            await asyncio.wait(tuple(self.socket_tasks), timeout=5)

    def wsgi(self, environ, start_response):
        # ASGI already provides decoded, bounded body chunks, including chunked
        # uploads without Content-Length. Werkzeug may read until end-of-stream.
        environ['wsgi.input_terminated'] = True
        return self.app.wsgi_app(environ, start_response)

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            return await self.lifespan(receive, send)
        if scope['type'] == 'http':
            return await self.http(scope, receive, send)
        if scope['type'] == 'websocket':
            return await self.socket(scope, receive, send)
        raise RuntimeError('Unsupported ASGI scope.')

    async def lifespan(self, receive, send):
        message = await receive()
        if message['type'] != 'lifespan.startup':
            raise RuntimeError('Expected application startup.')
        try:
            self.app.runtime.attach(asyncio.get_running_loop())
            await asyncio.to_thread(self.app.start_services)
        except Exception:
            self.app.logger.exception('Application startup failed')
            await send({'type': 'lifespan.startup.failed', 'message': 'Application startup failed.'})
            return
        await send({'type': 'lifespan.startup.complete'})
        try:
            message = await receive()
            if message['type'] != 'lifespan.shutdown':
                raise RuntimeError('Expected application shutdown.')
            await asyncio.to_thread(self.app.stop_services)
            await asyncio.to_thread(self.http.executor.shutdown, wait=True, cancel_futures=True)
        except Exception:
            self.app.logger.exception('Application shutdown failed')
            await send({'type': 'lifespan.shutdown.failed', 'message': 'Application shutdown failed.'})
        else:
            await send({'type': 'lifespan.shutdown.complete'})

    async def socket(self, scope, receive, send):
        first = await receive()
        if first['type'] != 'websocket.connect':
            return
        ws = Socket(scope, receive, send, getattr(self.app, 'security_policy', None))
        task = asyncio.current_task()
        self.sockets.add(ws);self.socket_tasks.add(task)
        watcher = None
        try:
            try:
                function, values = self.routes.bind('localhost').match(scope['path'], method='GET')
            except HTTPException:
                await ws.close(4404)
                return
            if ws.security_policy:
                await asyncio.to_thread(ws.security_policy.check, ws)
            if ws.headers.get('origin', '').rstrip('/') != getattr(function, 'origin', None):
                if ws.security_policy:
                    await asyncio.to_thread(ws.security_policy.monitor.record, ws, 'origin_rejected', outcome='blocked')
                await ws.close(4403)
                return
            authorize = getattr(function, 'authorize', None)
            if authorize:
                if self.app.storage_middleware:
                    async with self.app.storage_middleware.gate.shared():
                        await asyncio.to_thread(authorize, ws, **values)
                else:
                    await asyncio.to_thread(authorize, ws, **values)

            # Match Flask-Sock: after HTTP authorization the upgrade is complete.
            # Handler-level limits/errors retain their WebSocket close codes.
            await ws.accept()
            if self.draining:
                await ws.close(1012)
                return

            async def watch():
                while not ws.closed.is_set():
                    await asyncio.sleep(1)
                    if ws.security_policy:
                        try:
                            if self.app.storage_middleware:
                                async with self.app.storage_middleware.gate.shared_wait():
                                    await asyncio.to_thread(ws.security_policy.check, ws)
                            else:
                                await asyncio.to_thread(ws.security_policy.check, ws)
                        except HTTPError:
                            await ws.close(4403)
                            return
            watcher = asyncio.create_task(watch())
            await function(ws, **values)
        except (HTTPError, WebSocketDisconnect):
            await ws.close(4403)
        except Exception:
            self.app.logger.exception('WebSocket handler failed')
            await ws.close(1011)
        finally:
            if watcher:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            await ws.close()
            self.sockets.discard(ws);self.socket_tasks.discard(task)
