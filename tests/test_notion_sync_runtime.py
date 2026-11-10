import json
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,imported,page,ROOT,CHILD,NEW
from app import main as m
from app.notion_sync.runtime import retention


def test_retention_expires_only_transient_state_and_abandoned_previews():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    fresh_source='66666666-6666-4666-8666-666666666666'
    provider.pages[NEW]=page(NEW,'Old independent preview')
    provider.pages[fresh_source]=page(fresh_source,'Fresh independent preview')
    old=service.preview(s['user_id'],'read',NEW)['id']
    fresh=service.preview(s['user_id'],'read',fresh_source)['id']
    now=int(stamp[0])
    with m.db() as db:
        db.execute('UPDATE notion_roots SET updated=? WHERE id IN (?,?)',(now-2*86400,identity,old))
        for suffix,created in [('old',now-8*86400),('fresh',now)]:
            db.execute('INSERT INTO notion_events(id,workspace_id,payload,created) VALUES(?,?,?,?)',(suffix,'workspace','{}',created))
            db.execute('INSERT INTO notion_oauth_states(id,user_id,session_token,kind,expires) VALUES(?,?,?,?,?)',(suffix,s['user_id'],s['token'],'read',now-1 if suffix=='old' else now+600))
        for kind,created in [('read',now-2*86400),('write',now)]:
            db.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',('notion-webhook-verification:'+kind,json.dumps({'created':created,'token':'test-only-verification'})))
    retention(service);retention(service)
    with m.db() as db:
        assert {row[0] for row in db.execute('SELECT id FROM notion_roots')}=={identity,fresh}
        assert {row[0] for row in db.execute('SELECT id FROM notion_events')}=={'fresh'}
        assert {row[0] for row in db.execute('SELECT id FROM notion_oauth_states')}=={'fresh'}
        assert db.execute('SELECT 1 FROM app_settings WHERE key=?',('notion-webhook-verification:read',)).fetchone() is None
        assert db.execute('SELECT 1 FROM app_settings WHERE key=?',('notion-webhook-verification:write',)).fetchone()
    assert c.get('/api/notes/'+mapping[ROOT]).json()['title']=='Root'


def test_failed_maintenance_backs_off_while_queue_work_continues(monkeypatch,tmp_path,caplog):
    import asyncio
    from types import SimpleNamespace
    from app.notion_sync import runtime
    from app.storage import StorageGate
    from app.process_locks import try_file_lock
    clock=[1000];calls={'queue':0,'retention':0,'ticks':0}
    def queue():calls['queue']+=1;return False
    def failed_retention(service):calls['retention']+=1;raise RuntimeError('test-only-maintenance-failure')
    async def direct(function,*args):return function(*args)
    async def tick(delay):
        clock[0]+=delay;calls['ticks']+=1
        if calls['ticks']==4:raise asyncio.CancelledError()
    service=SimpleNamespace(config=SimpleNamespace(configured=True),data_dir=tmp_path,clock=lambda:clock[0],work_once=queue,recover_icons=lambda after:None)
    monkeypatch.setattr(runtime,'blocking',direct)
    monkeypatch.setattr(runtime,'retention',failed_retention)
    from app.notion_sync import rendering
    monkeypatch.setattr(rendering,'recover',lambda *args:None)
    monkeypatch.setattr(runtime,'route_unknown',lambda service:False)
    monkeypatch.setattr(runtime,'reconcile',lambda service:False)
    monkeypatch.setattr(runtime.asyncio,'sleep',tick)
    import pytest
    with pytest.raises(asyncio.CancelledError):asyncio.run(runtime.run(service,StorageGate(tmp_path)))
    assert calls['retention']==1 and calls['queue']==4
    assert 'maintenance (RuntimeError' in caplog.text
    assert 'test-only-maintenance-failure' not in caplog.text
    lease=try_file_lock(tmp_path,'notion-runner.lock');assert lease is not None;lease.close()
