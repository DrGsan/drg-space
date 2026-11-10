"""Persistent identities/queues, independent of the canonical note document."""
TABLES=('notion_connections','notion_oauth_states','notion_roots','notion_pages','notion_jobs','notion_events','notion_staging','notion_budgets')
SCHEMA="""
CREATE TABLE IF NOT EXISTS notion_connections(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,kind TEXT NOT NULL,workspace_id TEXT NOT NULL,bot_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',cooldown INTEGER NOT NULL DEFAULT 0,next_scan INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL,updated INTEGER NOT NULL,UNIQUE(user_id,kind,workspace_id));
CREATE INDEX IF NOT EXISTS notion_connection_owner_idx ON notion_connections(user_id,kind,status);
CREATE INDEX IF NOT EXISTS notion_connection_workspace_idx ON notion_connections(workspace_id,kind,status);
CREATE INDEX IF NOT EXISTS notion_connection_scan_idx ON notion_connections(kind,status,next_scan,cooldown);
CREATE TABLE IF NOT EXISTS notion_oauth_states(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,session_token TEXT NOT NULL REFERENCES sessions(token) ON DELETE CASCADE,kind TEXT NOT NULL,platform TEXT NOT NULL DEFAULT 'web',expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS notion_roots(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,connection_id TEXT NOT NULL REFERENCES notion_connections(id) ON DELETE CASCADE,notion_id TEXT NOT NULL,note_id TEXT REFERENCES notes(id) ON DELETE SET NULL,mode TEXT NOT NULL DEFAULT 'pull',status TEXT NOT NULL DEFAULT 'active',payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created INTEGER NOT NULL,updated INTEGER NOT NULL,UNIQUE(connection_id,notion_id));
CREATE INDEX IF NOT EXISTS notion_root_connection_idx ON notion_roots(connection_id,status);
CREATE TABLE IF NOT EXISTS notion_pages(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,root_id TEXT NOT NULL REFERENCES notion_roots(id) ON DELETE CASCADE,notion_id TEXT NOT NULL,note_id TEXT NOT NULL UNIQUE REFERENCES notes(id) ON DELETE CASCADE,parent_id TEXT,status TEXT NOT NULL DEFAULT 'active',writable INTEGER NOT NULL DEFAULT 0,note_version INTEGER NOT NULL DEFAULT 1,remote_stamp TEXT NOT NULL DEFAULT '',payload TEXT NOT NULL,updated INTEGER NOT NULL,UNIQUE(root_id,notion_id));
CREATE INDEX IF NOT EXISTS notion_page_remote_idx ON notion_pages(notion_id,root_id);
CREATE TABLE IF NOT EXISTS notion_jobs(id TEXT PRIMARY KEY,root_id TEXT NOT NULL REFERENCES notion_roots(id) ON DELETE CASCADE,direction TEXT NOT NULL,entity_id TEXT NOT NULL,due INTEGER NOT NULL,lease_until INTEGER NOT NULL DEFAULT 0,lease_owner TEXT NOT NULL DEFAULT '',generation INTEGER NOT NULL DEFAULT 1,attempts INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL,updated INTEGER NOT NULL,UNIQUE(root_id,direction,entity_id));
CREATE INDEX IF NOT EXISTS notion_job_due_idx ON notion_jobs(due,lease_until);
CREATE TABLE IF NOT EXISTS notion_staging(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,root_id TEXT NOT NULL REFERENCES notion_roots(id) ON DELETE CASCADE,notion_id TEXT NOT NULL,parent_id TEXT,payload TEXT NOT NULL,updated INTEGER NOT NULL,UNIQUE(root_id,notion_id));
CREATE TABLE IF NOT EXISTS notion_events(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,entity_id TEXT NOT NULL DEFAULT '',pending INTEGER NOT NULL DEFAULT 0,due INTEGER NOT NULL DEFAULT 0,payload TEXT NOT NULL,created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS notion_event_entity_idx ON notion_events(workspace_id,entity_id,created);
CREATE INDEX IF NOT EXISTS notion_event_pending_idx ON notion_events(pending,due,created);
CREATE TABLE IF NOT EXISTS notion_budgets(workspace_id TEXT PRIMARY KEY,next_request BIGINT NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS notion_event_expiry_idx ON notion_events(created);
"""
