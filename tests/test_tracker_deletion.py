"""Project recovery boundaries, file-scope bypasses and canonical revocation."""
import io
import json
from zipfile import ZipFile

import pytest

from tests.test_security import owner,member,reset
from app import main as m,tracker
from app.http import HTTPError
from app import tracker_lifecycle as lifecycle
from tests.test_tracker import enable,project,issue,ok
from tests.test_task_lists import friend
from tests.test_storage_encryption import databases,seed
from tests.test_tracker_federation import pair,remote,accept,event


def current(c,p):return ok(c.get('/api/tracker/projects/'+p['id']))


def remove(c,p,**fields):
    body={'version':p['version'],'confirmation':p['name'],**fields}
    return c.request('DELETE','/api/tracker/projects/'+p['id'],json=body)


def deleted(c,p):return next(r for r in ok(c.get('/api/tracker'))['deleted_projects'] if r['id']==p['id'])


def attachment(c,item,name='receipt.txt',body=b'PRIVATE_RECEIPT'):
    return ok(c.post('/api/tracker/issues/'+item['id']+'/attachments',params={'name':name},content=body))


def test_owner_consent_version_idempotence_and_complete_recovery():
    c,_,_=owner();g,_=member(c);enable(c);enable(g);friend(c,g)
    p=project(c);parent=issue(c,p,kind='epic');child=issue(c,p,parent_id=parent['id'],status='done')
    ok(c.post('/api/tracker/issues/'+child['id']+'/comments',json={'body':'Retain comment'}))
    file=attachment(c,child)
    guest=ok(g.get('/api/me'))['id']
    ok(c.post('/api/tracker/projects/'+p['id']+'/members',json={'actor_id':guest,'role':'manager'}))
    ok(g.post('/api/tracker/projects/'+p['id']+'/accept'))
    p=current(c,p)
    assert p['can_delete'] and not current(g,p)['can_delete']
    assert remove(g,p).status_code==403
    assert remove(c,p,confirmation='Different name').status_code==422
    assert remove(c,p,version=p['version']-1).status_code==409
    assert remove(c,p,version=True).status_code==422
    scheduled=ok(remove(c,p))['deletion']
    assert scheduled['delete_at']-scheduled['created']==30*86400
    assert ok(remove(c,p,confirmation='stale retry'))['deletion']==scheduled
    catalog=ok(c.get('/api/tracker'))
    assert not any(r['id']==p['id'] for r in catalog['projects'])
    retained=deleted(c,p)
    assert retained['role']=='owner' and retained['name']==p['name'] and retained['deletion']==scheduled
    assert ok(g.get('/api/tracker'))['deleted_projects']==[]
    for actor in (c,g):
        for path in ('/projects/'+p['id'],'/projects/'+p['id']+'/issues','/issues/'+child['id'],'/projects/'+p['id']+'/avatar'):
            assert actor.get('/api/tracker'+path).status_code==404
    assert c.patch('/api/tracker/issues/'+child['id'],json={'version':child['version'],'title':'Blocked'}).status_code==404
    assert c.post('/api/tracker/issues/'+child['id']+'/comments',json={'body':'Blocked'}).status_code==404
    assert g.post('/api/tracker/projects/'+p['id']+'/restore',json={'version':retained['version']}).status_code==404
    assert c.post('/api/tracker/projects/'+p['id']+'/restore',json={'version':p['version']}).status_code==409
    restored=ok(c.post('/api/tracker/projects/'+p['id']+'/restore',json={'version':retained['version']}))
    assert restored['id']==p['id'] and restored['key']==p['key'] and restored['can_delete']
    detail=ok(g.get('/api/tracker/issues/'+child['id']))
    assert detail['parent_id']==parent['id'] and detail['status']=='done'
    assert any(r['body']=='Retain comment' for r in detail['comments'])
    assert g.get('/api/tracker/issues/'+child['id']+'/attachments/'+file['id']).content==b'PRIVATE_RECEIPT'
    assert ok(c.get('/api/tracker'))['deleted_projects']==[]


def test_generic_files_and_public_mixed_folder_cannot_bypass_pending_project():
    c,_,_=owner();enable(c);p=project(c);task=issue(c,p)
    held=attachment(c,task)
    folder=ok(c.post('/api/files/folders',json={'name':'Mixed publication'}))
    normal=ok(c.post('/api/files/upload',params={'name':'ordinary.txt','parent_id':folder['id']},content=b'ORDINARY_FILE'))
    # Legacy data: older Files clients could move/publish tracker attachments.
    link={'token':'legacy-tracker-publication'}
    with m.db() as db:
        db.execute('UPDATE files SET parent_id=? WHERE id=?',(folder['id'],held['id']))
        db.execute('INSERT INTO file_shares(token,file_id,mode,created) VALUES(?,?,?,?)',(link['token'],held['id'],'public',m.now()))
    mixed=ok(c.post('/api/files/'+folder['id']+'/shares',json={'mode':'public'}))
    p=current(c,p);ok(remove(c,p))
    assert c.get('/api/files/'+held['id']+'/content').status_code==404
    assert c.get('/api/files/'+held['id']+'/text').status_code==404
    assert c.delete('/api/files/'+held['id']).status_code==404
    assert c.post('/api/files/'+held['id']+'/shares',json={'mode':'public'}).status_code==404
    assert c.get('/api/shared-files/'+link['token']).status_code==404
    assert c.get('/api/shared-files/'+mixed['token']+'/'+held['id']+'/content').status_code==404
    entries=ok(c.get('/api/shared-files/'+mixed['token']))['items']
    assert [r['id'] for r in entries]==[normal['id']]
    response=c.get('/api/shared-files/'+mixed['token']+'/download.zip')
    assert response.status_code==200,response.text
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert any(archive.read(name)==b'ORDINARY_FILE' for name in archive.namelist() if not name.endswith('/'))
        assert all(b'PRIVATE_RECEIPT' not in archive.read(name) for name in archive.namelist() if not name.endswith('/'))
    assert c.get('/api/files/download.zip',params={'ids':held['id']}).status_code==404
    ok(c.post('/api/tracker/projects/'+p['id']+'/restore',json={'version':deleted(c,p)['version']}))
    assert c.get('/api/files/'+held['id']+'/content').status_code==404
    assert c.get('/api/files/'+held['id']+'/shares').status_code==404
    assert c.get('/api/tracker/issues/'+task['id']+'/attachments/'+held['id']).content==b'PRIVATE_RECEIPT'


@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_file_trash_pause_expiry_gc_and_foreign_project_isolation(databases,engine):
    from app.file_store import FileStore
    from app.modules.tracker import store_attachment
    from app.file_trash import purge_file_trash
    source=databases(engine);seed(source);uid='a'*32;objects=FileStore(source.root,source.cipher)
    data={}
    with source.connection() as db:
        for key,identity in [('A','1'*32),('B','2'*32)]:
            writer=objects.writer(identity);writer.write(key.encode());writer.finish()
            p=tracker.create_project(db,tracker.Project(name='Project '+key,key='WORK'+key),uid,100)
            item=tracker.create_issue(db,p['id'],tracker.Issue(title=key,status='done'),uid,101)
            file=store_attachment(db,tracker.issue_row(db,item['id']),uid,'',key+'.txt','text/plain',1,identity,102)
            data[key]=(p,item,file)
        a,ai,af=data['A'];b,bi,bf=data['B']
        # A pre-existing trash item must keep its remaining7day period.
        db.execute('UPDATE files SET deleted=90,trash_batch=? WHERE id=?',(af['id'],af['id']))
        # A foreign project's folder nested under A must survive A's purge.
        db.execute('UPDATE files SET parent_id=? WHERE id=?',(ai['id'],bi['id']))
        # Unlinked ordinary files can remain with a previous owner after a
        # project ownership transfer; their bytes must never be deleted here.
        other_uid='b'*32
        db.execute('INSERT INTO users(id,login,password,role,created) VALUES(?,?,?,?,?)',(other_uid,'other','unused','member',1))
        writer=objects.writer('3'*32);writer.write(b'FOREIGN_USER_FILE');writer.finish()
        db.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',('foreign-file-id1',other_uid,ai['id'],'file','other.txt','text/plain',17,'3'*32,1,1))
        row=tracker.project_row(db,a['id'])
        deletion=lifecycle.schedule(db,a['id'],uid,row['version'],'Project A',100)
        assert af['id'] in lifecycle.associated_files(db,a['id'])
        assert bi['id'] not in lifecycle.associated_files(db,a['id'])
    with source.connection() as db:
        purge_file_trash(db,100+8*86400)
        assert db.execute('SELECT 1 FROM files WHERE id=?',(af['id'],)).fetchone()
        row=tracker.project_row(db,a['id'])
        lifecycle.restore(db,a['id'],uid,row['version'],100+8*86400)
        shifted=db.execute('SELECT deleted,trash_batch FROM files WHERE id=?',(af['id'],)).fetchone()
        assert shifted['deleted']==90+8*86400 and shifted['trash_batch']==af['id']
        row=tracker.project_row(db,a['id'])
        deletion=lifecycle.schedule(db,a['id'],uid,row['version'],'Project A',100+8*86400)
    with source.connection() as db:
        row=tracker.project_row(db,a['id'])
        with pytest.raises(HTTPError) as error:lifecycle.restore(db,a['id'],uid,row['version'],deletion['delete_at'])
        assert error.value.status_code==409
    with source.connection() as db:
        lifecycle.purge(db,deletion['delete_at'])
        assert not db.execute('SELECT 1 FROM tracker_projects WHERE id=?',(a['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM files WHERE id=?',(af['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM blob_gc WHERE id=? AND kind=?',('1'*32,'file')).fetchone()
        assert tracker.issue_row(db,bi['id'])['status']=='done'
        assert db.execute('SELECT parent_id FROM files WHERE id=?',(bi['id'],)).fetchone()[0] is None
        assert db.execute('SELECT 1 FROM files WHERE id=?',(bf['id'],)).fetchone()
        assert db.execute('SELECT parent_id FROM files WHERE id=?',('foreign-file-id1',)).fetchone()[0] is None
        assert lifecycle.pending(db,a['id']) is None
    assert b''.join(objects.iter_read('2'*32))==b'B'
    assert b''.join(objects.iter_read('3'*32))==b'FOREIGN_USER_FILE'


def test_workspace_deletion_detaches_pending_project_without_shortening_timer():
    from tests.test_people_groups import workspace,invite
    from app.workspace_lifecycle import schedule,destroy
    c,_,_=owner();g,_=member(c);enable(c);enable(g)
    sid=workspace(c,'group');invite(c,g,sid,'friend');p=project(c,space_id=sid);task=issue(c,p,status='done')
    p=current(c,p);deadline=ok(remove(c,p))['deletion']
    uid=ok(c.get('/api/me'))['id']
    with m.db() as db:
        row=dict(db.execute('SELECT * FROM spaces WHERE id=?',(sid,)).fetchone())
        schedule(db,row,uid,m.now())
        from app.account_lifecycle import ensure_transfer
        with pytest.raises(HTTPError):ensure_transfer(db,uid,lambda code,message:(_ for _ in ()).throw(HTTPError(code,message)))
    assert c.post('/api/tracker/projects/'+p['id']+'/restore',json={'version':deleted(c,p)['version']}).status_code==409
    with m.db() as db:destroy(db,sid,m.now()+86400)
    summary=deleted(c,p)
    assert summary['space_id'] is None and summary['deletion']==deadline
    restored=ok(c.post('/api/tracker/projects/'+p['id']+'/restore',json={'version':summary['version']}))
    assert restored['space_id'] is None
    assert ok(c.get('/api/tracker/issues/'+task['id']))['status']=='done'
    assert g.get('/api/tracker/issues/'+task['id']).status_code==404


def test_full_quota_still_allows_schedule_and_restore_but_not_new_content():
    c,_,_=owner();enable(c);p=project(c);issue(c,p)
    uid=ok(c.get('/api/me'))['id'];p=current(c,p)
    with m.db() as db:db.execute('UPDATE user_limits SET limit_bytes=used_bytes WHERE user_id=?',(uid,))
    ok(remove(c,p))
    restored=ok(c.post('/api/tracker/projects/'+p['id']+'/restore',json={'version':deleted(c,p)['version']}))
    assert restored['can_delete']
    assert c.post('/api/tracker/projects/'+p['id']+'/issues',json={'title':'Over quota'}).status_code==413


def test_federated_access_closes_and_old_grants_stay_invalid_after_restore(pair):
    from app.federation.tracker import execute
    from app.federation.identity import FederationError
    from tests.test_federation_storage import UID
    p=pair;accept(p)
    assert remote(p,'tracker_page')['items']
    with p.backend.connection() as db:
        row=tracker.project_row(db,p.project['id'])
        lifecycle.schedule(db,row['id'],UID,row['version'],'Shared project',100)
    assert remote(p,'tracker_catalog')['projects']==[]
    with pytest.raises(FederationError):remote(p,'tracker_page')
    with p.backend.connection() as db:
        row=tracker.project_row(db,p.project['id']);lifecycle.restore(db,row['id'],UID,row['version'],101)
    with pytest.raises(FederationError):remote(p,'tracker_page')
    catalog=remote(p,'tracker_catalog')['projects']
    assert len(catalog)==1 and catalog[0]['grant_revision']>1
    with p.service.state.transaction() as db:
        result=execute(p.service,db,p.peer,event(p,'tracker_page',{'project_id':p.project['id'],'revision':catalog[0]['grant_revision']}))[0]
        assert result['items'][0]['id']==p.task['id']
