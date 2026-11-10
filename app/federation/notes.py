"""Grant-scoped rich documents, descendants and attachments using existing stores."""
import base64
import copy
import json
import secrets
import time

from pycrdt import XmlElement, XmlFragment
from app.http import HTTPError
from .actors import canonical
from .identity import FederationError
from .protocol import MAX_BODY_BYTES


def collaborative(c, note):
    if note['space_id']:return True
    c=getattr(c,'application_connection',c)
    shared=getattr(c,'federation_collaborative_ids',None)
    if shared is None:
        shared={row[0] for row in c.execute("WITH RECURSIVE inherited(id) AS (SELECT g.note_id FROM federation_grants g JOIN notes n ON n.id=g.note_id WHERE g.active=1 AND g.role!='none' AND n.deleted IS NULL UNION SELECT n.id FROM notes n JOIN inherited p ON n.parent_id=p.id WHERE n.deleted IS NULL) SELECT id FROM inherited UNION SELECT note_id FROM collaborative_docs")}
        if hasattr(c,'__dict__'):c.federation_collaborative_ids=shared
    return note['id'] in shared


def ensure_document(c, note, fail, stamp):
    from app.collaboration import load_doc
    from app.share_editing import replace_content
    saved = c.execute('SELECT * FROM collaborative_docs WHERE note_id=?', (note['id'],)).fetchone()
    if saved:
        return saved
    blocks = json.loads(note['blocks']) if note['blocks'] else None
    if not blocks:
        if note['body']:
            raise FederationError('open_rich_editor_first')
        blocks = [{'id': secrets.token_hex(12), 'type': 'paragraph', 'props': {}, 'content': [], 'children': []}]
    doc = load_doc(None)
    title, body, blocks = replace_content(doc, note['title'], blocks, fail)
    epoch = secrets.token_urlsafe(16)
    c.execute('INSERT INTO collaborative_docs VALUES(?,?,?,?)', (note['id'], doc.get_update(), epoch, stamp))
    c.execute('UPDATE notes SET title=?,body=?,blocks=? WHERE id=?', (title, body, json.dumps(blocks, ensure_ascii=False), note['id']))
    return c.execute('SELECT * FROM collaborative_docs WHERE note_id=?', (note['id'],)).fetchone()


def effective_grant(service, connection, note_id, peer_id, actor_id, device_id, *, write=False):
    seen = set()
    identity = note_id
    while identity and identity not in seen and len(seen) < 100:
        seen.add(identity)
        row = connection.execute('SELECT * FROM grants WHERE note_id=? AND peer_id=? AND actor_id=?',
                                  (identity, peer_id, actor_id)).fetchone()
        if row:
            if not row['active'] or row['role'] == 'none' or write and row['role'] != 'editor':
                raise FederationError('resource_access_denied')
            service.confirmed_card(connection, row['owner_id'], peer_id, actor_id, device_id)
            return row
        page = connection.execute('SELECT parent_id FROM notes WHERE id=? AND deleted IS NULL', (identity,)).fetchone()
        identity = page[0] if page else None
    raise FederationError('resource_access_denied')


def serialized(service, c, page, grant, saved, *, scope=None):
    from app.modules.note_assets import asset_payload
    result = {key: page[key] for key in ('id','title','body','parent_id','space_id','created','updated','icon','cover',
                                        'favorite','locked','full_width','position','version')}
    result.update(asset_payload(c, page))
    result.update(note_id=page['id'], owner_id=grant['owner_id'], actor_id=grant['owner_id'],
                  peer_id=service.identity.descriptor.server_id, role=grant['role'], revision=grant['revision'],
                  blocks=json.loads(page['blocks']) if page['blocks'] else None,
                  kind='database' if c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(page['id'],)).fetchone() else 'page',
                  manage=False, federation_shared=True, web_origin=service.m.PUBLIC_URL.rstrip('/'))
    record=c.execute('SELECT database_id FROM database_records WHERE note_id=?',(page['id'],)).fetchone()
    result['database_id']=record[0] if record and (scope is None or scope.allowed(record[0])) else None
    if scope and page['parent_id'] and not scope.allowed(page['parent_id']):result['parent_id']=None
    if saved is not None:result.update(epoch=saved['epoch'],state=base64.b64encode(saved['state']).decode())
    return result


def execute(service, connection, peer, intent):
    from app.collaboration import MAX_STATE, load_doc, project
    from app.page_links import page_links, prune_stored_links
    from app.modules.note_files import validate_file_links, page_file
    from app.note_history import snapshot
    stamp = int(time.time())
    operation, payload = intent.operation, intent.payload
    c = connection.application_connection if hasattr(connection, 'application_connection') else connection
    from .databases import DatabaseScope
    scope = DatabaseScope(service,connection,peer,intent)
    if operation == 'catalog':
        notes = []
        for page in c.execute('SELECT * FROM notes WHERE deleted IS NULL ORDER BY position,updated DESC').fetchall():
            try:
                grant = effective_grant(service, connection, page['id'], peer.server_id, intent.actor_id, intent.device_id)
                if grant['owner_id'] != intent.target_actor_id:
                    continue
                scope.access(page['id'])
            except (FederationError, HTTPError):
                continue
            item = serialized(service, c, page, grant, None, scope=scope)
            item.pop('state', None); item.pop('body', None); item.pop('blocks', None)
            notes.append(item)
        known = {item['id'] for item in notes}
        for item in notes:
            if item['parent_id'] not in known: item['parent_id'] = None
        return {'notes': notes}, None
    nid = payload.get('parent_id') if operation == 'create' else payload.get('note_id')
    grant = effective_grant(service, connection, nid, peer.server_id, intent.actor_id, intent.device_id,
                            write=operation not in ('read','asset_read'))
    if grant['owner_id'] != intent.target_actor_id or operation != 'create' and payload['revision'] != grant['revision']:
        raise FederationError('resource_access_denied')
    page = service.authorized_note(c, nid, grant['owner_id'])
    scope.access(nid,write=operation not in ('read','asset_read'))
    is_database=bool(c.execute('SELECT 1 FROM note_databases WHERE note_id=?',(nid,)).fetchone())
    if is_database and operation=='read':return serialized(service,c,page,grant,None,scope=scope),None
    if is_database and operation in ('create','update'):raise FederationError('database_operation_required')
    is_record=bool(c.execute('SELECT 1 FROM database_records WHERE note_id=?',(nid,)).fetchone())
    if is_record and operation=='read' and page['body'] and not (page['blocks'] and json.loads(page['blocks'])) and not c.execute('SELECT 1 FROM collaborative_docs WHERE note_id=?',(nid,)).fetchone():
        from .databases import record_context
        result=serialized(service,c,page,grant,None,scope=scope)
        result.update(legacy=True,record_database=record_context(service,connection,peer,intent,nid))
        return result,None
    saved = ensure_document(c, page, service.m.fail, stamp) if not is_database and operation in ('read','update','create') else None
    if operation == 'asset_read':
        asset = payload['asset_id']
        offset, length = payload.get('offset', 0), payload.get('length', 262144)
        if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= 262144:
            raise FederationError('invalid_asset_range')
        if len(asset) == 16:
            row = page_file(c, page, asset, service.m.fail, referenced=grant['role'] == 'viewer', uid=grant['owner_id'])
            # New attachment references cannot be used to launder another page's private file.
            if row['parent_id'] != page['id']:
                from app.personal_data import NOTE_FILE_LINK
                if asset not in {match[2] for match in NOTE_FILE_LINK.finditer((page['blocks'] or '') + page['body'])}:
                    raise FederationError('resource_access_denied')
            from app.file_store import file_store
            stream = file_store(service.m.db, service.m.DATA).iter_read(row['object_id'])
            total, mime, name = row['size'], row['mime'], row['name']
        else:
            from app.personal_data import ASSET_LINK, NOTE_ASSET_LINK
            text=(page['blocks'] or '')+page['body']
            references=set(ASSET_LINK.findall(text))|{item[1] for item in NOTE_ASSET_LINK.findall(text)}
            bound=c.execute('SELECT 1 FROM note_assets WHERE id=? AND note_id=?',(asset,page['id'])).fetchone()
            if asset not in references and not bound:
                raise FederationError('resource_access_denied')
            row = c.execute('SELECT * FROM media WHERE id=?', (asset,)).fetchone()
            if not row: raise FederationError('asset_not_found')
            from app.data_crypto import media_store
            data = media_store(service.m.db, service.m.DATA).read(asset)
            stream, total, mime, name = iter((data,)), len(data), row['mime'], 'attachment'
        data, position = bytearray(), 0
        try:
            for chunk in stream:
                first, last = max(offset - position, 0), min(offset + length - position, len(chunk))
                if first < last: data.extend(chunk[first:last])
                position += len(chunk)
                if position >= offset + length: break
        finally:
            if hasattr(stream, 'close'): stream.close()
        return {'body': base64.b64encode(data).decode(), 'size': total, 'mime': mime, 'name': name,
                'offset': offset, 'more': offset + len(data) < total}, None
    if operation.startswith('asset_upload'):
        from .uploads import execute_upload
        return execute_upload(service, connection, page, grant, peer, intent), None
    if operation == 'delete':
        rows = c.execute('WITH RECURSIVE tree(id) AS (SELECT id FROM notes WHERE id=? AND deleted IS NULL UNION SELECT n.id FROM notes n JOIN tree t ON n.parent_id=t.id WHERE n.deleted IS NULL) SELECT n.* FROM notes n JOIN tree t ON n.id=t.id', (nid,)).fetchall()
        for child in rows:
            effective_grant(service, connection, child['id'], peer.server_id, intent.actor_id, intent.device_id, write=True)
            service.authorized_note(c, child['id'], grant['owner_id'])
        from app.note_trash import record
        record(c, [child['id'] for child in rows], stamp)
        batch = secrets.token_urlsafe(12)
        for child in rows:
            c.execute('UPDATE notes SET deleted=?,trash_batch=?,version=version+1 WHERE id=?', (stamp,batch,child['id']))
            c.execute('UPDATE shares SET revoked=1 WHERE note_id=?', (child['id'],))
        removed={child['id'] for child in rows};parents=set()
        for source in c.execute('SELECT id,blocks FROM notes WHERE deleted IS NULL AND blocks IS NOT NULL'):
            if not page_links(json.loads(source['blocks']))&removed:continue
            try:scope.access(source['id'],write=True)
            except (HTTPError,FederationError):continue
            parents.add(source['id'])
        events = prune_stored_links(c, removed, stamp, service.m.fail, parents)
        return {'deleted': True, 'note_id': nid}, events
    if operation == 'create':
        from app.modules.notes import validate_blocks
        title, blocks = payload['title'], copy.deepcopy(payload['blocks'])
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise FederationError('invalid_title')
        validate_blocks(blocks, service.m.fail)
        if page_links(blocks): raise FederationError('create_page_links_separately')
        from app.personal_data import NOTE_FILE_LINK, ASSET_LINK, NOTE_ASSET_LINK
        references = json.dumps(blocks,ensure_ascii=False)
        # A new child starts without attachments. Upload through this child's
        # own grant before inserting references; guessed IDs never gain trust.
        if NOTE_FILE_LINK.search(references) or ASSET_LINK.search(references) or NOTE_ASSET_LINK.search(references):
            raise FederationError('upload_media_to_shared_note')
        identity = secrets.token_urlsafe(12)
        position = c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM notes WHERE parent_id=?',(nid,)).fetchone()[0]
        if position > 1000000: raise FederationError('subpage_position_limit')
        c.execute('INSERT INTO notes(id,user_id,title,body,created,updated,blocks,parent_id,space_id,position) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (identity,grant['owner_id'],title.strip(),'',stamp,stamp,json.dumps(blocks),nid,page['space_id'],position))
        child = c.execute('SELECT * FROM notes WHERE id=?', (identity,)).fetchone()
        child_saved = ensure_document(c, child, service.m.fail, stamp)
        doc = load_doc(saved['state']); before = doc.get_state()
        root = list(doc.get('prosemirror', type=XmlFragment).children)[0]
        root.children.append(XmlElement('blockContainer', {'id': secrets.token_hex(12)},
                            [XmlElement('pageLink', {'pageId': identity,'title': title.strip()}, [])]))
        title_parent, body_parent, blocks_parent = project(doc, service.m.fail)
        c.execute('UPDATE collaborative_docs SET state=?,updated=? WHERE note_id=?', (doc.get_update(),stamp,nid))
        c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',
                  (title_parent,body_parent,json.dumps(blocks_parent,ensure_ascii=False),stamp,nid))
        child = c.execute('SELECT * FROM notes WHERE id=?', (identity,)).fetchone()
        return serialized(service,c,child,grant,child_saved,scope=scope), (nid,doc.get_update(),doc.get_update(before),saved['epoch'],page['version']+1,False)
    doc = load_doc(saved['state'])
    broadcast = None
    if operation == 'update':
        if payload['epoch'] != saved['epoch']: raise FederationError('document_epoch_changed')
        try:
            update = base64.b64decode(payload['update'], validate=True)
            if len(update) > MAX_STATE: raise ValueError()
            doc.apply_update(update)
        except (ValueError,TypeError,KeyError,RuntimeError):
            raise FederationError('invalid_document_update') from None
        if len(doc.get_update()) > MAX_STATE: raise FederationError('document_too_large')
        title, body, blocks = project(doc, service.m.fail)
        previous = json.loads(page['blocks']) if page['blocks'] else []
        for identity in page_links(blocks) - page_links(previous):
            scope.access(identity)
        from app.personal_data import NOTE_FILE_LINK, ASSET_LINK, NOTE_ASSET_LINK
        old_text, new_text = (page['blocks'] or '') + page['body'], json.dumps(blocks) + body
        old_files = {match[2] for match in NOTE_FILE_LINK.finditer(old_text)}
        for match in NOTE_FILE_LINK.finditer(new_text):
            if match[2] in old_files: continue
            source = c.execute('SELECT parent_id FROM files WHERE id=?', (match[2],)).fetchone()
            if not source: raise FederationError('invalid_asset_reference')
            scope.access(source[0])
        media_refs=lambda text:set(ASSET_LINK.findall(text))|{item[1] for item in NOTE_ASSET_LINK.findall(text)}
        if media_refs(new_text) - media_refs(old_text):
            raise FederationError('upload_media_to_shared_note')
        validate_file_links(c,nid,body,blocks,grant['owner_id'],service.m.fail,old_text)
        for identity in page_links(previous) - page_links(blocks):
            child = c.execute('SELECT id FROM notes WHERE id=? AND parent_id=?', (identity,nid)).fetchone()
            if child:
                descendants = c.execute('WITH RECURSIVE tree(id) AS (SELECT id FROM notes WHERE id=? UNION SELECT n.id FROM notes n JOIN tree t ON n.parent_id=t.id) SELECT id FROM tree', (identity,)).fetchall()
                for descendant in descendants:
                    effective_grant(service, connection, descendant['id'], peer.server_id, intent.actor_id, intent.device_id, write=True)
                    service.authorized_note(c, descendant['id'], grant['owner_id'])
        from app.note_subpages import reconcile_links
        reconcile_links(c,page,blocks,{'user_id':grant['owner_id']},stamp,service.m.fail)
        snapshot(c,page,stamp,interval=60)
        state = doc.get_update()
        c.execute('UPDATE collaborative_docs SET state=?,updated=? WHERE note_id=?', (state,stamp,nid))
        c.execute('UPDATE notes SET title=?,body=?,blocks=?,updated=?,version=version+1 WHERE id=?',
                  (title,body,json.dumps(blocks,ensure_ascii=False),stamp,nid))
        broadcast = (nid,state,update,saved['epoch'],page['version']+1,False)
    page = c.execute('SELECT * FROM notes WHERE id=?', (nid,)).fetchone()
    saved = c.execute('SELECT * FROM collaborative_docs WHERE note_id=?', (nid,)).fetchone()
    result = serialized(service,c,page,grant,saved,scope=scope)
    if c.execute('SELECT 1 FROM database_records WHERE note_id=?',(nid,)).fetchone():
        from .databases import record_context
        result['record_database']=record_context(service,connection,peer,intent,nid)
    if len(canonical(result)) > MAX_BODY_BYTES * 3 // 4 - 4096:
        raise FederationError('result_too_large')
    return result,broadcast
