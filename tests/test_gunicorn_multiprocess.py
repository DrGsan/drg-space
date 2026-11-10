"""Real PostgreSQL/Gunicorn transport checks, with sockets on different workers.

Set DRG_TEST_DATABASE_URL to a PostgreSQL database where the test user can create
and drop its own schema. This module never imports app.main in the pytest process,
uses a random dedicated schema, and does not read production environment files.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import time

import httpx
import psycopg
from psycopg import sql
import pyotp
from pycrdt import Doc, Map, XmlElement, XmlFragment, XmlText
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


pytestmark = pytest.mark.skipif(
    not os.getenv('DRG_TEST_DATABASE_URL'), reason='Requires isolated PostgreSQL test access')
ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'Owner-password-123'
SETUP_TOKEN = 'isolated-gunicorn-test-setup'


def eventually(probe, *, timeout=20):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = probe()
            if value:
                return value
        except (OSError, httpx.HTTPError, psycopg.Error) as error:
            last_error = type(error).__name__
        time.sleep(0.05)
    pytest.fail('Isolated Gunicorn condition did not become true' +
                (': ' + last_error if last_error else ''))


def checked(response):
    assert response.status_code == 200, response.text
    return response.json()


class LiveSocket:
    def __init__(self, pid, cid, connection):
        self.pid, self.cid, self.connection = pid, cid, connection
        self.pending = []

    def send(self, data):
        self.connection.send(json.dumps(data))

    def event(self, kind, *, timeout=12, match=lambda event: True):
        kinds = {kind} if isinstance(kind, str) else set(kind)
        deadline = time.monotonic() + timeout
        while True:
            for index, event in enumerate(self.pending):
                if event['type'] in kinds and match(event):
                    return self.pending.pop(index)
            event = json.loads(self.connection.recv(timeout=max(0.01, deadline-time.monotonic())))
            assert event['type'] != 'error', event
            if event['type'] in kinds and match(event):
                return event
            self.pending.append(event)
            assert len(self.pending) < 256, 'Unexpected unbounded socket backlog'

    def close(self):
        self.connection.close()


class Server:
    def __init__(self, process, data, schema, port, database_url):
        self.process, self.data, self.schema, self.port = process, data, schema, port
        self.database_url = database_url
        self.url = 'http://127.0.0.1:' + str(port)
        self.client = httpx.Client(base_url=self.url, headers={'origin': self.url},
                                  timeout=30, trust_env=False)
        self.cid = 1000

    def cookie(self):
        return '; '.join(name + '=' + value for name, value in self.client.cookies.items())

    def pinned_http(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
            try:
                connection.request('GET', '/__test_worker_pid', headers={'Connection': 'keep-alive'})
                response = connection.getresponse()
                body = response.read()
                if response.status == 503:
                    # A sibling initializing/restarting holds the global gate
                    # briefly; wait before pinning a request to its TCP socket.
                    connection.close()
                    time.sleep(0.05)
                    continue
                assert response.status == 200, body.decode()
                pid = json.loads(body)['pid']
                assert connection.sock is not None, 'Gunicorn must keep the TCP connection alive'
                return pid, connection
            except (OSError, http.client.HTTPException):
                connection.close()
                time.sleep(0.05)
        pytest.fail('No worker could provide a persistent HTTP connection')

    def worker_pids(self, *, exclude=()):
        found = set()
        deadline = time.monotonic() + 15
        while len(found) < 3 and time.monotonic() < deadline:
            pid, connection = self.pinned_http()
            connection.close()
            if pid not in exclude:
                found.add(pid)
            if len(found) < 3:
                # Accepting a TCP connection isn't round-robin across workers.
                # Let other accept threads run before sampling again.
                time.sleep(0.02)
        assert len(found) == 3, 'Expected three independently serving Gunicorn workers'
        return found

    def open_socket(self, path, *, only_pids=None):
        deadline = time.monotonic() + 15
        observed = set()
        while time.monotonic() < deadline:
            pid, http_connection = self.pinned_http()
            observed.add(pid)
            if only_pids is not None and pid not in only_pids:
                http_connection.close()
                # A hot request loop can keep waking the same accept worker.
                # Bound the wait by time and yield between unmatched samples.
                time.sleep(0.02)
                continue
            # The upgrade reuses the socket that returned the PID. Gunicorn
            # cannot reassign a keepalive TCP connection to another worker.
            raw_socket, http_connection.sock = http_connection.sock, None
            # websockets clears its own TCP sockets' timeouts, but preserves
            # those of supplied sockets. The HTTP request deadline must not
            # disconnect an otherwise healthy, idle WebSocket in slower CI.
            raw_socket.settimeout(None)
            try:
                connection = connect(self.url.replace('http:', 'ws:') + path,
                                     sock=raw_socket, origin=self.url,
                                     additional_headers={'Cookie': self.cookie()},
                                     proxy=None, ping_interval=None, close_timeout=1,
                                     # LiveSocket owns several simultaneous
                                     # connections and closes each explicitly.
                                     legacy=True, max_size=2100000)
            except BaseException:
                raw_socket.close()
                raise
            self.cid += 1
            peer = LiveSocket(pid, self.cid, connection)
            peer.send({'type': 'auth', 'csrf': self.client.headers['x-csrf-token'],
                       'client_id': self.cid})
            return peer
        pytest.fail('Could not pin a WebSocket to the requested Gunicorn worker; '
                    f'wanted={only_pids}, observed={observed}')

    @contextmanager
    def sockets(self, path, *, count=3):
        peers = []
        try:
            wanted = self.worker_pids()
            for _ in range(min(count, 3)):
                peer = self.open_socket(path, only_pids=wanted)
                wanted.remove(peer.pid)
                peers.append(peer)
            while len(peers) < count:
                peers.append(self.open_socket(path))
            yield peers
        finally:
            for peer in peers:
                peer.close()
            eventually(lambda: not list(self.data.glob('.process-state/*/*.state')))

    def room(self):
        return checked(self.client.post('/api/social/families', json={
            'name': 'Isolated multiworker room'}))['id']

    def listener_pids(self):
        prefix = 'drg-gunicorn-test-' + self.schema.removeprefix('drg_gunicorn_test_') + ':'
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            return {row[0] for row in connection.execute(
                'SELECT pid FROM pg_stat_activity WHERE application_name LIKE %s',
                (prefix + '%',))}

    def terminate_listeners(self):
        prefix = 'drg-gunicorn-test-' + self.schema.removeprefix('drg_gunicorn_test_') + ':'
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            rows = connection.execute(
                'SELECT pid,pg_terminate_backend(pid) FROM pg_stat_activity '
                'WHERE application_name LIKE %s', (prefix + '%',)).fetchall()
        assert len(rows) == 3 and all(terminated for _, terminated in rows)
        return {pid for pid, _ in rows}


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    database_url = os.environ['DRG_TEST_DATABASE_URL']
    schema = 'drg_gunicorn_test_' + secrets.token_hex(10)
    assert re.fullmatch(r'drg_gunicorn_test_[a-f0-9]{20}', schema)
    data = tmp_path_factory.mktemp('drg-gunicorn-test-')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment.update({
        'DATABASE_URL': database_url, 'DATABASE_SCHEMA': schema, 'DATA_DIR': str(data),
        'FILE_STORAGE': 'local', 'DATA_ENCRYPTION_ENABLED': 'true',
        'DATA_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'APP_SECRET': secrets.token_urlsafe(48), 'SETUP_TOKEN': SETUP_TOKEN,
        'PUBLIC_URL': 'http://127.0.0.1:' + str(port), 'TRUSTED_PROXIES': '',
        'GUNICORN_WORKERS': '3', 'GUNICORN_THREADS': '24', 'DRG_MULTIPROCESS_TEST': '1',
        'PYTHON_DOTENV_DISABLED': '1',
        'REDIS_URL':os.getenv('DRG_TEST_REDIS_URL',''),
    })
    process = None
    instance = None
    try:
        with (data / 'gunicorn-test.log').open('wb') as log:
            process = subprocess.Popen([
                sys.executable, '-m', 'gunicorn', '--config', str(ROOT / 'gunicorn.conf.py'),
                '--chdir', str(ROOT), '--bind', environment['PUBLIC_URL'].removeprefix('http://'),
                '--workers', '3', '--no-control-socket', 'tests.multiprocess_server:app',
            ], cwd=data, env=environment, stdout=log, stderr=subprocess.STDOUT,
               start_new_session=True)
        instance = Server(process, data, schema, port, database_url)
        def healthy():
            assert process.poll() is None, 'Isolated Gunicorn exited during startup'
            return instance.client.get('/api/health').status_code == 200
        eventually(healthy, timeout=40)
        instance.worker_pids()
        response = checked(instance.client.post('/api/setup', json={
            'login': 'owner', 'password': PASSWORD, 'token': SETUP_TOKEN}))
        instance.client.headers['x-csrf-token'] = response['csrf']
        enrollment = checked(instance.client.post('/api/2fa/enroll', json={'password': PASSWORD}))
        checked(instance.client.post('/api/2fa/confirm', json={
            'code': pyotp.TOTP(enrollment['secret']).now()}))
        response = checked(instance.client.post('/api/2fa/activate', json={
            'saved_recovery_codes': True}))
        instance.client.headers['x-csrf-token'] = response['csrf']
        checked(instance.client.post('/api/admin/access/unlock', json={'password': PASSWORD}))
        yield instance
    finally:
        if instance:
            instance.client.close()
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        # Only the generated schema is eligible for removal, never 'drg' or
        # a schema inherited through DRG_TEST_DATABASE_SCHEMA/environment.
        assert re.fullmatch(r'drg_gunicorn_test_[a-f0-9]{20}', schema)
        if environment['REDIS_URL']:
            import redis
            from types import SimpleNamespace
            from app.data_crypto import DataCipher
            from app.redis_cache import RedisCache
            from redis.backoff import NoBackoff
            from redis.retry import Retry
            storage_id=json.loads((data/'.storage-format.json').read_text())['storage_id']
            cache=RedisCache(SimpleNamespace(root=data,schema=schema,storage_id=storage_id,
                cipher=DataCipher(base64.urlsafe_b64decode(environment['DATA_ENCRYPTION_KEY']))),environment['REDIS_URL'])
            client=redis.Redis.from_url(environment['REDIS_URL'],protocol=2,socket_timeout=.2,
                socket_connect_timeout=.2,retry=Retry(NoBackoff(),0))
            try:
                keys=list(client.scan_iter(match=cache.prefix+':*',count=100))
                if keys:client.delete(*keys)
            except redis.RedisError:pass  # Only five-second test keys can remain during an outage.
            finally:client.close()
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def seed_doc():
    block = XmlElement('blockContainer', {'id': 'paragraph-one'}, [
        XmlElement('paragraph', {}, [XmlText('Before')])])
    return Doc({'prosemirror': XmlFragment([XmlElement('blockGroup', {}, [block])]),
                'meta': Map({'title': 'Together'})})


def decode_doc(encoded):
    doc = Doc({'prosemirror': XmlFragment(), 'meta': Map()})
    doc.apply_update(base64.b64decode(encoded))
    return doc


def encoded_doc(doc):
    return base64.b64encode(doc.get_update()).decode()


def doc_body(doc):
    group = list(doc.get('prosemirror', type=XmlFragment).children)[0]
    paragraph = list(list(group.children)[0].children)[0]
    return ''.join(text.to_py() for text in paragraph.children)


def test_crdt_and_presence_cross_three_real_workers(server):
    room = server.room()
    page = checked(server.client.post('/api/notes', json={
        'title': 'Together', 'body': 'Before', 'space_id': room}))
    with server.sockets('/api/live/notes/' + page['id']) as peers:
        assert len({peer.pid for peer in peers}) == 3
        initial = [peer.event('init') for peer in peers]
        peers[0].send({'type': 'init', 'version': initial[0]['page']['version'],
                       'update': encoded_doc(seed_doc())})
        synced = [peer.event('sync') for peer in peers]
        assert len({event['epoch'] for event in synced}) == 1
        assert len({event['state'] for event in synced}) == 1
        for peer in peers:
            peer.send({'type': 'presence', 'state': {'cursor': None}})
        for peer in peers:
            for other in peers:
                if other is peer:
                    continue
                event = peer.event('presence', match=lambda e, cid=other.cid:
                                   e['client_id'] == cid and e['state'] is not None)
                assert event['state']['user']['name'] == 'owner'
        # Two independent clients edit different CRDT fields from the same
        # starting state; no worker-local snapshot may overwrite the other.
        title_doc = decode_doc(synced[0]['state'])
        body_doc = decode_doc(synced[1]['state'])
        title_doc.get('meta', type=Map)['title'] = 'Across three workers'
        root = list(body_doc.get('prosemirror', type=XmlFragment).children)[0]
        paragraph = list(list(root.children)[0].children)[0]
        list(paragraph.children)[0].insert(6, ' and after')
        barrier = server.data / '__test_reconcile_hold'
        barrier.touch()
        try:
            peers[0].send({'type': 'update', 'epoch': synced[0]['epoch'], 'seq': 101,
                           'update': encoded_doc(title_doc)})
            peers[1].send({'type': 'update', 'epoch': synced[0]['epoch'], 'seq': 102,
                           'update': encoded_doc(body_doc)})
            acknowledgments = [peers[0].event('ack', match=lambda e: e['seq'] == 101),
                               peers[1].event('ack', match=lambda e: e['seq'] == 102)]
        finally:
            barrier.unlink(missing_ok=True)
        final_version = max(event['version'] for event in acknowledgments)
        final = peers[2].event('sync', match=lambda e: e['version'] == final_version)
        merged = decode_doc(final['state'])
        assert merged.get('meta', type=Map)['title'] == 'Across three workers'
        assert doc_body(merged) == 'Before and after'
        for peer, client_doc in zip(peers[:2], (title_doc, body_doc)):
            # Both writers must learn the foreign edit, including the writer
            # whose own ACK advanced to the final canonical version.
            while (client_doc.get('meta', type=Map)['title'] != 'Across three workers'
                   or doc_body(client_doc) != 'Before and after'):
                event = peer.event(('sync', 'update'))
                client_doc.apply_update(base64.b64decode(event.get('state') or event['update']))
        stored = checked(server.client.get('/api/notes/' + page['id']))
        assert stored['title'] == 'Across three workers' and stored['body'] == 'Before and after'
        for peer in peers:
            peer.send({'type': 'ping'})
            peer.event('pong')


def test_chat_and_postgres_listener_reconnect_reconcile_all_workers(server):
    room = server.room()
    with server.sockets('/api/live/chat/' + room) as peers:
        for peer in peers:
            peer.event('ready')
        peers[0].send({'type': 'message', 'body': 'WebSocket across workers',
                       'client_id': secrets.token_hex(12)})
        received = [peer.event('message', match=lambda e:
                              e['message']['body'] == 'WebSocket across workers') for peer in peers]
        assert len({event['message']['id'] for event in received}) == 1
        eventually(lambda: len(server.listener_pids()) == 3)
        old = server.terminate_listeners()
        committed = checked(server.client.post('/api/chat/' + room + '/messages', json={
            'body': 'Committed during listener reconnect', 'client_id': secrets.token_hex(12)}))
        for peer in peers:
            event = peer.event('message', match=lambda e: e['message']['id'] == committed['id'])
            assert event['message']['body'] == 'Committed during listener reconnect'
        def listeners_reconnected():
            current = server.listener_pids()
            return len(current) == 3 and not current.intersection(old)
        eventually(listeners_reconnected)
        after = checked(server.client.post('/api/chat/' + room + '/messages', json={
            'body': 'After reconnect', 'client_id': secrets.token_hex(12)}))
        for peer in peers:
            peer.event('message', match=lambda e: e['message']['id'] == after['id'])


def test_upgraded_socket_survives_inherited_http_idle_timeout(server, monkeypatch):
    room = server.room()
    pinned_http = server.pinned_http

    def short_http_timeout():
        pid, connection = pinned_http()
        connection.sock.settimeout(0.25)
        return pid, connection

    monkeypatch.setattr(server, 'pinned_http', short_http_timeout)
    with server.sockets('/api/live/chat/' + room, count=1) as peers:
        peer = peers[0]
        peer.event('ready')
        # The HTTP request timeout must not become a WebSocket idle deadline.
        # No chat or presence events are generated in this dedicated room.
        time.sleep(0.5)
        peer.send({'type': 'ping'})
        peer.event('pong')


def test_global_socket_quota_and_sigkill_cleanup_with_autorespawn(server):
    room = server.room()
    path = '/api/live/chat/' + room
    with server.sockets(path, count=12) as peers:
        for peer in peers:
            peer.event('ready')
        rejected = server.open_socket(path)
        try:
            with pytest.raises(ConnectionClosed) as closed:
                rejected.connection.recv(timeout=10)
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 4429
        finally:
            rejected.close()
        for peer in peers[:3]:
            peer.send({'type': 'presence', 'state': {'typing': True}})
        victim = peers[0]
        survivor = peers[1]
        survivor.event('presence', match=lambda e:
                       e['client_id'] == victim.cid and e['state'] is not None)
        old_pids = {peer.pid for peer in peers}
        assert len(old_pids) == 3
        os.kill(victim.pid, signal.SIGKILL)
        alive = [peer for peer in peers if peer.pid != victim.pid]
        for peer in alive:
            peer.send({'type': 'ping'})
            peer.event('pong')
        departed = survivor.event('presence', match=lambda e:
                                 e['client_id'] == victim.cid and e['state'] is None)
        assert departed['state'] is None
        current_pids = server.worker_pids(exclude={victim.pid})
        assert len(current_pids-old_pids) == 1
        replacements = []
        try:
            # Kernel leases of all sockets in the killed worker must be gone;
            # surviving clients keep their capacity, and replacements can fill
            # exactly the released slots across all workers.
            for _ in range(12-len(alive)):
                peer = server.open_socket(path)
                replacements.append(peer)
                peer.event('ready')
            rejected = server.open_socket(path)
            try:
                with pytest.raises(ConnectionClosed) as closed:
                    rejected.connection.recv(timeout=10)
                assert closed.value.rcvd is not None and closed.value.rcvd.code == 4429
            finally:
                rejected.close()
            assert server.client.get('/api/health').status_code == 200
        finally:
            for peer in replacements:
                peer.close()


def test_active_encrypted_backup_survives_another_worker_restart(server):
    hold = server.data / '__test_backup_hold'
    ready = server.data / '__test_backup_ready'
    release = server.data / '__test_backup_release'
    hold.touch()
    before = server.worker_pids()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            exported = executor.submit(server.client.post, '/api/admin/backups/export', json={
                'admin_password': PASSWORD, 'backup_password': 'Isolated-backup-password-123'},
                timeout=60)
            try:
                eventually(ready.exists)
                state = json.loads(ready.read_text())
                directory = Path(state['directory'])
                archive = directory / 'site.drgbackup'
                assert directory.parent == server.data
                original = archive.read_bytes()
                assert original.startswith(b'DRG-BACKUP-02\x00')
                assert b'Across three workers' not in original
                victim = next(pid for pid in before if pid != state['pid'])
                os.kill(victim, signal.SIGKILL)
                after = server.worker_pids(exclude={victim})
                replacement = next(iter(after-before))
                # Force the same cleanup used during worker maintenance in the
                # newly booted worker while another worker owns the export.
                for _ in range(60):
                    pid, connection = server.pinned_http()
                    if pid != replacement:
                        connection.close()
                        continue
                    try:
                        connection.request('POST', '/__test_cleanup_backup_temps',
                                           headers={'Origin': server.url,
                                                    'Cookie': server.cookie(),
                                                    'X-CSRF-Token': server.client.headers['x-csrf-token'],
                                                    'Content-Length': '0'})
                        response = connection.getresponse()
                        assert response.status == 200
                        assert json.loads(response.read())['pid'] == replacement
                    finally:
                        connection.close()
                    break
                else:
                    pytest.fail('Could not run cleanup in the replacement worker')
                assert archive.read_bytes() == original
            finally:
                release.touch()
            response = exported.result(timeout=15)
            assert response.status_code == 200, response.text
            assert response.content == original
        eventually(lambda: not directory.exists())
        assert server.client.get('/api/health').status_code == 200
    finally:
        for marker in (hold, ready, release):
            marker.unlink(missing_ok=True)


def test_chat_timer_and_clear_reconcile_on_three_workers_without_listener(server):
    room=server.room()
    with server.sockets('/api/live/chat/'+room) as peers:
        for peer in peers:peer.event('ready')
        message=checked(server.client.post('/api/chat/'+room+'/messages',json={'body':'Clear across workers','client_id':secrets.token_hex(12)}))
        for peer in peers:peer.event('message',match=lambda e:e['message']['id']==message['id'])
        server.terminate_listeners()
        checked(server.client.put('/api/chat/'+room+'/timer',json={'seconds':86400}))
        for peer in peers:peer.event('chat_state',match=lambda e:e['chat_ttl']==86400)
        state=checked(server.client.post('/api/chat/'+room+'/clear'))
        assert state['chat_cleared']==message['id']
        for peer in peers:peer.event('chat_state',match=lambda e:e['chat_cleared']==message['id'])
        assert checked(server.client.get('/api/chat/'+room+'/messages'))['items']==[]
        after=checked(server.client.post('/api/chat/'+room+'/messages',json={'body':'After clear','client_id':secrets.token_hex(12)}))
        assert after['expires']==after['created']+86400
        for peer in peers:peer.event('message',match=lambda e:e['message']['id']==after['id'])


def test_inbox_invalidations_cross_workers_and_recover_a_listen_gap(server):
    with server.sockets('/api/live/inbox') as peers:
        for peer in peers:peer.event('ready')
        def notification():
            return checked(server.client.post('/__test_notification'))['id']
        def delivered(nid):
            for peer in peers:
                peer.event('notifications',match=lambda e:any(n['id']==nid for n in e['items']))
        delivered(notification())
        eventually(lambda:len(server.listener_pids())==3)
        old=server.terminate_listeners()
        delivered(notification())
        eventually(lambda:len(server.listener_pids())==3 and not server.listener_pids().intersection(old))
        checked(server.client.post('/api/notifications/read-all'))
        for peer in peers:peer.event('notifications',match=lambda e:e['unread']==0)


def test_redis_counter_is_shared_by_independently_serving_workers(server):
    if not os.getenv('DRG_TEST_REDIS_URL'):pytest.skip('Requires the optional real Redis test connection')
    connections={}
    try:
        while len(connections)<3:
            pid,connection=server.pinned_http()
            if pid in connections:connection.close()
            else:connections[pid]=connection
        def get(connection,path):
            connection.request('GET',path,headers={'Cookie':server.cookie(),'Origin':server.url})
            response=connection.getresponse();body=response.read()
            assert response.status==200,body
            return json.loads(body)
        checked(server.client.post('/__test_notification'))  # Fresh committed generation.
        for i,(pid,connection) in enumerate(connections.items()):
            before=get(connection,'/__test_redis_stats')
            assert before['configured'] and before['ready'] and before['pid']==pid
            assert get(connection,'/api/chat/unread')=={'unread':0}
            after=get(connection,'/__test_redis_stats')
            if i:assert after['hits']==before['hits']+1
    finally:
        for connection in connections.values():connection.close()
