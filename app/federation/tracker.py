"""Device-signed tracker RPC, owned and committed by one canonical server."""
import base64
import binascii
import json
import mimetypes
import secrets
import time

from pydantic import Field, ValidationError
from app import tracker as domain
from app.http import HTTPError
from app.modules.preferences import module_enabled
from .identity import FederationError

READS = {'tracker_catalog','tracker_project','tracker_page','tracker_issue_read','tracker_attachment_read','tracker_avatar_read','tracker_group_read','tracker_deleted_issues'}


class Address(domain.Strict):
    project_id: str = Field(pattern=domain.IDENTITY)
    revision: int = Field(ge=1)
    include_cancelled: bool = False


class Page(Address):
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=200)
    group_id: str | None = Field(default=None,pattern=r'^(?:[A-Za-z0-9_-]{16})?$')


class GroupAddress(Address):
    group_id: str = Field(pattern=domain.IDENTITY)


class IssueAddress(Address):
    issue_id: str = Field(pattern=domain.IDENTITY)


class Create(Address):
    data: domain.Issue


class Update(IssueAddress):
    data: domain.IssueUpdate


class RestoreIssue(IssueAddress):
    version: int = Field(strict=True, ge=1)
    project_version: int = Field(strict=True, ge=1)


class DeleteIssue(RestoreIssue):
    confirmation: str = Field(max_length=32)


class Comment(IssueAddress):
    body: str = Field(min_length=1, max_length=10000)


class Attachment(IssueAddress):
    attachment_id: str = Field(pattern=domain.IDENTITY)
    offset: int = Field(default=0, ge=0, le=50*1024*1024)
    length: int = Field(default=192*1024, ge=1, le=192*1024)


class Upload(IssueAddress):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0, le=50*1024*1024)


class UploadAddress(IssueAddress):
    upload_id: str = Field(pattern=domain.USER)


class Chunk(UploadAddress):
    offset: int = Field(ge=0, le=50*1024*1024)
    body: str = Field(max_length=256*1024)


MODELS = {'tracker_catalog': domain.Strict, 'tracker_project': Address, 'tracker_page': Page,
          'tracker_avatar_read':Address,'tracker_group_read':GroupAddress,
          'tracker_issue_read': IssueAddress, 'tracker_issue_create': Create, 'tracker_issue_update': Update,
          'tracker_comment': Comment, 'tracker_accept': Address, 'tracker_attachment_read': Attachment,
          'tracker_upload_start': Upload, 'tracker_upload_chunk': Chunk, 'tracker_upload_finish': UploadAddress,
          'tracker_deleted_issues': Address, 'tracker_issue_delete': DeleteIssue, 'tracker_issue_restore': RestoreIssue}


def validate_payload(operation, payload):
    try: return MODELS[operation].model_validate(payload)
    except (KeyError, ValidationError): raise FederationError('invalid_tracker_fields') from None


def application(connection): return getattr(connection, 'application_connection', connection)


def authorize(service, connection, peer, intent):
    c = application(connection)
    settings = c.execute('SELECT settings FROM users WHERE id=?', (intent.target_actor_id,)).fetchone()
    if not settings or not module_enabled(json.loads(settings[0]), 'tracker'): raise FederationError('resource_access_denied')
    if intent.operation == 'tracker_catalog': return None
    payload = validate_payload(intent.operation, intent.payload)
    try:
        project = domain.project_row(c, payload.project_id)
        grant = c.execute('SELECT * FROM tracker_members WHERE project_id=? AND actor_id=? AND peer_id=?', (project['id'], intent.actor_id, peer.server_id)).fetchone()
        pending = intent.operation == 'tracker_accept'
        actual = domain.role(c, project, intent.actor_id, peer.server_id, pending=pending)
        if not grant or project['owner_id'] != intent.target_actor_id or grant['revision'] != payload.revision or not actual:
            raise FederationError('resource_access_denied')
        service.confirmed_card(connection, project['owner_id'], peer.server_id, intent.actor_id, intent.device_id)
        if intent.operation not in READS and not pending and domain.ROLES[actual] < domain.ROLES['editor']:
            raise FederationError('resource_access_denied')
        if hasattr(payload, 'issue_id'):
            row = domain.issue_row(c, payload.issue_id, include_deleted=intent.operation in ('tracker_issue_delete','tracker_issue_restore'))
            if row['project_id'] != project['id']: raise FederationError('resource_access_denied')
        if intent.operation=='tracker_group_read':domain.group_row(c,payload.group_id,project['id'])
        if not payload.include_cancelled and (hasattr(payload,'issue_id') and row['status']=='cancelled' or hasattr(payload,'data') and getattr(payload.data,'status',None)=='cancelled'):
            raise FederationError('tracker_update_required')
        return project
    except HTTPError as error:
        raise FederationError('resource_access_denied') from error


def annotated(c, project, uid, peer_id, stamp, invitation=False):
    result = domain.serialize_project(c, project, uid, stamp, peer_id, invitation=invitation)
    result['grant_revision'] = c.execute('SELECT revision FROM tracker_members WHERE project_id=? AND actor_id=? AND peer_id=?', (project['id'], uid, peer_id)).fetchone()[0]
    # Project administration remains with the canonical owner's local session.
    result['manage'] = False
    return result


def execute(service, connection, peer, intent):
    c = application(connection); stamp = int(time.time()); payload = validate_payload(intent.operation, intent.payload)
    project = authorize(service, connection, peer, intent)
    if project and intent.operation not in READS:
        if intent.operation in ('tracker_issue_delete','tracker_issue_restore'):
            domain.lock_owner(c, project['owner_id'])
        domain.lock_project(c,project['id'])
        project = authorize(service, connection, peer, intent)
    uid, peer_id = intent.actor_id, peer.server_id
    try:
        if intent.operation == 'tracker_catalog':
            result = []
            for item in domain.projects(c, uid, stamp, peer_id, owner_id=intent.target_actor_id, invitations=True):
                result.append(annotated(c, domain.project_row(c, item['id']), uid, peer_id, stamp, invitation=item['pending']))
            return {'projects': result}, None
        if intent.operation == 'tracker_project': return annotated(c, project, uid, peer_id, stamp), None
        if intent.operation=='tracker_avatar_read':
            body=json.loads(project['payload']).get('avatar','')
            if not body:raise FederationError('resource_access_denied')
            from app.tracker_avatar import version
            return {'body':body,'mime':'image/webp','version':version(body)},None
        if intent.operation=='tracker_group_read':
            return domain.serialize_group(c,domain.group_row(c,payload.group_id,project['id']),True),None
        if intent.operation == 'tracker_accept':
            c.execute("UPDATE tracker_members SET state='active' WHERE project_id=? AND actor_id=? AND peer_id=? AND state='pending'", (project['id'], uid, peer_id))
            domain.history(c, project['id'], None, uid, peer_id, 'member_accepted', {}, stamp)
            return annotated(c, project, uid, peer_id, stamp), None
        if intent.operation == 'tracker_page':
            result = domain.issue_page(c, project['id'], uid, stamp, peer_id, after=payload.after, limit=payload.limit,group_id=payload.group_id,include_cancelled=payload.include_cancelled)
            result['project'] = annotated(c, project, uid, peer_id, stamp)
            return result, None
        if intent.operation == 'tracker_deleted_issues':
            from app.tracker_issue_lifecycle import deleted_issues
            return deleted_issues(c, project['id'], uid, stamp, peer_id), None
        if intent.operation == 'tracker_issue_delete':
            from app.tracker_issue_lifecycle import schedule
            return {'ok': True, 'deletion': schedule(c, payload.issue_id, uid, payload.version, payload.project_version, payload.confirmation, stamp, peer_id)}, None
        if intent.operation == 'tracker_issue_restore':
            from app.tracker_issue_lifecycle import restore
            return restore(c, payload.issue_id, uid, payload.version, payload.project_version, stamp, peer_id), None
        if intent.operation == 'tracker_issue_read':
            return domain.serialize_issue(c, domain.issue_row(c, payload.issue_id), project, stamp, detail=True, delete_scope=domain.ROLES[domain.role(c, project, uid, peer_id)] >= domain.ROLES['editor']), None
        if intent.operation == 'tracker_issue_create':
            return domain.create_issue(c, project['id'], payload.data, uid, stamp, peer_id), None
        if intent.operation == 'tracker_issue_update':
            return domain.update_issue(c, payload.issue_id, payload.data, uid, stamp, peer_id), None
        if intent.operation == 'tracker_comment':
            return domain.add_comment(c, payload.issue_id, domain.Comment(body=payload.body), uid, stamp, peer_id), None
        from app.modules.tracker import attachment, store_attachment
        from app.file_store import file_store
        objects = file_store(service.m.db, service.m.DATA, namespace='tracker-attachments')
        if intent.operation == 'tracker_attachment_read':
            row = attachment(c, payload.issue_id, payload.attachment_id, uid, peer_id)
            if payload.offset > row['size']: raise FederationError('invalid_tracker_fields')
            output = bytearray(); offset = 0
            stream = objects.iter_read(row['object_id'])
            try:
                for chunk in stream:
                    end = offset + len(chunk)
                    if end > payload.offset and offset < payload.offset + payload.length:
                        output.extend(chunk[max(0, payload.offset-offset):min(len(chunk), payload.offset+payload.length-offset)])
                    offset = end
                    if offset >= payload.offset + payload.length: break
            finally: stream.close()
            return {'id': row['id'], 'offset': payload.offset, 'size': row['size'], 'body': base64.b64encode(output).decode()}, None
        if intent.operation == 'tracker_upload_start':
            from app.modules.files import filename
            c.execute('DELETE FROM tracker_uploads WHERE created<=?', (stamp-3600,))
            if c.execute('SELECT COUNT(*) FROM tracker_uploads WHERE actor_id=? AND peer_id=?', (uid, peer_id)).fetchone()[0] >= 3: raise FederationError('upload_capacity')
            from app.quota import summary
            quota = summary(c, project['owner_id'])
            reserved = c.execute('SELECT COALESCE(SUM(u.size),0) FROM tracker_uploads u JOIN tracker_issues i ON i.id=u.issue_id JOIN tracker_projects p ON p.id=i.project_id WHERE p.owner_id=?', (project['owner_id'],)).fetchone()[0]
            if quota['limit_bytes'] is not None and payload.size + reserved > quota['limit_bytes'] - quota['used_bytes']: raise FederationError('quota_exceeded')
            identity = secrets.token_hex(16)
            c.execute('INSERT INTO tracker_uploads(id,issue_id,actor_id,peer_id,payload,size,created) VALUES(?,?,?,?,?,?,?)', (identity, payload.issue_id, uid, peer_id, domain.dump({'name': filename(payload.name)}), payload.size, stamp))
            return {'id': identity}, None
        upload = c.execute('SELECT * FROM tracker_uploads WHERE id=? AND issue_id=? AND actor_id=? AND peer_id=? AND created>?', (payload.upload_id, payload.issue_id, uid, peer_id, stamp-3600)).fetchone()
        if not upload: raise FederationError('invalid_upload')
        if intent.operation == 'tracker_upload_chunk':
            try: body = base64.b64decode(payload.body, validate=True)
            except (ValueError, binascii.Error): raise FederationError('invalid_upload') from None
            if not body or len(body) > 192*1024 or payload.offset + len(body) > upload['size']: raise FederationError('invalid_upload')
            if upload['received'] != payload.offset:
                old = c.execute('SELECT body FROM tracker_upload_chunks WHERE upload_id=? AND position=?', (upload['id'], payload.offset)).fetchone()
                if old and bytes(old[0]) == body: return {'received': upload['received']}, None
                raise FederationError('invalid_upload')
            c.execute('INSERT INTO tracker_upload_chunks(upload_id,position,body) VALUES(?,?,?)', (upload['id'], payload.offset, body))
            c.execute('UPDATE tracker_uploads SET received=received+? WHERE id=?', (len(body), upload['id']))
            return {'received': upload['received'] + len(body)}, None
        if upload['received'] != upload['size']: raise FederationError('invalid_upload')
        identity = secrets.token_hex(16); writer = objects.writer(identity)
        try:
            for chunk in c.execute('SELECT body FROM tracker_upload_chunks WHERE upload_id=? ORDER BY position', (upload['id'],)):
                writer.write(bytes(chunk[0]))
            size, _ = writer.finish()
            name = json.loads(upload['payload'])['name']
            result = store_attachment(c, domain.issue_row(c, payload.issue_id), uid, peer_id, name, mimetypes.guess_type(name)[0] or 'application/octet-stream', size, identity, stamp)
            c.execute('DELETE FROM tracker_uploads WHERE id=?', (upload['id'],))
            return result, None
        except BaseException:
            writer.abort(); raise
    except (HTTPError, ValidationError) as error:
        exception = FederationError(str(error) if isinstance(error, HTTPError) else 'invalid_tracker_fields')
        exception.status_code = error.status_code if isinstance(error, HTTPError) else 422
        raise exception from error
