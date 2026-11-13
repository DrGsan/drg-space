"""Personal exports copy accessible local projects, never their access grants."""
import json
import secrets
from pydantic import Field
from app import tracker


class Comment(tracker.Comment):
    id: str = Field(pattern=tracker.IDENTITY)
    created: int = Field(ge=0)


class Issue(tracker.Issue):
    id: str = Field(pattern=tracker.IDENTITY)
    number: int = Field(ge=1)
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    completed_at: int | None = Field(default=None, ge=0)
    comments: list[Comment] = Field(default_factory=list, max_length=20000)
    attachments: list[str] = Field(default_factory=list, max_length=100)


class Project(tracker.Project):
    id: str = Field(pattern=tracker.IDENTITY)
    source_shared: bool = False
    created: int = Field(ge=0)
    updated: int = Field(ge=0)
    issues: list[Issue] = Field(default_factory=list, max_length=100000)
    groups: list['Group'] = Field(default_factory=list,max_length=200)


class Group(tracker.Group):
    id: str = Field(pattern=tracker.IDENTITY)
    position: int = Field(ge=0)
    created: int = Field(ge=0)
    updated: int = Field(ge=0)


Project.model_rebuild()


def snapshot(c, uid, stamp):
    result = []
    # Owners can export their retained content, without transferring deletion
    # timers or another owner's suspended project through a personal archive.
    for visible in tracker.projects(c, uid, stamp, include_deleted=True):
        row = tracker.project_row(c, visible['id'])
        project = {'id': row['id'], **json.loads(row['payload']), 'space_id': None,
                   'source_shared': visible['shared'], 'created': row['created'], 'updated': row['updated'], 'issues': [],'groups':[]}
        for group in c.execute('SELECT * FROM tracker_groups WHERE project_id=? ORDER BY position,id',(row['id'],)):
            project['groups'].append(json.loads(group['payload']) | {k:group[k] for k in ('id','position','created','updated')})
        # Only the owner may copy retained deleted content. Other members must
        # not bypass the issue recovery boundary through a personal export.
        clause = '' if row['owner_id'] == uid else ' AND ' + tracker.visible('tracker_issues')
        for issue in c.execute('SELECT * FROM tracker_issues WHERE project_id=?'+clause+' ORDER BY number', (row['id'],)):
            value = json.loads(issue['payload']) | {key: issue[key] for key in ('id','number','parent_id','group_id','status','kind','created','updated','completed_at')}
            value.update(assignee_id='', assignee_peer='')
            value['comments'] = [dict(comment) for comment in c.execute("SELECT id,body,created FROM tracker_comments WHERE issue_id=? AND actor_id=? AND peer_id='' ORDER BY created,id", (issue['id'], uid))]
            value['attachments'] = [r[0] for r in c.execute('SELECT a.id FROM tracker_attachments a JOIN files f ON f.id=a.id WHERE a.issue_id=? AND f.deleted IS NULL ORDER BY a.created,a.id', (issue['id'],))]
            project['issues'].append(value)
        result.append(project)
    return result


def related_files(c, projects):
    ids = set()
    for project in projects:
        for issue in project['issues']:
            if issue['attachments']: ids.add(issue['id']); ids.update(issue['attachments'])
    result = []
    for identity in ids:
        row = c.execute('SELECT id,parent_id,kind,name,mime,size,object_id,created,updated,deleted,trash_batch FROM files WHERE id=?', (identity,)).fetchone()
        if row: result.append(dict(row))
    return result


def validate(projects, files, invalid, note_ids=()):
    seen_projects, seen_issues, seen_comments,seen_groups = set(), set(), set(),set()
    files = {f.id: f for f in files}
    for project in projects:
        if project.id in seen_projects or project.space_id is not None: invalid()
        seen_projects.add(project.id); numbers = set(); by_id = {i.id: i for i in project.issues}
        group_ids={g.id for g in project.groups}
        if len(group_ids)!=len(project.groups) or group_ids.intersection(seen_groups|seen_issues|set(note_ids)):invalid()
        seen_groups.update(group_ids)
        if len(by_id) != len(project.issues): invalid()
        for issue in project.issues:
            if issue.id in seen_issues or issue.id in seen_groups or issue.id in note_ids or issue.number in numbers or issue.assignee_id or issue.assignee_peer: invalid()
            if issue.group_id and issue.group_id not in group_ids:invalid()
            seen_issues.add(issue.id); numbers.add(issue.number)
            if issue.status != 'done' and issue.completed_at is not None: invalid()
            seen = {issue.id}; parent = issue.parent_id
            while parent:
                if parent in seen or parent not in by_id or len(seen) > 20: invalid()
                seen.add(parent); parent = by_id[parent].parent_id
            if issue.parent_id and issue.group_id!=by_id[issue.parent_id].group_id:invalid()
            if len(set(issue.attachments)) != len(issue.attachments): invalid()
            for identity in issue.attachments:
                f = files.get(identity); folder = files.get(issue.id)
                if not f or f.kind != 'file' or f.deleted is not None or f.parent_id != issue.id or not folder or folder.kind != 'folder': invalid()
            for comment in issue.comments:
                if comment.id in seen_comments: invalid()
                seen_comments.add(comment.id)


def restore(c, projects, uid, stamp, project_ids, issue_ids, file_ids, remap,group_ids):
    keys = {json.loads(r[0])['key'] for r in c.execute('SELECT payload FROM tracker_projects WHERE owner_id=?', (uid,))}
    for project in projects:
        key = project.key; suffix = 2
        while key in keys:
            key = project.key[:10-len(str(suffix))] + str(suffix); suffix += 1
        keys.add(key); pid = project_ids[project.id]
        c.execute('INSERT INTO tracker_projects(id,owner_id,payload,created,updated,next_number) VALUES(?,?,?,?,?,?)',
                  (pid, uid, tracker.dump({'name': project.name, 'key': key, 'description': remap(project.description),'avatar':project.avatar}), project.created, project.updated, max((i.number for i in project.issues), default=0)+1))
        for group in project.groups:
            c.execute('INSERT INTO tracker_groups(id,project_id,payload,position,created,updated) VALUES(?,?,?,?,?,?)',(group_ids[group.id],pid,tracker.dump({'name':group.name,'description':remap(group.description)}),group.position,group.created,group.updated))
        for issue in project.issues:
            iid = issue_ids[issue.id]
            value = tracker.Issue.model_validate(issue.model_dump(include=set(tracker.Issue.model_fields)))
            payload = tracker.issue_payload(value); payload['description'] = remap(payload['description']); payload['url'] = remap(payload['url']);payload['merge_request_url']=remap(payload['merge_request_url'])
            c.execute('INSERT INTO tracker_issues(id,project_id,number,parent_id,status,kind,author_id,payload,position,created,updated,completed_at,group_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (iid, pid, issue.number, issue_ids.get(issue.parent_id), issue.status, issue.kind, uid, tracker.dump(payload), issue.number*1024, issue.created, issue.updated, issue.completed_at,group_ids.get(issue.group_id)))
            tracker.history(c, pid, iid, uid, '', 'imported', {}, stamp)
            for comment in issue.comments:
                c.execute('INSERT INTO tracker_comments(id,issue_id,actor_id,body,created) VALUES(?,?,?,?,?)', (secrets.token_urlsafe(12), iid, uid, remap(comment.body), comment.created))
            for identity in issue.attachments:
                c.execute('INSERT INTO tracker_attachments(id,issue_id,actor_id,created) VALUES(?,?,?,?)', (file_ids[identity], iid, uid, stamp))
            if issue.id in file_ids:
                c.execute('UPDATE files SET mime=? WHERE id=? AND kind=?', ('application/x-drg-tracker;project='+pid, file_ids[issue.id], 'folder'))
