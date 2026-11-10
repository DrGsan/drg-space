"""Workers share room invalidations and coordinate storage through one volume."""
import os
from dotenv import load_dotenv
load_dotenv('.env')

bind = '0.0.0.0:8000'
def bounded_env(name,default,maximum):
    raw=os.getenv(name,str(default))
    if not raw.isdecimal() or not 1<=int(raw)<=maximum:raise RuntimeError(f'{name} must be between 1 and {maximum}.')
    return int(raw)

workers = bounded_env('GUNICORN_WORKERS',1,16)
worker_class = 'gthread'
threads = bounded_env('GUNICORN_THREADS',64,128)
keepalive = 15
# Upload/archive duration is bounded by the app/proxy, not a 30-second worker kill.
timeout = 0
graceful_timeout = 60
# Preserve the socket peer; app.protection validates trusted proxy chains itself.
forwarded_allow_ips = ''
preload_app = False

def on_starting(server):
    if server.cfg.preload_app:raise RuntimeError('Preloading the app is unsupported; initialize services after fork.')

def post_worker_init(worker):
    worker.wsgi.start_services()

def worker_exit(server, worker):
    if hasattr(worker, 'wsgi'):
        worker.wsgi.stop_services()
