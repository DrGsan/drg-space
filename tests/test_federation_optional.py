"""Normal installations need neither federation settings nor its published port."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from app.http import Application
from app.federation.runtime import FederationRuntime
from tests.test_storage_encryption import databases


@pytest.mark.parametrize('public,encrypted',[('http://localhost:8000',True),('https://site.example',True),('https://site.example',False)])
def test_runtime_stays_disabled_without_federation_configuration(databases,monkeypatch,public,encrypted):
    for name in list(os.environ):
        if name.startswith('FEDERATION_'):monkeypatch.delenv(name,raising=False)
    backend=databases(encrypted=encrypted)
    app=Application(__name__)
    @app.get('/api/health')
    def health():return {'ok':True}
    main=SimpleNamespace(db=backend.connection,data_cipher=backend.cipher,DATA=backend.root,PUBLIC_URL=public,app=app)
    runtime=FederationRuntime(main)
    try:
        runtime.maintain()
        assert runtime.server is None and runtime.worker is None
        with runtime.state.transaction() as connection:assert not runtime.service.enabled(connection)
        assert app.test_client().get('/api/health').get_json()=={'ok':True}
    finally:
        runtime.stop();app.stop_services()


def test_compose_needs_no_federation_parameters_or_port_until_optional_overlay():
    if not shutil.which('docker'):pytest.skip('Docker Compose CLI is required for this deployment contract.')
    root=Path(__file__).resolve().parents[1]
    environment={key:value for key,value in os.environ.items() if key in ('PATH','HOME','DOCKER_CONFIG')}
    environment['APP_SECRET']='disposable-optional-federation-test-secret-at-least-32-characters'
    base=['docker','compose','--env-file',os.devnull,'--project-name','drg-optional-config','--file',str(root/'compose.yaml')]
    def configuration(extra=()):
        result=subprocess.run([*base,*extra,'config','--format','json'],check=True,env=environment,capture_output=True,text=True,timeout=15)
        return json.loads(result.stdout)['services']['app']
    app=configuration()
    assert app['environment'].get('FEDERATION_PUBLIC_URL','')==''
    assert not any(port['target']==8444 for port in app.get('ports',[]))
    enabled=configuration(('--file',str(root/'compose.federation.yaml')))
    port=next(port for port in enabled['ports'] if port['target']==8444)
    assert port['host_ip']=='127.0.0.1'


def test_unused_https_installation_can_configure_a_different_federation_origin_later(databases,monkeypatch):
    for name in list(os.environ):
        if name.startswith('FEDERATION_'):monkeypatch.delenv(name,raising=False)
    backend=databases()
    def start():
        app=Application(__name__)
        main=SimpleNamespace(db=backend.connection,data_cipher=backend.cipher,DATA=backend.root,
                             PUBLIC_URL='https://site.example',app=app)
        return FederationRuntime(main)
    first=start();original=first.service.identity.descriptor
    assert original.origin=='https://site.example:8444'
    first.stop()
    monkeypatch.setenv('FEDERATION_PUBLIC_URL','https://federation.site.example:9444')
    second=start()
    try:
        assert second.service.identity.descriptor.origin=='https://federation.site.example:9444'
        assert second.service.identity.descriptor.server_id!=original.server_id
        assert second.server is None
        with second.state.transaction() as c:
            assert not second.service.enabled(c)
            c.execute("INSERT INTO metadata VALUES('ever_enabled','true')")
    finally:second.stop()
    monkeypatch.setenv('FEDERATION_PUBLIC_URL','https://changed.site.example:9444')
    third=start()
    try:
        assert third.service.identity is None  # Existing pins require explicit recovery.
        assert third.server is None
    finally:third.stop()


def test_federation_dispatcher_lease_has_one_owner_and_can_fail_over(databases,monkeypatch):
    monkeypatch.setenv('FEDERATION_EXTERNAL_TLS','true')
    backend=databases();runtimes=[];delivered=[]
    monkeypatch.setattr('app.federation.revocations.deliver',lambda service:delivered.append(service) or False)
    try:
        for _ in range(2):
            main=SimpleNamespace(db=backend.connection,data_cipher=backend.cipher,DATA=backend.root,
                                 PUBLIC_URL='https://site.example',app=Application(__name__))
            runtimes.append(FederationRuntime(main))
        first,second=runtimes
        with first.state.transaction() as c:c.execute("INSERT INTO metadata VALUES('enabled','true')")
        assert first.maintain()==30
        assert second.maintain()==30
        assert delivered==[first.service]
        first.stop()
        assert second.maintain()==30 and delivered==[first.service,second.service]
    finally:
        for runtime in runtimes:runtime.stop()
