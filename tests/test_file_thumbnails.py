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
    original=m.files_router.store.read
    def revoked(*args,**kwargs):
        body=original(*args,**kwargs)
        with m.db() as db:db.execute('DELETE FROM file_shares WHERE token=?',(token,))
        return body
    monkeypatch.setattr(m.files_router.store,'read',revoked)
    assert guest.get(url+'/'+picture['id']+'/thumbnail').status_code==404
