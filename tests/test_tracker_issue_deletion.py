"""Recovery clocks, enclosing deletion, authorization and complete disposal."""
import json
import pytest

from app import tracker, tracker_lifecycle as projects
from app import tracker_issue_lifecycle as issues
from app.http import HTTPError
from app.file_store import FileStore
from app.modules.tracker import store_attachment
from tests.test_storage_encryption import databases, seed, backup_service

UID = 'a' * 32


def setup(c):
    p = tracker.create_project(c, tracker.Project(name='Branch recovery', key='KEEP'), UID, 100)
    parent = tracker.create_issue(c, p['id'], tracker.Issue(title='Parent', kind='epic'), UID, 101)
    child = tracker.create_issue(c, p['id'], tracker.Issue(title='Child', parent_id=parent['id'], status='done'), UID, 102)
    leaf = tracker.create_issue(c, p['id'], tracker.Issue(title='Leaf', parent_id=child['id'], status='cancelled', cancellation_reason='Not needed'), UID, 103)
    tracker.add_comment(c, child['id'], tracker.Comment(body='Keep history'), UID, 104)
    return p, parent, child, leaf


def remove(c, item, stamp, uid=UID):
    row = tracker.issue_row(c, item['id'], include_deleted=True); p = tracker.project_row(c, row['project_id'])
    return issues.schedule(c, row['id'], uid, row['version'], p['version'], json.loads(p['payload'])['key']+'-'+str(row['number']), stamp)


def restore(c, item, stamp, uid=UID):
    row = tracker.issue_row(c, item['id'], include_deleted=True); p = tracker.project_row(c, row['project_id'])
    return issues.restore(c, row['id'], uid, row['version'], p['version'], stamp)


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_branch_recovery_is_atomic_and_preserves_numbers_states_history_and_counts(databases, engine):
    source=databases(engine);seed(source)
    with source.connection() as c:
        p,parent,child,leaf=setup(c)
        value=remove(c, parent, 200)
        assert value['delete_at']==200+7*86400
        assert remove(c, parent, 201)==value
        assert tracker.issue_page(c,p['id'],UID,202)['items']==[]
        assert sum(tracker.serialize_project(c,tracker.project_row(c,p['id']),UID,202)['counts'].values())==0
        for item in (parent,child,leaf):
            with pytest.raises(HTTPError) as denied:tracker.issue_row(c,item['id'])
            assert denied.value.status_code==404
        deleted=issues.deleted_issues(c,p['id'],UID,202)
        assert len(deleted['items'])==1 and deleted['items'][0]['count']==3
        tracker.validate_archive(c,lambda:pytest.fail('Invalid recovery state'))
        restored=restore(c,parent,203)
        assert restored['number']==parent['number'] and restored['progress']=={'total':1,'done':1,'cancelled':0}
        detail=tracker.serialize_issue(c,tracker.issue_row(c,child['id']),tracker.project_row(c,p['id']),203,detail=True)
        assert detail['status']=='done' and detail['parent_id']==parent['id']
        assert any(x['body']=='Keep history' for x in detail['comments'])
        assert tracker.issue_row(c,leaf['id'])['status']=='cancelled'
        assert not issues.records(c,p['id'])


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_project_pause_preserves_remaining_time_and_project_purge_wins(databases, engine):
    source=databases(engine);seed(source)
    with source.connection() as c:
        p,parent,child,leaf=setup(c);remove(c,parent,200)
        old=issues.pending(c,parent['id'])
        project=tracker.project_row(c,p['id']);project_deletion=projects.schedule(c,p['id'],UID,project['version'],p['name'],200+2*86400)
        assert issues.pending(c,parent['id'])['paused_at']==200+2*86400
        issues.purge(c,200+10*86400)
        assert tracker.issue_row(c,parent['id'],include_deleted=True)
        project=tracker.project_row(c,p['id']);projects.restore(c,p['id'],UID,project['version'],200+12*86400)
        shifted=issues.pending(c,parent['id'])
        assert shifted['delete_at']==old['delete_at']+10*86400
        assert shifted['delete_at']-(200+12*86400)==5*86400
        assert shifted['paused_at'] is None
        tracker.validate_archive(c,lambda:pytest.fail('Invalid shifted clock'))
        # Delete the project again: its final cleanup removes all issue markers,
        # even though their recovery clocks remain paused.
        project=tracker.project_row(c,p['id']);deadline=projects.schedule(c,p['id'],UID,project['version'],p['name'],200+13*86400)
        projects.purge(c,deadline['delete_at'])
        assert not c.execute('SELECT 1 FROM tracker_issues WHERE project_id=?',(p['id'],)).fetchone()
        assert not c.execute('SELECT 1 FROM app_settings WHERE key LIKE ?',(issues.PREFIX+'%',)).fetchone()


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_independent_child_batch_pauses_until_parent_restored_without_reset_or_resurrection(databases, engine):
    source=databases(engine);seed(source)
    with source.connection() as c:
        p,parent,child,leaf=setup(c)
        remove(c,child,200);original=issues.pending(c,child['id'])
        remove(c,parent,200+86400)
        assert len(issues.deleted_issues(c,p['id'],UID,200+86400)['items'])==1
        assert issues.pending(c,child['id'])['paused_at']==200+86400
        with pytest.raises(HTTPError):restore(c,child,200+2*86400)
        project=tracker.project_row(c,p['id']);projects.schedule(c,p['id'],UID,project['version'],p['name'],200+2*86400)
        project=tracker.project_row(c,p['id']);projects.restore(c,p['id'],UID,project['version'],200+12*86400)
        assert issues.pending(c,child['id'])['paused_at']==200+86400
        restore(c,parent,200+13*86400)
        assert tracker.issue_row(c,parent['id'])
        with pytest.raises(HTTPError):tracker.issue_row(c,child['id'])
        shifted=issues.pending(c,child['id'])
        assert shifted['delete_at']==original['delete_at']+12*86400
        assert shifted['delete_at']-(200+13*86400)==6*86400
        tracker.validate_archive(c,lambda:pytest.fail('Invalid nested deletion'))
        restore(c,child,200+14*86400)
        assert tracker.issue_row(c,leaf['id'])['parent_id']==child['id']


@pytest.mark.parametrize('engine',['sqlite','postgres'])
@pytest.mark.parametrize('backend',['local','s3'])
def test_expiry_cleans_all_rows_files_chunks_notices_and_keeps_foreign_content(databases, engine, backend, monkeypatch):
    from tests.test_tracker_attachment_scope import Remote
    source=databases(engine);seed(source)
    store=FileStore(source.root,source.cipher,backend=backend,client=Remote() if backend=='s3' else None,bucket='private')
    objects=store.scoped('tracker-attachments')
    with source.connection() as c:
        p,parent,child,leaf=setup(c)
        other=tracker.create_issue(c,p['id'],tracker.Issue(title='Survivor'),UID,105)
        files=[]
        for item,identity in [(child,'d'*32),(other,'e'*32)]:
            w=objects.writer(identity);w.write(identity.encode());w.finish()
            f=store_attachment(c,tracker.issue_row(c,item['id']),UID,'','proof.txt','text/plain',32,identity,106)
            files.append(f)
        # A service folder from another retained issue must be detached, not
        # swept merely because a legacy client nested it inside this branch.
        c.execute('UPDATE files SET parent_id=? WHERE id=?',(child['id'],other['id']))
        c.execute('INSERT INTO tracker_uploads(id,issue_id,actor_id,peer_id,payload,size,created) VALUES(?,?,?,?,?,?,?)',('f'*32,leaf['id'],UID,'','{}',3,107))
        c.execute('INSERT INTO tracker_upload_chunks VALUES(?,?,?)',('f'*32,0,b'abc'))
        c.execute('INSERT INTO notifications(user_id,kind,payload,created) VALUES(?,?,?,?)',(UID,'tracker',json.dumps({'project_id':p['id'],'issue_id':child['id']}),107))
        deadline=remove(c,parent,200)
        assert not c.execute('SELECT 1 FROM tracker_upload_chunks').fetchone()
        with pytest.raises(HTTPError) as error:restore(c,parent,deadline['delete_at'])
        assert error.value.status_code==409
        issues.purge(c,deadline['delete_at'])
        assert not issues.records(c,p['id'])
        for table in ['tracker_comments','tracker_attachments','tracker_history','tracker_uploads']:
            column='issue_id'
            assert not c.execute('SELECT 1 FROM '+table+' WHERE '+column+' IN (?,?,?)',(parent['id'],child['id'],leaf['id'])).fetchone()
        assert not c.execute('SELECT 1 FROM files WHERE id=?',(files[0]['id'],)).fetchone()
        assert not c.execute("SELECT 1 FROM notifications WHERE kind='tracker'").fetchone()
        assert c.execute('SELECT 1 FROM blob_gc WHERE id=?',('d'*32,)).fetchone()
        assert tracker.issue_row(c,other['id'])
        assert c.execute('SELECT parent_id FROM files WHERE id=?',(other['id'],)).fetchone()[0] is None
        assert c.execute('SELECT 1 FROM files WHERE id=?',(files[1]['id'],)).fetchone()
    from app.modules.files import create_files_router
    monkeypatch.setattr('app.modules.files.file_store',lambda db,root:store)
    router=create_files_router(source.connection,None,None,None,None,lambda:999999,lambda *a:None,source.root,'https://test.example',None)
    original=router.store.delete
    def unavailable(identity):raise OSError('simulated storage outage')
    monkeypatch.setattr(router.store,'delete',unavailable)
    router.cleanup()
    with source.connection() as c:assert c.execute('SELECT 1 FROM blob_gc WHERE id=?',('d'*32,)).fetchone()
    assert objects.exists('d'*32)
    monkeypatch.setattr(router.store,'delete',original)
    router.cleanup()
    with source.connection() as c:assert not c.execute('SELECT 1 FROM blob_gc WHERE id=?',('d'*32,)).fetchone()
    assert not objects.exists('d'*32)
    assert b''.join(objects.iter_read('e'*32))==b'e'*32


@pytest.mark.parametrize('project_first',[False,True])
def test_project_delete_and_issue_restore_serialize_without_deadlock_or_lost_state(databases, monkeypatch, project_first):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    source=databases('postgres');seed(source)
    with source.connection() as c:
        p,parent,child,leaf=setup(c);remove(c,parent,200)
    entered=Event();release=Event();second_started=Event()
    module,name=(projects,'locked_owner') if project_first else (issues,'_locked')
    original=getattr(module,name)
    def held(*args,**kwargs):
        result=original(*args,**kwargs)
        if not entered.is_set():
            entered.set();assert release.wait(5)
        return result
    monkeypatch.setattr(module,name,held)
    def project_delete():
        try:
            with source.connection() as c:
                row=tracker.project_row(c,p['id'])
                return projects.schedule(c,p['id'],UID,row['version'],p['name'],300)
        except HTTPError as error:return error.status_code
    def issue_restore():
        try:
            with source.connection() as c:return restore(c,parent,300)
        except HTTPError as error:return error.status_code
    first,second=(project_delete,issue_restore) if project_first else (issue_restore,project_delete)
    def run_second():second_started.set();return second()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(first);assert entered.wait(5)
        b=pool.submit(run_second);assert second_started.wait(5);release.set()
        a.result(timeout=8);out=b.result(timeout=8)
    with source.connection() as c:
        if not projects.pending(c,p['id']):
            assert not project_first and out==409
            row=tracker.project_row(c,p['id']);projects.schedule(c,p['id'],UID,row['version'],p['name'],300)
        assert projects.pending(c,p['id'])
        if project_first:
            assert out==404 and issues.pending(c,parent['id'])['paused_at']==300
        else:assert issues.pending(c,parent['id']) is None
        tracker.validate_archive(c,lambda:pytest.fail('Concurrent operations corrupted recovery state'))


@pytest.mark.parametrize('engine',['sqlite','postgres'])
@pytest.mark.parametrize('paused',[False,True])
def test_backup_preserves_paused_issue_clocks_and_expired_restore_does_not_renew(databases, engine, paused):
    source=databases(engine);seed(source)
    with source.connection() as c:
        p,parent,child,leaf=setup(c);deadline=remove(c,parent,200)
        if paused:
            project=tracker.project_row(c,p['id']);projects.schedule(c,p['id'],UID,project['version'],p['name'],200+2*86400)
    service=backup_service(source)
    service.now=lambda:200+3*86400
    with service.temp() as directory:
        manifest=service.snapshot(directory);body=service.encrypt(directory,manifest,'Issue-backup-password-123').read_bytes()
    target=databases(engine);restored=backup_service(target);restored.now=lambda:200+10*86400
    with restored.temp() as directory:
        metadata=restored.prepare(body,'Issue-backup-password-123',directory);restored.restore(directory,metadata)
    with target.connection() as c:
        assert tracker.project_row(c,p['id'])
        if paused:
            value=issues.pending(c,parent['id']);assert value['paused_at']==200+2*86400
            project=tracker.project_row(c,p['id']);projects.restore(c,p['id'],UID,project['version'],200+10*86400)
            assert issues.pending(c,parent['id'])['delete_at']-(200+10*86400)==5*86400
            assert restore(c,parent,200+10*86400+1)['id']==parent['id']
        else:
            assert not c.execute('SELECT 1 FROM tracker_issues WHERE project_id=?',(p['id'],)).fetchone()
            assert not issues.records(c,p['id'])


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_wrong_versions_rights_and_forged_archive_do_not_destroy_or_restore(databases, engine):
    source=databases(engine);seed(source)
    with source.connection() as c:
        p,parent,child,leaf=setup(c);row=tracker.issue_row(c,parent['id']);project=tracker.project_row(c,p['id'])
        with pytest.raises(HTTPError):issues.schedule(c,parent['id'],UID,row['version'],project['version']-1,'KEEP-1',200)
        with pytest.raises(HTTPError):issues.schedule(c,parent['id'],UID,row['version'],project['version'],'wrong key',200)
        assert not issues.pending(c,parent['id'])
        other='b'*32;c.execute('INSERT INTO users(id,login,password,role,created) VALUES(?,?,?,?,?)',(other,'viewer','unused','member',1))
        c.execute("INSERT INTO tracker_members(project_id,actor_id,role,state,created) VALUES(?,?,'viewer','active',1)",(p['id'],other))
        with pytest.raises(HTTPError):remove(c,parent,200,uid=other)
        remove(c,parent,200)
        with pytest.raises(HTTPError):restore(c,parent,201,uid=other)
        forged=issues.pending(c,parent['id']);forged['delete_at']+=1
        c.execute('UPDATE app_settings SET value=? WHERE key=?',(json.dumps(forged),issues.PREFIX+parent['id']))
        with pytest.raises(ValueError):tracker.validate_archive(c,lambda:(_ for _ in ()).throw(ValueError('forged clock')))
