import builtins,io
import pytest
from app.file_store import FileStore,CHUNK
from app.data_crypto import DataCipher,StorageError
from tests.test_s3_media import S3

class RangedS3(S3):
 def __init__(self):super().__init__();self.ranges=[]
 def get_object(self,Bucket,Key,Range=None):
  result=super().get_object(Bucket=Bucket,Key=Key)
  if Range:
   self.ranges.append(Range);start,end=Range[6:].split('-');data=result['Body'].read();result['Body']=io.BytesIO(data[int(start):int(end)+1 if end else None])
  return result

@pytest.mark.parametrize('backend',['local','s3'])
def test_authenticated_range_reads_only_needed_frames_and_footer(tmp_path,backend,monkeypatch):
 client=RangedS3();store=FileStore(tmp_path,DataCipher.ephemeral(),backend=backend,client=client,bucket='test',bind=False)
 identity='a'*32;body=b'a'*(CHUNK-3)+b'123456789'+b'b'*(4*CHUNK+17);writer=store.writer(identity);writer.write(body);writer.finish()
 read_bytes=[0];original=builtins.open
 class Counted:
  def __init__(self,file):self.file=file
  def __getattr__(self,name):return getattr(self.file,name)
  def read(self,n=-1):result=self.file.read(n);read_bytes[0]+=len(result);return result
 def opened(path,*args,**kwargs):
  f=original(path,*args,**kwargs)
  return Counted(f) if str(path).endswith(identity) and args and args[0]=='rb' else f
 if backend=='local':monkeypatch.setattr(builtins,'open',opened)
 start=CHUNK-4;end=CHUNK+5
 assert b''.join(store.iter_range(identity,start,end,len(body)))==body[start:end+1]
 if backend=='local':assert read_bytes[0]<2*CHUNK+1024
 else:assert len(client.ranges)==3 and all(x.startswith('bytes=') for x in client.ranges)

@pytest.mark.parametrize('damaged',['footer','frame'])
def test_range_rejects_changed_authentication_before_yield(tmp_path,damaged):
 store=FileStore(tmp_path,DataCipher.ephemeral(),bind=False);identity='a'*32;writer=store.writer(identity);writer.write(b'hello world');writer.finish();path=store.root/identity
 data=bytearray(path.read_bytes());data[-1 if damaged=='footer' else 30]^=1;path.write_bytes(data)
 with pytest.raises(StorageError):b''.join(store.iter_range(identity,1,3,11))
