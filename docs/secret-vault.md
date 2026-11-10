# Secret vault

The secret vault provides an additional PIN and access boundary for personal notes and files. An account needs 2FA before enabling it. The unlocked state is temporary and can be locked explicitly. After losing access or returning from the background, the client requires the applicable unlock flow again.

Content has separate encryption and is excluded from ordinary search, sharing, widgets and version history. This is server-side protection, not end-to-end encryption: the running server and its keys remain trusted.

## Moving content

Supported personal pages and file folders can be moved with their children and attachments. Shared content, active publications and conflicting attachment references must be resolved first. Copies are validated before the source is removed. Moving content out returns it to ordinary history, trash and backup rules; old publication permissions are not silently restored.

## Deletion and recovery

Deleting vault items permanently removes their descendants and attachments without ordinary trash. Removing the entire vault requires the account password and fresh 2FA or a recovery code. While a vault or its pending cleanup exists, operations that would reset or bypass its 2FA protection are restricted.

Portable full and personal backups exclude vault content, keys and grants. The export interface reports exclusions. Move any content that must be included into ordinary storage before export. Independent infrastructure snapshots may still contain older encrypted data and keys.

Local and S3 backends use separate vault namespaces. Object cleanup retries failures and checks storage versions where applicable. Do not remove storage markers or treat failed cleanup as successful erasure. `scripts/storage_audit.py` reports structural and object-storage issues without dumping secret content. See [backup](admin-backups.md) and [retention](data-retention.md).
