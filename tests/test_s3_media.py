import io
import pytest
from tests.test_security import reset
from app.data_crypto import DataCipher,MediaStore,StorageError
from app.file_store import FileStore
from tests.test_storage_encryption import databases,seed,backup_service


class S3:
    def __init__(self):self.objects={};self.uploads=[];self.bad_read=False
    def put_object(self,**kw):
        if kw.get('IfNoneMatch')=='*' and kw['Key'] in self.objects:raise ValueError('exists')
        self.objects[kw['Key']]=bytes(kw['Body']);return {}
    def get_object(self,**kw):return {'Body':io.BytesIO(b'bad' if self.bad_read else self.objects[kw['Key']])}
    def delete_object(self,**kw):self.objects.pop(kw['Key'],None);return {}
    def abort_multipart_upload(self,**kw):self.uploads=[u for u in self.uploads if u['UploadId']!=kw['UploadId']]
    def get_paginator(self,name):
        client=self
        class Pages:
            def paginate(self,**kw):
                if name=='list_multipart_uploads':yield {'Uploads':[u for u in client.uploads if u['Key'].startswith(kw['Prefix'])]}
                else:yield {'Contents':[{'Key':key,'Size':len(client.objects[key])} for key in client.objects if key.startswith(kw['Prefix'])]}
        return Pages()


def test_s3_media_transfer_verifies_readback_retries_and_deletes(tmp_path):
    cipher=DataCipher.ephemeral();local=MediaStore(tmp_path,cipher);identity='a'*32;body=b'private media'
    local.write(identity,body);client=S3();objects=FileStore(tmp_path,cipher,backend='s3',client=client,bucket='private',bind=False)
    store=MediaStore(tmp_path,cipher,objects);client.bad_read=True
    with pytest.raises(StorageError):store.transfer_local()
    assert (store.root/identity).exists()
    client.bad_read=False;store.transfer_local();assert not list(store.root.iterdir())
    assert store.read(identity)==body and store.ids()=={identity}
    assert body not in client.objects[store.key(identity)]
    store.transfer_local();store.delete(identity);assert store.ids()==set() and not client.objects
    client.uploads=[{'Key':objects.key('b'*32),'UploadId':'own'},{'Key':'another-space/objects/test','UploadId':'foreign'}]
    objects.abort_pending();assert [u['UploadId'] for u in client.uploads]==['foreign']


def test_full_archive_s3_media_preserves_old_format_and_failed_restore_cleans(databases,monkeypatch):
    import boto3
    client=S3();monkeypatch.setattr(boto3,'client',lambda *a,**kw:client)
    monkeypatch.setenv('FILE_STORAGE','s3');monkeypatch.setenv('S3_BUCKET','private')
    source=databases();_,body=seed(source);service=backup_service(source)
    with source.connection() as c:identity=c.execute('SELECT id FROM media').fetchone()[0]
    assert service.media.read(identity)==body and not list(service.media.root.iterdir())
    password='S3-portable-password-123'
    with service.temp() as directory:
        metadata=service.snapshot(directory);archive=service.encrypt(directory,metadata,password).read_bytes()
    monkeypatch.setenv('FILE_STORAGE','local');target=databases();restorer=backup_service(target)
    with restorer.temp() as directory:
        metadata=restorer.prepare(archive,password,directory);restorer.restore(directory,metadata)
    assert restorer.media.read(identity)==body

    monkeypatch.setenv('FILE_STORAGE','s3')
    failed=databases();failed_service=backup_service(failed)
    with failed_service.temp() as directory:
        metadata=failed_service.prepare(archive,password,directory)
        def rejected(*args,**kwargs):raise ValueError('injected failure after asset upload')
        monkeypatch.setattr(failed_service,'snapshot_database',rejected)
        with pytest.raises(ValueError):failed_service.restore(directory,metadata)
        assert failed_service.media.ids()==set()
