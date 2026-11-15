"""Files checks stay bounded without weakening legacy namespaces or project locks."""
import secrets
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import time

import pytest

from app import tracker
from app.data_crypto import DataCipher
from app.database import Connection
from app.file_scopes import file_module_rows, is_tracker_file, note_attachment_ids
from tests.test_security import owner, member, reset, m
from tests.test_files_trash import folder, ok
from tests.test_storage_encryption import databases


def user(c):
    uid = 'a'*32
    c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',
              (uid,'synthetic','unused','owner','{}',1))
    return uid


def file(c, uid, fid, parent=None, kind='file', mime='application/octet-stream'):
    c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,created,updated) VALUES(?,?,?,?,?,?,?,?)',
              (fid,uid,parent,kind,fid,mime,1,1))


@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_selected_paths_keep_note_tracker_and_moved_linked_attachments_hidden(databases, engine):
    backend=databases(engine)
    with backend.connection() as c:
        uid=user(c)
        project=tracker.create_project(c,tracker.Project(name='Scope',key='SCOPE'),uid,1)
        issue=tracker.create_issue(c,project['id'],tracker.Issue(title='Linked'),uid,2)
        file(c,uid,'ordinary',kind='folder')
        file(c,uid,'visible','ordinary')
        file(c,uid,'note',kind='folder',mime='application/x-drg-note;space=legacy')
        file(c,uid,'note-child','note',kind='folder');file(c,uid,'note-leaf','note-child')
        file(c,uid,'tracker',kind='folder',mime='application/x-drg-tracker;project='+project['id'])
        file(c,uid,'tracker-leaf','tracker')
        file(c,uid,'moved-link','ordinary')
        c.execute('INSERT INTO tracker_attachments(id,issue_id,actor_id,created) VALUES(?,?,?,?)',('moved-link',issue['id'],uid,3))
        selected=list(c.execute("SELECT * FROM files WHERE id IN ('visible','note-leaf','tracker-leaf','moved-link')"))
        assert [row['id'] for row in file_module_rows(c,selected,uid)]==['visible']
        assert note_attachment_ids(c,uid,selected=selected)=={'note-leaf'}
        assert is_tracker_file(c,next(row for row in selected if row['id']=='moved-link'))
        assert is_tracker_file(c,next(row for row in selected if row['id']=='tracker-leaf'))
        assert not is_tracker_file(c,next(row for row in selected if row['id']=='visible'))
        assert file_module_rows(c,[],uid)==[]


@pytest.mark.parametrize('engine', ['sqlite', 'postgres'])
def test_ancestor_cycles_and_multiple_query_chunks_preserve_every_visible_row(databases, engine):
    backend=databases(engine)
    with backend.connection() as c:
        uid=user(c)
        file(c,uid,'cycle-a',kind='folder');file(c,uid,'cycle-b','cycle-a',kind='folder')
        c.execute('UPDATE files SET parent_id=? WHERE id=?',('cycle-b','cycle-a'))
        for number in range(520):file(c,uid,f'visible-{number}')
        selected=list(c.execute('SELECT * FROM files'))
        assert {row['id'] for row in file_module_rows(c,selected,uid)}=={row['id'] for row in selected}
        assert not is_tracker_file(c,next(row for row in selected if row['id']=='cycle-a'))


def test_empty_folder_work_does_not_grow_with_unrelated_owners(monkeypatch):
    client,_,_=owner();other,_=member(client)
    uid=ok(client.get('/api/me'))['id'];foreign=ok(other.get('/api/me'))['id']
    empty=folder(client,'Empty')
    with m.db() as c:
        for account in [uid,foreign]:
            for number in range(1000):file(c,account,secrets.token_hex(16))
    counts={'sql':0,'decode':0}
    execute=Connection._execute;decode=DataCipher.decode
    def counted_execute(self,*args,**kwargs):counts['sql']+=1;return execute(self,*args,**kwargs)
    def counted_decode(self,*args,**kwargs):counts['decode']+=1;return decode(self,*args,**kwargs)
    monkeypatch.setattr(Connection,'_execute',counted_execute);monkeypatch.setattr(DataCipher,'decode',counted_decode)
    result=ok(client.get('/api/files',params={'parent_id':empty['id']}))
    assert result['items']==[]
    assert counts['sql']<=40
    if m.database.kind=='postgres':assert counts['decode']<=100


def test_scoped_file_checks_retain_postgres_project_lock(databases):
    backend=databases('postgres')
    with backend.connection() as c:
        uid=user(c);project=tracker.create_project(c,tracker.Project(name='Locks',key='LOCKS'),uid,1)
        file(c,uid,'legacy-root',kind='folder',mime='application/x-drg-tracker;project='+project['id'])
        file(c,uid,'legacy-leaf','legacy-root')
    pid=Queue()
    def delete_lock():
        with backend.connection() as c:
            pid.put(c.raw.execute('SELECT pg_backend_pid()').fetchone()[0])
            c.raw.execute('SELECT id FROM tracker_projects WHERE id=%s FOR UPDATE',(project['id'],))
        return True
    with ThreadPoolExecutor(max_workers=1) as worker:
        with backend.connection() as c:
            selected=list(c.execute('SELECT * FROM files WHERE id=?',('legacy-leaf',)))
            assert file_module_rows(c,selected,uid)==[]
            future=worker.submit(delete_lock);waiting=pid.get(timeout=5);deadline=time.monotonic()+5
            while not future.done() and time.monotonic()<deadline:
                blocked=c.raw.execute('SELECT 1 FROM pg_locks WHERE pid=%s AND NOT granted',(waiting,)).fetchone()
                if blocked:break
                time.sleep(.01)
            else:pytest.fail('Project deletion did not wait for the Files transaction')
            assert not future.done()
        assert future.result(timeout=5)
