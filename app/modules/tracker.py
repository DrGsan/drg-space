"""HTTP boundary for durable project tasks; all object access is server checked."""
import asyncio
import base64
import json
import mimetypes
import secrets
from contextlib import suppress

from pydantic import Field, ValidationError
from app.http import Router, Request, Query, Response
from app.storage import blocking
from app.file_store import file_store
from app import tracker as domain


class Import(domain.Strict):
    list_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,80}$')
    name: str = Field(min_length=1, max_length=100)
    key: str = Field(pattern=r'^[A-Z][A-Z0-9]{1,9}$')


class ProjectUpdate(domain.Strict):
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=5000)
    avatar: str | None = Field(default=None,max_length=1400000)
    key: str | None = Field(default=None,pattern=r'^[A-Z][A-Z0-9]{1,9}$')


class Ownership(domain.Strict):
    actor_id: str = Field(pattern=domain.USER)


class ProjectDelete(domain.Strict):
    version: int = Field(strict=True, ge=1)
    confirmation: str = Field(max_length=100)


class ProjectRestore(domain.Strict):
    version: int = Field(strict=True, ge=1)


def add_member(c, pid, data, uid, stamp):
    domain.access(c, pid, uid, required='manager');domain.lock_project(c, pid)
    project = domain.access(c, pid, uid, required='manager')
    if data.actor_id == project['owner_id'] and not data.peer_id: domain.fail(422, 'trackerInvalidMember')
    if data.peer_id:
        if data.role == 'manager': domain.fail(422, 'trackerInvalidMember')
        if project['owner_id'] != uid: domain.fail(403, 'trackerReadOnly')
        contact = c.execute("SELECT 1 FROM federation_contacts f JOIN federation_peers p ON p.id=f.peer_id WHERE f.user_id=? AND f.peer_id=? AND f.actor_id=? AND f.status='accepted' AND p.active=1", (uid, data.peer_id, data.actor_id)).fetchone()
        if not contact: domain.fail(422, 'trackerInvalidMember')
    else:
        if project['space_id']:
            if not c.execute('SELECT 1 FROM space_members WHERE space_id=? AND user_id=?', (project['space_id'], data.actor_id)).fetchone(): domain.fail(422, 'trackerInvalidMember')
        else:
            from app.modules.tasks import _friend
            if not _friend(c, uid, data.actor_id): domain.fail(422, 'trackerInvalidMember')
        if not c.execute('SELECT 1 FROM users WHERE id=?', (data.actor_id,)).fetchone(): domain.fail(422, 'trackerInvalidMember')
    old = c.execute('SELECT state,revision FROM tracker_members WHERE project_id=? AND actor_id=? AND peer_id=?', (pid, data.actor_id, data.peer_id)).fetchone()
    if not old and c.execute("SELECT COUNT(*) FROM tracker_members WHERE project_id=? AND state!='revoked'", (pid,)).fetchone()[0] >= 499: domain.fail(422, 'trackerMemberLimit')
    state = 'active' if old and old['state'] == 'active' or not data.peer_id and project['space_id'] else 'pending'
    revision = old['revision'] + 1 if old else 1
    c.execute('INSERT INTO tracker_members(project_id,actor_id,peer_id,role,state,revision,created) VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,actor_id,peer_id) DO UPDATE SET role=excluded.role,state=excluded.state,revision=excluded.revision',
              (pid, data.actor_id, data.peer_id, data.role, state, revision, stamp))
    domain.history(c, pid, None, uid, '', 'member_changed', data.model_dump() | {'state': state}, stamp)
    c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?', (stamp, pid))
    if not data.peer_id and state == 'pending':
        from app.notifications import notify
        notify(c, data.actor_id, 'tracker_invitation', {'project_id': pid, 'project_name': json.loads(project['payload'])['name'], 'actor_id': uid}, stamp)
    return domain.serialize_project(c, domain.project_row(c, pid), uid, stamp)


def attachment(c, iid, fid, uid, peer_id=''):
    issue = domain.issue_row(c, iid)
    domain.access(c, issue['project_id'], uid, peer_id)
    row = c.execute("SELECT f.* FROM files f JOIN tracker_attachments a ON a.id=f.id WHERE a.issue_id=? AND a.id=? AND f.deleted IS NULL AND f.kind='file'", (iid, fid)).fetchone()
    if not row: domain.fail(404, 'trackerAttachmentNotFound')
    return dict(row)


def store_attachment(c, issue, uid, peer_id, name, mime, size, object_id, stamp):
    domain.access(c, issue['project_id'], uid, peer_id, required='editor');domain.lock_project(c, issue['project_id'])
    project = domain.access(c, issue['project_id'], uid, peer_id, required='editor')
    if c.execute('SELECT COUNT(*) FROM tracker_attachments WHERE issue_id=?', (issue['id'],)).fetchone()[0] >= 100: domain.fail(422, 'trackerAttachmentLimit')
    folder_id = issue['id']
    folder = c.execute('SELECT kind,user_id,mime,deleted FROM files WHERE id=?', (folder_id,)).fetchone()
    marker = 'application/x-drg-tracker;project=' + project['id']
    if folder and (folder['kind'] != 'folder' or folder['user_id'] != project['owner_id'] or folder['mime'] != marker or folder['deleted'] is not None): domain.fail(409, 'trackerAttachmentNotFound')
    if not folder:
        c.execute('INSERT INTO files(id,user_id,kind,name,mime,created,updated) VALUES(?,?,?,?,?,?,?)', (folder_id, project['owner_id'], 'folder', json.loads(issue['payload'])['title'][:200], marker, stamp, stamp))
    fid = secrets.token_urlsafe(12)
    c.execute('INSERT INTO files(id,user_id,parent_id,kind,name,mime,size,object_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
              (fid, project['owner_id'], folder_id, 'file', name, mime, size, object_id, stamp, stamp))
    c.execute('INSERT INTO tracker_attachments(id,issue_id,actor_id,peer_id,created) VALUES(?,?,?,?,?)', (fid, issue['id'], uid, peer_id, stamp))
    domain.history(c, project['id'], issue['id'], uid, peer_id, 'attachment_added', {'id': fid, 'name': name}, stamp)
    c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?', (stamp, project['id']))
    return {'id': fid, 'name': name, 'mime': mime, 'size': size, 'created': stamp}


def create_tracker_router(db, ready, now, fail, rate, data_dir):
    router = Router(); objects = file_store(db, data_dir, namespace='tracker-attachments'); uploads = asyncio.Semaphore(2)

    def session(request, mutation=False):
        s = ready(request)
        if mutation: rate(request, 'tracker-write', 240, subject=s['user_id'])
        return s

    @router.get('/api/tracker')
    def catalog(request: Request):
        uid = session(request)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            from app.tracker_lifecycle import purge,deleted_projects
            purge(c,now())
            from app.space_profiles import templates
            from app.workspace_lifecycle import pending
            profiles = templates(c)
            contexts = [{'id': 'personal', 'kind': 'personal', 'name': ''}, {'id': 'shared', 'kind': 'shared', 'name': ''}]
            for row in c.execute('SELECT s.* FROM spaces s JOIN space_members m ON m.space_id=s.id WHERE m.user_id=? ORDER BY s.created,s.id', (uid,)):
                if pending(c, row['id']): continue
                name = row['name']
                if row['kind'] == 'friends':
                    other = c.execute('SELECT user_id FROM space_members WHERE space_id=? AND user_id!=?', (row['id'], uid)).fetchone()
                    if other:
                        who = domain.person(c, other[0], stamp=now()); name = who.get('display_name') or who['login']
                contexts.append({'id': row['id'], 'kind': profiles.get(row['id'], 'family') if row['kind'] != 'friends' else 'friends', 'name': name})
            return {'contexts': contexts, 'projects': domain.projects(c, uid, now(), invitations=True), 'deleted_projects': deleted_projects(c,uid)}

    @router.delete('/api/tracker/projects/{pid}')
    def delete_project(pid: str, data: ProjectDelete, request: Request):
        uid = session(request, True)['user_id']
        from app.tracker_lifecycle import schedule
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            deletion = schedule(c,pid,uid,data.version,data.confirmation,now())
            return {'ok':True,'deletion':deletion}

    @router.post('/api/tracker/projects/{pid}/restore')
    def restore_project(pid: str, data: ProjectRestore, request: Request):
        uid = session(request, True)['user_id']
        from app.tracker_lifecycle import restore
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return restore(c,pid,uid,data.version,now())

    @router.post('/api/tracker/projects')
    def create(data: domain.Project, request: Request):
        uid = session(request, True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return domain.create_project(c, data, uid, now())

    @router.get('/api/tracker/projects/{pid}')
    def project(pid: str, request: Request):
        uid = session(request)['user_id']
        with db() as c: return domain.serialize_project(c, domain.access(c, pid, uid), uid, now())

    @router.patch('/api/tracker/projects/{pid}')
    def configure(pid: str, data: ProjectUpdate, request: Request):
        uid = session(request, True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); first=domain.access(c,pid,uid,required='manager')
            domain.lock_owner(c,first['owner_id']);domain.lock_project(c,pid)
            row = domain.access(c, pid, uid, required='manager')
            if row['owner_id']!=first['owner_id']:domain.fail(409,'trackerConflict')
            if row['version'] != data.version: domain.fail(409, 'trackerConflict')
            payload = json.loads(row['payload'])
            fields={'space_id':row['space_id'],'name':data.name,'description':data.description}
            if data.avatar is not None:fields['avatar']=data.avatar
            if data.key is not None:
                for other in c.execute('SELECT payload FROM tracker_projects WHERE owner_id=? AND id!=?',(row['owner_id'],pid)):
                    if json.loads(other[0])['key']==data.key:domain.fail(409,'trackerDuplicateKey')
                fields['key']=data.key
            value = domain.Project.model_validate(payload | fields)
            result = c.execute('UPDATE tracker_projects SET payload=?,updated=?,version=version+1 WHERE id=? AND version=?', (domain.dump(value.model_dump(exclude={'space_id'})), now(), pid, data.version))
            if result.rowcount != 1: domain.fail(409, 'trackerConflict')
            from app.tracker_avatar import metadata
            domain.history(c, pid, None, uid, '', 'project_updated', {'before':metadata(payload),'after':metadata(value.model_dump(exclude={'space_id'}))}, now())
            return domain.serialize_project(c, domain.project_row(c, pid), uid, now())

    @router.get('/api/tracker/projects/{pid}/avatar')
    def avatar(pid: str,request: Request):
        uid=session(request)['user_id']
        with db() as c:row=domain.access(c,pid,uid);body=json.loads(row['payload']).get('avatar','')
        if not body:domain.fail(404,'trackerAvatarNotFound')
        return Response(base64.b64decode(body,validate=True),media_type='image/webp',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @router.post('/api/tracker/projects/{pid}/groups')
    def create_group(pid: str,data: domain.Group,request: Request):
        uid=session(request,True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE');return domain.create_group(c,pid,data,uid,now())

    @router.get('/api/tracker/groups/{gid}')
    def group(gid: str,request: Request):
        uid=session(request)['user_id']
        with db() as c:
            row=domain.group_row(c,gid);domain.access(c,row['project_id'],uid)
            return domain.serialize_group(c,row,True)

    @router.patch('/api/tracker/groups/{gid}')
    def update_group(gid: str,data: domain.GroupUpdate,request: Request):
        uid=session(request,True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE');return domain.update_group(c,gid,data,uid,now())

    @router.get('/api/tracker/projects/{pid}/issues')
    def issues(pid: str, request: Request, after: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=500),group_id: str|None=Query(default=None,max_length=16),include_cancelled: bool = Query(default=False)):
        uid = session(request)['user_id']
        with db() as c: return domain.issue_page(c, pid, uid, now(), after=after, limit=limit,group_id=group_id,include_cancelled=include_cancelled)

    @router.post('/api/tracker/projects/{pid}/issues')
    def create_issue(pid: str, data: domain.Issue, request: Request, include_cancelled: bool = Query(default=False)):
        uid = session(request, True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            domain.access(c,pid,uid,required='editor')
            if data.status=='cancelled' and not include_cancelled:domain.fail(409,'trackerUpdateRequired')
            return domain.create_issue(c, pid, data, uid, now())

    @router.get('/api/tracker/issues/{iid}')
    def issue(iid: str, request: Request, include_cancelled: bool = Query(default=False)):
        uid = session(request)['user_id']
        with db() as c:
            row = domain.issue_row(c, iid); project = domain.access(c, row['project_id'], uid)
            if row['status']=='cancelled' and not include_cancelled:domain.fail(409,'trackerUpdateRequired')
            return domain.serialize_issue(c, row, project, now(), detail=True)

    @router.patch('/api/tracker/issues/{iid}')
    def update(iid: str, data: domain.IssueUpdate, request: Request, include_cancelled: bool = Query(default=False)):
        uid = session(request, True)['user_id']
        try:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                row=domain.issue_row(c,iid);domain.access(c,row['project_id'],uid,required='editor')
                if not include_cancelled and (row['status']=='cancelled' or data.status=='cancelled'):domain.fail(409,'trackerUpdateRequired')
                return domain.update_issue(c, iid, data, uid, now())
        except ValidationError: domain.fail(422, 'trackerInvalidIssue')

    @router.post('/api/tracker/issues/{iid}/comments')
    def comment(iid: str, data: domain.Comment, request: Request):
        uid = session(request, True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return domain.add_comment(c, iid, data, uid, now())

    @router.post('/api/tracker/projects/{pid}/members')
    def invite(pid: str, data: domain.Member, request: Request):
        uid = session(request, True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return add_member(c, pid, data, uid, now())

    @router.get('/api/tracker/projects/{pid}/people')
    def people(pid: str, request: Request):
        uid = session(request)['user_id']
        with db() as c:
            project = domain.access(c, pid, uid, required='manager')
            if project['space_id']:
                ids = [r[0] for r in c.execute('SELECT user_id FROM space_members WHERE space_id=?', (project['space_id'],))]
            else:
                ids = [r['second_id'] if r['first_id'] == uid else r['first_id'] for r in c.execute('SELECT first_id,second_id FROM friendships WHERE first_id=? OR second_id=?', (uid, uid))]
            return [domain.person(c, identity, stamp=now()) for identity in ids if identity != project['owner_id']]

    @router.post('/api/tracker/projects/{pid}/ownership')
    def transfer(pid: str, data: Ownership, request: Request):
        uid = session(request, True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); domain.access(c, pid, uid, required='owner');domain.lock_project(c,pid)
            project = domain.access(c, pid, uid, required='owner')
            if data.actor_id == uid or not domain.role(c, project, data.actor_id): domain.fail(422, 'trackerInvalidMember')
            if c.execute("SELECT 1 FROM tracker_members WHERE project_id=? AND peer_id!='' AND state!='revoked'", (pid,)).fetchone(): domain.fail(409, 'trackerTransferRemote')
            c.execute("INSERT INTO tracker_members(project_id,actor_id,role,state,created) VALUES(?,?,'manager','active',?) ON CONFLICT(project_id,actor_id,peer_id) DO UPDATE SET role='manager',state='active',revision=tracker_members.revision+1", (pid, uid, now()))
            c.execute('UPDATE tracker_projects SET owner_id=?,version=version+1,updated=? WHERE id=?', (data.actor_id, now(), pid))
            c.execute("UPDATE files SET user_id=? WHERE id IN (SELECT id FROM tracker_attachments WHERE issue_id IN (SELECT id FROM tracker_issues WHERE project_id=?)) OR id IN (SELECT id FROM tracker_issues WHERE project_id=?)", (data.actor_id, pid, pid))
            c.quota_recount.update((uid, data.actor_id))
            domain.history(c, pid, None, uid, '', 'ownership_transferred', {'actor_id': data.actor_id}, now())
            return domain.serialize_project(c, domain.project_row(c, pid), uid, now())

    @router.delete('/api/tracker/projects/{pid}/members/{actor_id}')
    def revoke(pid: str, actor_id: str, request: Request, peer_id: str = ''):
        uid = session(request, True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); domain.access(c, pid, uid, required='manager');domain.lock_project(c,pid)
            project = domain.access(c, pid, uid, required='manager')
            if actor_id == project['owner_id'] and not peer_id: domain.fail(422, 'trackerInvalidMember')
            if peer_id and uid != project['owner_id']: domain.fail(403, 'trackerReadOnly')
            c.execute("INSERT INTO tracker_members(project_id,actor_id,peer_id,role,state,created) VALUES(?,?,?,'viewer','revoked',?) ON CONFLICT(project_id,actor_id,peer_id) DO UPDATE SET state='revoked',revision=tracker_members.revision+1", (pid, actor_id, peer_id, now()))
            domain.history(c, pid, None, uid, '', 'member_revoked', {'actor_id': actor_id, 'peer_id': peer_id}, now())
            c.execute('UPDATE tracker_projects SET updated=?,version=version+1 WHERE id=?', (now(), pid))
            return {'ok': True}

    @router.post('/api/tracker/projects/{pid}/accept')
    def accept(pid: str, request: Request):
        uid = session(request, True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); domain.lock_project(c,pid); project = domain.project_row(c, pid)
            if not domain.role(c, project, uid, pending=True): domain.fail(404, 'trackerProjectNotFound')
            c.execute("UPDATE tracker_members SET state='active',revision=revision+1 WHERE project_id=? AND actor_id=? AND peer_id='' AND state='pending'", (pid, uid))
            return domain.serialize_project(c, project, uid, now())

    @router.post('/api/tracker/import-reminders')
    def import_list(data: Import, request: Request):
        uid = session(request, True)['user_id']
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            return domain.import_reminders(c, data.list_id, uid, now(), name=data.name, key=data.key)

    @router.post('/api/tracker/issues/{iid}/attachments')
    async def upload(iid: str, request: Request, name: str = Query(max_length=255)):
        uid = session(request, True)['user_id']
        from app.modules.files import filename
        name = filename(name)
        with db() as c:
            issue = domain.issue_row(c, iid); domain.access(c, issue['project_id'], uid, required='editor')
        limit = 50 * 1024 * 1024
        if request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > limit: domain.fail(413, 'trackerAttachmentLimit')
        identity = secrets.token_hex(16); writer = None
        async with uploads:
            try:
                writer = await blocking(objects.writer, identity)
                async for chunk in request.stream():
                    if writer.size + len(chunk) > limit: domain.fail(413, 'trackerAttachmentLimit')
                    await blocking(writer.write, chunk)
                size, _ = await blocking(writer.finish)
                with db() as c:
                    c.execute('BEGIN IMMEDIATE'); uid = session(request)['user_id']; issue = domain.issue_row(c, iid)
                    return store_attachment(c, issue, uid, '', name, mimetypes.guess_type(name)[0] or 'application/octet-stream', size, identity, now())
            except BaseException:
                if writer:
                    with suppress(Exception): await blocking(writer.abort)
                raise

    @router.get('/api/tracker/issues/{iid}/attachments/{fid}')
    def read_attachment(iid: str, fid: str, request: Request):
        uid = session(request)['user_id']
        with db() as c: row = attachment(c, iid, fid, uid)
        def authorize():
            with db() as c: attachment(c, iid, fid, session(request)['user_id'])
        from app.modules.note_files import file_response
        return file_response(objects, row, request, authorize)

    return router
