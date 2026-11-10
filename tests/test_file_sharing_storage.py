import pytest
from app.file_sharing import purge,consume,fingerprint,ticket
from app.file_store import FileStore
from app.auth_tokens import AuthTokens
from app.http import HTTPError
from tests.test_storage_encryption import databases,seed,backup_service

@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_backup_restores_limits_and_dates_but_not_download_grants(databases,engine):
 b=databases(engine);seed(b);store=FileStore(b.root,b.cipher,backend='local');writer=store.writer('c'*32);writer.write(b'recoverable download');writer.finish()
 with b.connection() as c:
  c.execute('INSERT INTO files(id,user_id,kind,name,object_id,size,created,updated) VALUES(?,?,?,?,?,?,?,?)',('f'*16,'a'*32,'file','temporary.txt','c'*32,20,1,1))
  c.execute('INSERT INTO file_shares(token,file_id,mode,expires,created) VALUES(?,?,?,?,?)',('test-link','f'*16,'public',4000000000,1))
  c.execute('INSERT INTO file_share_limits VALUES(?,5,3,2)',('test-link',))
  c.execute('INSERT INTO file_download_grants VALUES(?,?,?,?)',('d'*64,'test-link','e'*64,4000000000))
  c.execute('INSERT INTO file_retention VALUES(?,?,?,2,?)',('f'*16,4000000000,4000000000,1))
  c.execute('INSERT INTO files(id,user_id,kind,name,created,updated) VALUES(?,?,?,?,?,?)',('g'*16,'a'*32,'folder','Temporary',1,1))
  c.execute('INSERT INTO file_folder_retention VALUES(?,?,?,3,?)',('g'*16,4000000000,4000000000,1))
 service=backup_service(b)
 with service.temp() as stage:
  metadata=service.snapshot(stage);archive=service.encrypt(stage,metadata,'Backup-password-123')
  body=archive.read_bytes()
 with service.temp() as stage:
  metadata=service.prepare(body,'Backup-password-123',stage)
  service.restore(stage,metadata)
 with b.connection() as c:
  assert tuple(c.execute('SELECT download_limit,used,version FROM file_share_limits').fetchone())==(5,3,2)
  assert c.execute('SELECT due_at FROM file_retention').fetchone()[0]==4000000000
  assert tuple(c.execute('SELECT due_at,version FROM file_folder_retention').fetchone())==(4000000000,3)
  assert c.execute('SELECT COUNT(*) FROM file_download_grants').fetchone()[0]==0

@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_idle_retention_is_bounded_and_download_receipts_expire(databases,engine):
 b=databases(engine);seed(b)
 with b.connection() as c:
  c.execute('INSERT INTO files(id,user_id,kind,name,created,updated) VALUES(?,?,?,?,?,?)',('f'*16,'a'*32,'folder','Empty',1,1))
  c.execute('INSERT INTO file_shares(token,file_id,mode,created) VALUES(?,?,?,?)',('test-link','f'*16,'public',1))
  c.execute('INSERT INTO file_share_limits VALUES(?,1,1,1)',('test-link',))
  c.execute('INSERT INTO file_download_grants VALUES(?,?,?,?)',('d'*64,'test-link','e'*64,5))
 with b.connection() as c:
  c.execute('BEGIN IMMEDIATE');purge(c,6)
 with b.connection() as c:assert c.execute('SELECT COUNT(*) FROM file_download_grants').fetchone()[0]==0

@pytest.mark.parametrize('engine',['sqlite','postgres'])
def test_idle_work_is_constant_with_five_hundred_accounts(databases,engine):
 b=databases(engine)
 with b.connection() as c:
  for i in range(500):
   c.execute('INSERT INTO users(id,login,password,role,settings,created) VALUES(?,?,?,?,?,?)',(f'{i:032x}',f'user{i}','not-a-real-password','member','{}',1))
   c.execute('INSERT INTO files(id,user_id,kind,name,created,updated) VALUES(?,?,?,?,?,?)',(f'{i:032x}',f'{i:032x}','folder','Waiting',1,1))
   c.execute('INSERT INTO file_folder_retention VALUES(?,?,NULL,1,?)',(f'{i:032x}',1,1))
 class Counted:
  def __init__(self,connection):self.connection=connection;self.queries=0
  def execute(self,*args,**kw):self.queries+=1;return self.connection.execute(*args,**kw)
 with b.connection() as c:
  counted=Counted(c);purge(counted,123)
  assert counted.queries<=3
