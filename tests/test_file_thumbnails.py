import io
from PIL import Image
from tests.test_security import owner,member,client,reset,note
from app import main as m
from tests.test_widgets import enable,image_bytes
from tests.test_community_files import upload


def test_thumbnail_is_private_bounded_and_does_not_change_original_or_storage():
    c,_,_=owner();g,_=member(c);enable(c,'files');enable(g,'files')
    source=io.BytesIO();Image.new('RGB',(640,320),'#2caee0').save(source,'PNG')
    original=source.getvalue();f=upload(c,'image.png',original)
    before=m.files_router.store.ids()
    url='/api/files/'+f['id']+'/thumbnail?v=1'
    assert client().get(url).status_code==401
    assert g.get(url).status_code==404
    response=c.get(url);assert response.status_code==200,response.text
    assert response.headers['content-type']=='image/webp'
    assert response.headers['cache-control']=='no-store'
    assert response.headers['x-content-type-options']=='nosniff'
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size==(256,128)
        assert not image.getexif()
    assert c.get('/api/files/'+f['id']+'/content').content==original
    assert m.files_router.store.ids()==before
    assert c.delete('/api/files/'+f['id']).status_code==200
    assert c.get(url).status_code==404


def test_thumbnail_does_not_render_non_images_or_unbounded_files(monkeypatch):
    c,_,_=owner();enable(c,'files')
    invalid=upload(c,'bad.png',b'<html>not an image</html>')
    assert c.get('/api/files/'+invalid['id']+'/thumbnail').status_code==415
    text=upload(c,'file.txt',image_bytes())
    assert c.get('/api/files/'+text['id']+'/thumbnail').status_code==415
    folder=c.post('/api/files/folders',json={'name':'Folder'}).json()
    assert c.get('/api/files/'+folder['id']+'/thumbnail').status_code==415
    large=upload(c,'large.png',image_bytes())
    with m.db() as db:db.execute('UPDATE files SET size=? WHERE id=?',(33*1024**2,large['id']))
    monkeypatch.setattr(m.files_router.store,'read',lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('Oversized image must not be read')))
    assert c.get('/api/files/'+large['id']+'/thumbnail').status_code==415


def test_repeated_thumbnail_reuses_derivative_but_keeps_live_access_checks(monkeypatch):
    c,_,_=owner();g,_=member(c);enable(c,'files');enable(g,'files')
    f=upload(c,'picture.png',image_bytes());url='/api/files/'+f['id']+'/thumbnail'
    assert c.get(url).status_code==200
    monkeypatch.setattr(m.files_router.store,'read',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Warm thumbnail must not read its original')))
    assert c.get(url).status_code==200
    assert g.get(url).status_code==404 and client().get(url).status_code==401
    assert c.delete('/api/files/'+f['id']).status_code==200
    assert c.get(url).status_code==404


def test_revoked_share_is_rechecked_after_a_warm_cache_read(monkeypatch):
    from app.file_thumbnail_cache import ThumbnailCache
    c,_,_=owner();enable(c,'files');f=upload(c,'picture.png',image_bytes())
    token=c.post('/api/files/'+f['id']+'/shares',json={'mode':'public'}).json()['token']
    url='/api/shared-files/'+token+'/'+f['id']+'/thumbnail';guest=client()
    assert guest.get(url).status_code==200
    read=ThumbnailCache.read
    def revoked(cache,identity):
        body=read(cache,identity)
        assert body is not None
        with m.db() as db:db.execute('DELETE FROM file_shares WHERE token=?',(token,))
        return body
    monkeypatch.setattr(ThumbnailCache,'read',revoked)
    assert guest.get(url).status_code==404


def test_replacement_uses_new_source_and_collects_retired_derivative():
    from app.file_thumbnail_cache import ThumbnailCache
    c,_,_=owner();enable(c,'files');first=io.BytesIO();second=io.BytesIO()
    Image.new('RGB',(160,80),'red').save(first,'PNG');Image.new('RGB',(80,160),'blue').save(second,'PNG')
    f=upload(c,'picture.png',first.getvalue());url='/api/files/'+f['id']+'/thumbnail?v=1'
    before=c.get(url);assert before.status_code==200
    with m.db() as db:old=db.execute('SELECT object_id FROM files WHERE id=?',(f['id'],)).fetchone()[0]
    cache=ThumbnailCache(m.files_router.store);assert cache.path(old).exists()
    replaced=c.put('/api/files/'+f['id']+'/content',params={'version':f['version']},content=second.getvalue())
    assert replaced.status_code==200,replaced.text
    assert not cache.path(old).exists() and old not in m.files_router.store.ids()
    after=c.get(url);assert after.status_code==200 and after.content!=before.content
    with Image.open(io.BytesIO(after.content)) as image:assert image.size==(80,160)
    assert c.get('/api/files/'+f['id']+'/content').content==second.getvalue()


def test_disposable_cache_is_excluded_from_backup_and_reconstructs(tmp_path):
    from app.file_thumbnail_cache import ThumbnailCache
    c,_,_=owner();enable(c,'files');f=upload(c,'picture.png',image_bytes());url='/api/files/'+f['id']+'/thumbnail'
    expected=c.get(url).content;cache=ThumbnailCache(m.files_router.store)
    with m.db() as db:identity=db.execute('SELECT object_id FROM files WHERE id=?',(f['id'],)).fetchone()[0]
    stage=tmp_path/'backup';stage.mkdir()
    with m.storage_gate.exclusive_sync():metadata=m.admin_router.backups.snapshot(stage)
    assert identity in {entry['id'] for entry in metadata['objects']}
    assert all('file-thumbnails' not in str(path) and not path.name==cache.path(identity).name for path in stage.rglob('*'))
    with cache.lock(identity):cache.discard_locked(identity)
    assert c.get(url).content==expected


def test_reconciliation_removes_cache_only_orphans():
    from app.file_thumbnail_cache import ThumbnailCache
    import secrets
    c,_,_=owner();enable(c,'files');cache=ThumbnailCache(m.files_router.store);identity=secrets.token_hex(16)
    cache.get(identity,lambda:b'synthetic orphan');assert cache.path(identity).exists()
    with m.storage_gate.exclusive_sync():m.admin_router.backups.collect_orphans()
    assert not cache.path(identity).exists()


def test_thumbnail_preserves_orientation_and_note_attachment_scope():
    c,_,_=owner();enable(c,'files')
    source=io.BytesIO();image=Image.new('RGB',(80,160),'red');exif=Image.Exif();exif[274]=6;image.save(source,'JPEG',exif=exif)
    f=upload(c,'rotated.jpg',source.getvalue())
    response=c.get('/api/files/'+f['id']+'/thumbnail');assert response.status_code==200
    with Image.open(io.BytesIO(response.content)) as thumbnail:assert thumbnail.size==(160,80)
    nid=note(c);attachment=c.post('/api/notes/'+nid+'/files?name=private.png',content=image_bytes()).json()
    assert c.get('/api/files/'+attachment['id']+'/thumbnail').status_code==404


def test_published_thumbnail_and_breadcrumbs_stay_inside_granted_root(monkeypatch):
    from tests.test_files_trash import folder
    c,_,_=owner();enable(c,'files');outer=folder(c,'Private ancestor');root=folder(c,'Publication',outer['id']);child=folder(c,'Child',root['id'])
    picture=upload(c,'picture.png',image_bytes(),child['id']);secret=upload(c,'secret.png',image_bytes(),outer['id'])
    published=c.post('/api/files/'+root['id']+'/shares',json={'mode':'public'});assert published.status_code==200,published.text
    token=published.json()['token'];guest=client();url='/api/shared-files/'+token
    listing=guest.get(url,params={'parent_id':child['id']});assert listing.status_code==200,listing.text
    assert [crumb['id'] for crumb in listing.json()['breadcrumbs']]==[root['id'],child['id']]
    assert listing.json()['max_file_bytes']>0
    assert guest.get(url+'/'+picture['id']+'/thumbnail').status_code==200
    assert guest.get(url+'/'+secret['id']+'/thumbnail').status_code==404
    # Authorization is rechecked after decoding as well as before it.
    fresh=upload(c,'fresh.png',image_bytes(),child['id'])
    original=m.files_router.store.read
    def revoked(*args,**kwargs):
        body=original(*args,**kwargs)
        with m.db() as db:db.execute('DELETE FROM file_shares WHERE token=?',(token,))
        return body
    monkeypatch.setattr(m.files_router.store,'read',revoked)
    assert guest.get(url+'/'+fresh['id']+'/thumbnail').status_code==404
