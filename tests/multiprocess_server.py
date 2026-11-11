"""Test-only instrumentation around the real application and Gunicorn hooks.

The production Dockerfile copies app/, not tests/. Each subprocess must use a
fresh test schema and data directory; importing this module in production fails.
"""
import json
import os
import re
import time
from pathlib import Path

if (os.getenv('DRG_MULTIPROCESS_TEST') != '1'
        or not re.fullmatch(r'drg_gunicorn_test_[a-f0-9]{20}', os.getenv('DATABASE_SCHEMA', ''))
        or not Path(os.getenv('DATA_DIR', '')).name.startswith('drg-gunicorn-test-')):
    raise RuntimeError('The multiprocess test wrapper requires isolated test storage.')

# Scope reconnect tests to these listeners. No production connection can match
# the generated application_name, even if the test uses the same PostgreSQL DB.
import psycopg

_connect = psycopg.AsyncConnection.connect


@classmethod
async def _isolated_connect(cls, *args, **kwargs):
    if kwargs.get('application_name', '').startswith('drg-space-invalidations'):
        kwargs['application_name'] = (
            'drg-gunicorn-test-' + os.environ['DATABASE_SCHEMA'].removeprefix('drg_gunicorn_test_')
            + ':' + str(os.getpid()))
    return await _connect(*args, **kwargs)


psycopg.AsyncConnection.connect = _isolated_connect

from app import main

app = main.app


def _pausable(callback):
    async def invoke():
        if not (main.DATA / '__test_reconcile_hold').exists():
            await callback()
    return invoke


# A short barrier models delayed/missed worker invalidations deterministically;
# durable reconciliation must still send foreign edits after local ACKs.
main.process_coordinator._callbacks = {
    _pausable(callback) for callback in main.process_coordinator._callbacks}


@app.get('/__test_worker_pid')
def worker_pid():
    return {'pid': os.getpid()}


@app.post('/__test_cleanup_backup_temps')
def cleanup_backup_temps():
    main.admin_router.backups.cleanup_temps()
    return {'pid': os.getpid()}


_encrypt = main.admin_router.backups.encrypt


def _held_encrypt(directory, metadata, password):
    """Hold a real, finished encrypted export before returning its response."""
    output = _encrypt(directory, metadata, password)
    if (main.DATA / '__test_backup_hold').exists():
        ready = main.DATA / '__test_backup_ready'
        temporary = ready.with_suffix('.tmp')
        temporary.write_text(json.dumps({'pid': os.getpid(), 'directory': str(directory)}))
        temporary.replace(ready)
        deadline = time.monotonic() + 40
        while not (main.DATA / '__test_backup_release').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('Timed out waiting for the isolated backup test.')
            time.sleep(0.02)
    return output


main.admin_router.backups.encrypt = _held_encrypt


from app.http import Request

@app.post('/__test_notification')
def notification(request:Request):
    from app.notifications import notify
    session=main.ready(request)
    with main.db() as c:
        notify(c,session['user_id'],'session',{'event':'isolated-test'},main.now())
        nid=c.execute('SELECT MAX(id) FROM notifications WHERE user_id=?',(session['user_id'],)).fetchone()[0]
    return {'id':nid}


@app.get('/__test_redis_stats')
def redis_stats(request:Request):
    main.ready(request)
    return {'pid':os.getpid(),**main.database.redis_cache.stats()}
