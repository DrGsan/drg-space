"""Existing document uploads gain scoped previews without rewriting originals."""
from tests.test_security import reset,client,m
from tests.test_chat_retention import room,upload,send,image_bytes
from tests.test_next_features import ok


def test_quicktime_and_original_previews_preserve_bytes_and_authorization():
    c,g,o,sid=room();body=b'\x00\x00\x00\x18ftypqt  '+b'0'*100
    movie=upload(c,sid,'iPhone.MOV',body)
    assert movie['kind']=='video' and movie['mime']=='video/quicktime'
    original=ok(c.post('/api/chat/'+sid+'/attachments?name=original.png&as_file=true',content=image_bytes()))
    assert original['kind']=='file' and original['preview_url']
    legacy=ok(c.post('/api/chat/'+sid+'/attachments?name=older.mov&as_file=true',content=body))
    assert client().get(legacy['preview_url']).status_code==401
    assert g.get(legacy['preview_url']).status_code==404
    assert o.get(legacy['preview_url']).status_code==404
    for file,kind,mime in [(original,'image','image/png'),(legacy,'video','video/quicktime')]:
        preview=ok(c.get(file['preview_url']))
        assert preview['kind']==kind and preview['mime']==mime
        assert '?preview=true' in preview['url']
    message=ok(send(c,sid,[movie,original,legacy]))
    preview=ok(g.get(legacy['preview_url']))
    response=g.get(preview['url'],headers={'range':'bytes=4-11'})
    assert response.status_code==206 and response.content==body[4:12]
    assert response.headers['content-type']=='video/quicktime'
    assert response.headers['content-disposition'].startswith('inline')
    download=g.get(preview['url']+'&download=1')
    assert download.content==body and download.headers['content-disposition'].startswith('attachment')
    # Preview never changes document metadata, ciphertext, or original bytes.
    assert g.get(legacy['url']).content==body
    with m.db() as db:assert db.execute('SELECT mime FROM files WHERE id=?',(legacy['id'],)).fetchone()[0]=='application/octet-stream'
    ok(c.delete('/api/social/families/'+sid+'/members/'+g.get('/api/me').json()['id']))
    assert g.get(legacy['preview_url']).status_code==404
    assert g.get(preview['url']).status_code==404
    with m.db() as db:db.execute('UPDATE messages SET expires=? WHERE id=?',(m.now()-1,message['id']))
    assert c.get(legacy['preview_url']).status_code==404
    assert c.get(preview['url']).status_code==404


def test_preview_rejects_forged_extensions_and_keeps_download_sandboxed():
    c,g,o,sid=room()
    for name in ('fake.png','fake.mov','fake.mp4','fake.webm'):
        file=upload(c,sid,name,b'<html><script>alert(1)</script></html>')
        assert file['kind']=='file'
        preview=ok(c.get(file['preview_url']))
        assert preview['kind']=='file' and 'preview_url' not in preview
        response=c.get(file['url']+'?preview=true')
        assert response.headers['content-type']=='application/octet-stream'
        assert response.headers['content-disposition'].startswith('attachment')
        assert response.headers['x-content-type-options']=='nosniff'
        assert 'sandbox' in response.headers['content-security-policy']


def test_gif_previews_and_old_uploads_keep_animated_bytes_and_scoped_streaming():
    import io
    from PIL import Image
    c,g,o,sid=room();out=io.BytesIO()
    Image.new('RGB',(16,16),'red').save(out,format='GIF',save_all=True,append_images=[Image.new('RGB',(16,16),'blue')],duration=80,loop=0)
    body=out.getvalue();file=upload(c,sid,'animation.gif',body)
    assert file['kind']=='animation' and file['mime']=='image/gif'
    original=ok(c.post('/api/chat/'+sid+'/attachments?name=old.gif&as_file=true',content=body))
    ok(send(c,sid,[file,original]))
    checked=ok(g.get(original['preview_url']));assert checked['kind']=='animation'
    for item in [file,checked]:
        response=g.get(item['url'])
        assert response.content==body and response.headers['content-type']=='image/gif'
        assert response.headers['content-disposition'].startswith('inline')
        assert o.get(item['url']).status_code==404


def test_audio_recording_uses_existing_private_upload_send_range_and_expiry():
    from pathlib import Path
    c,g,o,sid=room();raw=(Path(__file__).parent/'fixtures/chat-audio.m4a').read_bytes()
    audio=upload(c,sid,'Голосовое.m4a',raw)
    assert audio['kind']=='audio' and audio['mime']=='audio/mp4'
    assert g.get(audio['url']).status_code==404
    saved=ok(send(c,sid,[audio]));assert saved['attachments'][0]['kind']=='audio'
    response=g.get(audio['url'],headers={'range':'bytes=0-31'})
    assert response.status_code==206 and response.content==raw[:32]
    assert response.headers['content-type']=='audio/mp4' and response.headers['content-disposition'].startswith('inline')
    assert o.get(audio['url']).status_code==404
    assert g.get(audio['url']+'?download=1').content==raw
    with m.db() as db:db.execute('UPDATE messages SET expires=? WHERE id=?',(m.now()-1,saved['id']))
    assert c.get(audio['url']).status_code==404


def test_audio_containers_reject_forged_extensions_and_video_is_not_audio():
    from app.audio_media import audio_mime
    from pathlib import Path
    assert audio_mime((Path(__file__).parent/'fixtures/chat-audio.m4a').read_bytes())=='audio/mp4'
    assert audio_mime((Path(__file__).parent/'fixtures/chat-video.mp4').read_bytes()) is None
    for raw in (b'<html>OpusHead</html>',b'ftypM4A',b'\x00\x00\x00\x01ftyp'+b'\xff'*8,b'\x00\x00\x00\x08ftyp'+b'bad'):
        assert audio_mime(raw) is None
    c,_,_,sid=room()
    for name in ('fake.m4a','fake.ogg','fake.webm'):
        file=upload(c,sid,name,b'<html><script>bad()</script></html>');assert file['kind']=='file'
        assert c.get(file['url']).headers['content-type']=='application/octet-stream'
