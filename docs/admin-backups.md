# Administration, backup and restore

## Accounts and roles

The first configured account is the site owner. Site roles (`owner`, `admin`, `member`) are separate from shared-space and page roles. The owner controls administrative roles; role changes revoke affected sessions and management grants. Administrative accounts require 2FA.

Management actions require a server-verified password unlock. The grant expires after 15 minutes of inactivity; background polling does not extend it. Backup encryption has its own password. Administrative privileges do not automatically grant access to every user's private content through the interface.

The account table supports search, role filters, quotas and account details. Account deletion uses a pending-deletion period and explicit confirmation. Owner-only actions and restrictions on deleting administrative accounts are enforced on the server.

## Portable full backups

Use the backup action in Management and choose a strong archive password. Store the downloaded file and password separately. The backup includes ordinary accounts, application settings, notes, note databases, reminders, files, collaboration data and supported local module records. External Family Photo archive contents remain in the external service.

Secret-vault content, keys and grants are excluded. The interface reports excluded content before export. See [secret vault](secret-vault.md) before relying on a full backup for recovery.

Working `.env` values, source code, signing material and server TLS certificates are not exported. Active login sessions and temporary administrative grants are not restored. A full restore requires users to sign in again.

## Restore procedure

1. Retain a current backup if the destination data will still be needed.
2. Confirm that the archive's schema fingerprint is compatible with the destination version.
3. Open Restore, select the encrypted archive, enter its password and type the required replacement confirmation.
4. Wait for validation and replacement to finish, then sign in again and inspect important content and permissions.

Restore validates the password, authenticated archive structure, paths, size limits, database integrity, relationships, content and media checksums before applying data. It replaces the logical contents of the current schema; it does not migrate an incompatible schema. Supported workers coordinate through the storage gate. Do not modify storage manually during the operation.

An invalid archive leaves the current data in place. After a successful replacement, stale unreferenced objects are removed through normal cleanup. Deletion deadlines retain their original timestamps.

For an offline export, stop every application process sharing the storage and run `scripts/backup.py --offline --output /path/to/site.drgbackup` with the installation's configuration. Never run the offline tool alongside active writers. See [storage](data-storage.md) and [worker deployment](production-workers.md).
