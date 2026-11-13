# Data retention

Retention is a property of each data type and configured policy. A sharing deadline controls access; it does not automatically delete the underlying file.

| Content | Retention behavior |
| --- | --- |
| Ordinary notes, files, projects and reminders | Retained until explicitly deleted or covered by an enabled retention policy |
| Completed or cancelled project-tracker issues | Retained indefinitely; status changes never start a deletion clock |
| Explicitly deleted tracker issue branches | Seven days to restore the deletion batch; enclosing project deletion pauses the remaining time |
| Explicitly deleted tracker projects | Thirty days to restore; final cleanup removes tasks, history and attachments, including paused issue deletions |
| Deleted notes | Kept in the note trash for its retention period; the current default is 30 days |
| Files scheduled for disposal | Moved to file trash after the confirmed policy becomes due; file trash lasts seven days |
| Temporary messages, expense records and access grants | Follow their module-specific expiry settings |
| Secret-vault items | Permanent removal after confirmation, without ordinary trash |
| Downloaded backups and exports | Managed by the person storing those external copies |

The interface shows applicable deadlines and confirmation requirements. New files added to a previously shared folder are not silently included in an earlier confirmed disposal set. Moving a file does not cancel its existing policy. A file waiting for all relevant links to expire remains available while a qualifying link is active.

Deadlines use absolute timestamps. After downtime, maintenance processes overdue work when the service resumes. Expired access is rejected by request-time checks even if the expired record has not yet been physically removed. Browser views refresh from server state rather than changing deadlines locally.

Background cleanup also handles expired sessions, temporary archives, stale grants and unreferenced objects. Failed object deletion remains queued for retry. Active operations and valid objects are preserved.

Application cleanup does not erase independent infrastructure snapshots, downloaded archives, external Telegram archives or storage copies managed outside the application. See [sharing](sharing.md), [backup](admin-backups.md) and [secret vault](secret-vault.md).
