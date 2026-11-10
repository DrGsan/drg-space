"""Account vaults, deliberately outside ordinary documents and portable archives.

The existing encrypted settings table keeps the envelope, PIN verifier and grants.
There is no schema change. All document/index content has an additional random
vault key; immutable blobs use that key in a separate local/S3 namespace.
"""
from app.i18n import message as localized_message
import base64
import hashlib
import json
import re
import secrets
from pathlib import Path

from app.data_crypto import DataCipher, StorageError
from app.file_store import FileStore, object_id
from app.http import HTTPError

PREFIX = 'secret-vault:'


def backup_warning(notes, files):
    return (f'Секретное хранилище включено. В резервную копию не входят секретные заметки: {notes}; файлы: {files}. '
            'Чтобы включить нужные элементы в копию, сначала перенесите их в обычные разделы, затем создайте копию.')


def counts(value):
    return {'notes':len(value['notes']), 'files':sum(item['kind']=='file' for item in value['files'].values())} if value else {'notes':0,'files':0}


def exists(c, uid):
    return bool(c.execute('SELECT 1 FROM app_settings WHERE key=?', (PREFIX+uid,)).fetchone())


def prevent_factor_reset(c, uid, fail):
    if exists(c, uid):
        fail(409, localized_message('firstDeleteTheSecretStorageUntilItExists'))


def load(c, uid):
    row = c.execute('SELECT value FROM app_settings WHERE key=?', (PREFIX+uid,)).fetchone()
    if not row:
        return None
    value = json.loads(row[0])
    cipher = vault_cipher(value)
    value.update(json.loads(cipher.decode(value.pop('payload'), 'vault:'+uid)))
    return value


def vault_cipher(value):
    return DataCipher(base64.urlsafe_b64decode(value['key']))


def save(c, uid, value):
    if len(value['notes'])+len(value['files']) > 20000:
        raise HTTPError(413, localized_message('theRepositoryAllowsUpTo20000Items'))
    envelope = {k: v for k, v in value.items() if k not in ('notes', 'files')}
    content = json.dumps({'notes':value['notes'], 'files':value['files']}, ensure_ascii=False, separators=(',', ':'))
    if len(content.encode()) > 32*1024**2:
        raise HTTPError(413, localized_message('theContentOfNotesAndTheRepositoryCatalog'))
    envelope['payload'] = vault_cipher(value).encode(content, 'vault:'+uid)
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (PREFIX+uid, json.dumps(envelope, separators=(',', ':'))))
    if hasattr(c, 'quota_recount'):
        c.quota_recount.add(uid)


def usage(c, uid):
    value = load(c, uid)
    if not value:
        return 0
    return (len(json.dumps(value['notes'], ensure_ascii=False).encode())
            + sum(f.get('size', 0)+len(f['name'].encode())+128 for f in value['files'].values()))


def remote_ids(client,bucket,prefix):
    """Include invisible old versions and delete markers in orphan inventory."""
    found=set()
    try:
        for page in client.get_paginator('list_object_versions').paginate(Bucket=bucket,Prefix=prefix):
            found.update(item['Key'] for kind in ('Versions','DeleteMarkers') for item in page.get(kind,[]))
    except Exception as error:
        if getattr(error,'response',{}).get('Error',{}).get('Code') not in ('NotImplemented','NotImplementedException','501'):raise
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket,Prefix=prefix):
            found.update(item['Key'] for item in page.get('Contents',[]))
    return found


def delete_remote(client,bucket,key):
    try:
        versions=[]
        try:
            for page in client.get_paginator('list_object_versions').paginate(Bucket=bucket,Prefix=key):
                versions.extend({'Key':key,'VersionId':item['VersionId']} for kind in ('Versions','DeleteMarkers') for item in page.get(kind,[]) if item['Key']==key)
            for start in range(0,len(versions),1000):
                result=client.delete_objects(Bucket=bucket,Delete={'Objects':versions[start:start+1000]})
                if result.get('Errors'):raise StorageError('Не удалось удалить все версии файла.')
            return
        except Exception as error:
            if getattr(error,'response',{}).get('Error',{}).get('Code') not in ('NotImplemented','NotImplementedException','501'):raise
        if client.delete_object(Bucket=bucket,Key=key).get('DeleteMarker'):
            raise StorageError('Хранилище сохранило прежние версии файла.')
    except Exception:
        raise StorageError('Удаление файла не завершено. Очистка будет повторена.') from None


def collect_source(db,ordinary,media,identity,kind):
    is_file=kind=='vault-file'
    with db() as c:
        table,field=('files','object_id') if is_file else ('media','id')
        if c.execute('SELECT 1 FROM '+table+' WHERE '+field+'=?',(identity,)).fetchone():
            c.execute('DELETE FROM blob_gc WHERE id=? AND kind=?',(identity,kind))
            return
    if ordinary.backend=='s3':
        delete_remote(ordinary.client,ordinary.bucket,(ordinary if is_file else media).key(identity))
        if not is_file:(media.root/identity).unlink(missing_ok=True)
    else:(ordinary if is_file else media).delete(identity)
    with db() as c:c.execute('DELETE FROM blob_gc WHERE id=? AND kind=?',(identity,kind))


class VaultWriter:
    def __init__(self,writer):self.writer=writer
    def __getattr__(self,name):return getattr(self.writer,name)
    def write(self,body):
        try:return self.writer.write(body)
        except Exception:raise StorageError('Не удалось записать секретный файл.') from None
    def finish(self):
        try:return self.writer.finish()
        except Exception:raise StorageError('Не удалось завершить запись секретного файла.') from None
    def abort(self):return self.writer.abort()


class VaultObjects(FileStore):
    def __init__(self, root, uid, value, base):
        self.uid=uid
        self.vault_prefix = '/'.join(filter(None, (base.prefix, base.storage_id, 'secret', uid)))
        super().__init__(Path(root)/'secret'/uid, vault_cipher(value), backend=base.backend,
                         client=base.client, bucket=base.bucket, bind=False)

    def locations(self):
        # A vault has no tracker attachment namespace.
        yield self

    def key(self, identity):
        return self.vault_prefix+'/'+object_id(identity)

    def writer(self,identity):return VaultWriter(super().writer(identity))

    def iter_read(self,identity):
        try:yield from super().iter_read(identity)
        except Exception:raise StorageError('Не удалось прочитать секретный файл.') from None

    def ids(self):
        if self.backend!='s3':return super().ids()
        prefix=self.vault_prefix+'/'
        try:return {key[len(prefix):] for key in remote_ids(self.client,self.bucket,prefix) if re.fullmatch(r'[a-f0-9]{32}',key[len(prefix):])}
        except Exception:raise StorageError('Не удалось проверить объекты секретного хранилища.') from None

    def delete(self, identity):
        """Remove all S3 versions too; never report a delete marker as erasure."""
        if self.backend != 's3':
            return super().delete(identity)
        delete_remote(self.client,self.bucket,self.key(identity))


class VaultService:
    def __init__(self, db, root, now, ready, fail, ph, check_password, factor_cipher, protection, monitor):
        from app.file_store import file_store
        self.db, self.root, self.now, self.ready, self.fail = db, Path(root), now, ready, fail
        self.ph, self.check_password, self.factor_cipher = ph, check_password, factor_cipher
        self.protection, self.monitor = protection, monitor
        self.base = file_store(db, root)
        self.encrypted = getattr(getattr(db, '__self__', None), 'cipher', DataCipher(enabled=False)).enabled

    def objects(self, uid, value):
        return VaultObjects(self.root, uid, value, self.base)

    def discard(self,store,identity):
        """Compensate failed writes without removing an ambiguously committed file."""
        with self.db() as c:
            value=load(c,store.uid)
            if value and any(item.get('object_id')==identity for item in value['files'].values()):return
        try:store.delete(identity)
        except Exception:
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                value=load(c,store.uid)
                if value and not any(item.get('object_id')==identity for item in value['files'].values()):
                    value['garbage']=list(set(value['garbage'])|{identity})
                    save(c,store.uid,value)

    def audit(self, c, request, action, uid, outcome='success'):
        self.monitor.record(request, 'secret_vault', action=action, user_id=uid, outcome=outcome, connection=c)

    def current(self, c, request):
        s = self.ready(request)
        user = c.execute('SELECT * FROM users WHERE id=?', (s['user_id'],)).fetchone()
        if not user or not user['totp']:
            self.fail(403, localized_message('aSecretRepositoryRequires2FaToBeEnabled'))
        value = load(c, s['user_id'])
        if value and value['factor'] != hashlib.sha256(user['totp'].encode()).hexdigest():
            self.fail(423, localized_message('accountProtectionHasBeenChangedTheSecretRepository'))
        return s, user, value

    def authorized(self, c, request, section, allow_deleting=False, token=None, grant_id=None):
        s, user, value = self.current(c, request)
        if not value:
            self.fail(404, localized_message('theSecretRepositoryIsNotEnabled'))
        if value.get('deleting') and not allow_deleting:
            self.fail(423, localized_message('repositoryDeletionWasNotCompletedPleaseRepeatThe'))
        token = token if token is not None else request.headers.get('x-vault-token', '')
        grant = value['grants'].get(grant_id) if grant_id is not None else value['grants'].get(hashlib.sha256(token.encode()).hexdigest()) if 20 <= len(token) <= 100 else None
        if not grant or grant['session'] != s['token'] or grant['section'] != section or grant['expires'] <= self.now():
            self.fail(423, localized_message('theSecretSectionIsLockedEnterPin'))
        return s, value, grant

    def factor(self, c, user, code):
        from app.modules.account_security import verify_account_factor
        if not user['totp'] or not verify_account_factor(c, user, code, self.factor_cipher, self.now):
            self.fail(403, localized_message('incorrect2FaCodeOrRecoveryCode'))

    def cleanup(self, uid):
        """Durable tombstones revoke access before potentially failing IO."""
        from app.process_locks import sync_file_lock
        self.expire_grants(uid)
        with sync_file_lock(self.root, 'secret-cleanup-'+uid+'.lock'):
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                value = load(c, uid)
                if value:
                    from app.page_links import page_links,prune_vault_links
                    missing=set().union(*(page_links(note.get('blocks')) for note in value['notes'].values()))-value['notes'].keys()
                    changed=prune_vault_links(value,missing,self.now(),self.fail) if missing else False
                    if retire_unused_page_assets(value) or changed:save(c,uid,value)
            if not value:
                return
            store = self.objects(uid, value)
            if value.get('deleting'):
                store.abort_pending()
                objects=store.ids()
                with self.db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    latest=load(c,uid)
                    if not latest:return
                    latest['garbage']=list(set(latest.get('garbage',[]))|objects)
                    save(c,uid,latest)
                    value=latest
            for identity in list(value.get('garbage', [])):
                store.delete(identity)
                with self.db() as c:
                    c.execute('BEGIN IMMEDIATE')
                    latest = load(c, uid)
                    if not latest:
                        return
                    latest['garbage'] = [x for x in latest.get('garbage', []) if x != identity]
                    save(c, uid, latest)
            with self.db() as c:
                c.execute('BEGIN IMMEDIATE')
                latest = load(c, uid)
                if latest and latest.get('deleting') and not latest.get('garbage'):
                    c.execute('DELETE FROM app_settings WHERE key=?', (PREFIX+uid,))
                    if hasattr(c, 'quota_recount'):
                        c.quota_recount.add(uid)
            if latest and latest.get('deleting') and not latest.get('garbage'):
                self.remove_empty_directories(store)

    def remove_empty_directories(self,store):
        # Only empty app-owned directories; never remove lock inodes or a tree.
        for directory in (store.root,store.root.parent/'tracker-attachments',store.root.parent,store.root.parent.parent):
            try:directory.rmdir()
            except (FileNotFoundError,OSError):pass

    def expire_grants(self, uid):
        # Persist once per grant, even if the tab was closed or suspended.
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            value=load(c,uid)
            if not value:return
            expired=[identity for identity,grant in value['grants'].items() if grant['expires']<=self.now()]
            for identity in expired:
                value['grants'].pop(identity)
                self.audit(c,None,'vault-auto-locked',uid)
            tickets=value.get('downloads',{})
            value['downloads']={identity:ticket for identity,ticket in tickets.items() if ticket.get('grant') in value['grants'] and ticket['expires']>self.now()}
            if expired or tickets!=value['downloads']:
                save(c,uid,value)

    def reconcile(self):
        """Exclusive storage gate required: no upload can own an unlisted blob."""
        with self.db() as c:
            accounts=[row[0][len(PREFIX):] for row in c.execute('SELECT key FROM app_settings WHERE key LIKE ?',(PREFIX+'%',))]
        candidates=set(accounts)
        local=self.root/'secret'
        if local.exists():candidates.update(path.name for path in local.iterdir() if path.is_dir() and not path.is_symlink() and re.fullmatch(r'[a-f0-9]{32}',path.name))
        if self.base.backend=='s3':
            prefix='/'.join(filter(None,(self.base.prefix,self.base.storage_id,'secret'))) +'/'
            for key in remote_ids(self.base.client,self.base.bucket,prefix):
                uid=key[len(prefix):].split('/')[0]
                if re.fullmatch(r'[a-f0-9]{32}',uid):candidates.add(uid)
            for page in self.base.client.get_paginator('list_multipart_uploads').paginate(Bucket=self.base.bucket,Prefix=prefix):
                for upload in page.get('Uploads',[]):
                    uid=upload['Key'][len(prefix):].split('/')[0]
                    if re.fullmatch(r'[a-f0-9]{32}',uid):candidates.add(uid)
        for uid in candidates:
            with self.db() as c:value=load(c,uid)
            store=self.objects(uid,value or new_vault('',''))
            store.abort_pending()
            live={f['object_id'] for f in value['files'].values() if f.get('object_id')} if value else set()
            for identity in store.ids()-live:
                store.delete(identity)
            if not value:self.remove_empty_directories(store)


def branch(items, root):
    if root not in items:
        raise HTTPError(404, localized_message('itemNotFound'))
    selected = {root}
    while True:
        found = selected | {k for k,v in items.items() if v.get('parent_id') in selected}
        if found == selected:
            return selected
        selected = found


def remove_notes(value, selected, stamp, fail):
    """Use the same permanent cleanup for a page action and an owned shortcut."""
    from app.vault_databases import remove_references
    from app.page_links import prune_vault_links
    files={fid for fid,item in value['files'].items() if item.get('note_id') in selected}
    protect_attachment_references(value,files,selected,fail)
    remove_references(value,selected)
    prune_vault_links(value,selected,stamp,fail)
    for nid in selected:value['notes'].pop(nid)
    for fid in files:
        item=value['files'].pop(fid)
        if item.get('object_id'):value['garbage'].append(item['object_id'])


def retire_unused_page_assets(value):
    # A former cover/icon may still be embedded in another secret page. Keep
    # those bytes until the final document reference is explicitly removed.
    content=json.dumps(value['notes'])
    referenced=set(re.findall(r'/api/secret-vault/notes/content/([a-zA-Z0-9_-]+)',content))
    referenced.update(re.findall(r'image:([a-f0-9]{32})',content))
    unused={fid for fid,item in value['files'].items() if item.get('page_asset') and fid not in referenced}
    for fid in unused:
        value['garbage'].append(value['files'].pop(fid)['object_id'])
    return bool(unused)


def protect_attachment_references(value, files, removed_notes, fail):
    for nid,note in value['notes'].items():
        if nid in removed_notes:continue
        referenced=set(re.findall(r'/api/secret-vault/notes/content/([a-zA-Z0-9_-]+)',json.dumps(note)))
        if referenced & files:
            fail(409,localized_message('attachmentIsUsedByAnotherSecretNoteFirst'))


def new_vault(pin_hash, factor):
    return {'version':1, 'key':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(), 'pin':pin_hash,
            'factor':hashlib.sha256(factor.encode()).hexdigest(), 'notes':{}, 'files':{}, 'grants':{},
            'failures':0, 'blocked_until':0, 'garbage':[], 'deleting':False}
