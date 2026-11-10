"""Password-protected streaming ZIPs; decrypted staging stays encrypted on disk."""
from app.i18n import message as localized_message
import hashlib
import io
import os
import struct
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from app.data_crypto import DataCipher

CHUNK=1024*1024
# Operational limit, independent of a user's quota. ZIP metadata is bounded separately.
MAX_ARCHIVE=int(os.getenv('ARCHIVE_MAX_GB','50'))*1024**3
if not 1<=MAX_ARCHIVE//1024**3<=1024:raise ValueError('ARCHIVE_MAX_GB must be 1..1024.')

def maximum_upload(limit=MAX_ARCHIVE):return limit+(limit//CHUNK+2)*64+128

def error(message='Неверный пароль архива или файл повреждён.'):
    from app.backups import BackupError
    raise BackupError(message)

def derived(password,salt):
    from app.backups import key
    return key(password,salt)

class ArchiveWriter:
    def __init__(self,path,password,legacy_magic,limit=MAX_ARCHIVE):
        self.stream=os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb')
        magic=legacy_magic.replace(b'-01\0',b'-02\0');salt=os.urandom(16)
        self.header=magic+salt+os.urandom(12);self.stream.write(self.header)
        self.aes=AESGCM(derived(password,salt));self.buffer=bytearray();self.index=0;self.size=0;self.hash=hashlib.sha256();self.limit=limit
    def tell(self):return self.size
    def seek(self,*args):raise OSError('Streaming archive is not seekable.')
    def flush(self):self.stream.flush()
    def frame(self,body):
        nonce=self.header[-12:-4]+struct.pack('>I',self.index)
        raw=self.aes.encrypt(nonce,body,self.header+struct.pack('>I',self.index))
        self.stream.write(struct.pack('>I',len(raw)));self.stream.write(raw);self.index+=1
    def write(self,body):
        if self.size+len(body)>self.limit:error('Архив превышает серверный лимит ARCHIVE_MAX_GB.')
        self.size+=len(body);self.hash.update(body);self.buffer.extend(body)
        while len(self.buffer)>=CHUNK:
            self.frame(bytes(self.buffer[:CHUNK]));del self.buffer[:CHUNK]
        return len(body)
    def finish(self):
        if self.buffer:self.frame(bytes(self.buffer));self.buffer.clear()
        self.stream.write(struct.pack('>I',0));self.frame(struct.pack('>QQ',self.index,self.size)+self.hash.digest())
        self.stream.flush();os.fsync(self.stream.fileno());self.stream.close()
    def abort(self):self.stream.close()

class EncryptedSpool:
    """Append once, then read/seek through authenticated 1 MiB pages."""
    def __init__(self,directory):
        self.root=Path(directory)/('zip-'+os.urandom(8).hex());self.root.mkdir(mode=0o700)
        self.cipher=DataCipher.ephemeral();self.buffer=bytearray();self.pages=0;self.size=0;self.position=0;self.cache=None
    def page(self,body):
        path=self.root/str(self.pages)
        with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as f:f.write(self.cipher.seal(body,f'zip:{self.pages}'))
        self.pages+=1
    def write(self,body):
        self.size+=len(body);self.buffer.extend(body)
        while len(self.buffer)>=CHUNK:self.page(bytes(self.buffer[:CHUNK]));del self.buffer[:CHUNK]
    def finish(self):
        if self.buffer:self.page(bytes(self.buffer));self.buffer.clear()
    def seek(self,offset,whence=0):
        target=offset if whence==0 else self.position+offset if whence==1 else self.size+offset if whence==2 else -1
        if target<0:raise OSError('Invalid archive offset.')
        self.position=target;return target
    def tell(self):return self.position
    def seekable(self):return True
    def read(self,size=-1):
        size=max(0,self.size-self.position) if size<0 else min(size,max(0,self.size-self.position))
        result=bytearray()
        while size:
            index=self.position//CHUNK;offset=self.position%CHUNK
            if not self.cache or self.cache[0]!=index:self.cache=(index,self.cipher.open((self.root/str(index)).read_bytes(),f'zip:{index}'))
            part=self.cache[1][offset:offset+size];result.extend(part);self.position+=len(part);size-=len(part)
        return bytes(result)
    def close(self):self.cache=None


def open_archive(source,password,magic,directory,limit=MAX_ARCHIVE,legacy_limit=512*1024**2):
    stream=io.BytesIO(source) if isinstance(source,bytes) else open(source,'rb')
    spool=EncryptedSpool(directory)
    def exact(n):
        data=stream.read(n)
        if len(data)!=n:error()
        return data
    try:
        actual=exact(len(magic));header=actual+exact(28)
        aes=AESGCM(derived(password,header[len(magic):len(magic)+16]))
        if actual==magic:
            encrypted=stream.read(legacy_limit+17)
            if len(encrypted)>legacy_limit+16:error('Старый архив превышает допустимый размер.')
            raw=aes.decrypt(header[-12:],encrypted,header)
            if len(raw)>limit:error('Архив превышает серверный лимит.')
            for offset in range(0,len(raw),CHUNK):spool.write(raw[offset:offset+CHUNK])
        elif actual==magic.replace(b'-01\0',b'-02\0'):
            index=0;size=0;checksum=hashlib.sha256()
            while True:
                length=struct.unpack('>I',exact(4))[0];footer=length==0
                if footer:length=struct.unpack('>I',exact(4))[0]
                if length<16 or length>CHUNK+16 or (footer and length!=64):error()
                nonce=header[-12:-4]+struct.pack('>I',index)
                body=aes.decrypt(nonce,exact(length),header+struct.pack('>I',index))
                if footer:
                    if body!=struct.pack('>QQ',index,size)+checksum.digest() or stream.read(1):error()
                    break
                if size+len(body)>limit:error('Архив превышает серверный лимит.')
                # Fixed-size spool pages needn't match archive encryption frames.
                spool.write(body);size+=len(body);checksum.update(body);index+=1
        else:error('Нужен архив DrG Space соответствующего типа.')
        spool.finish();return spool
    except (InvalidTag,ValueError,struct.error):error()
    finally:stream.close()

async def receive_archive(request,directory,limit,fail):
    from app.storage import blocking
    maximum=maximum_upload(limit)
    if request.headers.get('content-length','').isdigit() and int(request.headers['content-length'])>maximum:fail(413,localized_message('archiveIsTooLarge'))
    path=Path(directory)/'upload.encrypted';size=0
    with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as file:
        async for chunk in request.stream():
            size+=len(chunk)
            if size>maximum:fail(413,localized_message('archiveIsTooLarge'))
            await blocking(file.write,chunk)
    return path
