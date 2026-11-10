"""Bounded encrypted chunk uploads, finalized atomically with the note's event."""
import base64
import mimetypes
import secrets
import time
from .identity import FederationError

LIMIT = 50 * 1024 * 1024
CHUNK = 262144


def execute_upload(service, connection, page, grant, peer, intent):
    from app.modules.files import filename
    from app.file_store import file_store
    c = connection.application_connection
    payload, operation, stamp = intent.payload,intent.operation,int(time.time())
    c.execute('DELETE FROM federation_uploads WHERE created<?', (stamp-3600,))
    if operation in ('asset_upload','asset_upload_start'):
        name = filename(payload['name'])
        if operation == 'asset_upload':
            try: body = base64.b64decode(payload['body'],validate=True)
            except (ValueError,TypeError): raise FederationError('invalid_asset_body') from None
            size = len(body)
        else: size=payload['size']
        if type(size) is not int or not 0 <= size <= LIMIT: raise FederationError('asset_too_large')
        reserved = c.execute('SELECT COALESCE(SUM(size),0) FROM federation_uploads WHERE owner_id=?', (page['user_id'],)).fetchone()[0]
        limits = c.execute('SELECT limit_bytes,used_bytes FROM user_limits WHERE user_id=?',(page['user_id'],)).fetchone()
        if limits and limits[0] is not None and limits[1]+reserved+size > limits[0]: raise FederationError('storage_quota_exceeded')
        if c.execute('SELECT COUNT(*) FROM federation_uploads WHERE peer_id=? AND actor_id=?', (peer.server_id,intent.actor_id)).fetchone()[0]>=5:
            raise FederationError('too_many_uploads')
        identity = secrets.token_hex(16)
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        c.execute('INSERT INTO federation_uploads VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (identity,page['id'],page['user_id'],peer.server_id,intent.actor_id,intent.device_id,
                   grant['revision'],name,mime,size,0,stamp))
        if operation=='asset_upload_start': return {'upload_id':identity,'offset':0}
        c.execute('INSERT INTO federation_upload_chunks VALUES(?,?,?)',(identity,0,body))
        c.execute('UPDATE federation_uploads SET received=? WHERE id=?',(size,identity))
    else: identity=payload['upload_id']
    row = c.execute('SELECT * FROM federation_uploads WHERE id=?',(identity,)).fetchone()
    if (not row or row['note_id']!=page['id'] or row['peer_id']!=peer.server_id or row['actor_id']!=intent.actor_id
            or row['device_id']!=intent.device_id or row['revision']!=grant['revision']):
        raise FederationError('upload_not_found')
    if operation=='asset_upload_chunk':
        offset=payload['offset']
        try: body=base64.b64decode(payload['body'],validate=True)
        except (ValueError,TypeError): raise FederationError('invalid_asset_body') from None
        if type(offset) is not int or offset!=row['received'] or not 0<len(body)<=CHUNK or offset+len(body)>row['size']:
            raise FederationError('invalid_upload_range')
        c.execute('INSERT INTO federation_upload_chunks VALUES(?,?,?)',(identity,offset,body))
        c.execute('UPDATE federation_uploads SET received=? WHERE id=?',(offset+len(body),identity))
        return {'upload_id':identity,'offset':offset+len(body)}
    if row['received']!=row['size']: raise FederationError('upload_incomplete')
    folder=c.execute('SELECT * FROM files WHERE id=?',(page['id'],)).fetchone()
    if folder and (folder['kind']!='folder' or folder['user_id']!=page['user_id'] or folder['deleted'] is not None):
        raise FederationError('attachment_folder_unavailable')
    if not folder:
        c.execute('INSERT INTO files(id,user_id,kind,name,mime,created,updated) VALUES(?,?,?,?,?,?,?)',
                  (page['id'],page['user_id'],'folder','Вложения — '+page['title'][:200],
                   'application/x-drg-note;space='+(page['space_id'] or 'private'),stamp,stamp))
    object_id=secrets.token_hex(16); file_id=secrets.token_urlsafe(12)
    store=file_store(service.m.db,service.m.DATA); writer=store.writer(object_id)
    connection.rollback_actions.append(writer.abort)
    try:
        for chunk in c.execute('SELECT payload FROM federation_upload_chunks WHERE upload_id=? ORDER BY position',(identity,)):
            writer.write(chunk[0])
        size,_=writer.finish()
        c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (file_id,page['user_id'],page['id'],'file',row['name'],row['mime'],size,object_id,stamp,stamp))
        c.execute('DELETE FROM federation_uploads WHERE id=?',(identity,))
    except BaseException:
        writer.abort()
        raise
    return {'id':file_id,'name':row['name'],'mime':row['mime'],'size':size,
            'url':service.m.PUBLIC_URL.rstrip('/')+'/api/notes/'+page['id']+'/files/'+file_id}
