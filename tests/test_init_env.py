"""First-install configuration, secret preservation and legacy schema compatibility."""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time

from dotenv import dotenv_values
import pytest


ROOT = Path(__file__).resolve().parents[1]
GENERATED = {'APP_SECRET', 'SETUP_TOKEN', 'DATA_ENCRYPTION_KEY',
             'POSTGRES_PASSWORD', 'POSTGRES_ADMIN_PASSWORD'}


def clean_environment():
    # Never inherit a developer's database, application secrets or Compose files.
    return {key: value for key, value in os.environ.items()
            if key in ('PATH', 'HOME', 'TMPDIR', 'DOCKER_CONFIG')}


def generate(directory, root=ROOT, extra=None):
    return subprocess.run([sys.executable, str(root / 'scripts/init-env.py')],
                          cwd=directory, env=clean_environment() | (extra or {}),
                          capture_output=True, text=True, timeout=15)


@pytest.fixture
def fresh_env(tmp_path):
    result = generate(tmp_path)
    assert result.returncode == 0
    return tmp_path / '.env'


def test_complete_template_with_comments_defaults_and_empty_optional_connections(tmp_path):
    result = generate(tmp_path, extra={'DATABASE_SCHEMA': 'unrelated_host_schema',
                                      'REDIS_URL': 'redis://unrelated.example:6379/0'})
    assert result.returncode == 0
    path = tmp_path / '.env'
    expected = dotenv_values(ROOT / '.env.example')
    actual = dotenv_values(path)
    assert set(actual) == set(expected)
    assert {key: value for key, value in actual.items() if key not in GENERATED} == {
        key: value for key, value in expected.items() if key not in GENERATED}
    assert actual['DATABASE_SCHEMA'] == 'drg_site'
    assert actual['DATA_ENCRYPTION_ENABLED'] == 'true'
    assert actual['FILE_STORAGE'] == 'local'
    assert actual['NOTION_SYNC_ENABLED'] == 'false'
    assert not {'NOTION_WRITE_OAUTH_CLIENT_ID','NOTION_WRITE_OAUTH_CLIENT_SECRET','NOTION_WEBHOOK_WRITE_SECRET'} & actual.keys()
    assert actual['GUNICORN_WORKERS'] == '1' and actual['GUNICORN_THREADS'] == '64'
    for name in ('DATABASE_URL', 'REDIS_URL', 'S3_BUCKET', 'S3_ENDPOINT_URL',
                 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
                 'FAMILY_PHOTO_URL', 'FAMILY_PHOTO_TOKEN', 'UNSPLASH_ACCESS_KEY',
                 'CALL_TURN_URLS', 'CALL_TURN_SECRET', 'FEDERATION_PUBLIC_URL',
                 'SITE_DOMAIN', 'HTTP_PROXY', 'HTTPS_PROXY', 'OUTBOUND_CA_FILE', 'NOTION_OAUTH_CLIENT_ID', 'NOTION_OAUTH_CLIENT_SECRET',
                 'NOTION_OAUTH_REDIRECT_URI', 'NOTION_WEBHOOK_READ_SECRET'):
        assert actual[name] == ''
    body = path.read_text()
    # Formatting and key order have one source, not a second hand-maintained
    # list in the generator. Only installation secret values may differ.
    normalize = lambda text: re.sub(
        r'^(' + '|'.join(sorted(GENERATED)) + r')=.*$',
        r'\1=', text, flags=re.MULTILINE)
    assert normalize(body) == normalize((ROOT / '.env.example').read_text())
    for line in (ROOT / '.env.example').read_text().splitlines():
        if line.startswith('#'):
            assert line in body
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(base64.b64decode(actual['DATA_ENCRYPTION_KEY'], altchars=b'-_', validate=True)) == 32
    for name in GENERATED - {'DATA_ENCRYPTION_KEY'}:
        assert re.fullmatch(r'[A-Za-z0-9_-]{32,}', actual[name])
    assert len({actual[name] for name in GENERATED}) == len(GENERATED)
    for name in GENERATED:
        assert actual[name] not in result.stdout + result.stderr
    another = tmp_path / 'another-installation'
    another.mkdir()
    assert generate(another).returncode == 0
    second = dotenv_values(another / '.env')
    assert all(actual[name] != second[name] for name in GENERATED)


@pytest.mark.parametrize('kind', ['file', 'symlink', 'dangling-symlink'])
def test_existing_configuration_is_never_replaced(tmp_path, kind):
    original = b'APP_SECRET=existing-owner-secret\nDATABASE_SCHEMA=drg\nCUSTOM_SETTING=keep\n'
    path = tmp_path / '.env'
    target = tmp_path / 'owner.env'
    if kind == 'file':
        path.write_bytes(original)
    else:
        if kind == 'symlink':
            target.write_bytes(original)
        path.symlink_to(target)
    result = generate(tmp_path)
    assert result.returncode != 0 and 'already exists' in result.stderr
    if kind == 'dangling-symlink':
        assert path.is_symlink() and not target.exists()
    else:
        assert path.read_bytes() == original
    assert 'existing-owner-secret' not in result.stdout + result.stderr


@pytest.mark.parametrize('broken', ['missing', 'duplicate'])
def test_invalid_template_leaves_no_partial_configuration(tmp_path, broken):
    source = tmp_path / 'source'
    (source / 'scripts').mkdir(parents=True)
    shutil.copyfile(ROOT / 'scripts/init-env.py', source / 'scripts/init-env.py')
    body = (ROOT / '.env.example').read_text()
    body = body.replace('APP_SECRET=\n', '') if broken == 'missing' else body + '\nAPP_SECRET=\n'
    (source / '.env.example').write_text(body)
    result = generate(tmp_path, root=source)
    assert result.returncode != 0
    assert not (tmp_path / '.env').exists()


def compose_configuration(path, tmp_path, extras=None, overlays=()):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose CLI is required')
    command = ['docker', 'compose', '--env-file', str(path), '-p', 'drg-env-config',
               '-f', str(ROOT / 'compose.yaml')]
    for overlay in overlays:
        command += ['-f', str(ROOT / overlay)]
    result = subprocess.run(command + ['config', '--format', 'json'], cwd=tmp_path,
                            env=clean_environment() | (extras or {}),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, 'Compose configuration failed'
    return json.loads(result.stdout)


@pytest.mark.parametrize('schema', ['drg_site', 'drg', 'custom_site', None])
def test_compose_uses_same_schema_for_app_and_postgres_without_changing_legacy_default(fresh_env, tmp_path, schema):
    if schema is None:
        body = re.sub(r'^DATABASE_SCHEMA=.*\n', '', fresh_env.read_text(), flags=re.MULTILINE)
        fresh_env.write_text(body)
    config = compose_configuration(fresh_env, tmp_path,
                                   {'DATABASE_SCHEMA': schema} if schema else {},
                                   ('compose.postgres.yaml',))
    app = config['services']['app']['environment']
    postgres = config['services']['postgres']['environment']
    assert app['DATABASE_SCHEMA'] == postgres['DRG_APP_SCHEMA'] == (schema or 'drg')
    assert 'POSTGRES_ADMIN_PASSWORD' not in app
    assert postgres['POSTGRES_PASSWORD'] not in app['DATABASE_URL']


def test_generated_env_renders_optional_compose_overlays(fresh_env, tmp_path):
    basic = compose_configuration(fresh_env, tmp_path)
    assert basic['services']['app']['ports'][0]['host_ip'] == '127.0.0.1'
    assert basic['services']['app']['environment']['DATA_DIR'] == '/data'
    assert basic['services']['app']['environment']['REDIS_URL'] == ''
    compose_configuration(fresh_env, tmp_path,
                          {'PUBLIC_URL': 'https://site.example', 'SITE_DOMAIN': 'site.example'},
                          ('compose.production.yaml', 'compose.postgres.yaml', 'compose.federation.yaml'))
    token = tmp_path / 'tunnel.token'
    token.write_text('disposable-test-token')
    compose_configuration(fresh_env, tmp_path, {'CLOUDFLARED_TOKEN_FILE': str(token)},
                          ('compose.cloudflare.yaml',))
    compose_configuration(fresh_env, tmp_path, {'OUTBOUND_CA_FILE': str(tmp_path / 'ca.pem')},
                          ('compose.proxy.yaml',))


def test_legacy_runtime_keeps_default_schema_when_setting_is_absent(tmp_path, monkeypatch):
    from app.database import Database
    from app.data_crypto import DataCipher
    monkeypatch.delenv('DATABASE_SCHEMA', raising=False)
    backend = Database(tmp_path, url='', cipher=DataCipher.ephemeral(), redis_url='')
    assert backend.schema == 'drg'


def test_generated_env_supports_encrypted_setup_2fa_and_restart(fresh_env, tmp_path):
    environment = clean_environment() | {'PYTHONPATH': str(ROOT)}
    setup = '''
from dotenv import load_dotenv
load_dotenv('.env')
import os
import pyotp
from app import main as m
try:
    assert m.database.kind == 'sqlite' and m.data_cipher.enabled
    assert m.database.schema == 'drg_site'
    c = m.app.test_client()
    assert c.get('/api/health').status_code == 200
    assert c.get('/api/status').get_json()['configured'] is False
    payload = {'login':'env_audit_owner','password':'EnvAudit-password-123','token':os.environ['SETUP_TOKEN']}
    r = c.post('/api/setup', json=payload, headers={'Origin':m.PUBLIC_URL})
    assert r.status_code == 200
    headers = {'Origin':m.PUBLIC_URL,'X-CSRF-Token':r.get_json()['csrf']}
    r = c.post('/api/2fa/enroll', json={'password':payload['password']}, headers=headers)
    assert r.status_code == 200
    r = c.post('/api/2fa/confirm', json={'code':pyotp.TOTP(r.get_json()['secret']).at(m.now())}, headers=headers)
    assert r.status_code == 200 and r.get_json()['recovery_codes']
    assert c.post('/api/2fa/activate', json={'saved_recovery_codes':True}, headers=headers).status_code == 200
    assert c.post('/api/setup', json=payload, headers={'Origin':m.PUBLIC_URL}).status_code == 409
    assert not m.DB.read_bytes().startswith(b'SQLite format 3')
finally:
    m.app.stop_services()
'''
    restart = '''
from dotenv import load_dotenv
load_dotenv('.env')
import os
os.environ['SETUP_TOKEN'] = ''
from app import main as m
try:
    with m.db() as c:
        user = c.execute('SELECT * FROM users WHERE login=?', ('env_audit_owner',)).fetchone()
        assert user and m.check_password('EnvAudit-password-123', user['password'])
        assert m.cipher.decrypt(user['totp'].encode())
    assert m.app.test_client().get('/api/status').get_json()['configured'] is True
finally:
    m.app.stop_services()
'''
    for code in (setup, restart):
        result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=environment,
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('schema', ['drg_site', 'drg', 'custom_site', None])
def test_bundled_postgres_initializes_owned_schema_and_preserves_data(fresh_env, tmp_path, schema):
    image = os.getenv('DRG_INIT_ENV_POSTGRES_IMAGE')
    if not image:
        pytest.skip('Set DRG_INIT_ENV_POSTGRES_IMAGE=postgres:17-alpine for disposable Docker integration')
    import psycopg
    from app.database import Database
    from app.data_crypto import DataCipher

    if schema is None:
        fresh_env.write_text(re.sub(r'^DATABASE_SCHEMA=.*\n', '', fresh_env.read_text(), flags=re.MULTILINE))
    config = compose_configuration(fresh_env, tmp_path,
                                   {'DATABASE_SCHEMA': schema} if schema else {},
                                   ('compose.postgres.yaml',))
    settings = config['services']['postgres']['environment']
    if schema is None:
        settings.pop('DRG_APP_SCHEMA')  # Also exercise the old init-script environment.
    name = 'drg-env-test-' + secrets.token_hex(6)
    command = ['docker', 'run', '--detach', '--rm', '--name', name,
               '--tmpfs', '/var/lib/postgresql/data:rw', '--publish', '127.0.0.1::5432',
               '--mount', 'type=bind,source=' + str(ROOT / 'scripts/postgres-init.sh') +
               ',target=/docker-entrypoint-initdb.d/10-drg.sh,readonly']
    for key, value in settings.items():
        command += ['--env', key + '=' + value]
    try:
        result = subprocess.run(command + [image], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, 'Disposable PostgreSQL did not start'
        result = subprocess.run(['docker', 'port', name, '5432/tcp'], capture_output=True,
                                text=True, check=True, timeout=10)
        port = int(result.stdout.strip().rsplit(':', 1)[1])
        url = 'postgresql://drg:' + settings['DRG_APP_PASSWORD'] + '@127.0.0.1:' + str(port) + '/drg'
        deadline = time.monotonic() + 40
        while True:
            try:
                with psycopg.connect(url, connect_timeout=1) as raw:
                    assert raw.execute('SELECT 1').fetchone()[0] == 1
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail('Disposable PostgreSQL did not become ready')
                time.sleep(0.3)
        actual_schema = schema or 'drg'
        with psycopg.connect(url) as raw:
            assert raw.execute('SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s', (actual_schema,)).fetchone()[0] == 'drg'
            assert raw.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone() == (False, False, False)
            assert raw.execute("SELECT has_database_privilege(current_user,'drg','CREATE')").fetchone()[0] is False
        cipher = DataCipher(base64.urlsafe_b64decode(dotenv_values(fresh_env)['DATA_ENCRYPTION_KEY']))
        backend = Database(tmp_path / 'pg-data', url=url, cipher=cipher, schema=actual_schema, redis_url='')
        backend.initialize()
        with backend.connection() as c:
            c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)', ('env-audit', 'preserved-value'))
        reopened = Database(backend.root, url=url, cipher=cipher, schema=actual_schema, redis_url='')
        reopened.initialize()
        with reopened.connection() as c:
            assert c.execute('SELECT value FROM app_settings WHERE key=?', ('env-audit',)).fetchone()[0] == 'preserved-value'
    finally:
        subprocess.run(['docker', 'rm', '--force', '--volumes', name], capture_output=True, timeout=15)
