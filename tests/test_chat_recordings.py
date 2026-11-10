import json
from pathlib import Path
import pytest
from tests.test_security import reset,m
from tests.test_chat_retention import room,send
from tests.test_next_features import ok


def recorded(c,sid,kind='voice',**extra):
    raw=(Path(__file__).parent/('fixtures/chat-audio.m4a' if kind=='voice' else 'fixtures/chat-video.mp4')).read_bytes()
    info={'kind':kind,'duration_ms':2200,**({'waveform':[i%32 for i in range(64)]} if kind=='voice' else {}),**extra}
    return c.post('/api/chat/'+sid+'/attachments',params={'name':'recording.m4a' if kind=='voice' else 'recording.mp4'},content=raw,headers={'X-DrG-Recording':json.dumps(info)}),raw


@pytest.mark.parametrize('kind',['voice','video_note'])
def test_recordings_keep_explicit_metadata_and_existing_private_streaming(kind):
    c,g,o,sid=room();response,raw=recorded(c,sid,kind);file=ok(response)
    assert file['kind']==kind and file['duration']==2.2
    assert file['waveform']==([i%32 for i in range(64)] if kind=='voice' else [])
    assert g.get(file['url']).status_code==404
    message=ok(send(c,sid,[file]));assert message['attachments'][0]==file
    assert g.get(file['url'],headers={'Range':'bytes=0-15'}).content==raw[:16]
    assert o.get(file['url']).status_code==404
    assert g.get(file['url']+'?download=1').content==raw
    assert ok(g.get('/api/chat/'+sid+'/messages'))['items'][0]['attachments'][0]['kind']==kind


@pytest.mark.parametrize('metadata',[{'duration_ms':True},{'duration_ms':600001},{'duration_ms':0},{'waveform':[32]},{'waveform':[True]},{'waveform':[1]*65},{'kind':'unknown'},{'extra':'ignored'}])
def test_invalid_metadata_rolls_back_the_draft_and_blob(metadata):
    c,g,o,sid=room();response,_=recorded(c,sid,**metadata)
    assert response.status_code==422 and response.json()['code']=='invalidChatRecording'
    with m.db() as db:
        assert db.execute('SELECT COUNT(*) FROM chat_recordings').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM files WHERE id LIKE ?",('chat-%',)).fetchone()[0]==0


def test_video_note_limit_and_mismatched_audio_cannot_be_claimed_as_video():
    c,g,o,sid=room();response,_=recorded(c,sid,'video_note',duration_ms=60001);assert response.status_code==422
    raw=(Path(__file__).parent/'fixtures/chat-audio.m4a').read_bytes()
    r=c.post('/api/chat/'+sid+'/attachments',params={'name':'round.mp4'},content=raw,headers={'X-DrG-Recording':json.dumps({'kind':'video_note','duration_ms':1000})})
    assert r.status_code==422


def test_cancel_removes_metadata_but_does_not_remove_another_attachment():
    c,g,o,sid=room();first,_=recorded(c,sid);second,_=recorded(c,sid);a,b=ok(first),ok(second)
    assert c.delete(a['url']).status_code==200
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM chat_recordings WHERE file_id=?',(a['id'],)).fetchone()
        assert db.execute('SELECT 1 FROM chat_recordings WHERE file_id=?',(b['id'],)).fetchone()


def test_full_backup_and_restore_keeps_recording_metadata_and_original_bytes():
    from tests.test_admin import backup,headers
    from tests.test_security import client
    import pyotp
    c,g,o,sid=room();r,raw=recorded(c,sid,'video_note');file=ok(r);ok(send(c,sid,[file]))
    archive=backup(c)
    with m.db() as db:db.execute('DELETE FROM chat_recordings')
    response=c.post('/api/admin/backups/restore',content=archive,headers=headers());assert response.status_code==200,response.text
    c=client();r=ok(c.post('/api/login',json={'login':'owner','password':'Owner-password-123'}));c.headers['x-csrf-token']=r['csrf']
    with m.db() as db:seed=m.cipher.decrypt(db.execute("SELECT totp FROM users WHERE login='owner'").fetchone()[0].encode()).decode()
    r=ok(c.post('/api/2fa/verify',json={'code':pyotp.TOTP(seed).at(m.now())}));c.headers['x-csrf-token']=r['csrf']
    restored=ok(c.get('/api/chat/'+sid+'/messages'))['items'][0]['attachments'][0]
    assert restored['kind']=='video_note' and restored['duration']==2.2
    assert c.get(restored['url']).content==raw


def test_expiring_a_recording_removes_metadata_with_its_private_file():
    from app.chat_storage import purge
    c,g,o,sid=room();r,_=recorded(c,sid);file=ok(r);saved=ok(send(c,sid,[file]))
    with m.db() as db:
        db.execute('UPDATE messages SET expires=? WHERE id=?',(m.now()-1,saved['id']))
        purge(db,m.now())
        assert not db.execute('SELECT 1 FROM chat_recordings WHERE file_id=?',(file['id'],)).fetchone()
        assert not db.execute('SELECT 1 FROM files WHERE id=?',(file['id'],)).fetchone()
