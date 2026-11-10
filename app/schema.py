"""Canonical fresh-install schema shared by engines and portable archives."""
CORE_SCHEMA = """

CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, login TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, totp TEXT, last_counter INTEGER DEFAULT -1, settings TEXT NOT NULL DEFAULT '{}', created INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, csrf TEXT NOT NULL, stage TEXT NOT NULL, expires INTEGER NOT NULL, created INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS refresh_tokens(token TEXT PRIMARY KEY, session_token TEXT NOT NULL REFERENCES sessions(token) ON DELETE CASCADE, expires INTEGER NOT NULL, created INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS refresh_session_idx ON refresh_tokens(session_token);
    CREATE TABLE IF NOT EXISTS auth_failures(key TEXT NOT NULL, ip TEXT NOT NULL, stamp INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS auth_failure_idx ON auth_failures(key, stamp);
    CREATE TABLE IF NOT EXISTS security_bans(key TEXT PRIMARY KEY, kind TEXT NOT NULL, ip TEXT NOT NULL, action TEXT NOT NULL, strikes INTEGER NOT NULL, until INTEGER NOT NULL, updated INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS security_events(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL UNIQUE,event TEXT NOT NULL,action TEXT NOT NULL,outcome TEXT NOT NULL,severity TEXT NOT NULL,ip TEXT NOT NULL,subject TEXT NOT NULL,subject_key TEXT NOT NULL,user_id TEXT NOT NULL,route TEXT NOT NULL,agent TEXT NOT NULL,details TEXT NOT NULL,first_seen INTEGER NOT NULL,last_seen INTEGER NOT NULL,count INTEGER NOT NULL,incident_id TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS security_event_time_idx ON security_events(last_seen,id);
    CREATE INDEX IF NOT EXISTS security_event_ip_idx ON security_events(ip,last_seen);
    CREATE INDEX IF NOT EXISTS security_event_subject_idx ON security_events(subject_key,last_seen);
    CREATE INDEX IF NOT EXISTS security_event_kind_idx ON security_events(event,outcome,last_seen);
    CREATE INDEX IF NOT EXISTS security_event_incident_idx ON security_events(incident_id,last_seen);
    CREATE TABLE IF NOT EXISTS security_incidents(id TEXT PRIMARY KEY,correlation_key TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,title TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL,ip TEXT NOT NULL,subject TEXT NOT NULL,subject_key TEXT NOT NULL,user_id TEXT NOT NULL,first_seen INTEGER NOT NULL,last_seen INTEGER NOT NULL,event_count INTEGER NOT NULL,ip_count INTEGER NOT NULL,notified_at INTEGER NOT NULL DEFAULT 0,notified_count INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS security_incident_time_idx ON security_incidents(last_seen,id);
    CREATE INDEX IF NOT EXISTS security_incident_status_idx ON security_incidents(status,last_seen);
    CREATE TABLE IF NOT EXISTS recovery_codes(user_id TEXT REFERENCES users(id) ON DELETE CASCADE, code TEXT NOT NULL, PRIMARY KEY(user_id,code));
    CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), space_id TEXT REFERENCES spaces(id) ON DELETE CASCADE, title TEXT NOT NULL, body TEXT NOT NULL, created INTEGER NOT NULL, updated INTEGER NOT NULL, blocks TEXT, parent_id TEXT REFERENCES notes(id) ON DELETE SET NULL, icon TEXT NOT NULL DEFAULT '📄', cover TEXT NOT NULL DEFAULT '', favorite INTEGER NOT NULL DEFAULT 0, locked INTEGER NOT NULL DEFAULT 0, full_width INTEGER NOT NULL DEFAULT 0, deleted INTEGER, trash_batch TEXT, position INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1);
    CREATE INDEX IF NOT EXISTS notes_tree_idx ON notes(user_id,parent_id,deleted);
    CREATE TABLE IF NOT EXISTS note_revisions(id TEXT PRIMARY KEY,note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,title TEXT NOT NULL,body TEXT NOT NULL,blocks TEXT,created INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS shares(token TEXT PRIMARY KEY, note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE, mode TEXT NOT NULL, password TEXT, allowed TEXT NOT NULL DEFAULT '[]', expires INTEGER, revoked INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, permission TEXT NOT NULL DEFAULT 'view' CHECK(permission IN ('view','edit')));
    CREATE TABLE IF NOT EXISTS share_sessions(token TEXT PRIMARY KEY, share_token TEXT NOT NULL REFERENCES shares(token) ON DELETE CASCADE, expires INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,mime TEXT NOT NULL,size INTEGER NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,frames INTEGER NOT NULL,created INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL, day TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS invites(token TEXT PRIMARY KEY, login TEXT, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS throttle(key TEXT NOT NULL, stamp INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS throttle_idx ON throttle(key, stamp);
"""

from app.modules.social import SCHEMA as SOCIAL_SCHEMA
from app.modules.notion import SCHEMA as NOTION_SCHEMA
from app.modules.tasks import TASKS_SCHEMA
EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS password_resets(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,expires INTEGER NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS note_favorites(note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,created INTEGER NOT NULL,PRIMARY KEY(note_id,user_id));
CREATE TABLE IF NOT EXISTS note_unlocks(note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,session_token TEXT NOT NULL REFERENCES sessions(token) ON DELETE CASCADE,expires INTEGER NOT NULL,PRIMARY KEY(note_id,session_token));
CREATE TABLE IF NOT EXISTS authenticators(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,label TEXT NOT NULL,issuer TEXT NOT NULL,secret TEXT NOT NULL,algorithm TEXT NOT NULL,digits INTEGER NOT NULL,period INTEGER NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS expense_events(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,title TEXT NOT NULL,payload TEXT NOT NULL,created INTEGER NOT NULL,ends INTEGER NOT NULL,purge_at INTEGER NOT NULL,version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS expense_entries(id TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES expense_events(id) ON DELETE CASCADE,payload TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS expense_shares(token TEXT PRIMARY KEY,event_id TEXT NOT NULL REFERENCES expense_events(id) ON DELETE CASCADE,expires INTEGER NOT NULL,created INTEGER NOT NULL);
"""
COMMUNITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,kind TEXT NOT NULL,payload TEXT NOT NULL,read INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS notification_owner_idx ON notifications(user_id,id);
CREATE INDEX IF NOT EXISTS notification_expiry_idx ON notifications(created);
CREATE TABLE IF NOT EXISTS chat_reads(space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,message_id INTEGER NOT NULL,PRIMARY KEY(space_id,user_id));
CREATE TABLE IF NOT EXISTS session_presence(session_token TEXT PRIMARY KEY REFERENCES sessions(token) ON DELETE CASCADE,last_seen INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS user_presence(user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,last_seen INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS session_info(session_token TEXT PRIMARY KEY REFERENCES sessions(token) ON DELETE CASCADE,agent TEXT NOT NULL,ip TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blob_gc(id TEXT NOT NULL,kind TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(id,kind));
CREATE TABLE IF NOT EXISTS note_assets(id TEXT PRIMARY KEY REFERENCES media(id) ON DELETE CASCADE,note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,kind TEXT NOT NULL,payload TEXT NOT NULL DEFAULT '{}',created INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS note_asset_page_idx ON note_assets(note_id);
CREATE TABLE IF NOT EXISTS user_limits(user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,limit_bytes INTEGER DEFAULT 1073741824,used_bytes INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,parent_id TEXT REFERENCES files(id) ON DELETE CASCADE,kind TEXT NOT NULL CHECK(kind IN ('file','folder')),name TEXT NOT NULL,mime TEXT NOT NULL DEFAULT 'application/octet-stream',size INTEGER NOT NULL DEFAULT 0,object_id TEXT UNIQUE,version INTEGER NOT NULL DEFAULT 1,created INTEGER NOT NULL,updated INTEGER NOT NULL,deleted INTEGER,trash_batch TEXT);
CREATE INDEX IF NOT EXISTS file_owner_idx ON files(user_id,parent_id);
CREATE INDEX IF NOT EXISTS file_trash_idx ON files(deleted);
CREATE TABLE IF NOT EXISTS file_shares(token TEXT PRIMARY KEY,file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,mode TEXT NOT NULL,password TEXT,allowed TEXT NOT NULL DEFAULT '[]',expires INTEGER,revoked INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL,permission TEXT NOT NULL DEFAULT 'view' CHECK(permission IN ('view','edit')));
CREATE TABLE IF NOT EXISTS file_share_sessions(token TEXT PRIMARY KEY,share_token TEXT NOT NULL REFERENCES file_shares(token) ON DELETE CASCADE,expires INTEGER NOT NULL);
"""
from app.web_push import SCHEMA as PUSH_SCHEMA
from app.modules.calls import SCHEMA as CALLS_SCHEMA
from app.modules.guest_calls import SCHEMA as GUEST_CALLS_SCHEMA
from app.federation.schema import FEDERATION_SCHEMA
from app.tracker_schema import TRACKER_SCHEMA
SCHEMA = CALLS_SCHEMA + GUEST_CALLS_SCHEMA + COMMUNITY_SCHEMA + CORE_SCHEMA + SOCIAL_SCHEMA + NOTION_SCHEMA + EXTRA_SCHEMA + TASKS_SCHEMA + PUSH_SCHEMA + FEDERATION_SCHEMA + TRACKER_SCHEMA

from app.notion_sync.schema import SCHEMA as NOTION_SYNC_SCHEMA
SCHEMA += NOTION_SYNC_SCHEMA
from app.chat_recordings import SCHEMA as CHAT_RECORDING_SCHEMA
SCHEMA += CHAT_RECORDING_SCHEMA

from app.file_sharing import SCHEMA as FILE_SHARING_SCHEMA
SCHEMA += FILE_SHARING_SCHEMA
