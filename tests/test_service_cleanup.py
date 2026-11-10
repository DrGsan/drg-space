import json
from tests.test_security import m,owner,reset,note
from tests.test_next_features import ok
from tests.test_widgets import enable
from app.retention import cleanup


def test_expired_service_rows_are_removed_and_live_grants_preserved():
    c,_,_=owner();nid=note(c);uid=c.get('/api/me').json()['id'];stamp=m.now();enable(c,'files')
    file=ok(c.post('/api/files/upload?name=sample.txt',content=b'sample'))
    with m.db() as db:
        for token,end,used in [('expired',stamp,0),('used',stamp+100,1),('live',stamp+100,0)]:db.execute('INSERT INTO invites VALUES(?,?,?,?)',(token,None,end,used))
        for token,end in [('expired',stamp),('live',stamp+100)]:
            db.execute('INSERT INTO password_resets VALUES(?,?,?,?)',(token,uid,end,stamp))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(token,uid,'csrf','full',end,stamp))
            db.execute('INSERT INTO session_info VALUES(?,?,?)',(token,'test','local'))
            db.execute('INSERT INTO refresh_tokens VALUES(?,?,?,?,?)',(token,token,end+100,stamp,1))
            db.execute('INSERT INTO note_unlocks VALUES(?,?,?)',(nid,token,end))
        for parent,foreign,sessions in [('shares',nid,'share_sessions'),('file_shares',file['id'],'file_share_sessions')]:
            for token,end,revoked in [('expired',stamp,0),('revoked',stamp+100,1),('live',stamp+100,0)]:
                db.execute(f'INSERT INTO {parent}(token,{"note_id" if parent=="shares" else "file_id"},mode,expires,revoked,created) VALUES(?,?,?,?,?,?)',(token,foreign,'public',end,revoked,stamp))
                db.execute(f'INSERT INTO {sessions} VALUES(?,?,?)',(token,token,stamp+100))
            db.execute(f'INSERT INTO {sessions} VALUES(?,?,?)',('old-grant','live',stamp))
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]);settings['_pending_totp']='STALE_SECRET';settings['_pending_until']=stamp;settings['profile']={'display_name':'DrG'}
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
        db.execute('INSERT INTO auth_failures VALUES(?,?,?)',('expired','local',stamp-900))
        db.execute('INSERT INTO auth_failures VALUES(?,?,?)',('live','local',stamp-899))
        db.execute('INSERT INTO throttle VALUES(?,?)',('expired',stamp-86400))
        db.execute('INSERT INTO throttle VALUES(?,?)',('live',stamp-86399))
        db.execute('INSERT INTO security_bans VALUES(?,?,?,?,?,?,?)',('expired','ip','local','login',1,stamp,stamp-30*86400))
        db.execute('INSERT INTO security_bans VALUES(?,?,?,?,?,?,?)',('live','ip','local','login',1,stamp,stamp-30*86400+1))
    cleanup(m.db,stamp)
    with m.db() as db:
        for table in ('invites','password_resets','note_unlocks','shares','file_shares','share_sessions','file_share_sessions','throttle','auth_failures','security_bans'):
            key='key' if table in ('throttle','auth_failures','security_bans') else 'token' if table!='note_unlocks' else 'session_token'
            assert [r[0] for r in db.execute(f"SELECT {key} FROM {table} WHERE {key} IN ('expired','revoked','used','old-grant','live')")]==['live'],table
        assert not db.execute('SELECT 1 FROM sessions WHERE token=?',('expired',)).fetchone()
        assert not db.execute('SELECT 1 FROM session_info WHERE session_token=?',('expired',)).fetchone()
        assert not db.execute('SELECT 1 FROM refresh_tokens WHERE token=?',('expired',)).fetchone()
        assert db.execute('SELECT used FROM refresh_tokens WHERE token=?',('live',)).fetchone()[0]==1
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0]);assert '_pending_totp' not in settings and settings['profile']['display_name']=='DrG'
        assert db.execute('SELECT 1 FROM notes WHERE id=?',(nid,)).fetchone()
        assert db.execute('SELECT 1 FROM files WHERE id=?',(file['id'],)).fetchone()


def test_transient_settings_and_orphan_passkey_indexes_expire_without_new_visits():
    from app.retention import purge_service_records
    from app.passkeys import index_key
    c,_,_=owner();uid=c.get('/api/me').json()['id'];stamp=m.now()
    with m.db() as db:
        token=db.execute('SELECT token FROM sessions WHERE user_id=? ORDER BY created DESC LIMIT 1',(uid,)).fetchone()[0]
        settings=json.loads(db.execute('SELECT settings FROM users WHERE id=?',(uid,)).fetchone()[0])
        settings['_passkeys']=[{'credential_id':'live-credential'}]
        db.execute('UPDATE users SET settings=? WHERE id=?',(json.dumps(settings),uid))
        values={'admin-access:'+token:{'last_activity':stamp-900},'admin-access:deleted-session':{'last_activity':stamp},
                'passkey:challenge:expired':{'expires':stamp},'passkey:challenge:live':{'expires':stamp+180},
                index_key('live-credential'):uid,index_key('orphan'):uid}
        for key,value in values.items():db.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value)))
        purge_service_records(db,stamp)
        remaining={r[0] for r in db.execute('SELECT key FROM app_settings')}
        assert 'admin-access:'+token not in remaining and 'admin-access:deleted-session' not in remaining
        assert 'passkey:challenge:expired' not in remaining and index_key('orphan') not in remaining
        assert {'passkey:challenge:live',index_key('live-credential')}<=remaining


def test_blob_queue_cancels_live_objects_and_failure_does_not_starve_later_deletions(monkeypatch):
    from app.data_crypto import StorageError
    c,_,_=owner();enable(c,'files')
    file=ok(c.post('/api/files/upload?name=live.txt',content=b'keep'))
    with m.db() as db:
        live=db.execute('SELECT object_id FROM files WHERE id=?',(file['id'],)).fetchone()[0]
        db.execute('INSERT INTO blob_gc VALUES(?,?,?)',(live,'file',0))
        for i in range(100):db.execute('INSERT INTO blob_gc VALUES(?,?,?)',(format(i,'032x'),'file',i+1))
        db.execute('INSERT INTO blob_gc VALUES(?,?,?)',('f'*32,'file',101))
    deleted=[]
    def remove(identity):
        assert identity!=live
        if identity=='f'*32:deleted.append(identity)
        else:raise StorageError('Temporary failure')
    monkeypatch.setattr(m.files_router.store,'delete',remove)
    m.files_router.cleanup();m.files_router.cleanup()
    assert deleted==['f'*32]
    with m.db() as db:
        assert not db.execute('SELECT 1 FROM blob_gc WHERE id=?',(live,)).fetchone()
        assert db.execute('SELECT COUNT(*) FROM blob_gc').fetchone()[0]==100
        assert db.execute('SELECT 1 FROM files WHERE id=?',(file['id'],)).fetchone()
