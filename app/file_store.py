"""Immutable, authenticated streaming objects in a private volume or S3.

Names and paths never become storage keys. Chunk authentication includes the
object id and index; an authenticated terminator rejects truncated objects.
"""
import hashlib
import json
import os
import re
import secrets
import struct
import stat
from pathlib import Path
from app.data_crypto import DataCipher,StorageError

MAGIC=b'DRG-FILE-01\0'
CHUNK=1024*1024
NAMESPACES=('files','tracker-attachments')

def object_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{32}',value):raise StorageError('Invalid object identity.')
    return value

def local_exists(path):
    try:mode=path.stat(follow_symlinks=False).st_mode
    except FileNotFoundError:return False
    except OSError:raise StorageError('Could not inspect private storage.') from None
    if not stat.S_ISREG(mode):raise StorageError('Private object is not a regular file.')
    return True

def remote_exists(client,bucket,key):
    try:client.head_object(Bucket=bucket,Key=key);return True
    except Exception as error:
        response=getattr(error,'response',{})
        if response.get('ResponseMetadata',{}).get('HTTPStatusCode')==404 and response.get('Error',{}).get('Code') in ('404','NoSuchKey','NotFound'):return False
        # Permissions, outages and missing buckets are never proof of a lost file.
        raise StorageError('Could not confirm private object existence.') from None

class LocalWriter:
    def __init__(self,path):
        self.path=path;self.stream=os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb')
    def write(self,body):self.stream.write(body)
    def finish(self):self.stream.flush();os.fsync(self.stream.fileno());self.stream.close()
    def abort(self):
        if not self.stream.closed:self.stream.close()
        self.path.unlink(missing_ok=True)

class S3Writer:
    def __init__(self,client,bucket,key):
        self.client,self.bucket,self.key=client,bucket,key;self.buffer=bytearray();self.parts=[];self.upload=None;self.finished=False
    def part(self,body):
        if self.upload is None:self.upload=self.client.create_multipart_upload(Bucket=self.bucket,Key=self.key,ContentType='application/octet-stream')['UploadId']
        number=len(self.parts)+1
        result=self.client.upload_part(Bucket=self.bucket,Key=self.key,UploadId=self.upload,PartNumber=number,Body=body)
        self.parts.append({'ETag':result['ETag'],'PartNumber':number})
    def write(self,body):
        self.buffer.extend(body)
        while len(self.buffer)>=8*1024*1024:
            part=bytes(self.buffer[:8*1024*1024]);del self.buffer[:len(part)];self.part(part)
    def finish(self):
        if self.upload:
            if self.buffer:self.part(bytes(self.buffer))
            self.client.complete_multipart_upload(Bucket=self.bucket,Key=self.key,UploadId=self.upload,MultipartUpload={'Parts':self.parts})
        else:self.client.put_object(Bucket=self.bucket,Key=self.key,Body=bytes(self.buffer),ContentType='application/octet-stream')
        self.buffer.clear();self.finished=True
    def abort(self):
        if self.upload and not self.finished:self.client.abort_multipart_upload(Bucket=self.bucket,Key=self.key,UploadId=self.upload)
        if self.finished:self.client.delete_object(Bucket=self.bucket,Key=self.key)
        self.buffer.clear()

class ObjectWriter:
    def __init__(self,sink,cipher,identity):
        self.sink,self.cipher,self.identity=sink,cipher,identity;self.buffer=bytearray();self.index=0;self.size=0;self.hash=hashlib.sha256();self.finished=False
        sink.write(MAGIC)
    def frame(self,body):
        encrypted=self.cipher.seal(body,f'file:{self.identity}:{self.index}')
        self.sink.write(struct.pack('>I',len(encrypted)));self.sink.write(encrypted);self.index+=1
    def write(self,body):
        self.size+=len(body);self.hash.update(body);self.buffer.extend(body)
        while len(self.buffer)>=CHUNK:
            part=bytes(self.buffer[:CHUNK]);del self.buffer[:CHUNK];self.frame(part)
    def finish(self):
        if self.buffer:self.frame(bytes(self.buffer));self.buffer.clear()
        self.sink.write(struct.pack('>I',0))
        footer=self.cipher.seal(struct.pack('>QQ',self.index,self.size)+self.hash.digest(),f'file:{self.identity}:end')
        self.sink.write(struct.pack('>I',len(footer)));self.sink.write(footer);self.sink.finish();self.finished=True
        return self.size,self.hash.hexdigest()
    def abort(self):self.buffer.clear();self.sink.abort()

class FileStore:
    def __init__(self,root,cipher,backend=None,client=None,bucket=None,prefix=None,bind=True):
        self.base=Path(root);self.namespace='files'
        self.root=self.base/self.namespace;self.root.mkdir(mode=0o700,parents=True,exist_ok=True);self.cipher=cipher
        self.backend=backend or os.getenv('FILE_STORAGE','local')
        if self.backend not in ('local','s3'):raise StorageError('FILE_STORAGE must be local or s3.')
        self.client=client;self.bucket=bucket or os.getenv('S3_BUCKET','');self.prefix=prefix if prefix is not None else os.getenv('S3_PREFIX','drg-site').strip('/')
        marker=Path(root)/'.storage-format.json';self.storage_id=json.loads(marker.read_text())['storage_id'] if marker.exists() else 'test'
        if self.backend=='s3':
            if not self.bucket:raise StorageError('S3_BUCKET is required for S3 storage.')
            if self.client is None:
                import boto3
                from botocore.config import Config
                endpoint=os.getenv('S3_ENDPOINT_URL') or None
                self.client=boto3.client('s3',endpoint_url=endpoint,region_name=os.getenv('S3_REGION','us-east-1'),config=Config(connect_timeout=10,read_timeout=60,retries={'max_attempts':3},s3={'addressing_style':os.getenv('S3_ADDRESSING_STYLE','path')}))
        identity={'backend':self.backend,'bucket':self.bucket if self.backend=='s3' else '', 'prefix':self.prefix if self.backend=='s3' else '', 'endpoint':os.getenv('S3_ENDPOINT_URL','') if self.backend=='s3' else ''}
        if not bind:return
        binding=Path(root)/'.file-store.json';signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        from app.process_locks import sync_file_lock
        with sync_file_lock(root,'file-store-binding.lock'):
            if binding.exists():
                saved=json.loads(binding.read_text())
                if saved!={'version':1,'signature':signature,'storage_id':self.storage_id}:raise StorageError('File storage configuration differs. Use a fresh volume and a portable archive; automatic transfer is disabled.')
            else:
                if self.backend=='local' and any(path.exists() and any(path.iterdir()) for path in (self.base/name for name in NAMESPACES)):raise StorageError('Existing file objects have no storage binding. Restore the marker or use a fresh volume.')
                temporary=Path(root)/('.file-store.tmp-'+secrets.token_hex(8))
                fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                try:
                    with os.fdopen(fd,'w') as file:
                        json.dump({'version':1,'signature':signature,'storage_id':self.storage_id},file);file.flush();os.fsync(file.fileno())
                    os.link(temporary,binding)
                    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fsync(directory)
                    finally:os.close(directory)
                finally:temporary.unlink(missing_ok=True)
    def scoped(self,namespace):
        """Select a physical namespace without changing the storage binding/AAD."""
        if namespace not in NAMESPACES:raise StorageError('Invalid file namespace.')
        result=object.__new__(type(self));result.__dict__=self.__dict__.copy()
        result.namespace=namespace;result.root=self.base/namespace
        if self.backend=='local':result.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        return result
    def key(self,identity):return '/'.join(filter(None,(self.prefix,self.storage_id,self.namespace,object_id(identity))))
    def locations(self):
        yield self
        for namespace in NAMESPACES:
            if namespace!=self.namespace:yield self.scoped(namespace)
    def exists_here(self,identity):
        object_id(identity)
        return local_exists(self.root/identity) if self.backend=='local' else remote_exists(self.client,self.bucket,self.key(identity))
    def exists(self,identity):
        return any(location.exists_here(identity) for location in self.locations())
    def writer(self,identity):
        object_id(identity)
        try:sink=LocalWriter(self.root/identity) if self.backend=='local' else S3Writer(self.client,self.bucket,self.key(identity))
        except Exception:raise StorageError('Could not create private file object.') from None
        return ObjectWriter(sink,self.cipher,identity)
    def delete(self,identity):
        for location in self.locations():location.delete_here(identity)
    def delete_here(self,identity):
        if self.backend=='local':(self.root/object_id(identity)).unlink(missing_ok=True)
        else:
            try:self.client.delete_object(Bucket=self.bucket,Key=self.key(identity))
            except Exception:raise StorageError('Could not remove private file object.') from None
    def ids(self):
        return set().union(*(location.ids_here() for location in self.locations()))
    def ids_here(self):
        if self.backend=='local':return {p.name for p in self.root.iterdir() if p.is_file() and re.fullmatch(r'[a-f0-9]{32}',p.name)}
        found=set();prefix=self.key('0'*32)[:-32]
        try:
            for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket,Prefix=prefix):
                for item in page.get('Contents',[]):
                    name=item['Key'][len(prefix):]
                    if re.fullmatch(r'[a-f0-9]{32}',name):found.add(name)
        except Exception:raise StorageError('Could not list private file storage.') from None
        return found
    def abort_pending(self):
        """Exclusive storage gate required: no writer may still own these parts."""
        for location in self.locations():location.abort_pending_here()
    def abort_pending_here(self):
        if self.backend!='s3':return
        prefix=self.key('0'*32)[:-32]
        try:
            for page in self.client.get_paginator('list_multipart_uploads').paginate(Bucket=self.bucket,Prefix=prefix):
                for upload in page.get('Uploads',[]):
                    identity=upload['Key'][len(prefix):]
                    if re.fullmatch(r'[a-f0-9]{32}',identity):
                        self.client.abort_multipart_upload(Bucket=self.bucket,Key=upload['Key'],UploadId=upload['UploadId'])
        except Exception:raise StorageError('Could not clean unfinished private uploads.') from None

    def relocate_from(self,source,identity,*,keep_source=False):
        """Verified, restartable relocation under the exclusive storage gate.

        Identities, authenticated plaintext and database references stay intact.
        Shared source objects can be retained for their ordinary-file owners.
        """
        source=self.scoped(source)
        if source.namespace==self.namespace:raise StorageError('Relocation needs different namespaces.')
        def digest(store):
            size=0;checksum=hashlib.sha256()
            for chunk in store.iter_read(identity,fallback=False):size+=len(chunk);checksum.update(chunk)
            return size,checksum.hexdigest()
        if not source.exists_here(identity):
            return digest(self)
        expected=digest(source)
        if not self.exists_here(identity):
            writer=self.writer(identity)
            try:
                for chunk in source.iter_read(identity,fallback=False):writer.write(chunk)
                if writer.finish()!=expected:raise StorageError('Relocated content differs from its source.')
                if digest(self)!=expected:raise StorageError('Relocated object failed verification.')
            except BaseException:writer.abort();raise
        elif digest(self)!=expected:
            raise StorageError('Relocated content conflicts with an existing object.')
        if not keep_source:source.delete_here(identity)
        return expected

    def iter_read(self,identity,*,fallback=True):
        object_id(identity)
        stream=None
        for location in self.locations() if fallback else (self,):
            try:
                stream=open(location.root/identity,'rb') if self.backend=='local' else self.client.get_object(Bucket=self.bucket,Key=location.key(identity))['Body']
                break
            except FileNotFoundError:
                if self.backend!='local':raise StorageError('Could not open private file object.') from None
            except Exception as error:
                response=getattr(error,'response',{})
                if self.backend=='s3' and response.get('ResponseMetadata',{}).get('HTTPStatusCode')==404 and response.get('Error',{}).get('Code') in ('404','NoSuchKey','NotFound'):continue
                raise StorageError('Could not open private file object.') from None
        if stream is None:raise StorageError('Could not open private file object.')
        def read_exact(size):
            output=bytearray()
            while len(output)<size:
                chunk=stream.read(size-len(output))
                if not chunk:raise StorageError('File object is truncated.')
                output.extend(chunk)
            return bytes(output)
        try:
            if read_exact(len(MAGIC))!=MAGIC:raise StorageError('Invalid file object format.')
            index=0;size=0;checksum=hashlib.sha256()
            while True:
                length=struct.unpack('>I',read_exact(4))[0]
                if length==0:
                    footer_size=struct.unpack('>I',read_exact(4))[0]
                    if footer_size>256:raise StorageError('Invalid file terminator.')
                    footer=self.cipher.open(read_exact(footer_size),f'file:{identity}:end')
                    if footer!=struct.pack('>QQ',index,size)+checksum.digest() or stream.read(1):raise StorageError('File integrity check failed.')
                    break
                if length>CHUNK+128:raise StorageError('Invalid encrypted file chunk.')
                body=self.cipher.open(read_exact(length),f'file:{identity}:{index}');index+=1;size+=len(body);checksum.update(body);yield body
        finally:stream.close()
    def iter_range(self,identity,start,end,size):
        """Read authenticated frames for a range without decrypting its prefix.

        The writer uses fixed full-frame sizes. Authenticate the terminator
        against the declared length and each selected frame's identity/index.
        Full reads still use iter_read and its whole-object checksum.
        """
        object_id(identity)
        if not 0<=start<=end<size:raise StorageError('Invalid file range.')
        if not self.cipher.enabled:
            offset=0
            for body in self.iter_read(identity):
                stop=offset+len(body)
                if stop>start and offset<=end:yield body[max(0,start-offset):min(len(body),end-offset+1)]
                offset=stop
            return
        overhead=len(self.cipher.MAGIC)+28
        full,last=divmod(size,CHUNK);count=full+bool(last)
        footer_offset=len(MAGIC)+full*(4+CHUNK+overhead)+(4+last+overhead if last else 0)
        first_index,last_index=start//CHUNK,end//CHUNK
        begin=len(MAGIC)+first_index*(4+CHUNK+overhead)
        finish=len(MAGIC)+last_index*(4+CHUNK+overhead)+4+min(CHUNK,size-last_index*CHUNK)+overhead
        stream=None;location=None
        for candidate in self.locations():
            try:
                stream=open(candidate.root/identity,'rb') if self.backend=='local' else self.client.get_object(Bucket=self.bucket,Key=candidate.key(identity),Range=f'bytes=0-{len(MAGIC)-1}')['Body']
                location=candidate;break
            except FileNotFoundError:continue
            except Exception as error:
                response=getattr(error,'response',{})
                if response.get('ResponseMetadata',{}).get('HTTPStatusCode')==404 and response.get('Error',{}).get('Code') in ('404','NoSuchKey','NotFound'):continue
                raise StorageError('Could not open private file object.') from None
        if stream is None:raise StorageError('Could not open private file object.')
        def exact(length):
            out=bytearray()
            while len(out)<length:
                part=stream.read(length-len(out))
                if not part:raise StorageError('File object is truncated.')
                out.extend(part)
            return bytes(out)
        try:
            if exact(len(MAGIC))!=MAGIC:raise StorageError('Invalid file object format.')
            if self.backend=='local':stream.seek(footer_offset)
            else:
                stream.close();stream=self.client.get_object(Bucket=self.bucket,Key=location.key(identity),Range=f'bytes={footer_offset}-')['Body']
            if exact(4)!=b'\x00'*4 or struct.unpack('>I',exact(4))[0]!=48+overhead:raise StorageError('Invalid file terminator.')
            footer=self.cipher.open(exact(48+overhead),f'file:{identity}:end')
            if footer[:16]!=struct.pack('>QQ',count,size) or stream.read(1):raise StorageError('File integrity check failed.')
            if self.backend=='local':stream.seek(begin)
            else:
                stream.close();stream=self.client.get_object(Bucket=self.bucket,Key=location.key(identity),Range=f'bytes={begin}-{finish-1}')['Body']
            for index in range(first_index,last_index+1):
                length=struct.unpack('>I',exact(4))[0]
                if length!=min(CHUNK,size-index*CHUNK)+overhead:raise StorageError('Invalid encrypted file frame.')
                body=self.cipher.open(exact(length),f'file:{identity}:{index}')
                yield body[max(0,start-index*CHUNK):min(len(body),end-index*CHUNK+1)]
        finally:stream.close()
    def read(self,identity,maximum=2*1024*1024):
        body=bytearray()
        for chunk in self.iter_read(identity):
            if len(body)+len(chunk)>maximum:raise StorageError('File is too large for text preview.')
            body.extend(chunk)
        return bytes(body)

def file_store(db,root,*,namespace='files'):
    owner=getattr(db,'__self__',None)
    store=FileStore(root,getattr(owner,'cipher',DataCipher(enabled=False)))
    return store if namespace=='files' else store.scoped(namespace)
