"""Project work is durable and deliberately separate from reminder retention."""

TRACKER_SCHEMA = '''
CREATE TABLE IF NOT EXISTS tracker_projects(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,space_id TEXT REFERENCES spaces(id) ON DELETE CASCADE,payload TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL,version INTEGER NOT NULL DEFAULT 1,next_number INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS tracker_project_owner_idx ON tracker_projects(owner_id,space_id);
CREATE INDEX IF NOT EXISTS tracker_project_space_idx ON tracker_projects(space_id);
CREATE TABLE IF NOT EXISTS tracker_groups(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES tracker_projects(id) ON DELETE CASCADE,payload TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL,updated INTEGER NOT NULL,version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS tracker_group_project_idx ON tracker_groups(project_id,position,id);
CREATE TABLE IF NOT EXISTS tracker_members(project_id TEXT NOT NULL REFERENCES tracker_projects(id) ON DELETE CASCADE,actor_id TEXT NOT NULL,peer_id TEXT NOT NULL DEFAULT '',role TEXT NOT NULL CHECK(role IN ('viewer','editor','manager')),state TEXT NOT NULL CHECK(state IN ('pending','active','revoked')),revision INTEGER NOT NULL DEFAULT 1,created INTEGER NOT NULL,PRIMARY KEY(project_id,actor_id,peer_id));
CREATE INDEX IF NOT EXISTS tracker_member_actor_idx ON tracker_members(actor_id,peer_id,state);
CREATE TABLE IF NOT EXISTS tracker_issues(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES tracker_projects(id) ON DELETE CASCADE,number INTEGER NOT NULL,parent_id TEXT REFERENCES tracker_issues(id) ON DELETE SET NULL,status TEXT NOT NULL CHECK(status IN ('todo','work','review','done','cancelled')),kind TEXT NOT NULL CHECK(kind IN ('task','bug','improvement','epic')),assignee_id TEXT NOT NULL DEFAULT '',assignee_peer TEXT NOT NULL DEFAULT '',author_id TEXT NOT NULL,author_peer TEXT NOT NULL DEFAULT '',payload TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL,updated INTEGER NOT NULL,completed_at INTEGER,version INTEGER NOT NULL DEFAULT 1,group_id TEXT REFERENCES tracker_groups(id) ON DELETE SET NULL,UNIQUE(project_id,number));
CREATE INDEX IF NOT EXISTS tracker_issue_group_idx ON tracker_issues(project_id,group_id,status,number);
CREATE INDEX IF NOT EXISTS tracker_issue_project_idx ON tracker_issues(project_id,status,position,id);
CREATE INDEX IF NOT EXISTS tracker_issue_parent_idx ON tracker_issues(parent_id);
CREATE INDEX IF NOT EXISTS tracker_issue_assignee_idx ON tracker_issues(assignee_id,assignee_peer,status);
CREATE TABLE IF NOT EXISTS tracker_comments(id TEXT PRIMARY KEY,issue_id TEXT NOT NULL REFERENCES tracker_issues(id) ON DELETE CASCADE,actor_id TEXT NOT NULL,peer_id TEXT NOT NULL DEFAULT '',body TEXT NOT NULL,created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS tracker_comment_issue_idx ON tracker_comments(issue_id,created,id);
CREATE TABLE IF NOT EXISTS tracker_history(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES tracker_projects(id) ON DELETE CASCADE,issue_id TEXT REFERENCES tracker_issues(id) ON DELETE CASCADE,actor_id TEXT NOT NULL,peer_id TEXT NOT NULL DEFAULT '',event TEXT NOT NULL,payload TEXT NOT NULL,created INTEGER NOT NULL,group_id TEXT REFERENCES tracker_groups(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS tracker_history_issue_idx ON tracker_history(issue_id,created,id);
CREATE TABLE IF NOT EXISTS tracker_attachments(id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,issue_id TEXT NOT NULL REFERENCES tracker_issues(id) ON DELETE CASCADE,actor_id TEXT NOT NULL,peer_id TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS tracker_attachment_issue_idx ON tracker_attachments(issue_id);
CREATE TABLE IF NOT EXISTS tracker_imports(list_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES tracker_projects(id) ON DELETE CASCADE,owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,payload TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tracker_uploads(id TEXT PRIMARY KEY,issue_id TEXT NOT NULL REFERENCES tracker_issues(id) ON DELETE CASCADE,actor_id TEXT NOT NULL,peer_id TEXT NOT NULL,payload TEXT NOT NULL,size INTEGER NOT NULL,received INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tracker_upload_chunks(upload_id TEXT NOT NULL REFERENCES tracker_uploads(id) ON DELETE CASCADE,position INTEGER NOT NULL,body BLOB NOT NULL,PRIMARY KEY(upload_id,position));
'''

TRACKER_TABLES = ('tracker_projects', 'tracker_groups', 'tracker_members', 'tracker_issues',
                  'tracker_comments', 'tracker_history', 'tracker_attachments', 'tracker_imports', 'tracker_uploads', 'tracker_upload_chunks')
