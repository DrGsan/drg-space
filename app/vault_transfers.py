"""Verified copies before transactional moves across the vault boundary."""
from app.i18n import message as localized_message
import hashlib
import json
import re
import secrets
from contextlib import suppress
from typing import Literal

from app.http import Request
from app.secret_vault import branch, save, load, delete_remote, protect_attachment_references
from app.file_scopes import file_module_rows
from app.chat_storage import attachment_room
from app.data_crypto import media_store,StorageError
from app.personal_data import NOTE_FILE_LINK, NOTE_ASSET_LINK, ASSET_LINK


def register(r, service, Transfer):
    # Resolve the Pydantic model from this module's globals for Router hints.
    globals()['Transfer'] = Transfer
    db, fail, now = service.db, service.fail, service.now
    ordinary = service.base
    media = media_store(db, service.root)

    def discard(staged,staged_media=()):
        for target,identity in staged:
            try:
                if target is ordinary:
                    from app.secret_vault import collect_source
                    with db() as c:c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(identity,'vault-file',now()))
                    collect_source(db,ordinary,media,identity,'vault-file')
                else:service.discard(target,identity)
            except Exception:
                # The failed request never commits a visible document. Retain
                # the cleanup work if its backend is unavailable as well.
                with suppress(Exception):
                    with db() as c:
                        c.execute('BEGIN IMMEDIATE')
                        if target is ordinary:c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(identity,'vault-file',now()))
                        else:
                            value=load(c,target.uid)
                            if value and identity not in {f.get('object_id') for f in value['files'].values()}:
                                value['garbage'].append(identity);save(c,target.uid,value)
        for identity in staged_media:
            try:
                if ordinary.backend=='s3':delete_remote(ordinary.client,ordinary.bucket,media.key(identity))
                else:media.delete(identity)
            except Exception:
                with suppress(Exception):
                    with db() as c:c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(identity,'vault-media',now()))

    def copied(source, identity, target, staged):
        new_id = secrets.token_hex(16)
        writer = target.writer(new_id)
        staged.append((target, new_id))
        original = hashlib.sha256()
        try:
            for chunk in source.iter_read(identity):
                original.update(chunk)
                writer.write(chunk)
            size, digest = writer.finish()
            check = hashlib.sha256()
            for chunk in target.iter_read(new_id):
                check.update(chunk)
            if original.hexdigest()!=digest or check.hexdigest()!=digest:
                fail(503, localized_message('fileTransferVerificationFailedOriginalDataIsPreserved'))
        except BaseException:
            if not writer.finished:
                with suppress(Exception):writer.abort()
            raise
        return new_id, size

    def binary(body, target, staged):
        identity = secrets.token_hex(16)
        staged.append((target, identity))
        writer = target.writer(identity)
        try:
            writer.write(body)
            writer.finish()
            if target.read(identity, len(body)+1)!=body:
                fail(503, localized_message('imageTransferVerificationFailed'))
        except BaseException:
            with suppress(Exception):
                writer.abort()
            raise
        return identity

    def insert(c, table, row):
        c.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')', tuple(row.values()))

    def shares(c, table, column, ids):
        for identity in ids:
            if c.execute('SELECT 1 FROM '+table+' WHERE '+column+'=? AND revoked=0 AND (expires IS NULL OR expires>?)', (identity, now())).fetchone():
                fail(409, localized_message('firstRevokeAllExistingSharedAccessLinksFor'))

    @r.post('/api/secret-vault/{section}/move-in')
    def move_in(section: Literal['notes','files'], data: Transfer, request: Request):
        staged = []
        retired_media = []
        retired_files = set()
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                s, value, grant = service.authorized(c, request, section)
                uid = s['user_id']
                target = service.objects(uid, value)
                if section=='files':
                    all_items = {x['id']:dict(x) for x in file_module_rows(c, c.execute('SELECT * FROM files WHERE user_id=? AND deleted IS NULL', (uid,)), uid) if not attachment_room(x['id'])}
                    selected = branch(all_items, data.id)
                    if any(row['parent_id'] in selected for row in c.execute('SELECT parent_id FROM files WHERE user_id=? AND deleted IS NOT NULL',(uid,))):
                        fail(409,localized_message('thereAreElementsInTheTrashWithinThis'))
                    if all_items[data.id]['version']!=data.version:
                        fail(409, localized_message('objectHasBeenModifiedRefreshTheSection'))
                    shares(c, 'file_shares', 'file_id', selected)
                    for fid in selected:
                        item = all_items[fid]
                        if item['object_id']:
                            retired_files.add(item['object_id'])
                            item['object_id'], _ = copied(ordinary, item['object_id'], target, staged)
                        for field in ('user_id','deleted','trash_batch'):
                            item.pop(field, None)
                        if fid==data.id:
                            item['parent_id'] = None
                        value['files'][fid] = item
                    from app.file_trash import purge_file_rows
                    purge_file_rows(c, selected, now())
                else:
                    pages = {row['id']:dict(row) for row in c.execute('SELECT * FROM notes WHERE user_id=? AND deleted IS NULL', (uid,))}
                    selected = branch(pages, data.id)
                    if pages[data.id]['version']!=data.version:
                        fail(409, localized_message('noteHasBeenModifiedRefreshThePage'))
                    if any(pages[nid]['space_id'] for nid in selected):
                        fail(409, localized_message('privateStorageCanOnlyBeUsedForPersonal'))
                    shares(c, 'shares', 'note_id', selected)
                    for row in c.execute('SELECT * FROM note_databases'):
                        if row['note_id'] in pages:
                            pages[row['note_id']]['database']={'properties':json.loads(row['properties']), 'views':json.loads(row['views']), 'version':row['version']}
                        elif any(p['type']=='relation' and p['database_id'] in selected for p in json.loads(row['properties'])):
                            fail(409,localized_message('anotherRegularDatabaseReferencesThisDatabaseFirstRemove'))
                    for row in c.execute('SELECT * FROM database_records'):
                        if row['note_id'] in pages:
                            pages[row['note_id']]['record']={'database_id':row['database_id'], 'properties':json.loads(row['properties']), 'version':row['version']}
                    from app.vault_databases import boundary
                    boundary(pages,selected,fail)
                    for row in c.execute('SELECT id,parent_id FROM notes WHERE deleted IS NOT NULL'):
                        if row['parent_id'] in selected:
                            fail(409, localized_message('firstRestoreOrPermanentlyDeleteChildPagesFrom'))
                    assets = {}
                    file_ids = set()
                    media_ids = set()
                    for nid in selected:
                        note = pages[nid]
                        text = (note['blocks'] or '')+note['body']
                        file_ids.update(m[2] for m in NOTE_FILE_LINK.finditer(text))
                        media_ids.update(m[1] for m in ASSET_LINK.finditer(text))
                        media_ids.update(m[2] for m in NOTE_ASSET_LINK.finditer(text))
                        for row in c.execute('SELECT * FROM note_assets WHERE note_id=?', (nid,)):
                            assets[row['id']] = dict(row)
                            media_ids.add(row['id'])
                        file_ids.update(row[0] for row in c.execute("SELECT id FROM files WHERE parent_id=? AND kind='file'", (nid,)))
                    # Shared attachment references must be resolved explicitly;
                    # otherwise a move would either leak or break another page.
                    for other in c.execute('SELECT id,body,blocks FROM notes'):
                        if other['id'] in selected:
                            continue
                        text = (other['blocks'] or '')+other['body']
                        if any(m[2] in file_ids for m in NOTE_FILE_LINK.finditer(text)) or any(m[1] in media_ids for m in ASSET_LINK.finditer(text)) or any(m[2] in media_ids for m in NOTE_ASSET_LINK.finditer(text)):
                            fail(409, localized_message('attachmentIsUsedByAnotherPageFirstRemove'))
                    for fid in file_ids:
                        item = c.execute('SELECT * FROM files WHERE id=? AND user_id=? AND deleted IS NULL', (fid, uid)).fetchone()
                        if not item or item['parent_id'] not in selected:
                            fail(409, localized_message('pageReferencesAnAttachmentOutsideTheTransferableBranch'))
                        item = dict(item)
                        retired_files.add(item['object_id'])
                        item['object_id'], _ = copied(ordinary, item['object_id'], target, staged)
                        item.update(note_id=item['parent_id'], parent_id=None)
                        for field in ('user_id','deleted','trash_batch'):
                            item.pop(field, None)
                        value['files'][fid] = item
                    for aid in media_ids:
                        asset = c.execute('SELECT * FROM media WHERE id=? AND user_id=?', (aid, uid)).fetchone()
                        if not asset:
                            fail(409, localized_message('theImageBelongsToAnotherUserFirstReplace'))
                        settings = json.loads(c.execute('SELECT settings FROM users WHERE id=?', (uid,)).fetchone()[0])
                        if aid in json.dumps(settings):
                            fail(409, localized_message('theImageIsAlsoUsedInTheProfile'))
                        asset = dict(asset)
                        owner_note = assets.get(aid, {}).get('note_id', data.id)
                        value['files'][aid] = {'id':aid, 'name':'image-'+aid+'.'+asset['mime'].split('/')[-1], 'kind':'file', 'parent_id':None,
                            'note_id':owner_note, 'mime':asset['mime'], 'size':asset['size'], 'object_id':binary(media.read(aid), target, staged),
                            'version':1, 'created':asset['created'], 'updated':now(), 'source_asset':asset, 'page_asset':assets.get(aid)}
                        retired_media.append(aid)
                    def rewrite(text):
                        text = NOTE_FILE_LINK.sub(lambda m:'/api/secret-vault/notes/content/'+m[2] if m[2] in file_ids else m[0], text)
                        text = NOTE_ASSET_LINK.sub(lambda m:'/api/secret-vault/notes/content/'+m[2] if m[2] in media_ids else m[0], text)
                        return ASSET_LINK.sub(lambda m:'/api/secret-vault/notes/content/'+m[1] if m[1] in media_ids else m[0], text)
                    for nid in selected:
                        item = pages[nid]
                        item['body'] = rewrite(item['body'])
                        item['blocks'] = json.loads(rewrite(item['blocks'])) if item['blocks'] else None
                        for field in ('user_id','space_id','deleted','trash_batch'):
                            item.pop(field, None)
                        if nid==data.id:
                            item['parent_id'] = None
                        value['notes'][nid] = item
                    from app.page_links import page_links,prune_vault_links
                    outside=set().union(*(page_links(value['notes'][nid].get('blocks')) for nid in selected))-value['notes'].keys()
                    if outside:prune_vault_links(value,outside,now(),fail)
                    from app.note_access import remove_page_settings
                    remove_page_settings(c, selected)
                    from app.file_trash import purge_file_rows
                    purge_file_rows(c, file_ids | {nid for nid in selected if c.execute('SELECT 1 FROM files WHERE id=?', (nid,)).fetchone()}, now())
                    for nid in selected:
                        c.execute('DELETE FROM notes WHERE id=?', (nid,))
                    from app.page_links import prune_stored_links
                    private={row['id'] for row in c.execute('SELECT id FROM notes WHERE space_id IS NULL AND deleted IS NULL')}
                    prune_stored_links(c,selected,now(),fail,private)
                    for aid in media_ids:
                        c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)', (aid, 'media', now()))
                        c.execute('DELETE FROM media WHERE id=?', (aid,))
                # Keep permanent deletion semantics on retries, including all
                # historical S3 versions of the former ordinary object.
                for kind,identities in (('file',retired_files),('media',retired_media)):
                    for identity in identities:
                        c.execute('DELETE FROM blob_gc WHERE id=? AND kind=?',(identity,kind))
                        c.execute('INSERT OR IGNORE INTO blob_gc VALUES(?,?,?)',(identity,'vault-'+kind,now()))
                service.authorized(c, request, section)
                save(c, uid, value)
                service.audit(c, request, 'vault-moved-in', uid)
        except BaseException:
            discard(staged)
            raise
        # Original objects are already inaccessible; cleanup retries through the
        # existing ordinary blob_gc queue if a backend is temporarily offline.
        from app.secret_vault import collect_source
        for kind,identities in (('file',retired_files),('media',retired_media)):
            for identity in identities:
                with suppress(Exception):collect_source(db,ordinary,media,identity,'vault-'+kind)
        return {'ok':True}

    @r.post('/api/secret-vault/{section}/move-out')
    def move_out(section: Literal['notes','files'], data: Transfer, request: Request):
        staged = []
        staged_media = []
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                s, value, grant = service.authorized(c, request, section)
                uid = s['user_id']
                store = service.objects(uid, value)
                selected = branch(value[section], data.id)
                if value[section][data.id]['version']!=data.version:
                    fail(409, localized_message('elementHasBeenModifiedOpenItAgain'))
                if section=='files':
                    if any(value['files'][fid].get('note_id') for fid in selected):
                        fail(404, localized_message('fileNotFound'))
                    file_ids = selected
                else:
                    from app.vault_databases import boundary
                    boundary(value['notes'],selected,fail)
                    file_ids = {fid for fid,item in value['files'].items() if item.get('note_id') in selected}
                    protect_attachment_references(value,file_ids,selected,fail)
                translated = {}
                decorations={fid:secrets.token_hex(16) for fid in file_ids if value['files'][fid].get('page_asset')}
                for fid in file_ids:
                    item = value['files'][fid]
                    if item.get('source_asset'):
                        # Keep a new file attachment rather than restoring a
                        # publicly readable gallery object.
                        translated[fid] = secrets.token_urlsafe(12)
                    else:
                        translated[fid] = fid
                def rewrite(text):
                    return re.sub(r'/api/secret-vault/notes/content/([a-zA-Z0-9_-]+)',
                        lambda m:'/api/notes/'+value['files'][m[1]]['note_id']+'/files/'+translated[m[1]] if m[1] in translated else m[0], text)
                if section=='notes':
                    from app.page_links import page_links,without_page_links
                    from app.note_access import visible_ids
                    from app.collaboration import load_doc
                    from app.share_editing import replace_content
                    allowed=visible_ids(c,uid)|selected
                    pending = set(selected)
                    while pending:
                        ready_ids = [nid for nid in pending if value['notes'][nid].get('parent_id') not in pending]
                        if not ready_ids:
                            fail(409, localized_message('noteStructureIsCorrupted'))
                        for nid in ready_ids:
                            item = value['notes'][nid]
                            icon = item.get('icon','📄')
                            cover = item.get('cover','')
                            if icon.startswith('image:') and icon[6:] in decorations:icon='image:'+decorations[icon[6:]]
                            if cover.startswith('image:') and cover[6:] in decorations:cover='image:'+decorations[cover[6:]]
                            # Custom decorations remain ordinary note attachments
                            # and are preserved below without a gallery copy.
                            row = {'id':nid, 'user_id':uid, 'space_id':None, 'title':item['title'], 'body':rewrite(item['body']),
                                'blocks':json.dumps(json.loads(rewrite(json.dumps(item['blocks'])))) if item['blocks'] is not None else None,
                                'parent_id':item['parent_id'] if item.get('parent_id') in selected else None, 'icon':icon, 'cover':cover,
                                'created':item['created'], 'updated':now(), 'locked':item.get('locked',0), 'full_width':item.get('full_width',0), 'position':item.get('position',0)}
                            outside=page_links(item.get('blocks'))-allowed
                            if outside:
                                blocks=without_page_links(json.loads(row['blocks']),outside) or [{'type':'paragraph','content':[]}]
                                _,row['body'],_=replace_content(load_doc(None),row['title'],blocks,fail)
                                row['blocks']=json.dumps(blocks)
                            # Decorations are recreated in note_assets below.
                            insert(c, 'notes', row)
                            pending.remove(nid)
                    for nid in selected:
                        d=value['notes'][nid].get('database')
                        if d:
                            insert(c,'note_databases',{'note_id':nid,'properties':json.dumps(d['properties']),'views':json.dumps(d['views'])})
                    for nid in selected:
                        record=value['notes'][nid].get('record')
                        if record:
                            insert(c,'database_records',{'note_id':nid,'database_id':record['database_id'],'properties':json.dumps(record['properties'])})
                pending_files=set(file_ids)
                file_order=[]
                while pending_files:
                    ready_files=[fid for fid in pending_files if value['files'][fid].get('parent_id') not in pending_files]
                    if not ready_files:fail(409,localized_message('fileStructureIsCorrupted'))
                    file_order.extend(ready_files);pending_files.difference_update(ready_files)
                for fid in file_order:
                    item = value['files'][fid]
                    new_object = copied(store, item['object_id'], ordinary, staged)[0] if item.get('object_id') else None
                    note_id = item.get('note_id')
                    if note_id and not c.execute('SELECT 1 FROM files WHERE id=?', (note_id,)).fetchone():
                        insert(c, 'files', {'id':note_id, 'user_id':uid, 'kind':'folder', 'name':'Вложения', 'mime':'application/x-drg-note;space=private', 'created':now(), 'updated':now()})
                    insert(c, 'files', {'id':translated[fid], 'user_id':uid, 'parent_id':note_id if note_id else item['parent_id'] if item.get('parent_id') in file_ids else None,
                        'kind':item['kind'], 'name':item['name'], 'mime':item.get('mime',''), 'size':item['size'], 'object_id':new_object, 'created':item['created'], 'updated':now()})
                    # Icon/cover bytes also need the normal page-assets model.
                    if item.get('page_asset'):
                        aid=decorations[fid];info = dict(item['source_asset'],id=aid)
                        staged_media.append(aid)
                        media.write(aid, b''.join(store.iter_read(item['object_id'])))
                        insert(c, 'media', info)
                        insert(c, 'note_assets', dict(item['page_asset'],id=aid))
                    value['files'].pop(fid)
                    if item.get('object_id'):
                        value['garbage'].append(item['object_id'])
                if section=='notes':
                    for nid in selected:
                        value['notes'].pop(nid)
                    from app.page_links import prune_vault_links
                    prune_vault_links(value,selected,now(),fail)
                service.authorized(c, request, section)
                save(c, uid, value)
                service.audit(c, request, 'vault-moved-out', uid)
        except BaseException:
            discard(staged,staged_media)
            raise
        try:service.cleanup(uid)
        except (StorageError,OSError):fail(503,localized_message('dataHasBeenTransferredToTheRegularSection'))
        return {'ok':True}
