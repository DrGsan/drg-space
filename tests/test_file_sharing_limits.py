"""Download attempts and retention remain independent of publication access."""
from concurrent.futures import ThreadPoolExecutor
from app.retention import cleanup
from tests.test_security import m,owner,member,client,reset
from tests.test_widgets import enable
from tests.test_files_trash import ok,folder,upload,row,WEEK

def link(c,file,**options):return ok(c.post('/api/files/'+file['id']+'/shares',json={'mode':'public',**options}))
def budget(c,file):return ok(c.get('/api/files/'+file['id']+'/shares'))[0]
def url(share,file):return '/api/shared-files/'+share['token']+'/'+file['id']+'/content'
def confirm(c,file,body):
 plan=ok(c.post('/api/files/'+file['id']+'/share-preview',json=body))
 return plan,ok(c.post('/api/files/'+file['id']+'/shares',json={**body,'confirmation':plan['confirmation']}))

def test_budget_refresh_confirms_real_download_and_preserves_publication_access(monkeypatch):
 c,_,_=owner();enable(c,'files');root=folder(c);f=upload(c,parent=root['id']);s=link(c,root,download_limit=2);g=client()
 path='/api/shared-files/'+s['token']+'/budget';ticket=g.head(url(s,f)).headers['x-drg-download-grant']
 initial=ok(g.get(path,params={'download_grant':ticket}));assert initial['used']==0 and not initial['started']
 assert g.get(url(s,f)+'?download_grant='+ticket).status_code==200
 after=ok(g.get(path,params={'download_grant':ticket}));assert after['used']==1 and after['started']
 other=link(c,root,download_limit=1)
 assert not ok(g.get('/api/shared-files/'+other['token']+'/budget',params={'download_grant':ticket}))['started']
 # A large folder never expands its children or reads file bodies for this counter.
 from app.database import Connection
 execute=Connection.execute;queries=[]
 def count(db,query,*args,**kwargs):queries.append(query);return execute(db,query,*args,**kwargs)
 monkeypatch.setattr(Connection,'execute',count)
 assert ok(g.get(path))['used']==1
 assert not any('parent_id=' in query or 'RECURSIVE' in query for query in queries)
 assert len(queries)<20
 monkeypatch.setattr(Connection,'execute',execute)
 member(c)
 for mode,options,status in [('password',{'password':'Password-123'},423),('authenticated',{},401),('users',{'allowed':['friend']},401)]:
  restricted=link(c,root,mode=mode,**options)
  assert g.get('/api/shared-files/'+restricted['token']+'/budget').status_code==status
 ok(c.delete('/api/file-shares/'+s['token']));assert g.get(path).status_code==404

def test_catalogue_deadline_is_account_scoped_and_clears_after_maintenance(monkeypatch):
 c,_,_=owner();enable(c,'files');root=folder(c);upload(c,parent=root['id'])
 initial=ok(c.get('/api/files'));assert initial['refresh_at'] is None and initial['server_time']==m.now()
 _,s=confirm(c,root,{'mode':'public','duration':1,'unit':'minutes','auto_delete':True})
 assert ok(c.get('/api/files'))['refresh_at']==s['expires']
 # The deadline is also supplied while browsing elsewhere, so counts update there.
 assert ok(c.get('/api/files',params={'parent_id':root['id']}))['refresh_at']==s['expires']
 friend,_=member(c);enable(friend,'files')
 assert ok(friend.get('/api/files'))['refresh_at'] is None
 monkeypatch.setattr(m,'now',lambda:s['expires']+1)
 assert ok(c.get('/api/files'))['refresh_at']==s['expires']  # pending maintenance
 cleanup(m.db,m.now())
 final=ok(c.get('/api/files'));assert final['refresh_at'] is None and final['items']==[] and final['trash_count']==2

def test_preview_head_password_and_invalid_selection_do_not_spend_budget():
 c,_,_=owner();enable(c,'files');f=upload(c);s=link(c,f,download_limit=1);g=client();path=url(s,f)
 assert g.head(path).status_code==200
 assert g.get(path+'?preview=true').content==b'recoverable file'
 assert g.get('/api/shared-files/'+s['token']+'/'+f['id']+'/text').status_code==200
 assert g.get('/api/shared-files/'+s['token']+'/download.zip?ids=missing').status_code==404
 assert budget(c,f)['used']==0
 assert g.get(path).status_code==200;assert budget(c,f)['used']==1
 assert g.get(path).status_code==410;assert g.get(path+'?preview=true').status_code==410
 assert g.get('/api/shared-files/'+s['token']+'/'+f['id']+'/text').status_code==410
 assert g.get('/api/shared-files/'+s['token']).status_code==200
 p=link(c,f,mode='password',password='Password-123',download_limit=1)
 assert g.get(url(p,f)).status_code==423
 assert g.post('/api/shared-files/'+p['token']+'/unlock',json={'password':'wrong'}).status_code!=200
 with m.db() as db:assert db.execute('SELECT used FROM file_share_limits WHERE share_token=?',(p['token'],)).fetchone()[0]==0

def test_signed_attempt_retries_ranges_and_link_revocation():
 c,_,_=owner();enable(c,'files');f=upload(c,body=b'0123456789');s=link(c,f,download_limit=1);g=client();path=url(s,f)
 ticket=g.head(path).headers['x-drg-download-grant'];retry=path+'?download_grant='+ticket
 assert g.get(retry,headers={'Range':'bytes=0-3'}).content==b'0123'
 r=g.get(retry,headers={'Range':'bytes=4-'});assert r.status_code==206 and r.content==b'456789'
 assert g.get(retry).content==b'0123456789';assert budget(c,f)['used']==1
 other=upload(c,'other.txt');assert g.get(url(s,other)+'?download_grant='+ticket).status_code==404
 ok(c.delete('/api/file-shares/'+s['token']));assert g.get(retry).status_code==404

def test_missing_object_and_invalid_range_do_not_spend():
 c,_,_=owner();enable(c,'files');f=upload(c);s=link(c,f,download_limit=1);g=client()
 assert g.get(url(s,f),headers={'Range':'bytes=9999-'}).status_code==416
 m.files_router.store.delete(row(f['id'])['object_id'])
 assert g.get(url(s,f)).status_code>=500
 assert budget(c,f)['used']==0

def test_parallel_downloads_never_exceed_cap_and_limit_change_keeps_count():
 c,_,_=owner();enable(c,'files');f=upload(c);s=link(c,f,download_limit=2);path=url(s,f)
 def get(_):return client().get(path).status_code
 with ThreadPoolExecutor(max_workers=4) as executor:statuses=list(executor.map(get,range(4)))
 assert statuses.count(200)==2 and statuses.count(410)==2
 state=budget(c,f);assert state['used']==2
 ok(c.patch('/api/file-shares/'+s['token']+'/limit',json={'version':state['version'],'download_limit':3}))
 assert client().get(path).status_code==200;assert budget(c,f)['used']==3
 assert c.patch('/api/file-shares/'+s['token']+'/limit',json={'version':state['version'],'download_limit':4}).status_code==409

def test_zip_counts_once_for_whole_selection_both_get_and_post():
 c,_,_=owner();enable(c,'files');root=folder(c);upload(c,parent=root['id']);upload(c,'second.txt',parent=root['id']);s=link(c,root,download_limit=2);g=client();path='/api/shared-files/'+s['token']
 assert g.head(path+'/download.zip').status_code==200
 assert budget(c,root)['used']==0
 assert g.get(path+'/download.zip').status_code==200
 assert g.post(path+'/download',json={'ids':[]}).status_code==200
 assert budget(c,root)['used']==2
 assert g.get(path+'/download.zip').status_code==410

def test_individual_and_folder_expiry_do_not_rewrite_each_other(monkeypatch):
 c,_,_=owner();enable(c,'files');a=upload(c);b=upload(c,'second.txt');one=link(c,a,duration=1);two=link(c,b,duration=1);root=folder(c)
 for f in (a,b):ok(c.post('/api/files/move',json={'items':[{'id':f['id'],'version':f['version']}],'parent_id':root['id']}))
 combined=link(c,root,duration=2)
 assert max(one['expires'],two['expires'])<combined['expires']
 monkeypatch.setattr(m,'now',lambda:one['expires']+1)
 assert client().get(url(one,a)).status_code==404
 assert client().get(url(combined,a)).status_code==200
 assert client().get(url(combined,b)).status_code==200
 with m.db() as db:assert db.execute('SELECT COUNT(*) FROM file_retention').fetchone()[0]==0

def test_delete_waits_for_all_links_then_trash_restore_cancels(monkeypatch):
 c,_,_=owner();enable(c,'files');f=upload(c);root=folder(c);original_now=m.now
 _,first=confirm(c,f,{'mode':'public','duration':1,'auto_delete':True})
 ok(c.post('/api/files/move',json={'items':[{'id':f['id'],'version':f['version']}],'parent_id':root['id']}))
 # A later folder link requires explicit acknowledgement of the postponed date.
 body={'mode':'public','duration':2}
 assert c.post('/api/files/'+root['id']+'/shares',json=body).status_code==409
 plan,second=confirm(c,root,body);assert plan['files'][0]['delete_at']==second['expires']
 monkeypatch.setattr(m,'now',lambda:first['expires']+1);cleanup(m.db,m.now());assert row(f['id'])['deleted'] is None
 monkeypatch.setattr(m,'now',lambda:second['expires']+1);cleanup(m.db,m.now());assert row(f['id'])['deleted']==m.now()
 assert row(root['id'])['deleted'] is None
 monkeypatch.setattr(m,'now',original_now)
 ok(c.post('/api/files/'+f['id']+'/restore'));cleanup(m.db,m.now()+100);assert row(f['id'])['deleted'] is None
 assert ok(c.get('/api/files/'+f['id']+'/retention'))['files']==[]

def test_opt_in_fixed_snapshot_and_seven_day_physical_cleanup(monkeypatch):
 c,_,_=owner();enable(c,'files');root=folder(c);f=upload(c,parent=root['id']);identity=row(f['id'])['object_id']
 body={'mode':'public','duration':1,'auto_delete':True};plan,s=confirm(c,root,body);assert len(plan['files'])==2
 later=upload(c,'later.txt',parent=root['id'])
 monkeypatch.setattr(m,'now',lambda:s['expires']+1);cleanup(m.db,m.now());assert row(f['id'])['deleted'] is not None;assert row(later['id'])['deleted'] is None
 assert m.files_router.store.exists(identity)
 cleanup(m.db,m.now()+WEEK);m.files_router.cleanup();assert row(f['id']) is None;assert not m.files_router.store.exists(identity)

def test_unlimited_intersection_and_stale_confirmation_are_rejected():
 c,_,_=owner();enable(c,'files');root=folder(c);f=upload(c,parent=root['id']);link(c,root)
 body={'mode':'public','duration':1,'auto_delete':True}
 assert c.post('/api/files/'+f['id']+'/share-preview',json=body).status_code==422
 other=upload(c,'other.txt');plan=ok(c.post('/api/files/'+other['id']+'/share-preview',json=body))
 ok(c.patch('/api/files/'+other['id'],json={'name':'renamed.txt','version':other['version']}))
 assert c.post('/api/files/'+other['id']+'/shares',json={**body,'confirmation':plan['confirmation']}).status_code==409
 assert c.post('/api/files/'+other['id']+'/shares',json={'mode':'public','auto_delete':True}).status_code==422

def test_owner_only_cancel_and_change_with_confirmation():
 c,_,_=owner();enable(c,'files');f=upload(c);_,s=confirm(c,f,{'mode':'public','duration':1,'auto_delete':True});guest,_=member(c);enable(guest,'files')
 state=ok(c.get('/api/files/'+f['id']+'/retention'))['files'][0]
 assert guest.request('DELETE','/api/files/'+f['id']+'/retention',json={'version':state['version']}).status_code==404
 body={'version':state['version'],'delete_at':s['expires']+86400}
 plan=ok(c.post('/api/files/'+f['id']+'/retention-preview',json=body));ok(c.patch('/api/files/'+f['id']+'/retention',json={**body,'confirmation':plan['confirmation']}))
 state=ok(c.get('/api/files/'+f['id']+'/retention'))['files'][0];assert state['delete_at']==body['delete_at']
 ok(c.request('DELETE','/api/files/'+f['id']+'/retention',json={'version':state['version']}));assert ok(c.get('/api/files/'+f['id']+'/retention'))['files']==[]

def test_move_delays_timer_but_does_not_change_any_link_expiry():
 c,_,_=owner();enable(c,'files');f=upload(c);_,first=confirm(c,f,{'mode':'public','duration':1,'auto_delete':True});target=folder(c);later=link(c,target,duration=3)
 result=ok(c.post('/api/files/move',json={'items':[{'id':f['id'],'version':f['version']}],'parent_id':target['id']}));assert result['retention_changes'][0]['delete_at']==later['expires']
 assert ok(c.get('/api/files/'+f['id']+'/shares'))[0]['expires']==first['expires']
 assert ok(c.get('/api/files/'+f['id']+'/retention'))['files'][0]['delete_at']==later['expires']
 other=folder(c,'Unlimited');link(c,other)
 assert c.post('/api/files/move',json={'items':[{'id':f['id'],'version':row(f['id'])['version']}],'parent_id':other['id']}).status_code==422
 assert row(f['id'])['parent_id']==target['id']

def test_download_receipt_cannot_be_reused_for_other_selection_or_modified_file():
 c,_,_=owner();enable(c,'files');root=folder(c);a=upload(c,parent=root['id']);b=upload(c,'b.txt',parent=root['id']);s=link(c,root,download_limit=2);g=client()
 receipt=g.head(url(s,a)).headers['x-drg-download-grant']
 assert g.get(url(s,b)+'?download_grant='+receipt).status_code==409
 assert budget(c,root)['used']==0
 ok(c.put('/api/files/'+a['id']+'/text',json={'text':'modified','version':a['version']}))
 assert g.get(url(s,a)+'?download_grant='+receipt).status_code==409
 assert budget(c,root)['used']==0

def test_revoking_a_link_does_not_cancel_or_accelerate_scheduled_deletion():
 c,_,_=owner();enable(c,'files');f=upload(c);_,s=confirm(c,f,{'mode':'public','duration':1,'auto_delete':True})
 ok(c.delete('/api/file-shares/'+s['token']))
 assert ok(c.get('/api/files/'+f['id']+'/retention'))['files'][0]['delete_at']==s['expires']
 cleanup(m.db,s['expires']-1);assert row(f['id'])['deleted'] is None
 cleanup(m.db,s['expires']+1);assert row(f['id'])['deleted'] is not None

def test_unrelated_links_do_not_invalidate_retention_confirmation():
 c,_,_=owner();enable(c,'files');a=upload(c);b=upload(c,'other.txt');body={'mode':'public','duration':1,'auto_delete':True}
 plan=ok(c.post('/api/files/'+a['id']+'/share-preview',json=body));link(c,b)
 ok(c.post('/api/files/'+a['id']+'/shares',json={**body,'confirmation':plan['confirmation']}))
 assert len(ok(c.get('/api/files/'+a['id']+'/retention'))['files'])==1


def test_shared_catalogue_reports_latest_active_expiry_without_changing_file_date(monkeypatch):
 c,_,_=owner();enable(c,'files');f=upload(c);untouched=upload(c,'private.txt');modified=row(f['id'])['updated']
 short=link(c,f,duration=1,unit='minutes');long=link(c,f,duration=2,unit='minutes')
 items=ok(c.get('/api/files?view=shared'))['items'];assert len(items)==1
 assert items[0]['shared_until']==long['expires'] and items[0]['updated']==modified
 forever=link(c,f);assert ok(c.get('/api/files?view=shared'))['items'][0]['shared_until'] is None
 ok(c.delete('/api/file-shares/'+forever['token']))
 assert ok(c.get('/api/files?view=shared'))['items'][0]['shared_until']==long['expires']
 assert all('shared_until' not in x for x in ok(c.get('/api/files'))['items'])
 ok(c.delete('/api/file-shares/'+long['token']))
 assert ok(c.get('/api/files?view=shared'))['items'][0]['shared_until']==short['expires']
 monkeypatch.setattr(m,'now',lambda:short['expires']+1)
 assert ok(c.get('/api/files/'+f['id']+'/shares'))==[]
 assert ok(c.get('/api/files?view=shared'))['items']==[]


def test_overdue_timer_survives_database_reopen_and_starts_seven_days_at_actual_trash_time(monkeypatch):
 from app.database import Database
 c,_,_=owner();enable(c,'files');f=upload(c)
 _,s=confirm(c,f,{'mode':'public','duration':5,'unit':'minutes','auto_delete':True})
 resumed=s['expires']+600
 reopened=Database(m.DATA,cipher=m.data_cipher);reopened.initialize()
 cleanup(reopened.connection,resumed)
 assert row(f['id'])['deleted']==resumed
 monkeypatch.setattr(m,'now',lambda:resumed)
 assert client().get(url(s,f)).status_code==404
 cleanup(reopened.connection,resumed+WEEK-1);assert row(f['id']) is not None
 cleanup(reopened.connection,resumed+WEEK);assert row(f['id']) is None


def test_marked_folder_waits_for_individual_links_then_joins_trash_and_can_be_restored(monkeypatch):
 c,_,_=owner();enable(c,'files');root=folder(c);a=upload(c,parent=root['id']);b=upload(c,'b.txt',parent=root['id'])
 longer=link(c,a,duration=3,unit='minutes')
 plan,s=confirm(c,root,{'mode':'public','duration':1,'unit':'minutes','auto_delete':True})
 assert len(plan['files'])==3
 assert next(x for x in plan['files'] if x['id']==root['id'])['delete_at']==longer['expires']
 cleanup(m.db,s['expires']+1);assert row(b['id'])['deleted'] is not None and row(root['id'])['deleted'] is None
 resumed=longer['expires']+1;cleanup(m.db,resumed)
 assert row(root['id'])['deleted']==resumed and row(a['id'])['deleted']==resumed
 monkeypatch.setattr(m,'now',lambda:resumed)
 trashed=ok(c.get('/api/files/trash'))['items'];assert {x['id'] for x in trashed}=={root['id'],a['id'],b['id']}
 ok(c.post('/api/files/'+root['id']+'/restore'))
 # Restoring an empty container does not resurrect independently trashed files.
 assert row(a['id'])['deleted'] is not None
 for f in (a,b):ok(c.post('/api/files/'+f['id']+'/restore'));assert row(f['id'])['parent_id']==root['id']
 cleanup(m.db,resumed+60);assert row(root['id'])['deleted'] is None
 assert ok(c.get('/api/files/'+root['id']+'/retention'))['files']==[]


def test_new_content_parks_marked_folder_and_removing_it_resumes_without_deleting_new_files(monkeypatch):
 c,_,_=owner();enable(c,'files');root=folder(c);old=upload(c,parent=root['id'])
 _,s=confirm(c,root,{'mode':'public','duration':1,'unit':'minutes','auto_delete':True})
 fresh=upload(c,'new.txt',parent=root['id']);resumed=s['expires']+1
 cleanup(m.db,resumed);assert row(old['id'])['deleted'] is not None and row(root['id'])['deleted'] is None and row(fresh['id'])['deleted'] is None
 state=ok(c.get('/api/files/'+root['id']+'/retention'))['files'];assert len(state)==1 and state[0]['kind']=='folder' and state[0]['delete_at'] is None
 monkeypatch.setattr(m,'now',lambda:resumed)
 ok(c.post('/api/files/move',json={'items':[{'id':fresh['id'],'version':fresh['version']}],'parent_id':None}))
 cleanup(m.db,resumed+1);assert row(root['id'])['deleted'] is not None and row(fresh['id'])['deleted'] is None and row(fresh['id'])['parent_id'] is None


def test_empty_and_nested_folders_are_explicit_targets_but_new_subfolders_are_preserved():
 c,_,_=owner();enable(c,'files');root=folder(c);nested=folder(c,'Nested',root['id'])
 plan,s=confirm(c,root,{'mode':'public','duration':1,'unit':'minutes','auto_delete':True});assert len(plan['files'])==2
 cleanup(m.db,s['expires']+1);assert row(root['id'])['deleted'] is not None and row(nested['id'])['deleted'] is not None
 other=folder(c,'Other');_,s=confirm(c,other,{'mode':'public','duration':1,'unit':'minutes','auto_delete':True})
 fresh=folder(c,'New',other['id']);cleanup(m.db,s['expires']+1)
 assert row(other['id'])['deleted'] is None and row(fresh['id'])['deleted'] is None


def test_longer_child_link_requires_confirmation_for_marked_parent_folder():
 c,_,_=owner();enable(c,'files');root=folder(c);f=upload(c,parent=root['id'])
 _,s=confirm(c,root,{'mode':'public','duration':1,'unit':'minutes','auto_delete':True})
 body={'mode':'public','duration':3,'unit':'minutes'}
 assert c.post('/api/files/'+f['id']+'/shares',json=body).status_code==409
 plan,later=confirm(c,f,body)
 assert {x['id'] for x in plan['files']}=={f['id'],root['id']}
 assert all(x['delete_at']==later['expires'] for x in plan['files'])
 cleanup(m.db,s['expires']+1);assert row(root['id'])['deleted'] is None and row(f['id'])['deleted'] is None
 cleanup(m.db,later['expires']+1);assert row(root['id'])['deleted'] is not None and row(f['id'])['deleted'] is not None


def test_folder_timer_can_be_changed_or_cancelled_without_changing_child_policy():
 c,_,_=owner();enable(c,'files');root=folder(c);f=upload(c,parent=root['id'])
 _,s=confirm(c,root,{'mode':'public','duration':1,'unit':'minutes','auto_delete':True})
 state=next(x for x in ok(c.get('/api/files/'+root['id']+'/retention'))['files'] if x['id']==root['id'])
 body={'version':state['version'],'delete_at':s['expires']+600};plan=ok(c.post('/api/files/'+root['id']+'/retention-preview',json=body))
 ok(c.patch('/api/files/'+root['id']+'/retention',json={**body,'confirmation':plan['confirmation']}))
 state=next(x for x in ok(c.get('/api/files/'+root['id']+'/retention'))['files'] if x['id']==root['id'])
 ok(c.request('DELETE','/api/files/'+root['id']+'/retention',json={'version':state['version']}))
 cleanup(m.db,body['delete_at']+1);assert row(root['id'])['deleted'] is None and row(f['id'])['deleted'] is not None


def test_changing_child_date_updates_and_confirms_marked_parent_date():
 c,_,_=owner();enable(c,'files');root=folder(c);f=upload(c,parent=root['id'])
 _,s=confirm(c,root,{'mode':'public','duration':1,'unit':'minutes','auto_delete':True})
 state=ok(c.get('/api/files/'+f['id']+'/retention'))['files'][0]
 body={'version':state['version'],'delete_at':s['expires']+600}
 plan=ok(c.post('/api/files/'+f['id']+'/retention-preview',json=body));assert {x['id'] for x in plan['files']}=={f['id'],root['id']}
 result=ok(c.patch('/api/files/'+f['id']+'/retention',json={**body,'confirmation':plan['confirmation']}))
 assert {x['id'] for x in result['retention_changes']}=={f['id'],root['id']}
 assert all(x['delete_at']==body['delete_at'] for x in ok(c.get('/api/files/'+root['id']+'/retention'))['files'])
 cleanup(m.db,s['expires']+1);assert row(root['id'])['deleted'] is None and row(f['id'])['deleted'] is None
 cleanup(m.db,body['delete_at']+1);assert row(root['id'])['deleted'] is not None and row(f['id'])['deleted'] is not None
