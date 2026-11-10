"""Single-source navigation is automatic and keeps provider/storage identities."""
import copy,json
import httpx
import pytest
from tests.test_security import reset
from tests.test_notion_sync_service import initialized,page,paragraph,rt
from tests.test_notion_sync_databases import DATABASE,SOURCE,RECORD,NEW
from app import main as m
from app.notion_sync.state import enqueue


def database(*,multiple=False,cover=False,pending=False):
    c,s,provider,service,stamp=initialized();original=provider.handle
    sources=[SOURCE,NEW] if multiple else [SOURCE]
    raw={'object':'database','id':DATABASE,'title':rt('Заметки'),'icon':{'type':'emoji','emoji':'📒'},'data_sources':[{'id':x,'name':'Заметки'} for x in sources]}
    if cover:
        import io
        from PIL import Image
        output=io.BytesIO();Image.new('RGB',(800,300),'#244f77').save(output,format='PNG')
        service.fetch=lambda *_:output.getvalue()
        raw['cover']={'type':'external','external':{'url':'https://files.example/cover.png'}}
    for identity in (RECORD,):
        provider.pages[identity]=page(identity,'Первая запись',SOURCE)
        provider.pages[identity]['parent']={'type':'data_source_id','data_source_id':SOURCE}
        provider.pages[identity]['properties']['Cost']={'id':'cost','type':'number','number':12}
        provider.blocks[identity]=[paragraph('Содержимое без изменений')]
        if pending:
            from app.notion_sync.client import NotionError
            provider.blocks[identity].append({'id':'cccccccc-cccc-4ccc-8ccc-cccccccccccc','type':'file','file':{'type':'external','external':{'url':'https://files.example/missing.pdf'}}})
            def unavailable(*_):raise NotionError('notionAssetUnavailable',502)
            service.fetch=unavailable
    def respond(request):
        path=request.url.path
        if path=='/v1/pages/'+DATABASE:return httpx.Response(400,json={'code':'validation_error'})
        if path=='/v1/databases/'+DATABASE:value=raw
        elif path in ['/v1/data_sources/'+x for x in (SOURCE,NEW)]:
            identity=path.rsplit('/',1)[-1]
            value={'object':'data_source','id':identity,'title':rt('Внутреннее имя источника'),'parent':{'type':'database_id','database_id':DATABASE},'properties':{'Name':{'id':'title','type':'title','title':{}},'Cost':{'id':'cost','type':'number','number':{}}}}
        elif path in ['/v1/data_sources/'+x+'/query' for x in (SOURCE,NEW)]:value={'results':[provider.pages[RECORD]] if '/'+SOURCE+'/' in path else [],'has_more':False}
        else:return original(request)
        provider.requests.append((request.method,path));return httpx.Response(200,json=copy.deepcopy(value))
    service.connections.transport=httpx.MockTransport(respond)
    root=service.preview(s['user_id'],'read',DATABASE);assert service.work_once()
    ready=service.status(s['user_id'])['roots'][0];service.confirm(s['user_id'],root['id'],ready['preview_hash'])
    while service.work_once():pass
    with m.db() as db:mapping={r['notion_id']:r['note_id'] for r in db.execute('SELECT notion_id,note_id FROM notion_pages')}
    return c,s,provider,service,stamp,root['id'],mapping,raw


def test_existing_and_new_copies_have_one_database_and_preserve_ids_and_sync():
    c,s,provider,service,stamp,identity,mapping,raw=database()
    before=len(provider.requests)
    notes=c.get('/api/notes?catalog=true').json()
    assert {n['id'] for n in notes}=={mapping[SOURCE],mapping[RECORD]}
    shown=next(n for n in notes if n['id']==mapping[SOURCE])
    assert shown['title']=='Заметки' and shown['parent_id'] is None and shown['kind']=='database'
    assert shown['icon']=='📒' and shown['navigation_aliases']==[mapping[DATABASE]]
    assert shown['notion_sync']['can_reorder'] and shown['notion_sync']['read_only']
    linked=c.get('/api/notes/'+mapping[DATABASE]).json()
    assert linked['id']==mapping[SOURCE] and linked['parent_id'] is None
    assert c.get('/api/databases/'+mapping[DATABASE]).json()['records']==c.get('/api/databases/'+mapping[SOURCE]).json()['records']
    assert len(c.get('/api/notes/search?q=Заметки').json()['items'])==1
    assert len(provider.requests)==before
    assert c.patch('/api/notes/'+mapping[DATABASE],json={'favorite':True}).status_code==200
    assert c.get('/api/notes/'+mapping[SOURCE]).json()['favorite']==1
    assert c.patch('/api/notes/'+mapping[SOURCE],json={'favorite':False}).status_code==200
    assert c.get('/api/notes/'+mapping[DATABASE]).json()['favorite']==0
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM notion_pages').fetchone()[0]==3
        assert db.execute('SELECT parent_id FROM notes WHERE id=?',(mapping[SOURCE],)).fetchone()[0]==mapping[DATABASE]
    provider.blocks[RECORD]=[paragraph('Правка после объединённой навигации')]
    provider.pages[RECORD]['last_edited_time']='2026-11-02T12:00:00Z'
    with m.db() as db:enqueue(db,identity,RECORD,'pull',int(stamp[0]))
    assert service.work_once()
    assert 'Правка после объединённой навигации' in c.get('/api/notes/'+mapping[RECORD]).json()['body']
    assert c.get('/api/notes/'+mapping[RECORD]).json()['parent_id']==mapping[SOURCE]


def test_collapsed_root_reorders_locally_without_reparenting_source_or_changing_notion():
    c,s,provider,service,stamp,identity,mapping,raw=database()
    anchor=c.post('/api/notes',json={'title':'Соседняя папка','body':''}).json()
    shown=c.get('/api/notes/'+mapping[SOURCE]).json();before=len(provider.requests)
    result=c.post('/api/notes/'+mapping[SOURCE]+'/move',json={'parent_id':None,'before_id':None,'version':shown['version']})
    assert result.status_code==200,result.text
    assert result.json()['parent_id'] is None and result.json()['version']>shown['version']
    notes=c.get('/api/notes?catalog=true').json()
    assert next(n['position'] for n in notes if n['id']==mapping[SOURCE])>next(n['position'] for n in notes if n['id']==anchor['id'])
    with m.db() as db:assert db.execute('SELECT parent_id FROM notes WHERE id=?',(mapping[SOURCE],)).fetchone()[0]==mapping[DATABASE]
    assert len(provider.requests)==before


def test_multiple_sources_stay_separate_even_with_equal_names_and_new_source_expands_automatically():
    c,s,provider,service,stamp,identity,mapping,raw=database()
    raw['data_sources'].append({'id':NEW,'name':'Заметки'})
    with m.db() as db:enqueue(db,identity,DATABASE,'pull',int(stamp[0]))
    while service.work_once():pass
    notes=c.get('/api/notes?catalog=true').json()
    assert any(n['id']==mapping[DATABASE] and n['kind']=='page' for n in notes)
    assert next(n for n in notes if n['id']==mapping[SOURCE])['parent_id']==mapping[DATABASE]
    assert next(n for n in notes if n['id']==mapping[SOURCE])['navigation_aliases']==[]
    assert len([n for n in notes if n['kind']=='database'])==2


def test_independent_copy_is_flat_and_keeps_records_and_original_hierarchy():
    from app.notion_sync.copy import independent
    c,s,provider,service,stamp,identity,mapping,raw=database(cover=True)
    original=c.get('/api/notes/'+mapping[SOURCE]).json()
    assert mapping[DATABASE] in original['cover_url']
    copied=independent(service,s['user_id'],mapping[DATABASE])['note_id']
    note=c.get('/api/notes/'+copied).json();assert note['kind']=='database' and note['icon']=='📒'
    assert 'notion_sync' not in note and note['parent_id'] is None
    assert c.get(note['cover_url']).content==c.get(original['cover_url']).content
    db=c.get('/api/databases/'+copied).json();assert len(db['records'])==1
    record=c.get('/api/notes/'+db['records'][0]['note_id']).json()
    assert record['parent_id']==copied and record['body']=='Содержимое без изменений'
    assert db['records'][0]['properties']['n_'+b'cost'.hex()]==12
    with m.db() as conn:assert conn.execute('SELECT parent_id FROM notes WHERE id=?',(mapping[SOURCE],)).fetchone()[0]==mapping[DATABASE]


def test_pending_database_is_flat_and_disconnect_keeps_published_content():
    c,s,provider,service,stamp,identity,mapping,raw=database(pending=True)
    current=service.status(s['user_id'])['roots'][0];assert current['status']=='importing'
    assert len(c.get('/api/notes?catalog=true').json())==1
    assert c.get('/api/notes/'+mapping[SOURCE]).json()['kind']=='database'
    service.action(s['user_id'],identity,'disconnect',current['version'])
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]==2
        assert db.execute('SELECT 1 FROM notes WHERE id=?',(mapping[RECORD],)).fetchone() is None
        assert db.execute('SELECT COUNT(*) FROM notion_jobs').fetchone()[0]==0
    assert 'notion_sync' not in c.get('/api/notes/'+mapping[SOURCE]).json()


@pytest.mark.parametrize('protected',['locked','deleted'])
def test_projection_does_not_bypass_protected_container(protected):
    c,s,provider,service,stamp,identity,mapping,raw=database()
    with m.db() as db:db.execute('UPDATE notes SET '+protected+'=? WHERE id=?',(int(stamp[0]) if protected=='deleted' else 1,mapping[DATABASE]))
    with m.db() as db:
        from app.notion_sync.presentation import index
        assert index(db,s['user_id'])['aliases']=={}


def test_projection_is_batched_scoped_and_preserves_storage_snapshots(monkeypatch):
    from app.database import Connection
    from app.notion_sync.presentation import index,page
    c,s,provider,service,stamp,identity,mapping,raw=database(cover=True)
    with m.db() as db:
        originals=[dict(r) for r in db.execute('SELECT * FROM notion_pages ORDER BY id')]
        rows=db.execute('SELECT * FROM notes').fetchall();count=[0];execute=Connection._execute
        def counted(connection,sql,params=()):count[0]+=1;return execute(connection,sql,params)
        monkeypatch.setattr(Connection,'_execute',counted)
        assert index(db,s['user_id'])['aliases']=={mapping[DATABASE]:mapping[SOURCE]}
        assert count[0]==2
        for _ in range(5):
            for row in rows:
                if row['id']!=mapping[DATABASE]:page(db,row,s['user_id'])
        assert count[0]==2
        assert index(db,'another-account')['aliases']=={}
        assert [dict(r) for r in db.execute('SELECT * FROM notion_pages ORDER BY id')]==originals
