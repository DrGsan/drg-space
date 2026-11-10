import re
import pytest
from tests.test_security import reset,owner
from app import main as m


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
        assert r.json()['code']=='operationWithDataIsInProgressRetryLater'
        assert r.headers['cache-control']=='no-store' and r.headers['retry-after']=='5'


def test_locked_head_document_has_same_headers_without_a_body():
    c,_,_=owner()
    with m.storage_gate.exclusive_sync():
        r=c.request('HEAD','/',headers={'Accept':'text/html'})
        assert r.status_code==503 and r.headers['content-type'].startswith('text/html')
        assert not r.content and r.headers['cache-control']=='no-store'


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
