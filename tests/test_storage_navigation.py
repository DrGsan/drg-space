import re
import threading
import pytest
from tests.test_security import reset,owner,client
from app import main as m


def test_database_readiness_reports_failure_and_recovers(monkeypatch):
    c=client()
    assert c.get('/api/ready').json()=={'ok':True}
    with m.storage_gate.exclusive_sync():
        assert c.get('/api/ready').status_code==503
        assert c.get('/api/health').json()=={'ok':True}

    release=threading.Event()
    def stalled_probe():
        assert release.wait(5)
        raise OSError('Test database unavailable')
    try:
        with monkeypatch.context() as outage:
            outage.setattr(m.database,'check_readiness',stalled_probe)
            response=c.get('/api/ready')
            assert response.status_code==503
            assert response.json()=={'ok':False}
            assert response.headers['cache-control']=='no-store'
            assert c.get('/api/health').json()=={'ok':True}
    finally:
        release.set()
    # Wait for the bounded request's in-flight read to finish before probing again.
    async def finish_probe():
        await m.readiness_task
    m.app.runtime.run(finish_probe())
    assert c.get('/api/ready').json()=={'ok':True}


@pytest.mark.parametrize('language,heading',[('ru','Выполняется операция с данными'),('en','A data operation is in progress')])
def test_locked_document_is_self_contained_localized_uncached_and_retains_session(language,heading):
    c,_,_=owner();uid=c.get('/api/me').json()['id']
    headers={'Accept':'text/html','X-DrG-Language':language,'Sec-Fetch-Dest':'document'}
    with m.storage_gate.exclusive_sync():
        r=c.get('/?return=%3Cscript%3E',headers=headers)
        assert r.status_code==503 and r.headers['content-type'].startswith('text/html')
        assert r.headers['cache-control']=='no-store' and r.headers['retry-after']=='5'
        assert r.headers['content-language']==language and heading in r.text
        nonce=re.search(r'<script nonce="([\w-]+)">',r.text)[1]
        assert "script-src 'nonce-"+nonce+"'" in r.headers['content-security-policy']
        assert 'location.reload()' in r.text and 'fetch(' not in r.text
        assert not re.search(r'<(?:script|link|img)[^>]+(?:src|href)=',r.text)
        assert '<script>' not in r.text and not r.headers.get('set-cookie')
        assert c.get('/api/health').json()=={'ok':True}
    assert c.get('/api/me').json()['id']==uid
    assert c.get('/',headers=headers).status_code==200


@pytest.mark.parametrize('path,headers',[
    ('/api/me',{'Accept':'text/html','Sec-Fetch-Dest':'document'}),
    ('/api/notes',{'Accept':'application/json'}),
    ('/assets/app.js',{'Accept':'*/*','Sec-Fetch-Dest':'script'}),
    ('/',{'Accept':'application/json'}),
])
def test_locked_api_and_subresources_keep_machine_errors(path,headers):
    c,_,_=owner()
    with m.storage_gate.exclusive_sync():
        r=c.get(path,headers=headers)
        assert r.status_code==503 and r.headers['content-type'].startswith('application/json')
        assert r.json()['code']=='storage_busy'
        assert r.headers['cache-control']=='no-store' and r.headers['retry-after']=='5'


def test_locked_head_document_has_same_headers_without_a_body():
    c,_,_=owner()
    with m.storage_gate.exclusive_sync():
        r=c.request('HEAD','/',headers={'Accept':'text/html'})
        assert r.status_code==503 and r.headers['content-type'].startswith('text/html')
        assert not r.content and r.headers['cache-control']=='no-store'


@pytest.mark.parametrize('path,retries',[('/',True),('/page/first',True),('/share/link',False)])
def test_document_recovery_during_scheduled_maintenance(path,retries):
    c,_,_=owner()
    from app.process_locks import try_file_lock
    with try_file_lock(m.storage_gate.root,'storage-maintenance.lock'), m.storage_gate.exclusive_sync():
        headers={'Accept':'text/html'}
        page=c.get(path,headers=headers)
        probe=c.request('HEAD',path,headers=headers)
        assert page.status_code==probe.status_code==503 and not probe.content
        assert ('X-DrG-Scheduled-Maintenance' in probe.headers)==retries
        assert ("connect-src 'self'" in page.headers['content-security-policy'])==retries
        assert ('checkPage(4)' in page.text)==retries


def test_unreadable_security_state_during_exclusive_operation_still_has_public_recovery(monkeypatch):
    from app.data_crypto import StorageError
    c,_,_=owner()
    def unavailable_state(request):raise StorageError('isolated test-only unreadable state')
    monkeypatch.setattr(m.security_policy,'check',unavailable_state)
    with m.storage_gate.exclusive_sync():
        r=c.get('/',headers={'Accept':'text/html'})
        assert r.status_code==503 and 'busy-retry' in r.text
        assert r.headers['cache-control']=='no-store'
        # An API error remains machine-readable even before gate admission.
        assert c.get('/api/me',headers={'Accept':'text/html'}).headers['content-type'].startswith('application/json')
    r=c.get('/',headers={'Accept':'text/html'})
    assert r.status_code==503 and 'busy-retry' not in r.text
