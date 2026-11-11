import json
from tests.test_security import owner,member,reset
from tests.test_family_exports import exports
from tests.test_web_push import push_service,register
from app import main as m


def ready_job(jobs,uid,state='ready',expires=None):
    jobs['a'*32]={'id':'a'*32,'actor':uid,'groups':['-1001'],'state':state,'expires':expires or m.now()+604800,'ready_at':m.now()}


def notices():
    with m.db() as c:return [dict(r) for r in c.execute("SELECT * FROM notifications WHERE kind='family_export_ready'")]


def test_ready_archive_notifies_once_and_pushes_without_chat_or_gallery(exports,push_service):
    bridge,jobs,_,_=exports
    c,_,_=owner();uid=c.get('/api/me').json()['id'];register(c)
    ready_job(jobs,uid)
    bridge.poll_messages(m.now());bridge.poll_messages(m.now())
    assert len(notices())==1
    assert m.web_push.deliver_one()
    assert len(push_service)==1
    payload=push_service[0][1]
    assert payload['type']=='family-export' and payload['url']=='/#admin/family-photos/-1001'
    assert payload['title']=='Архив медиафайлов готов'
    assert c.get('/api/notifications').json()['items'][0]['kind']=='family_export_ready'
    c.delete('/api/notifications/'+str(notices()[0]['id']))
    # A fresh event handler still reads the durable deduplication record.
    from app.modules.family_export_events import FamilyExportEvents
    fresh=FamilyExportEvents();fresh.db=bridge.db
    fresh.export_events([{**jobs['a'*32],'actor_id':uid}], [{'id':'-1001'}],m.now())
    assert not notices()
    assert not m.web_push.deliver_one()


def test_running_expired_and_nonowner_archives_do_not_notify(exports,push_service):
    bridge,jobs,_,_=exports
    c,_,_=owner();uid=c.get('/api/me').json()['id'];g,_=member(c);other=g.get('/api/me').json()['id']
    ready_job(jobs,uid,state='running');bridge.poll_messages(m.now());assert not notices()
    ready_job(jobs,uid,expires=m.now()-1);bridge.poll_messages(m.now());assert not notices()
    ready_job(jobs,other);bridge.poll_messages(m.now());assert not notices()


def test_cancelled_archive_drops_push_and_cleans_inbox(exports,push_service):
    bridge,jobs,_,_=exports
    c,_,_=owner();uid=c.get('/api/me').json()['id'];register(c);ready_job(jobs,uid)
    bridge.poll_messages(m.now());assert len(notices())==1
    jobs['a'*32]['state']='cleaning'
    assert m.web_push.deliver_one() and not push_service
    bridge.poll_messages(m.now());assert not notices()


def test_archive_unavailable_retries_push_without_losing_notice(exports,push_service,monkeypatch):
    bridge,jobs,_,_=exports
    c,_,_=owner();uid=c.get('/api/me').json()['id'];register(c);ready_job(jobs,uid)
    bridge.poll_messages(m.now())
    def unavailable(*args):raise OSError('service unavailable')
    monkeypatch.setattr(m.web_push,'_family_retry_after',0,raising=False)
    monkeypatch.setattr(bridge,'push_export',unavailable)
    assert m.web_push.deliver_one() and not push_service
    with m.db() as db:assert db.execute('SELECT available FROM push_deliveries').fetchone()[0]>m.now()
    assert len(notices())==1
