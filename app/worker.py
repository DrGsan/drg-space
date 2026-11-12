"""Native ASGI worker with application socket draining on graceful shutdown."""
from gunicorn.workers.gasgi import ASGIWorker


class Worker(ASGIWorker):
    async def _shutdown(self):
        gateway = getattr(getattr(self, 'asgi', None), '_asgi_gateway', None)
        if gateway is not None:
            await gateway.drain()
        await super()._shutdown()
