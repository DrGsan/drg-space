#!/usr/bin/env python3
"""End-to-end disposable DrG/SQLCipher + DrG/PostgreSQL federation prototype.

The disposable nodes exercise the normal application federation runtime.
No production data, configuration, Git secrets, or existing volumes are used.
Temporary browser and TURN ports bind to random loopback ports only.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'scripts/federation'
spec = importlib.util.spec_from_file_location('federation_docker_recipe', ROOT / 'scripts/federation-docker.py')
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


def context(root):
    paths = ['requirements.lock.txt', 'VERSION', 'CHANGELOG.md', 'tests/fixtures/federation-upgrade.py', 'scripts/federation-smoke.py',
             'scripts/federation/prototype_node.py', 'scripts/federation/prototype_client.py',
             'scripts/federation/call_media_bridge.py',
             'scripts/federation/prototype_gateway.py',
             'scripts/federation/database_scenario.py',
             'tests/fixtures/animated.gif']
    paths += [str(path.relative_to(ROOT)) for path in sorted((ROOT / 'app').rglob('*.py'))]
    paths += [str(path.relative_to(ROOT)) for path in sorted((ROOT / 'app/static').rglob('*')) if path.is_file()]
    paths += [str(path.relative_to(ROOT)) for path in sorted((ROOT / 'tests').glob('*.py'))]
    for relative in paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    shutil.copyfile(RECIPE / 'prototype.Dockerfile', root / 'Dockerfile')


def run():
    subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], check=True, timeout=15)
    suffix = secrets.token_hex(6)
    project, image = 'drg-prototype-' + suffix, 'drg-federation-prototype:' + suffix
    native_fixture = Path(os.environ['DRG_FEDERATION_NATIVE_FIXTURE']) if os.environ.get('DRG_FEDERATION_NATIVE_FIXTURE') else None
    native_done = Path(os.environ.get('DRG_FEDERATION_NATIVE_DONE', '/tmp/drg-federation-native-done'))
    if native_fixture:
        if native_fixture.exists() or native_done.exists():
            raise RuntimeError('Remove this run\'s stale native fixture and completion sentinel before starting.')
    with tempfile.TemporaryDirectory(prefix='drg-federation-prototype-') as temporary:
        root = Path(temporary)
        source, fixtures, state, data = (root / name for name in ('context', 'fixtures', 'state', 'data'))
        for directory in (source, fixtures, state, data, state / 'attack', data / 'alice', data / 'bob'):
            directory.mkdir(mode=0o700)
        context(source)
        recipe.fixtures(fixtures, client_auth=True)
        password = secrets.token_hex(32)
        turn_secret = secrets.token_urlsafe(40)
        turn_configuration = '\n'.join([
            'listening-port=3478', 'fingerprint', 'use-auth-secret',
            'static-auth-secret=' + turn_secret, 'realm=drg-federation-test',
            'server-name=drg-federation-test', 'no-tls', 'no-multicast-peers',
            'no-tcp-relay', 'min-port=49160', 'max-port=49200',
            'total-quota=100', 'user-quota=10', 'pidfile=/tmp/turnserver.pid',
            'log-file=stdout', 'simple-log', '',
        ])
        (fixtures / 'coturn.conf').write_text(turn_configuration)
        (fixtures / 'coturn.conf').chmod(0o600)
        (fixtures / 'postgres-password').write_text(password)
        (fixtures / 'postgres-password').chmod(0o600)
        for name in ('alice', 'bob'):
            directory = fixtures / name
            # These old probe identities are not used. The full node creates its
            # own durable identity inside encrypted companion state.
            for filename in ('signing.pem', 'peer.json', 'descriptor.json'):
                (directory / filename).unlink()
            config = {'PUBLIC_URL': 'https://' + name + '.federation.test:8443',
                      'APP_SECRET': secrets.token_urlsafe(48), 'SETUP_TOKEN': secrets.token_urlsafe(32),
                      'DATA_ENCRYPTION_ENABLED': 'true',
                      'DATA_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                      'DATABASE_URL': '' if name == 'alice' else
                          'postgresql://federation_test:' + password + '@postgres:5432/federation_test',
                      'DATABASE_SCHEMA': 'drg'}
            config.update(CALL_TURN_URLS='turn:coturn:3478?transport=udp', CALL_TURN_SECRET=turn_secret)
            with (directory / 'app.json').open('w') as output:
                (directory / 'app.json').chmod(0o600)
                json.dump(config, output)
        environment = {name: value for name, value in os.environ.items() if not name.startswith('COMPOSE_')}
        environment.update(FEDERATION_TEST_IMAGE=image, FEDERATION_TEST_FIXTURES=str(fixtures),
                           FEDERATION_TEST_STATE=str(state), FEDERATION_TEST_DATA=str(data),
                           FEDERATION_TEST_UID=str(os.getuid()), FEDERATION_TEST_GID=str(os.getgid()))
        compose = ['docker', 'compose', '--env-file', os.devnull, '--project-name', project,
                   '--file', str(RECIPE / 'prototype.compose.yaml')]
        def execute(arguments, timeout=240):
            subprocess.run(arguments, check=True, timeout=timeout, cwd=root, env=environment)
        def verify(mode):
            execute(compose + ['run', '--rm', '--no-deps', 'verifier', 'python',
                               'scripts/federation/prototype_client.py', mode])
        native_deadline = None
        def wait_native():
            if native_fixture and native_fixture.exists():
                while not native_done.exists():
                    if time.monotonic() > native_deadline:
                        raise RuntimeError('Native end-to-end completion was not reported within 20 minutes.')
                    time.sleep(1)
        try:
            execute(['docker', 'build', '--tag', image, str(source)], timeout=900)
            execute(compose + ['up', '--detach', '--wait', 'postgres', 'coturn', 'alice', 'bob', 'gateway'])
            verify('regression')
            verify('scenario')
            execute(compose + ['exec', '-T', 'bob', 'python', 'scripts/federation/prototype_node.py', 'compromise'])
            published = subprocess.run(compose + ['port', '--protocol', 'udp', 'gateway', '3478'],
                                       check=True, cwd=root, env=environment, capture_output=True, text=True, timeout=15).stdout.strip()
            if not published.startswith('127.0.0.1:'):
                raise RuntimeError('The test TURN control port must be loopback-only: ' + published[:100])
            web_environment = environment | {'FEDERATION_BROWSER_STATE': str(state / 'client.json'),
                                             'FEDERATION_TURN_PORT': published.rsplit(':', 1)[1]}
            addresses = {}
            for name in ('alice', 'bob'):
                address = subprocess.run(compose + ['port', 'gateway', '8443' if name == 'alice' else '8444'], check=True, cwd=root,
                                         env=environment, capture_output=True, text=True, timeout=15).stdout.strip()
                if not address.startswith('127.0.0.1:'):
                    raise RuntimeError('Browser test ports must be loopback-only.')
                addresses[name] = address
                web_environment['FEDERATION_' + name.upper() + '_PORT'] = address.rsplit(':', 1)[1]
            if native_fixture:
                saved = json.loads((state / 'client.json').read_bytes())
                native_data = {'ca_pem': (fixtures / 'ca.pem').read_text(), 'turn_port': int(published.rsplit(':', 1)[1]),
                    'nodes': [{'origin': 'https://' + name + '.federation.test:8443',
                               'connect_url': 'https://' + addresses[name],
                               'cookies': [{key: cookie[key] for key in ('name', 'value', 'path')} for cookie in saved[name]['cookies']],
                               'password': 'Disposable-prototype-password-123'} for name in ('alice', 'bob')]}
                with os.fdopen(os.open(native_fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
                    json.dump(native_data, output)
                native_deadline = time.monotonic() + 1200
                print('READY: private native fixture and live disposable servers; waiting for native completion before attacks.', flush=True)
            media_environment = environment | {
                'DRG_CALL_MEDIA_BRIDGE': json.dumps(compose + ['run', '--rm', '--no-deps', '-T', 'verifier',
                    'python', 'scripts/federation/call_media_bridge.py']),
                'DRG_CALL_MEDIA_TURN_PORT': published.rsplit(':', 1)[1],
            }
            subprocess.run(['node', str(RECIPE / 'call_media.mjs')], check=True, timeout=240,
                           cwd=ROOT, env=media_environment)
            subprocess.run(['node', str(ROOT / 'tests/browser/federation-flow.mjs')], check=True,
                           timeout=360, cwd=ROOT, env=web_environment)
            wait_native()
            verify('attacks')
            execute(compose + ['stop', '--timeout', '5', 'alice'])
            verify('offline')
            execute(compose + ['up', '--detach', '--wait', '--no-deps', 'alice'])
            verify('restarted')
            print('PASS: full API prototype, real CRDT data, both database engines, theft attacks, durable replay, restart, and revocation.')
        except BaseException:
            subprocess.run(compose + ['logs', '--tail', '18', 'alice', 'bob'], cwd=root, env=environment, timeout=15)
            raise
        finally:
            try:
                wait_native()
            except RuntimeError:
                print('Native completion timeout; cleaning this disposable run.', flush=True)
            if native_fixture:
                native_fixture.unlink(missing_ok=True)
                native_done.unlink(missing_ok=True)
            cleanup = subprocess.run(compose + ['down', '--timeout', '5', '--volumes', '--remove-orphans'],
                                     cwd=root, env=environment, timeout=60)
            present = subprocess.run(['docker', 'image', 'inspect', image], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=15)
            removed = subprocess.run(['docker', 'image', 'rm', image], timeout=30) if present.returncode == 0 else None
            if cleanup.returncode or removed and removed.returncode:
                raise RuntimeError('Inspect cleanup for test project ' + project)
    print('CLEANED: this run containers, network, encrypted test stores, copied attack keys, certificates, and image tag.')


if __name__ == '__main__':
    try:
        run()
    except (subprocess.SubprocessError, OSError, RuntimeError, KeyboardInterrupt) as error:
        detail = ': ' + str(error) if isinstance(error, RuntimeError) else '; see the preceding diagnostic.'
        raise SystemExit('Prototype check failed (' + type(error).__name__ + ')' + detail) from None
