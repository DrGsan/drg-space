"""Batch presence keeps exact privacy/liveness semantics on both engines."""
import json
import pytest

from app.notifications import actor, actors
from tests.test_storage_encryption import databases


class Counted:
    def __init__(self, raw):
        self.raw, self.count = raw, 0
    def execute(self, *args):
        self.count += 1
        return self.raw.execute(*args)


@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_batch_matches_individual_profiles_session_expiry_and_history(databases, engine):
    backend = databases(engine);stamp = 1000000
    ids = [str(n) for n in range(9)]
    ages = [None, 0, 89, 90, 86400, 86401, -20, 1, 1]
    with backend.connection() as c:
        for uid, age in zip(ids, ages):
            profile = {'display_name':'Person '+uid,'avatar_preset':'space-1'}
            if uid == '1':profile['avatar_id']='saved-image'
            c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',
                      (uid, 'login-'+uid, 'test', 'user', json.dumps({'profile':profile}), 1))
            if age is not None:c.execute('INSERT INTO user_presence VALUES(?,?)',(uid,stamp-age))
            c.execute('INSERT INTO sessions(token,user_id,csrf,stage,expires,created) VALUES(?,?,?,?,?,?)',
                      ('session-'+uid,uid,'csrf','full' if uid!='7' else 'login',stamp if uid=='8' else stamp+100,1))
            c.execute('INSERT INTO session_presence VALUES(?,?)',('session-'+uid,stamp))
        requested = [*reversed(ids), 'deleted', '1']
        for history in [False, True]:
            expected = [actor(c, uid, stamp, include_history=history) for uid in requested]
            counted = Counted(c)
            assert actors(counted, requested, stamp, include_history=history) == expected
            assert counted.count <= 2
        assert actors(Counted(c), [], stamp) == []
        assert expected[requested.index('1')]['avatar_url'].endswith('?v=saved-image')


@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_five_hundred_people_use_bounded_queries_and_see_session_revocation(databases, engine):
    backend=databases(engine);stamp=1000000
    with backend.connection() as c:
        ids=[f'{n:032x}' for n in range(500)]
        for uid in ids:
            c.execute('INSERT INTO users(id,login,password,role,created) VALUES(?,?,?,?,?)',(uid,uid,'test','user',1))
        c.execute('INSERT INTO user_presence VALUES(?,?)',(ids[0],stamp))
        c.execute('INSERT INTO sessions(token,user_id,csrf,stage,expires,created) VALUES(?,?,?,?,?,?)',('live',ids[0],'csrf','full',stamp+100,1))
        c.execute('INSERT INTO session_presence VALUES(?,?)',('live',stamp))
        counted=Counted(c);result=actors(counted,ids,stamp)
        assert len(result)==500 and result[0]['online'] and sum(x['online'] for x in result)==1
        assert counted.count <= 8, 'Presence must not perform per-person SQL.'
        c.execute('DELETE FROM sessions WHERE token=?',('live',))
        assert not actors(c,[ids[0]],stamp)[0]['online']
