"""Real local storage/ACLs with a deterministic, isolated provider transport."""
import copy
import json
import httpx
import pytest
from tests.test_security import owner,member,reset
from app import main as m
from tests.test_notion_sync_connections import CONFIG,session,grant
from app.notion_sync.config import NotionConfig
from app.notion_sync.service import SyncService
from app.notion_sync.state import enqueue,dump
from app.notion_sync.client import NotionError

ROOT='11111111-1111-4111-8111-111111111111'
CHILD='22222222-2222-4222-8222-222222222222'
TEXT='33333333-3333-4333-8333-333333333333'
IMAGE='44444444-4444-4444-8444-444444444444'
NEW='55555555-5555-4555-8555-555555555555'

def rt(text):return [{'type':'text','text':{'content':text},'plain_text':text,'annotations':{'bold':True}}]
def page(identity,title,parent=None):
    return {'id':identity,'object':'page','properties':{'title':{'id':'title','type':'title','title':rt(title)}},'parent':{'type':'page_id','page_id':parent} if parent else {'type':'workspace','workspace':True},'last_edited_time':'2026-11-01T00:00:00.000Z','icon':None,'cover':None}
def paragraph(text):return {'id':TEXT,'type':'paragraph','paragraph':{'rich_text':rt(text)},'has_children':False}

class Provider:
    def __init__(self):
        self.pages={ROOT:page(ROOT,'Root'),CHILD:page(CHILD,'Child',ROOT)}
        self.blocks={ROOT:[paragraph('Hello'),{'id':CHILD,'type':'child_page','child_page':{'title':'Child'},'has_children':True}],CHILD:[]}
        self.requests=[];self.downloads=[];self.status=None
    def handle(self,req):
        self.requests.append((req.method,req.url.path))
        if req.url.path=='/v1/oauth/token':return httpx.Response(200,json=grant())
        if req.url.path=='/v1/views':return httpx.Response(200,json={'results':[],'has_more':False})
        if self.status:return httpx.Response(self.status,headers={'Retry-After':'600'})
        if req.url.path.startswith('/v1/pages/'):
            value=self.pages.get(req.url.path.rsplit('/',1)[-1]);return httpx.Response(200,json=value) if value else httpx.Response(404)
        if req.url.path.startswith('/v1/blocks/'):
            identity=req.url.path.split('/')[3]
            return httpx.Response(200,json={'results':copy.deepcopy(self.blocks[identity]),'has_more':False})
        raise AssertionError(req.url.path)
    def fetch(self,url,limit):self.downloads.append(url);return b'private attachment bytes'

def initialized():
    c,_,_=owner();s=session(c);provider=Provider();stamp=[float(m.now())]
    service=SyncService(m.db,CONFIG,m.DATA,m.PUBLIC_URL,clock=lambda:stamp[0],transport=httpx.MockTransport(provider.handle),wait=lambda seconds:stamp.__setitem__(0,stamp[0]+seconds),fetch=provider.fetch)
    with m.db() as db:
        db.execute('DELETE FROM notion_budgets')
        db.execute('DELETE FROM notion_events')
        db.execute('INSERT INTO notion_connections(id,user_id,kind,workspace_id,bot_id,payload,created,updated) VALUES(?,?,?,?,?,?,?,?)',('read',s['user_id'],'read','workspace','bot',dump(grant()),int(stamp[0]),int(stamp[0])))
    return c,s,provider,service,stamp

def imported(service,s):
    preview=service.preview(s['user_id'],'read',ROOT);identity=preview['id']
    assert service.work_once()
    status=service.status(s['user_id'])['roots'][0]
    assert status['status']=='ready',status
    service.confirm(s['user_id'],identity,status['preview_hash'])
    while service.work_once():pass
    status=service.status(s['user_id'])['roots'][0]
    assert status['status']=='active',status
    with m.db() as db:mapping={row['notion_id']:row['note_id'] for row in db.execute('SELECT * FROM notion_pages WHERE root_id=?',(identity,)).fetchall()}
    return identity,mapping

def test_disabled_integration_never_queries_db_or_calls_network():
    def forbidden():raise AssertionError('disabled integration started work')
    service=SyncService(forbidden,NotionConfig(),m.DATA,m.PUBLIC_URL)
    assert service.work_once() is False

def test_preview_is_additive_import_tree_is_readonly_and_duplicate_delivery_does_not_create_notes():
    c,s,provider,service,stamp=initialized()
    existing=c.post('/api/notes',json={'title':'Existing import','body':'Keep me'}).json()['id']
    identity,mapping=imported(service,s)
    assert len(provider.requests)==6 and not provider.downloads  # fresh ancestry at selection and confirmation
    root=c.get('/api/notes/'+mapping[ROOT]).json();child=c.get('/api/notes/'+mapping[CHILD]).json()
    assert root['role']=='viewer' and root['notion_sync']['read_only']
    assert child['parent_id']==root['id'] and root['blocks'][1]['props']['pageId']==child['id']
    assert root['blocks'][0]['content'][0]['styles']['bold']
    for path,method,data in [(f'/api/notes/{root["id"]}','put',{'title':'Bypass','body':'No','version':root['version']}),(f'/api/notes/{root["id"]}','patch',{'icon':'x'}),(f'/api/notes/{root["id"]}','delete',None),(f'/api/notes','post',{'title':'Bypass child','parent_id':root['id']})]:
        response=getattr(c,method)(path,**({'json':data} if data is not None else {}))
        assert response.status_code==423,(path,response.text)
    assert c.get('/api/notes/'+existing).json()['body']=='Keep me'
    with m.db() as db:
        db.execute('BEGIN IMMEDIATE')
        for _ in range(100):enqueue(db,identity,ROOT,'pull',int(stamp[0]))
        assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==1
    before=len(provider.requests);assert service.work_once();assert not service.work_once()
    assert len(provider.requests)-before==2
    assert c.get('/api/notes/'+root['id']).json()['version']==root['version']
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==3

def test_changed_page_fetches_no_existing_descendants_and_unchanged_file_is_reused():
    c,s,provider,service,stamp=initialized()
    provider.blocks[ROOT].append({'id':IMAGE,'type':'file','file':{'type':'file','file':{'url':'https://files.example/attachment.pdf?signature=first'},'name':'attachment.pdf'},'has_children':False})
    identity,mapping=imported(service,s)
    assert len(provider.downloads)==1
    initial=c.get('/api/notes/'+mapping[ROOT]).json();file_url=initial['blocks'][2]['props']['url']
    assert file_url.startswith(m.PUBLIC_URL+'/api/notes/')
    assert c.get(file_url.removeprefix(m.PUBLIC_URL)).content==b'private attachment bytes'
    provider.blocks[ROOT][0]=paragraph('Changed')
    provider.blocks[ROOT][2]['file']['file']['url']='https://files.example/attachment.pdf?signature=rotated'
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    before=len(provider.requests);assert service.work_once()
    saved=c.get('/api/notes/'+mapping[ROOT]).json()
    assert saved['blocks'][0]['content'][0]['text']=='Changed'
    assert saved['blocks'][2]['props']['url']==file_url and len(provider.downloads)==1
    assert len(provider.requests)-before==2

def test_new_child_appears_automatically_and_revoke_pause_disconnect_keep_existing_copy():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    provider.pages[NEW]=page(NEW,'New child',ROOT);provider.blocks[NEW]=[]
    provider.blocks[ROOT].append({'id':NEW,'type':'child_page','child_page':{'title':'New child'},'has_children':True})
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once();assert service.work_once()
    with m.db() as db:new=db.execute('SELECT note_id FROM notion_pages WHERE notion_id=?',(NEW,)).fetchone()[0]
    assert c.get('/api/notes/'+new).json()['parent_id']==mapping[ROOT]
    provider.status=403
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once()
    root=service.status(s['user_id'])['roots'][0]
    assert root['status']=='paused' and c.get('/api/notes/'+mapping[ROOT]).status_code==200
    assert not service.work_once()
    service.action(s['user_id'],identity,'disconnect',root['version'])
    assert c.get('/api/notes/'+mapping[ROOT]).json()['role']=='editor'

def test_other_account_cannot_confirm_or_manage_preview():
    c,s,provider,service,stamp=initialized();g,_=member(c);guest=g.get('/api/me').json()['id']
    identity=service.preview(s['user_id'],'read',ROOT)['id']
    for action in (lambda:service.confirm(guest,identity,'hash'),lambda:service.action(guest,identity,'cancel',1)):
        with pytest.raises(NotionError) as error:action()
        assert error.value.status==404

def test_provider_retry_after_survives_restart_without_additional_provider_requests():
    c,s,provider,service,stamp=initialized();service.preview(s['user_id'],'read',ROOT);provider.status=429
    assert service.work_once();count=len(provider.requests)
    assert not service.work_once() and len(provider.requests)==count
    with m.db() as db:
        assert db.execute('SELECT due FROM notion_jobs').fetchone()[0]>=stamp[0]+599
        assert db.execute('SELECT cooldown FROM notion_connections').fetchone()[0]>=stamp[0]+599


def test_independent_copy_owns_attachments_and_is_editable_after_source_file_is_removed():
    from app.notion_sync.copy import independent
    c,s,provider,service,stamp=initialized()
    provider.blocks[ROOT].append({'id':IMAGE,'type':'file','file':{'type':'file','file':{'url':'https://files.example/attachment.pdf?signature=first'},'name':'attachment.pdf'},'has_children':False})
    identity,mapping=imported(service,s)
    copied=independent(service,s['user_id'],mapping[ROOT])['note_id']
    source=c.get('/api/notes/'+mapping[ROOT]).json();copy=c.get('/api/notes/'+copied).json()
    assert copy['role']=='editor' and 'notion_sync' not in copy and copy['parent_id'] is None
    assert copy['blocks'][1]['props']['pageId']!=mapping[CHILD]
    source_file=source['blocks'][2]['props']['url'];copied_file=copy['blocks'][2]['props']['url']
    assert source_file!=copied_file
    assert c.put('/api/notes/'+copied,json={'title':'Independent','body':copy['body'],'blocks':copy['blocks'],'version':copy['version']}).status_code==200
    with m.db() as db:
        file=db.execute('SELECT object_id FROM files WHERE id=?',(source_file.rsplit('/',1)[-1],)).fetchone()[0]
        db.execute('DELETE FROM files WHERE id=?',(source_file.rsplit('/',1)[-1],))
    from app.file_store import file_store
    file_store(m.db,m.DATA).delete(file)
    assert c.get(copied_file.removeprefix(m.PUBLIC_URL)).content==b'private attachment bytes'
    assert c.get('/api/notes/'+mapping[ROOT]).json()['title']=='Root'


def test_readonly_link_cannot_be_published_with_edit_permission_but_view_publication_works():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    path='/api/notes/'+mapping[ROOT]+'/shares'
    assert c.post(path,json={'mode':'public','permission':'edit'}).status_code==423
    response=c.post(path,json={'mode':'public','permission':'view'});assert response.status_code==200,response.text
    token=response.json()['token']
    assert c.patch('/api/shares/'+token,json={'permission':'edit'}).status_code==423
    assert c.get('/api/shared/'+token).json()['permission']=='view'


def test_server_disabled_cannot_edit_previous_duplex_copy_or_enqueue_external_work():
    from app.notion_sync import access
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    try:
        with m.db() as db:db.execute("UPDATE notion_roots SET mode='duplex'")
        access.prepare(m.db,False)
        page=c.get('/api/notes/'+mapping[ROOT]).json()
        assert page['role']=='viewer' and page['notion_sync']['status']=='paused'
        assert c.put('/api/notes/'+page['id'],json={'title':'Forbidden','body':'No','version':page['version']}).status_code==423
        with m.db() as db:assert not db.execute('SELECT 1 FROM notion_jobs').fetchone()
    finally:access.enabled()


def test_pause_during_initial_transfer_retains_checkpoints_and_resume_finishes_without_duplicate_notes():
    c,s,provider,service,stamp=initialized()
    identity=service.preview(s['user_id'],'read',ROOT)['id'];service.work_once()
    ready=service.status(s['user_id'])['roots'][0]
    initial=service.confirm(s['user_id'],identity,ready['preview_hash'])
    paused=service.action(s['user_id'],identity,'pause',initial['version'])
    assert not service.work_once()
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_staging').fetchone()[0]==2
    resumed=service.action(s['user_id'],identity,'resume',paused['version'])
    assert resumed['status']=='importing'
    while service.work_once():pass
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==2
    assert service.status(s['user_id'])['roots'][0]['status']=='active'


def test_disconnect_then_reconnect_creates_new_copy_and_preserves_prior_editable_tree():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    root=service.status(s['user_id'])['roots'][0]
    service.action(s['user_id'],identity,'disconnect',root['version'])
    second,new_mapping=imported(service,s)
    assert second!=identity and new_mapping[ROOT]!=mapping[ROOT]
    assert c.get('/api/notes/'+mapping[ROOT]).json()['role']=='editor'
    assert c.get('/api/notes/'+new_mapping[ROOT]).json()['role']=='viewer'


def test_unavailable_child_keeps_its_copy_without_stopping_other_pages():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    del provider.pages[CHILD]
    with m.db() as db:enqueue(db,identity,CHILD,'pull',int(stamp[0]))
    assert service.work_once()
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    assert c.get('/api/notes/'+mapping[CHILD]).json()['notion_sync']['status']=='unavailable'


def test_child_moved_outside_subtree_through_a_column_block_is_not_updated():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    block='77777777-7777-4777-8777-777777777777'
    provider.pages[CHILD]['parent']={'type':'block_id','block_id':block}
    original=provider.handle
    def respond(request):
        if request.url.path=='/v1/blocks/'+block:
            provider.requests.append((request.method,request.url.path));return httpx.Response(200,json={'id':block,'object':'block','parent':{'type':'workspace','workspace':True}})
        return original(request)
    service.connections.transport=httpx.MockTransport(respond)
    with m.db() as db:enqueue(db,identity,CHILD,'pull',int(stamp[0]))
    before=len(provider.requests);assert service.work_once()
    after=c.get('/api/notes/'+mapping[CHILD]).json()
    assert after['notion_sync']['status']=='unavailable'
    assert len(provider.requests)-before==2
    assert not any(path.endswith('/children') for method,path in provider.requests[before:])


def test_scan_yields_after_completed_pages_without_retry_backoff_or_fetching_completed_pages_again():
    c,s,provider,service,stamp=initialized();service.batch_seconds=0
    identity=service.preview(s['user_id'],'read',ROOT)['id']
    with m.db() as db:
        db.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump({'error':'notionInvalidResponse'}),identity))
        db.execute("UPDATE notion_jobs SET error='notionInvalidResponse' WHERE root_id=?",(identity,))
    assert service.work_once()
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM notion_staging').fetchone()[0]==1
        assert db.execute('SELECT attempts FROM notion_jobs').fetchone()[0]==0
        assert db.execute('SELECT error FROM notion_jobs').fetchone()[0]==''
    assert service.status(s['user_id'])['roots'][0]['error'] is None
    stamp[0]+=2;assert service.work_once()
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_staging').fetchone()[0]==2
    stamp[0]+=2;assert service.work_once()
    ready=service.status(s['user_id'])['roots'][0]
    assert ready['status']=='ready' and ready['error'] is None
    assert len(provider.requests)==5


def test_successful_scan_chunk_keeps_errors_of_other_jobs_in_same_tree():
    c,s,provider,service,stamp=initialized();service.batch_seconds=0
    identity=service.preview(s['user_id'],'read',ROOT)['id']
    with m.db() as db:
        db.execute('UPDATE notion_roots SET payload=? WHERE id=?',(dump({'error':'notionAssetUnavailable'}),identity))
        enqueue(db,identity,CHILD,'pull',int(stamp[0]),delay=900)
        db.execute("UPDATE notion_jobs SET error='notionAssetUnavailable' WHERE root_id=? AND entity_id=?",(identity,CHILD))
    assert service.work_once()
    assert service.status(s['user_id'])['roots'][0]['error']=='notionAssetUnavailable'


def test_icon_cover_and_inline_image_are_private_reused_and_owned_by_independent_copy():
    from tests.test_widgets import image_bytes
    from app.notion_sync.copy import independent
    from app.file_store import file_store
    from app.data_crypto import media_store
    c,s,provider,service,stamp=initialized()
    image=image_bytes();provider.fetch=lambda url,limit:(provider.downloads.append(url) or image)
    service.fetch=provider.fetch
    provider.pages[ROOT]['icon']={'type':'external','external':{'url':'https://files.example/icon.png'}}
    provider.pages[ROOT]['cover']={'type':'file','file':{'url':'https://files.example/cover.png?signature=first'}}
    provider.blocks[ROOT].append({'id':IMAGE,'type':'image','image':{'type':'file','file':{'url':'https://files.example/photo.png?signature=first'},'caption':rt('Caption')},'has_children':False})
    identity,mapping=imported(service,s)
    source=c.get('/api/notes/'+mapping[ROOT]).json()
    assert source['icon'].startswith('image:') and source['cover'].startswith('image:')
    assert source['blocks'][2]['props']['url'].startswith(m.PUBLIC_URL+'/api/notes/')
    assert len(provider.downloads)==3
    provider.pages[ROOT]['cover']['file']['url']='https://files.example/cover.png?signature=second'
    provider.blocks[ROOT][2]['image']['file']['url']='https://files.example/photo.png?signature=second'
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once() and len(provider.downloads)==3
    copied=independent(service,s['user_id'],mapping[ROOT])['note_id']
    result=c.get('/api/notes/'+copied).json()
    assert result['icon']!=source['icon'] and result['cover']!=source['cover']
    with m.db() as db:
        original=db.execute('SELECT object_id FROM files WHERE id=?',(source['blocks'][2]['props']['url'].rsplit('/',1)[-1],)).fetchone()[0]
        db.execute('DELETE FROM files WHERE id=?',(source['blocks'][2]['props']['url'].rsplit('/',1)[-1],))
        for field in ('icon','cover'):
            db.execute('DELETE FROM note_assets WHERE id=?',(source[field][6:],))
            db.execute('DELETE FROM media WHERE id=?',(source[field][6:],))
    file_store(m.db,m.DATA).delete(original)
    media=media_store(m.db,m.DATA)
    for field in ('icon','cover'):assert media.read(result[field][6:])
    assert c.get(result['blocks'][2]['props']['url'].removeprefix(m.PUBLIC_URL)).content==image


def test_full_backup_from_previous_schema_is_explicitly_rejected_before_restore(tmp_path):
    import hashlib,sqlite3
    from app.backups import Backups,BackupError
    from app.schema import SCHEMA
    owner()
    with sqlite3.connect(':memory:') as old:
        old.executescript(SCHEMA)
        # Other modules append schema after Notion; removesuffix no longer
        # models a pre-Notion database and can equal the current fingerprint.
        old.execute('DROP TABLE notion_pages')
        rows=list(old.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
    old_hash=hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    service=Backups(m.db,m.DATA,m.cipher,'test-only-unused-key',m.now)
    with pytest.raises(BackupError,match='Схема копии'):
        service.validate_database(tmp_path,{'schema':old_hash})


def test_descendant_of_a_known_parent_moved_outside_selection_is_not_refreshed():
    c,s,provider,service,stamp=initialized()
    provider.pages[NEW]=page(NEW,'Grandchild',CHILD);provider.blocks[NEW]=[paragraph('Keep original')]
    provider.blocks[CHILD]=[{'id':NEW,'type':'child_page','child_page':{'title':'Grandchild'},'has_children':True}]
    identity,mapping=imported(service,s)
    provider.pages[CHILD]['parent']={'type':'workspace','workspace':True}
    provider.blocks[NEW]=[paragraph('Outside selected tree')]
    with m.db() as db:enqueue(db,identity,NEW,'pull',int(stamp[0]))
    before=len(provider.requests);assert service.work_once()
    result=c.get('/api/notes/'+mapping[NEW]).json()
    assert result['notion_sync']['status']=='unavailable'
    assert result['blocks'][0]['content'][0]['text']=='Keep original'
    assert len(provider.requests)-before==2
    assert not any(path.endswith('/children') for method,path in provider.requests[before:])


def test_repeated_preview_reuses_running_job_and_exposes_checkpoint_progress():
    c,s,provider,service,stamp=initialized();service.batch_seconds=0
    first=service.preview(s['user_id'],'read',ROOT)
    assert first['progress']=={'checked_pages':0}
    assert service.work_once()
    repeated=service.preview(s['user_id'],'read','https://app.notion.com/p/workspace/'+ROOT.replace('-',''))
    assert repeated['id']==first['id'] and repeated['progress']['checked_pages']==1
    status=service.status(s['user_id']);assert len(status['roots'])==1
    assert status['roots'][0]['progress']['checked_pages']==1
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==1
        assert db.execute('SELECT generation FROM notion_jobs').fetchone()[0]==1
    stamp[0]+=2;assert service.work_once();stamp[0]+=2;assert service.work_once()
    root=service.status(s['user_id'])['roots'][0];assert root['status']=='ready'
    service.confirm(s['user_id'],first['id'],root['preview_hash'])
    progress=service.status(s['user_id'])['roots'][0]['progress']
    assert progress=={'copied_pages':0,'total_pages':2}
    assert not service.status(s['user_id'])['roots'][0]['copy_available']
    assert c.get('/api/notes').json()==[]
    assert service.work_once()
    assert service.status(s['user_id'])['roots'][0]['progress']['copied_pages']==1
    assert service.status(s['user_id'])['roots'][0]['copy_available']
    partial=c.get('/api/notes').json()
    assert len(partial)==1 and partial[0]['notion_sync']['transfer_pending']
    assert partial[0]['role']=='viewer'
    from app.notion_sync.copy import independent
    with pytest.raises(NotionError) as error:independent(service,s['user_id'],partial[0]['id'])
    assert error.value.code=='notionImportInProgress'
    assert service.work_once()
    active=service.preview(s['user_id'],'read',ROOT)
    assert active['id']==first['id'] and active['status']=='active'
    assert active['copy_available']
    assert len(c.get('/api/notes').json())==2
    assert len(provider.requests)==6


def test_overlapping_tree_preview_stops_with_visible_error_instead_of_retrying_forever():
    c,s,provider,service,stamp=initialized();existing,mapping=imported(service,s)
    provider.pages[NEW]=page(NEW,'Another parent')
    provider.pages[CHILD]['parent']={'type':'page_id','page_id':NEW}
    provider.blocks[NEW]=[{'id':CHILD,'type':'child_page','child_page':{'title':'Child'},'has_children':True}]
    preview=service.preview(s['user_id'],'read',NEW)
    assert service.work_once()
    result=next(root for root in service.status(s['user_id'])['roots'] if root['id']==preview['id'])
    assert result['status']=='paused' and result['error']=='notionSelectionOverlap'
    stamp[0]+=3600;assert not service.work_once()
    assert c.get('/api/notes/'+mapping[ROOT]).json()['title']=='Root'
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==2


def test_large_unsplash_cover_uses_bounded_rendition_without_relaxing_image_limits():
    import io
    from PIL import Image
    from urllib.parse import urlsplit,parse_qs
    from app.notion_sync.assets import decoration_url
    c,s,provider,service,stamp=initialized()
    original='https://images.unsplash.com/photo-fixture?ixid=keep-credit&ixlib=rb-4.0.3&dpr=3&w=7492&auto=format'
    provider.pages[ROOT]['cover']={'type':'external','external':{'url':original}}
    def photo(url,limit):
        provider.downloads.append(url);query=parse_qs(urlsplit(url).query)
        size=(1600,600) if query.get('w')==['1600'] and query.get('h')==['600'] and query.get('dpr')==['1'] else (7492,4973)
        image=Image.new('RGB',size,'#406080');stream=io.BytesIO();image.save(stream,format='JPEG');image.close();return stream.getvalue()
    service.fetch=photo
    identity,mapping=imported(service,s)
    assert len(provider.downloads)==1
    query=parse_qs(urlsplit(provider.downloads[0]).query)
    assert query['ixid']==['keep-credit'] and query['ixlib']==['rb-4.0.3']
    assert query['fm']==['jpg'] and 'auto' not in query
    root=c.get('/api/notes/'+mapping[ROOT]).json();assert root['cover'].startswith('image:')
    with m.db() as db:
        image=db.execute('SELECT width,height FROM media WHERE id=?',(root['cover'][6:],)).fetchone()
        assert tuple(image)==(1600,600)
    signed='https://files.example/image.jpg?signature=keep-exact'
    unrelated='https://images.unsplash.com.other.invalid/photo?w=7492'
    assert decoration_url(signed,'cover')==signed and decoration_url(unrelated,'cover')==unrelated


def test_link_metadata_is_batched_and_repeated_lookups_do_not_query_again(monkeypatch):
    from app.notion_sync import access
    from app.database import Connection
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    for index in range(60):
        with m.db() as db:
            note='catalog-'+str(index)
            db.execute('INSERT INTO notes(id,user_id,title,body,created,updated) VALUES(?,?,?,?,?,?)',(note,s['user_id'],'Fixture','',int(stamp[0]),int(stamp[0])))
            db.execute('INSERT INTO notion_pages(id,user_id,root_id,notion_id,note_id,payload,updated) VALUES(?,?,?,?,?,?,?)',('catalog-link-'+str(index),s['user_id'],identity,'catalog-source-'+str(index),note,'{}',int(stamp[0])))
    with m.db() as db:
        notes=db.execute('SELECT id FROM notes').fetchall();queries=[0];original=Connection._execute
        def counted(connection,query,parameters=()):queries[0]+=1;return original(connection,query,parameters)
        monkeypatch.setattr(Connection,'_execute',counted)
        access.preload(db,notes);after=queries[0]
        assert after==3  # links, root state and one batched navigation projection
        for _ in range(3):
            for note in notes:assert access.info(db,note)['transfer_pending'] is False
        assert queries[0]==after


@pytest.mark.parametrize("bitmap_format",["png","bmp"])
def test_ico_page_icon_is_converted_without_allowing_ico_in_general_media(bitmap_format):
    import io
    from PIL import Image
    from app.modules.media import inspect_image
    c,s,provider,service,stamp=initialized()
    image=Image.new('RGBA',(256,256),'#4080cc');stream=io.BytesIO()
    image.save(stream,format='ICO',sizes=[(16,16),(32,32),(256,256)],bitmap_format=bitmap_format);image.close();body=stream.getvalue()
    provider.pages[ROOT]['icon']={'type':'file','file':{'url':'https://files.example/favicon.ico?signature=keep'}}
    service.fetch=lambda url,limit:body
    with pytest.raises(ValueError):inspect_image(body)
    identity,mapping=imported(service,s)
    root=c.get('/api/notes/'+mapping[ROOT]).json()
    assert root['icon'].startswith('image:')
    with m.db() as db:
        row=db.execute('SELECT width,height,mime FROM media WHERE id=?',(root['icon'][6:],)).fetchone()
        assert (row['width'],row['height'],row['mime'])==(96,96,'image/webp')


def test_ico_embedded_dimensions_are_checked_before_decoding():
    import io,struct
    from PIL import Image
    from app.notion_icons import raster_icon
    image=Image.new('RGB',(300,300),'#4080cc');stream=io.BytesIO();image.save(stream,format='PNG');image.close();frame=stream.getvalue()
    body=b'\x00\x00\x01\x00'+struct.pack('<H',1)+struct.pack('<BBBBHHII',0,0,0,0,1,32,len(frame),22)+frame
    with pytest.raises(ValueError,match='too large'):raster_icon(body)
    with pytest.raises(ValueError):raster_icon(b'\x00\x00\x01\x00\xff\xff')


@pytest.mark.parametrize('kind',['cover','icon'])
def test_missing_decoration_publishes_content_and_retries_once_per_interval_without_remote_edit(kind):
    from tests.test_widgets import image_bytes
    c,s,provider,service,stamp=initialized()
    url='https://missing.example/cover.jpg'
    provider.pages[ROOT][kind]={'type':'external','external':{'url':url}}
    calls=[];available=[False]
    def fetch(source,limit):
        calls.append(source)
        if not available[0]:raise NotionError('notionAssetUnavailable',502)
        return image_bytes()
    service.fetch=fetch
    identity,mapping=imported(service,s)
    root=c.get('/api/notes/'+mapping[ROOT]).json()
    assert 'Hello' in root['body']
    assert root[kind] in ('','📄') and root['notion_sync'][kind+'_warning']=='notion'+kind.title()+'Unavailable'
    summary=service.status(s['user_id'])['roots'][0]['summary']
    assert summary['issues'][-1]['reason']=='notion'+kind.title()+'Unavailable'
    assert calls==[url]
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0])
        assert value['source']['raw'][kind]['external']['url']==url
        assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE direction='cover'").fetchone()[0]==1
        enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once();assert calls==[url]
    # Repeated page edits keep content fresh without retrying the same dead
    # decoration before its deadline.
    provider.blocks[ROOT][0]=paragraph('Updated while cover missing')
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once();assert calls==[url]
    assert 'Updated while cover missing' in c.get('/api/notes/'+mapping[ROOT]).json()['body']
    stamp[0]+=901;assert service.work_once();assert calls==[url,url]
    assert not service.work_once()
    with m.db() as db:assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE direction='cover'").fetchone()[0]==1
    status=service.status(s['user_id'])['roots'][0]
    service.action(s['user_id'],identity,'pause',status['version'])
    status=service.status(s['user_id'])['roots'][0]
    service.action(s['user_id'],identity,'resume',status['version'])
    assert service.work_once();assert calls==[url,url]
    with m.db() as db:assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE direction='cover'").fetchone()[0]==1
    available[0]=True;stamp[0]+=901;assert service.work_once()
    result=c.get('/api/notes/'+mapping[ROOT]).json()
    assert result[kind].startswith('image:') and not result['notion_sync'][kind+'_warning']
    assert service.status(s['user_id'])['roots'][0]['summary']['issue_count']==0
    assert not service.work_once()
    with m.db() as db:assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE direction='cover'").fetchone()[0]==0


def test_pending_legacy_flaticon_icon_recovers_in_existing_retry_without_editing_notion(monkeypatch):
    from tests.test_widgets import image_bytes
    from app.notion_sync import assets
    c,s,provider,service,stamp=initialized()
    original='https://image.flaticon.com/icons/png/512/25/25231.png'
    current='https://cdn-icons-png.flaticon.com/512/25/25231.png'
    provider.pages[ROOT]['icon']={'type':'external','external':{'url':original}}
    calls=[];available=[False]
    async def fetch(url,**options):
        calls.append(url)
        if url!=current or not available[0]:raise OSError('CDN unavailable')
        return image_bytes(),url,'image/png'
    monkeypatch.setattr(assets,'fetch_public',fetch);service.fetch=None
    identity,mapping=imported(service,s)
    assert c.get('/api/notes/'+mapping[ROOT]).json()['notion_sync']['icon_warning']
    assert calls==[current,original]
    available[0]=True;stamp[0]+=901
    assert service.work_once()
    note=c.get('/api/notes/'+mapping[ROOT]).json()
    assert note['icon'].startswith('image:') and not note['notion_sync']['icon_warning']
    assert c.get(note['icon_url']).status_code==200
    assert calls==[current,original,current] and not service.work_once()
    with m.db() as db:
        value=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0])
        assert value['source']['raw']['icon']['external']['url']==original
        assert db.execute("SELECT mode FROM notion_roots WHERE id=?",(identity,)).fetchone()[0]=='pull'
        assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE direction='cover'").fetchone()[0]==0
    assert all(method=='GET' for method,path in provider.requests)


@pytest.mark.parametrize('kind',['cover','icon'])
def test_failed_new_decoration_keeps_last_good_local_value_and_blocks_file_failures(kind):
    from tests.test_widgets import image_bytes
    c,s,provider,service,stamp=initialized()
    provider.pages[ROOT][kind]={'type':'external','external':{'url':'https://files.example/good.jpg'}}
    service.fetch=lambda *_:image_bytes()
    identity,mapping=imported(service,s);before=c.get('/api/notes/'+mapping[ROOT]).json()[kind]
    provider.pages[ROOT][kind]['external']['url']='https://files.example/bad.jpg'
    def unavailable(*_):raise NotionError('notionAssetUnavailable',502)
    service.fetch=unavailable
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once()
    result=c.get('/api/notes/'+mapping[ROOT]).json()
    assert result[kind]==before and result['notion_sync'][kind+'_warning']
    provider.blocks[ROOT].append({'id':IMAGE,'type':'file','file':{'type':'external','external':{'url':'https://files.example/important.pdf'}},'has_children':False})
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once()
    assert c.get('/api/notes/'+mapping[ROOT]).json()['body']==result['body']
    assert service.status(s['user_id'])['roots'][0]['error']=='notionAssetUnavailable'


def test_icon_and_cover_share_one_job_but_keep_independent_retry_deadlines():
    from tests.test_widgets import image_bytes
    c,s,provider,service,stamp=initialized();available=set();calls=[]
    icon='https://files.example/icon.png';cover='https://files.example/cover.png'
    def fetch(url,limit):
        calls.append(url)
        if url not in available:raise NotionError('notionAssetUnavailable',502)
        return image_bytes()
    service.fetch=fetch;provider.pages[ROOT]['icon']={'type':'external','external':{'url':icon}}
    identity,mapping=imported(service,s);assert calls==[icon]
    stamp[0]+=300;provider.pages[ROOT]['cover']={'type':'external','external':{'url':cover}}
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once();assert calls==[icon,cover]
    with m.db() as db:
        assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE direction='cover'").fetchone()[0]==1
        expected_cover_due=json.loads(db.execute('SELECT payload FROM notion_pages WHERE note_id=?',(mapping[ROOT],)).fetchone()[0])['cover_retry_at']
    stamp[0]+=601;available.add(icon)
    assert service.work_once();assert calls==[icon,cover,icon]
    result=c.get('/api/notes/'+mapping[ROOT]).json()
    assert not result['notion_sync']['icon_warning'] and result['notion_sync']['cover_warning']
    assert not service.work_once()
    with m.db() as db:assert db.execute("SELECT due FROM notion_jobs WHERE direction='cover'").fetchone()[0]==expected_cover_due
    stamp[0]=expected_cover_due+1;available.add(cover)
    assert service.work_once();assert calls==[icon,cover,icon,cover]
    result=c.get('/api/notes/'+mapping[ROOT]).json()
    assert not result['notion_sync']['icon_warning'] and not result['notion_sync']['cover_warning']
    assert not service.work_once()


@pytest.mark.parametrize('kind',['cover','icon'])
def test_published_decoration_retries_while_another_page_is_still_importing(kind):
    from tests.test_widgets import image_bytes
    c,s,provider,service,stamp=initialized()
    provider.pages[ROOT][kind]={'type':'external','external':{'url':'https://missing.example/cover.jpg'}}
    def unavailable(*_):raise NotionError('notionAssetUnavailable',502)
    service.fetch=unavailable
    preview=service.preview(s['user_id'],'read',ROOT);assert service.work_once()
    root=service.status(s['user_id'])['roots'][0]
    service.confirm(s['user_id'],preview['id'],root['preview_hash']);assert service.work_once()
    with m.db() as db:
        db.execute("UPDATE notion_jobs SET due=? WHERE direction='import'",(int(stamp[0])+3600,))
    stamp[0]+=901
    calls=[]
    service.fetch=lambda url,limit:(calls.append(url) or image_bytes())
    assert service.work_once()
    assert calls==['https://missing.example/cover.jpg']
    assert service.status(s['user_id'])['roots'][0]['status']=='importing'
    with m.db() as db:
        nid=db.execute('SELECT note_id FROM notion_pages WHERE root_id=? AND notion_id=?',(preview['id'],ROOT)).fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE direction='cover'").fetchone()[0]==0
    assert c.get('/api/notes/'+nid).json()[kind].startswith('image:')
    assert not service.work_once()
    stamp[0]+=2700;assert service.work_once()
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    assert not service.work_once()


def test_custom_emoji_import_change_reuse_and_legacy_recovery_without_remote_edit():
    from app.notion_sync.assets import identity as fingerprint
    from tests.test_widgets import image_bytes
    c,s,provider,service,stamp=initialized();calls=[]
    icon={'type':'custom_emoji','custom_emoji':{'id':NEW,'name':'server','url':'https://files.example/server.png'}}
    provider.pages[ROOT]['icon']=copy.deepcopy(icon)
    service.fetch=lambda url,limit:(calls.append(url) or image_bytes())
    root,mapping=imported(service,s)
    original=c.get('/api/notes/'+mapping[ROOT]).json()
    assert original['icon'].startswith('image:') and calls==[icon['custom_emoji']['url']]
    assert c.get(original['icon_url']).status_code==200
    # Old code accepted the source stamp but hashed every custom emoji as an
    # empty file URL and published the default icon without a pending warning.
    with m.db() as db:
        row=db.execute('SELECT id,payload FROM notion_pages WHERE notion_id=?',(ROOT,)).fetchone()
        legacy=json.loads(row['payload'])
        legacy['assets'].pop('decoration:icon');legacy['baseline']['icon']='📄'
        from app.notion_sync.service import local_hash,remote_hash
        from app.notion_sync.snapshot import PageSnapshot
        # Preserve custom_emoji's old type while emulating its empty URL hash.
        from app.notion_sync.client import digest
        import app.notion_sync.assets as assets
        from unittest.mock import patch
        with patch.object(assets,'identity',side_effect=lambda value:digest(['custom_emoji','']) if (value or {}).get('type')=='custom_emoji' else fingerprint(value)):
            legacy['remote_hash']=remote_hash(PageSnapshot(**legacy['source']))
        db.execute('UPDATE notes SET icon=? WHERE id=?',('📄',mapping[ROOT]))
        legacy['local_hash']=local_hash(db.execute('SELECT * FROM notes WHERE id=?',(mapping[ROOT],)).fetchone())
        db.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(legacy),row['id']))
    before=len(provider.requests)
    cursor=''
    while cursor is not None:cursor=service.recover_icons(cursor,limit=1)
    assert len(provider.requests)==before
    service.recover_icons()
    with m.db() as db:assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE direction='pull'").fetchone()[0]==1
    assert service.work_once();assert not service.work_once()
    repaired=c.get('/api/notes/'+mapping[ROOT]).json()
    assert repaired['icon'].startswith('image:') and calls==[icon['custom_emoji']['url']]*2
    service.recover_icons();assert not service.work_once()
    # Renaming the library emoji keeps its image; a new URL or ID refreshes it.
    provider.pages[ROOT]['icon']['custom_emoji']['name']='renamed'
    with m.db() as db:enqueue(db,root,ROOT,'pull',int(stamp[0]))
    assert service.work_once();assert len(calls)==2
    provider.pages[ROOT]['icon']['custom_emoji']['url']='https://files.example/new-server.png'
    with m.db() as db:enqueue(db,root,ROOT,'pull',int(stamp[0]))
    assert service.work_once();assert calls[-1]=='https://files.example/new-server.png'
    provider.pages[ROOT]['icon']={'type':'emoji','emoji':'🖥️'}
    with m.db() as db:enqueue(db,root,ROOT,'pull',int(stamp[0]))
    assert service.work_once();assert c.get('/api/notes/'+mapping[ROOT]).json()['icon']=='🖥️'


def test_custom_emoji_failure_keeps_retry_deadline_without_recovery_job_storm():
    from tests.test_widgets import image_bytes
    c,s,provider,service,stamp=initialized();calls=[]
    provider.pages[ROOT]['icon']={'type':'custom_emoji','custom_emoji':{'id':NEW,'name':'server','url':'https://files.example/server.png'}}
    def missing(url,limit):calls.append(url);raise NotionError('notionAssetUnavailable',502)
    service.fetch=missing;identity,mapping=imported(service,s)
    assert c.get('/api/notes/'+mapping[ROOT]).json()['notion_sync']['icon_warning']
    before=len(provider.requests)
    for _ in range(5):service.recover_icons()
    assert len(provider.requests)==before and len(calls)==1
    with m.db() as db:
        assert db.execute("SELECT COUNT(*) FROM notion_jobs WHERE direction='cover'").fetchone()[0]==1
        assert not db.execute("SELECT 1 FROM notion_jobs WHERE direction='pull'").fetchone()
    service.fetch=lambda *_:image_bytes();stamp[0]+=901
    assert service.work_once();assert not service.work_once()
    assert c.get('/api/notes/'+mapping[ROOT]).json()['icon'].startswith('image:')


@pytest.mark.parametrize('guard',['paused','detached','reauthorize','role','deleted'])
def test_legacy_icon_recovery_respects_ownership_and_link_state(guard):
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    with m.db() as db:
        row=db.execute('SELECT id,payload FROM notion_pages WHERE notion_id=?',(ROOT,)).fetchone();value=json.loads(row['payload'])
        value['source']['raw']['icon']={'type':'custom_emoji','custom_emoji':{'id':NEW,'url':'https://files.example/icon.png'}}
        db.execute('UPDATE notion_pages SET payload=? WHERE id=?',(dump(value),row['id']))
        if guard in ('paused','detached'):db.execute('UPDATE notion_roots SET status=? WHERE id=?',(guard,identity))
        elif guard=='reauthorize':db.execute("UPDATE notion_connections SET status='reauthorize'")
        elif guard=='role':db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
        else:db.execute('UPDATE notes SET deleted=? WHERE id=?',(int(stamp[0]),mapping[ROOT]))
    before=len(provider.requests);assert service.recover_icons() is None
    assert len(provider.requests)==before
    with m.db() as db:assert not db.execute('SELECT 1 FROM notion_jobs').fetchone()


def test_sync_download_reuses_current_cdn_without_trying_retired_host_first(monkeypatch):
    import asyncio
    from app.notion_sync import assets
    calls=[]
    original='https://avatars.mds.yandex.net/get-zen_doc/1/pub_image/scale_600?keep=1'
    current=original.replace('avatars.mds.yandex.net','avatars.dzeninfra.ru')
    async def fetch(url,**options):
        calls.append((url,options))
        if url!=current:raise OSError('Retired host')
        return b'file bytes',url,'image/png'
    monkeypatch.setattr(assets,'fetch_public',fetch)
    assert asyncio.run(assets.download(original,1234))==b'file bytes'
    assert calls==[(current,{'max_bytes':1234,'accept':'*/*'})]
    calls.clear()
    unrelated='https://avatars.mds.yandex.net.evil.example/get-zen_doc/file?signature=secret'
    with pytest.raises(NotionError) as error:asyncio.run(assets.download(unrelated,1234))
    assert error.value.code=='notionAssetUnavailable' and 'secret' not in str(error.value)
    assert [call[0] for call in calls]==[unrelated]


def test_linked_root_can_be_reordered_locally_without_editing_or_reparenting_source():
    c,s,provider,service,stamp=initialized();identity,mapping=imported(service,s)
    anchor=c.post('/api/notes',json={'title':'Anchor','body':'Keep'}).json()
    root=c.get('/api/notes/'+mapping[ROOT]).json();assert root['notion_sync']['can_reorder']
    before_root=root['blocks']
    moved=c.post('/api/notes/'+root['id']+'/move',json={'parent_id':None,'before_id':anchor['id'],'version':root['version']})
    assert moved.status_code==200,moved.text
    assert moved.json()['role']=='viewer' and moved.json()['blocks']==before_root
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==0
    assert c.patch('/api/notes/'+root['id'],json={'icon':'🎉','version':moved.json()['version']}).status_code==423
    assert c.put('/api/notes/'+root['id'],json={'title':'Changed','body':'Overwrite','version':moved.json()['version']}).status_code==423
    assert c.post('/api/notes/'+root['id']+'/move',json={'parent_id':anchor['id'],'version':moved.json()['version']}).status_code==423
    child=c.get('/api/notes/'+mapping[CHILD]).json();assert child['notion_sync']['can_reorder']
    assert c.post('/api/notes/'+child['id']+'/move',json={'parent_id':None,'version':child['version']}).status_code==423
    provider.blocks[ROOT][0]=paragraph('Fresh source after local reorder')
    with m.db() as db:enqueue(db,identity,ROOT,'pull',int(stamp[0]))
    assert service.work_once()
    fresh=c.get('/api/notes/'+root['id']).json();assert 'Fresh source' in fresh['body']
    notes=c.get('/api/notes?catalog=true').json();ids=[n['id'] for n in notes]
    assert ids.index(root['id'])<ids.index(anchor['id'])
    assert service.status(s['user_id'])['roots'][0]['status']=='active'
    assert c.post('/api/notes/'+root['id']+'/lock',json={'mode':'lock'}).status_code==200
    assert c.post('/api/notes/'+root['id']+'/move',json={'parent_id':None,'version':fresh['version']}).status_code==423
    assert c.post('/api/notes/'+root['id']+'/lock',json={'mode':'unlock'}).status_code==200
    with m.db() as db:db.execute("UPDATE users SET role='admin' WHERE id=?",(s['user_id'],))
    restricted=c.get('/api/notes/'+root['id']).json();assert not restricted['notion_sync']['can_reorder']
    assert c.post('/api/notes/'+root['id']+'/move',json={'parent_id':None,'version':restricted['version']}).status_code==423
