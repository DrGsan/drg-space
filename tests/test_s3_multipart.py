"""Large S3 uploads, authenticated readback and rollback without a live bucket."""
import hashlib
import io
import secrets

import pytest
from botocore.exceptions import ClientError

from app.data_crypto import DataCipher, StorageError
from app.file_store import FileStore
from app.quota import usage
from tests.test_security import m, owner, reset
from tests.test_widgets import enable


MIB = 1024 * 1024


class MultipartS3:
    """Keep unfinished parts separate from the objects visible to readers."""
    def __init__(self, fail=None):
        self.fail = fail
        self.objects = {}
        self.uploads = {}
        self.events = []
        self.completed_parts = []

    def failure(self, operation):
        raise ClientError({'Error': {'Code': 'InternalError', 'Message': 'Temporary S3 failure'},
                           'ResponseMetadata': {'HTTPStatusCode': 500}}, operation)

    def put_object(self, *, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = bytes(Body)
        self.events.append(('put', Key))
        return {}

    def create_multipart_upload(self, *, Bucket, Key, ContentType):
        upload = secrets.token_hex(8)
        self.uploads[upload] = {'target': (Bucket, Key), 'parts': {}}
        self.events.append(('create', upload))
        return {'UploadId': upload}

    def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body):
        active = self.uploads[UploadId]
        assert active['target'] == (Bucket, Key)
        self.events.append(('part', PartNumber))
        if self.fail == 'upload_part' and PartNumber == 2:
            self.failure('UploadPart')
        body = bytes(Body)
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        active['parts'][PartNumber] = (etag, body)
        return {'ETag': etag}

    def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload):
        active = self.uploads[UploadId]
        assert active['target'] == (Bucket, Key)
        self.events.append(('complete', UploadId))
        if self.fail == 'complete':
            self.failure('CompleteMultipartUpload')
        parts = MultipartUpload['Parts']
        numbers = [part['PartNumber'] for part in parts]
        assert numbers == sorted(active['parts'])
        assert numbers == list(range(1, len(parts) + 1))
        bodies = []
        for part in parts:
            etag, body = active['parts'][part['PartNumber']]
            assert part['ETag'] == etag
            bodies.append(body)
        # S3 requires every part except the final part to contain at least 5 MiB.
        assert all(len(body) >= 5 * MIB for body in bodies[:-1])
        self.completed_parts = [(number, len(body)) for number, body in zip(numbers, bodies)]
        self.objects[(Bucket, Key)] = b''.join(bodies)
        del self.uploads[UploadId]
        return {}

    def abort_multipart_upload(self, *, Bucket, Key, UploadId):
        assert self.uploads[UploadId]['target'] == (Bucket, Key)
        self.events.append(('abort', UploadId))
        del self.uploads[UploadId]
        return {}

    def get_object(self, *, Bucket, Key):
        return {'Body': io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, *, Bucket, Key):
        self.events.append(('delete', Key))
        self.objects.pop((Bucket, Key), None)
        return {}


@pytest.fixture(scope='module')
def large_body():
    pattern = b'PRIVATE_MULTIPART_CONTENT/' + bytes(range(256))
    size = 17 * MIB + 137
    return (pattern * (size // len(pattern) + 1))[:size]


def redirect_file_store(monkeypatch, client):
    # The router captures this store. Change only its in-memory test backend,
    # preserving the local storage-binding marker and the configured cipher.
    for name, value in [('backend', 's3'), ('client', client),
                        ('bucket', 'isolated-test-bucket'), ('prefix', 'multipart-ci')]:
        monkeypatch.setattr(m.files_router.store, name, value)


def account_state(uid):
    with m.db() as db:
        return (db.execute('SELECT COUNT(*) FROM files WHERE user_id=?', (uid,)).fetchone()[0],
                db.execute('SELECT used_bytes FROM user_limits WHERE user_id=?', (uid,)).fetchone()[0],
                usage(db, uid))


@pytest.mark.parametrize('encrypted', [True, False])
def test_multipart_ordering_and_authenticated_roundtrip(tmp_path, large_body, encrypted):
    client = MultipartS3()
    cipher = DataCipher.ephemeral() if encrypted else DataCipher(enabled=False)
    store = FileStore(tmp_path, cipher, backend='s3', client=client,
                      bucket='isolated-test-bucket', prefix='multipart-ci')
    identity = secrets.token_hex(16)
    writer = store.writer(identity)
    # Uneven input chunks also split file frames and S3 part boundaries.
    for offset in range(0, len(large_body), 600137):
        writer.write(large_body[offset:offset + 600137])
    assert writer.finish() == (len(large_body), hashlib.sha256(large_body).hexdigest())
    assert [number for number, size in client.completed_parts] == [1, 2, 3]
    assert not client.uploads
    assert [event[0] for event in client.events] == ['create', 'part', 'part', 'part', 'complete']
    assert b''.join(store.iter_read(identity)) == large_body
    stored = client.objects[(store.bucket, store.key(identity))]
    if encrypted:
        assert b'PRIVATE_MULTIPART_CONTENT/' not in stored
    # Storage corruption must be detected even when the multipart upload itself
    # completed successfully. Do not accept a shortened encrypted terminator.
    client.objects[(store.bucket, store.key(identity))] = stored[:-1]
    with pytest.raises(StorageError):
        b''.join(store.iter_read(identity))


def test_large_api_upload_commits_readable_object_and_quota(monkeypatch, large_body):
    c, _, _ = owner()
    enable(c, 'files')
    uid = c.get('/api/me').json()['id']
    before = account_state(uid)
    client = MultipartS3()
    redirect_file_store(monkeypatch, client)
    response = c.post('/api/files/upload', params={'name': 'multipart.bin'}, content=large_body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['size'] == len(large_body)
    assert c.get('/api/files/' + saved['id'] + '/content').content == large_body
    assert not client.uploads and len(client.objects) == 1
    after = account_state(uid)
    assert after[0] == before[0] + 1
    assert after[1] == after[2] and after[1] >= before[1] + len(large_body)


@pytest.mark.parametrize('failure', ['upload_part', 'complete'])
def test_failed_multipart_api_upload_aborts_without_db_or_quota_changes(monkeypatch, large_body, failure):
    c, _, _ = owner()
    enable(c, 'files')
    uid = c.get('/api/me').json()['id']
    before = account_state(uid)
    client = MultipartS3(fail=failure)
    redirect_file_store(monkeypatch, client)
    response = c.post('/api/files/upload', params={'name': 'interrupted.bin'}, content=large_body)
    assert response.status_code == 500, response.text
    assert account_state(uid) == before
    assert not client.objects and not client.uploads
    assert [event[0] for event in client.events].count('abort') == 1
    assert client.events[-1][0] == 'abort'
    if failure == 'upload_part':
        assert [event for event in client.events if event[0] == 'part'] == [('part', 1), ('part', 2)]
        assert not any(event[0] == 'complete' for event in client.events)
    else:
        assert client.events[-2][0] == 'complete'


def test_quota_rejection_deletes_finalized_multipart_object(monkeypatch, large_body):
    c, _, _ = owner()
    enable(c, 'files')
    uid = c.get('/api/me').json()['id']
    before = account_state(uid)
    with m.db() as db:
        db.execute('UPDATE user_limits SET limit_bytes=? WHERE user_id=?', (before[1], uid))
    client = MultipartS3()
    redirect_file_store(monkeypatch, client)
    response = c.post('/api/files/upload', params={'name': 'quota-rejected.bin'}, content=large_body)
    assert response.status_code == 413, response.text
    assert account_state(uid) == before
    assert not client.objects and not client.uploads
    assert [event[0] for event in client.events][-2:] == ['complete', 'delete']
    assert not any(event[0] == 'abort' for event in client.events)
