import io
import json
import secrets
from pathlib import Path
import pytest
from PIL import Image
from tests.test_security import owner,member,client,reset,note,share
from tests.test_widgets import image_bytes
from app import main as m
from app.file_store import FileStore,CHUNK
from app.data_crypto import DataCipher,StorageError


def test_note_icons_and_covers_scoped_to_page_with_gallery_disabled_and_public_shares():
    c,_,_=owner();g,_=member(c);nid=note(c)
    assert c.put('/api/modules',json={'enabled':{'media':False}}).status_code==200
    r=c.post('/api/notes/'+nid+'/assets/icon',content=image_bytes());assert r.status_code==200,r.text
    icon=r.json()['icon'];page=c.get('/api/notes/'+nid).json();assert page['icon']==icon
    assert c.get(page['icon_url']).status_code==200
    assert g.get(page['icon_url']).status_code==404
    assert client().get(page['icon_url']).status_code==401
    assert c.get('/api/media').status_code==200
    cover=c.post('/api/notes/'+nid+'/assets/cover',content=image_bytes());assert cover.status_code==200,cover.text
    page=c.get('/api/notes/'+nid).json()
    with Image.open(io.BytesIO(c.get(page['cover_url']).content)) as image:assert image.size==(1600,600)
    other=note(c)
    assert c.patch('/api/notes/'+other,json={'icon':icon}).status_code==422
    token=share(c,nid,'public');public=client();data=public.get('/api/shared/'+token).json()
    assert public.get(data['icon_url']).status_code==200
    assert public.get(data['cover_url']).status_code==200
    assert c.delete('/api/shares/'+token).status_code==200
    assert public.get(data['cover_url']).status_code==404


def test_cover_search_uses_signed_selection_tokens_and_preserves_attribution(monkeypatch):
    from app.modules import note_assets
    c,_,_=owner();g,_=member(c);nid=note(c)
    calls=[]
    async def fetch(url,**kwargs):
        calls.append(url)
        if '/w/api.php' in url:
            body={'query':{'pages':{'123':{'pageid':123,'title':'File:Test wide photo','imageinfo':[{'width':1800,'height':600,'size':90000000,'mime':'image/jpeg','thumbwidth':1600,'thumbheight':533,'thumburl':'https://thumb.wikimedia.org/test.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/File:Test','extmetadata':{'Artist':{'value':'<b>Photographer</b>'},'LicenseShortName':{'value':'CC BY 4.0'}}}]}}}}
            return json.dumps(body).encode(),url,'application/json'
        return image_bytes(),url,'image/png'
    monkeypatch.setattr(note_assets,'fetch_public',fetch)
    result=c.get('/api/note-cover-search?q=ocean');assert result.status_code==200,result.text
    item=result.json()['items'][0];assert item['attribution']['author']=='Photographer'
    assert item['url']=='https://thumb.wikimedia.org/test.jpg'
    assert g.post('/api/notes/'+note(g)+'/select-cover',json={'token':item['selection_token']}).status_code==403
    selected=c.post('/api/notes/'+nid+'/select-cover',json={'token':item['selection_token']});assert selected.status_code==200,selected.text
    assert c.get('/api/notes/'+nid).json()['cover_attribution']['license']=='CC BY 4.0'
    assert calls[-1]=='https://thumb.wikimedia.org/test.jpg'

@pytest.mark.parametrize('encrypted',[True,False])
def test_streaming_objects_roundtrip_chunks_empty_and_detect_truncation(tmp_path,encrypted):
    cipher=DataCipher.ephemeral() if encrypted else DataCipher(enabled=False)
    store=FileStore(tmp_path,cipher)
    for body in (b'',b'small',b'x'*(CHUNK+15)):
        identity=secrets.token_hex(16);writer=store.writer(identity)
        for at in range(0,len(body),13777):writer.write(body[at:at+13777])
        size,_=writer.finish();assert size==len(body);assert b''.join(store.iter_read(identity))==body
        path=store.root/identity
        if encrypted and body:assert body[:100] not in path.read_bytes()
        content=path.read_bytes();path.write_bytes(content[:-10])
        with pytest.raises(StorageError):b''.join(store.iter_read(identity))


def test_s3_storage_receives_encrypted_opaque_objects_and_deletes(tmp_path):
    class Body(io.BytesIO):pass
    class S3:
        def __init__(self):self.objects={}
        def put_object(self,**kw):self.objects[kw['Key']]=kw['Body'];return {}
        def get_object(self,**kw):return {'Body':Body(self.objects[kw['Key']])}
        def delete_object(self,**kw):self.objects.pop(kw['Key'],None);return {}
    client=S3();store=FileStore(tmp_path,DataCipher.ephemeral(),backend='s3',client=client,bucket='test',prefix='site')
    identity=secrets.token_hex(16);writer=store.writer(identity);writer.write(b'SENSITIVE_TEST_CONTENT');writer.finish()
    assert store.read(identity)==b'SENSITIVE_TEST_CONTENT'
    assert b'SENSITIVE_TEST_CONTENT' not in next(iter(client.objects.values()))
    assert store.key(identity).endswith('/files/'+identity)
    store.delete(identity);assert not client.objects

def test_site_favicon_requests_only_public_origin_and_scopes_widget_owner(monkeypatch):
    from app.modules import site_icons
    from tests.test_widgets import enable,add
    c,_,_=owner();g,_=member(c);enable(c,'bookmarks');enable(g,'bookmarks')
    add(c,'bookmarks',{'links':[{'title':'Site','url':'https://example.com/private/path?token=secret','icon':'auto'}]});wid='extra_bookmarks'
    calls=[]
    async def fetch(url,**kwargs):
        calls.append(url)
        if url.endswith('/'):return b'<html><link rel="icon" href="/site.png"></html>',url,'text/html'
        return image_bytes(),url,'image/png'
    monkeypatch.setattr(site_icons,'fetch_public',fetch)
    assert g.get('/api/widgets/'+wid+'/site-icon/0').status_code==404
    r=c.get('/api/widgets/'+wid+'/site-icon/0');assert r.status_code==200,r.text
    assert calls==['https://example.com/','https://example.com/site.png']
    assert c.get('/api/widgets/'+wid+'/site-icon/0').status_code==200 and len(calls)==2

@pytest.mark.parametrize('changes,expected',[
    ({},'https://upload.wikimedia.org/original.jpg'),
    ({'size':21*1024*1024},None),
    ({'width':12000,'height':6000},None),
    ({'mime':'image/tiff'},None),
    ({'width':0},None),
    ({'width':600,'height':1800},None),
    ({'thumburl':'https://thumb.wikimedia.org/small.jpg','thumbwidth':1600,'thumbheight':600,'size':100000000,'width':24000,'height':12000},'https://thumb.wikimedia.org/small.jpg'),
    ({'thumburl':'https://upload.wikimedia.org/small.jpg','thumbwidth':1600,'thumbheight':600},'https://upload.wikimedia.org/small.jpg'),
    ({'thumburl':'https://thumb.wikimedia.org/small.jpg'},None),
    ({'thumburl':'https://thumb.wikimedia.org/huge.jpg','thumbwidth':12000,'thumbheight':6000},None),
    ({'thumburl':'https://example.org/small.jpg','thumbwidth':1600,'thumbheight':600},None),
    ({'url':'https://upload.wikimedia.org.evil.test/photo.jpg'},None),
])
def test_commons_cover_uses_safe_renditions_and_skips_oversized_originals(changes,expected):
    from app.modules.note_assets import commons_cover_url
    info={'width':1800,'height':600,'size':200000,'mime':'image/jpeg','url':'https://upload.wikimedia.org/original.jpg'}|changes
    assert commons_cover_url(info)==expected


def test_cover_download_timeout_keeps_existing_cover(monkeypatch):
    from app.modules import note_assets
    c,_,_=owner();nid=note(c)
    existing=c.post('/api/notes/'+nid+'/assets/cover',content=image_bytes()).json()['cover']
    user=c.get('/api/me').json()['id']
    token=m.tokens.encode('note-cover','timeout',m.now()+1800,owner=user,provider='commons',url='https://thumb.wikimedia.org/slow.jpg',attribution={})
    async def timeout(*args,**kwargs):raise TimeoutError()
    monkeypatch.setattr(note_assets,'fetch_public',timeout)
    response=c.post('/api/notes/'+nid+'/select-cover',json={'token':token})
    assert response.status_code==422,response.text
    assert c.get('/api/notes/'+nid).json()['cover']==existing


def test_unsplash_is_not_advertised_without_key(monkeypatch):
    from app.modules import note_assets
    c,_,_=owner()
    monkeypatch.setenv('UNSPLASH_ACCESS_KEY','  ')
    async def fetch(url,**kwargs):return b'{}',url,'application/json'
    monkeypatch.setattr(note_assets,'fetch_public',fetch)
    assert c.get('/api/note-cover-search?q=no-key').json()['providers']==['commons']
    response=c.get('/api/note-cover-search?provider=unsplash&q=no-key')
    assert response.status_code==503 and 'UNSPLASH_ACCESS_KEY' in response.text


@pytest.mark.parametrize('status',[200,401,403,429])
def test_unsplash_configured_search_and_actionable_errors(monkeypatch,status):
    from app.modules import note_assets
    import httpx
    c,_,_=owner();calls=[]
    monkeypatch.setenv('UNSPLASH_ACCESS_KEY',' test-key ')
    async def get(self,url,**kwargs):
        calls.append(kwargs)
        return httpx.Response(status,request=httpx.Request('GET',url),json={'results':[{
            'id':'photo','urls':{'regular':'https://images.unsplash.com/photo?w=1080','small':'https://images.unsplash.com/photo?w=400'},
            'links':{'download_location':'https://api.unsplash.com/photos/photo/download','html':'https://unsplash.com/photos/photo'},'user':{'name':'Photographer'}
        }]})
    monkeypatch.setattr(note_assets.httpx.AsyncClient,'get',get)
    response=c.get('/api/note-cover-search?provider=unsplash&q=status-'+str(status))
    assert calls[0]['headers']['Authorization']=='Client-ID test-key'
    if status==200:
        assert response.status_code==200,response.text
        assert response.json()['providers']==['commons','unsplash']
        item=response.json()['items'][0]
        assert item['selection_token'] and item['attribution']['author']=='Photographer'
        async def fetch(url,**kwargs):return image_bytes(),url,'image/png'
        monkeypatch.setattr(note_assets,'fetch_public',fetch)
        nid=note(c)
        selected=c.post('/api/notes/'+nid+'/select-cover',json={'token':item['selection_token']})
        assert selected.status_code==200,selected.text
        assert selected.json()['cover_attribution']['author']=='Photographer'
        assert len(calls)==2 and calls[1]['headers']['Authorization']=='Client-ID test-key'
    else:
        assert response.status_code==503,response.text
        assert ('UNSPLASH_ACCESS_KEY' if status in (401,403) else 'Лимит') in response.text


def test_replaced_decoration_cleanup_keeps_current_and_referenced_assets():
    from app.modules.note_assets import purge_unused_assets
    c,_,_=owner();nid=note(c);other=note(c)
    old=c.post('/api/notes/'+nid+'/assets/icon',content=image_bytes()).json()['icon'][6:]
    current=c.post('/api/notes/'+nid+'/assets/icon',content=image_bytes()).json()['icon'][6:]
    c.put('/api/notes/'+other,json={'title':'Ref','body':'/api/notes/'+nid+'/assets/'+old})
    c.put('/api/notes/'+other,json={'title':'Ref','body':'changed'})
    with m.db() as db:
        db.execute('UPDATE note_assets SET created=?',(m.now()-2*86400,))
        purge_unused_assets(db,m.now())
        assert db.execute('SELECT 1 FROM media WHERE id=?',(old,)).fetchone()
        db.execute('DELETE FROM note_revisions WHERE note_id=?',(other,))
        purge_unused_assets(db,m.now())
        assert not db.execute('SELECT 1 FROM media WHERE id=?',(old,)).fetchone()
        assert db.execute('SELECT 1 FROM media WHERE id=?',(current,)).fetchone()
        assert db.execute('SELECT 1 FROM blob_gc WHERE id=? AND kind=?',(old,'media')).fetchone()


def test_trash_images_are_read_only_and_keep_page_permissions():
    c,_,_=owner();g,_=member(c);nid=note(c)
    c.post('/api/notes/'+nid+'/assets/icon',content=image_bytes())
    page=c.get('/api/notes/'+nid).json();url=page['icon_url']
    assert c.delete('/api/notes/'+nid).status_code==200
    assert c.get(url).status_code==404
    assert c.get(url+'?trash=true').status_code==200
    assert g.get(url+'?trash=true').status_code==404
    assert client().get(url+'?trash=true').status_code==401
    assert c.post('/api/notes/'+nid+'/assets/icon?trash=true',content=image_bytes()).status_code==404
    assert c.post('/api/notes/'+nid+'/restore').status_code==200
    assert c.get(url).status_code==200
