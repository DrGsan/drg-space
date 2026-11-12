"""Batch history preserves rich payloads, privacy, expiry and fresh revocation."""
import json
import time
import pytest

from app.chat_storage import encode, serial_message, serial_messages
from app.notifications import actor
from tests.test_storage_encryption import databases

class Counted:
    def __init__(self, raw):self.raw, self.count = raw, 0
    def execute(self, *args):
        self.count += 1
        return self.raw.execute(*args)

@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_hundred_rich_messages_match_single_reads_with_bounded_queries(databases, engine):
    backend = databases(engine);stamp = int(time.time());sid = 'a'*16;foreign = 'b'*16
    people = [f'{n+1:032x}' for n in range(21)]
    with backend.connection() as c:
        for n, uid in enumerate(people):
            c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',
                (uid,'person.'+str(n),'test','user',json.dumps({'profile':{'display_name':'Person '+str(n)}}),stamp))
            c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',('session-'+uid,uid,'csrf','full',stamp+1000,stamp))
            c.execute('INSERT INTO session_presence VALUES(?,?)',('session-'+uid,stamp-10))
            c.execute('INSERT INTO user_presence VALUES(?,?)',(uid,stamp-10))
        for room in [sid,foreign]:
            c.execute('INSERT INTO spaces(id,kind,name,owner_id,created) VALUES(?,?,?,?,?)',(room,'family','Group',people[0],stamp-100))
        for uid in people[:20]:c.execute('INSERT INTO space_members VALUES(?,?,?)',(sid,uid,stamp-100))
        expired=c.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created,expires) VALUES(?,?,?,?,?,?)',(sid,people[0],'expired-reference','Expired private text',stamp-100,stamp-1)).lastrowid
        other=c.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',(foreign,people[20],'foreign-reference','Foreign private text',stamp-100)).lastrowid
        mids=[]
        for n in range(100):
            body='Message '+str(n)+' @person.1. https://example.org/@person.2'
            reference=expired if n%4==0 else other if n%4==1 else mids[0] if mids else None
            fid='chat-'+sid+'-'+f'{n+1:016x}'
            mid=c.execute('INSERT INTO messages(space_id,sender_id,client_id,body,created) VALUES(?,?,?,?,?)',
                (sid,people[n%20],'message-'+str(n),encode(body,[fid],reference),stamp-20)).lastrowid
            mids.append(mid)
            owner=people[20] if n%9==0 else people[n%20]
            version=1 if n%11==0 else mid+1
            c.execute('INSERT INTO files(id,user_id,kind,name,mime,size,version,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                (fid,owner,'file','clip.mp4','audio/mp4',10,version,stamp,stamp))
            c.execute('INSERT INTO chat_recordings(file_id,payload) VALUES(?,?)',(fid,json.dumps({'kind':'voice','duration_ms':1200,'waveform':[.2,.4]})))
            value={'version':1,'space':sid,'message':mid,'revision':n+1,'users':{people[0]:['👍'],people[1]:['❤️'],people[20]:['👍']}}
            c.execute('INSERT INTO app_settings VALUES(?,?)',('message-reactions:'+sid+':'+str(mid),json.dumps(value)))
        marks=','.join('?' for _ in mids)
        def rows():return c.execute('SELECT m.*,u.login AS sender FROM messages m JOIN users u ON u.id=m.sender_id WHERE m.id IN ('+marks+') ORDER BY m.id',tuple(mids)).fetchall()
        def compare():
            values=rows()
            expected=[serial_message(c,row,stamp)|{'sender_profile':actor(c,row['sender_id'],stamp)} for row in values]
            counted=Counted(c);actual=serial_messages(counted,values,stamp)
            assert actual==expected
            assert counted.count<=15, 'History must batch profiles, replies, reactions and attachments.'
            assert not any(p['id']==people[20] for item in actual for reaction in item['reactions'] for p in reaction['people'])
            assert actual[1]['reply']['unavailable']
            assert all('Foreign private text' not in json.dumps(item) for item in actual)
            return actual
        before=compare();assert before[0]['sender_profile']['online']
        c.execute('DELETE FROM sessions WHERE user_id=?',(people[0],))
        c.execute('DELETE FROM space_members WHERE space_id=? AND user_id=?',(sid,people[1]))
        after=compare();assert not after[0]['sender_profile']['online']
        assert not any(p['id']==people[1] for item in after for p in item['mentions'])
        assert not any(p['id']==people[1] for item in after for reaction in item['reactions'] for p in reaction['people'])
