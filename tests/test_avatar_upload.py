import base64
import io
import pytest
from PIL import Image
from app.modules.profile import thumbnail
from tests.test_security import owner,member,reset
from tests.test_admin import proof


def image_bytes(fmt='MPO',size=(600,400),orientation=1):
    image=Image.new('RGB',size,'red')
    image.paste('blue',(size[0]//2,0,size[0],size[1]))
    exif=Image.Exif();exif[274]=orientation
    out=io.BytesIO()
    image.save(out,format=fmt,exif=exif,**({'save_all':True,'append_images':[Image.new('RGB',size,'green')]} if fmt=='MPO' else {}))
    return out.getvalue()


@pytest.mark.parametrize('fmt',['JPEG','MPO'])
def test_camera_photo_is_oriented_and_saved_as_one_small_private_raster(fmt):
    body=thumbnail(image_bytes(fmt,orientation=6))
    with Image.open(io.BytesIO(body)) as avatar:
        assert avatar.format=='WEBP' and avatar.size==(256,256) and avatar.n_frames==1
        assert avatar.getpixel((128,32))[0]>220  # Left half becomes the top.
        assert avatar.getpixel((128,224))[2]>220
        assert not avatar.getexif()
    assert len(body)<100000


def test_high_resolution_camera_photo_is_reduced_before_avatar_creation():
    source=image_bytes('JPEG',(6000,4000))  # Above the ordinary gallery's 20MP limit.
    with Image.open(io.BytesIO(thumbnail(source))) as avatar:
        assert avatar.size==(256,256) and avatar.format=='WEBP'


def test_own_and_managed_avatar_accept_camera_mpo_and_invalid_file_keeps_profile():
    c,_,_=owner();guest,_=member(c);uid=guest.get('/api/me').json()['id']
    source=image_bytes();assert Image.open(io.BytesIO(source)).format=='MPO'
    own=c.post('/api/account/avatar',content=source);assert own.status_code==200,own.text
    result=c.put('/api/admin/users/'+uid+'/profile',json=proof(display_name='Фото камеры',avatar=base64.b64encode(source).decode()))
    assert result.status_code==200,result.text
    saved=guest.get('/api/account/avatar');assert saved.status_code==200
    with Image.open(io.BytesIO(saved.content)) as avatar:assert avatar.size==(256,256) and avatar.n_frames==1
    previous=guest.get('/api/me').json()['settings']['profile']
    invalid=c.put('/api/admin/users/'+uid+'/profile',json=proof(display_name='Не сохранять',avatar=base64.b64encode(b'broken image').decode()))
    assert invalid.status_code==422
    assert guest.get('/api/me').json()['settings']['profile']==previous
    assert guest.get('/api/account/avatar').content==saved.content


def test_avatar_keeps_alpha_and_uses_first_gif_frame_only():
    out=io.BytesIO();Image.new('RGBA',(40,60),(0,0,0,0)).save(out,format='PNG')
    with Image.open(io.BytesIO(thumbnail(out.getvalue()))) as avatar:assert avatar.getpixel((0,0))[3]==0
    out=io.BytesIO();Image.new('RGB',(40,60),'red').save(out,format='GIF',save_all=True,append_images=[Image.new('RGB',(40,60),'blue')])
    with Image.open(io.BytesIO(thumbnail(out.getvalue()))) as avatar:
        assert avatar.n_frames==1 and avatar.getpixel((128,128))[0]>220
