"""Stable public release hints, permissions, shared caching and outages."""
import httpx
import pytest
from app import app_update as update
from app.process_locks import try_file_lock
from tests.test_security import owner, member, reset
from app import main as m

STAMP=1794390000
def release(tag='v5.10.0',**changes):
    return {'tag_name':tag,'html_url':update.RELEASES+tag,'draft':False,'prerelease':False,'published_at':'2026-11-10T00:00:00Z',**changes}

@pytest.mark.parametrize('installed,tag,available',[
    ('5.9.9','v5.10.0',True),('5.10.0','v5.10.0',False),('6.0.0','v5.10.0',False),
    ('5.10.0-rc.3','v5.10.0',True),('5.11.0-rc.1','v5.10.0',False),
    ('5.10.0+build.1','v5.10.0',False),('invalid','v5.10.0',False),
    ('5.0.0','v5.10.0-rc.1',False),('5.0.0','v05.10.0',False)])
def test_versions_are_numeric_and_only_stable_releases_are_selected(tmp_path,monkeypatch,installed,tag,available):
    monkeypatch.setattr(update,'fetch_latest',lambda:release(tag))
    assert update.check(tmp_path,installed,stamp=STAMP)['available'] is available

def test_cache_shared_across_calls_and_failed_refresh_hides_stale_result(tmp_path,monkeypatch):
    calls=[]
    def fetch():calls.append(1);return release()
    monkeypatch.setattr(update,'fetch_latest',fetch)
    assert update.check(tmp_path,'5.0.2',stamp=STAMP)['available']
    assert update.check(tmp_path,'5.0.2',stamp=STAMP+1)['available'] and len(calls)==1
    assert not update.check(tmp_path,'5.10.0',stamp=STAMP+2)['available']
    def fail():calls.append(1);raise httpx.ReadTimeout('Test outage')
    monkeypatch.setattr(update,'fetch_latest',fail)
    assert not update.check(tmp_path,'5.0.2',stamp=STAMP+update.SUCCESS_TTL)['available']
    assert not update.check(tmp_path,'5.0.2',stamp=STAMP+update.SUCCESS_TTL+1)['available'] and len(calls)==2

@pytest.mark.parametrize('changes',[{'draft':True},{'prerelease':True},{'html_url':'https://unexpected.example/'},{'published_at':'2099-01-01T00:00:00Z'},{'published_at':'invalid'}])
def test_unconfirmed_or_invalid_metadata_never_produces_a_badge(tmp_path,monkeypatch,changes):
    monkeypatch.setattr(update,'fetch_latest',lambda:release(**changes))
    assert update.check(tmp_path,'5.0.2',stamp=STAMP)=={'available':False}

def test_concurrent_worker_does_not_fetch_while_another_worker_refreshes(tmp_path,monkeypatch):
    monkeypatch.setattr(update,'fetch_latest',lambda:pytest.fail('Duplicate upstream request'))
    with try_file_lock(tmp_path,'github-release-check.lock'):
        assert update.check(tmp_path,'5.0.2',stamp=STAMP)=={'available':False}

def test_only_owner_and_administrator_can_check_updates(monkeypatch):
    c,_,_=owner();g,_=member(c);calls=[]
    monkeypatch.setattr(update,'check',lambda *a,**kw:calls.append(1) or {'available':False})
    assert g.get('/api/app-update').status_code==403 and not calls
    assert c.get('/api/app-update').status_code==200
    uid=g.get('/api/me').json()['id']
    with m.db() as db:db.execute("UPDATE users SET role='admin' WHERE id=?",(uid,))
    assert g.get('/api/app-update').status_code==200 and len(calls)==2
