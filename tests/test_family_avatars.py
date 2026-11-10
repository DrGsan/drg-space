"""Private family portraits: ownership, lifecycle and portable backup recovery."""
import io
from PIL import Image
from tests.test_security import owner,member,client,reset
from app import main as m
from app.modules.family_avatars import PREFIX,purge_avatars
from tests.test_social import family,join,enabled
from tests.test_admin import backup,headers


def image(color='red'):
    output=io.BytesIO();Image.new('RGB',(1800,1200),color).save(output,'JPEG');return output.getvalue()


def test_family_photo_owner_only_private_membership_and_common_room_identity():
    c,_,_=owner();g,_=member(c);other,_=member(c,'outsider');sid=family(c);join(c,g,sid)
    url=f'/api/social/families/{sid}/avatar'
    assert c.get(url).status_code==404
    assert client().get(url).status_code==401
    assert g.post(url,content=image()).status_code==403
    assert other.post(url,content=image()).status_code==404
    uploaded=c.post(url,content=image());assert uploaded.status_code==200,uploaded.text
    version=uploaded.json()['avatar_url']
    for user in (c,g):
        saved=user.get(version);assert saved.status_code==200,saved.text
        assert 'no-store' in saved.headers['cache-control']
        with Image.open(io.BytesIO(saved.content)) as avatar:assert avatar.format=='WEBP' and avatar.size==(256,256)
        assert next(s for s in user.get('/api/social').json()['spaces'] if s['id']==sid)['avatar_url']==version
        enabled(user)
        assert next(s for s in user.get('/api/chat/rooms').json()['items'] if s['id']==sid)['avatar_url']==version
    assert other.get(version).status_code==404
    assert g.delete(url).status_code==403
    assert c.delete(f'/api/social/families/{sid}/members/{g.get("/api/me").json()["id"]}').status_code==200
    assert g.get(version).status_code==404


def test_photo_replacement_invalid_upload_remove_and_delete_family_leave_no_garbage():
    c,_,_=owner();sid=family(c);url=f'/api/social/families/{sid}/avatar'
    first=c.post(url,content=image()).json()['avatar_url'];original=c.get(first).content
    assert c.post(url,content=b'not an image').status_code==422
    assert c.get(first).content==original
    assert c.post(url,content=b'',headers={'content-length':str(11*1024*1024)}).status_code==413
    second=c.post(url,content=image('blue')).json()['avatar_url'];assert second!=first
    with m.db() as db:assert db.execute('SELECT COUNT(*) FROM app_settings WHERE key=?',(PREFIX+sid,)).fetchone()[0]==1
    assert c.delete(url).status_code==200
    assert c.get(url).status_code==404
    assert c.post(url,content=image()).status_code==200
    deletion=c.delete('/api/social/spaces/'+sid);assert deletion.status_code==200
    assert c.get(url).status_code==200
    assert c.post(url,content=image()).status_code==423
    assert c.delete(url).status_code==423
    with m.db() as db:
        from app.workspace_lifecycle import purge
        purge(db,deletion.json()['deletion']['delete_at'])
    with m.db() as db:assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',(PREFIX+sid,)).fetchone()
    assert c.get(url).status_code==404


def test_photo_survives_full_encrypted_backup_restore_with_unchanged_schema():
    c,_,_=owner();sid=family(c);url=f'/api/social/families/{sid}/avatar'
    version=c.post(url,content=image()).json()['avatar_url'];original=c.get(version).content
    saved=backup(c)
    assert c.delete(url).status_code==200
    restored=c.post('/api/admin/backups/restore',content=saved,headers=headers());assert restored.status_code==200,restored.text
    import base64
    from app.modules.family_avatars import avatar_url
    with m.db() as db:
        assert avatar_url(db,sid)==version
        assert base64.b64decode(db.execute('SELECT value FROM app_settings WHERE key=?',(PREFIX+sid,)).fetchone()[0])==original


def test_orphaned_family_portrait_is_purged_when_owner_disappears():
    c,_,_=owner();sid=family(c);assert c.post(f'/api/social/families/{sid}/avatar',content=image()).status_code==200
    with m.db() as db:
        db.execute('DELETE FROM spaces WHERE id=?',(sid,));purge_avatars(db)
        assert not db.execute('SELECT 1 FROM app_settings WHERE key=?',(PREFIX+sid,)).fetchone()


def test_pending_space_portrait_remains_visible_only_to_members_until_final_deletion():
    c,_,_=owner();guest,_=member(c);outsider,_=member(c,'outsider');sid=family(c);join(c,guest,sid)
    url=f'/api/social/spaces/{sid}/avatar'
    response=c.post(url,content=image());assert response.status_code==200,response.text
    portrait=c.get(response.json()['avatar_url']).content
    deletion=c.delete('/api/social/spaces/'+sid);assert deletion.status_code==200,deletion.text
    for user in (c,guest):
        row=next(space for space in user.get('/api/social').json()['spaces'] if space['id']==sid)
        assert row['deletion']
        avatar=user.get(row['avatar_url']);assert avatar.status_code==200,avatar.text
        assert avatar.content==portrait and 'no-store' in avatar.headers['cache-control']
    assert outsider.get(url).status_code==404 and client().get(url).status_code==401
    with m.db() as db:
        from app.workspace_lifecycle import purge
        purge(db,deletion.json()['deletion']['delete_at'])
    for user in (c,guest,outsider):assert user.get(url).status_code==404
