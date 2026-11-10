# Storage and encryption

## Configuration

Storage encryption is enabled by default. `scripts/init-env.py` creates an independent 32-byte `DATA_ENCRYPTION_KEY`, generates the installation secrets and writes the complete `.env.example` template to a mode-0600 `.env`. It refuses to replace an existing file or symbolic link. Optional integrations remain unconfigured.

With an empty `DATABASE_URL`, the application uses `DATA_DIR/site.sqlite3`. Encrypted SQLite uses SQLCipher, including encrypted journals. A normal SQLite client cannot open it. Invalid or missing encryption configuration stops startup rather than silently selecting unencrypted storage.

For PostgreSQL, create the database and a role with the appropriate schema permissions first:

```dotenv
DATABASE_URL=postgresql://drg:URL_ENCODED_PASSWORD@db.example.com:5432/drg?sslmode=verify-full
DATABASE_SCHEMA=drg_site
```

The application creates its tables and indexes. Connection errors do not fall back to SQLite. New installations use `drg_site`; existing installations must retain their previous schema, including the legacy `drg` default. Changing the setting does not rename or migrate a schema. The optional `compose.postgres.yaml` initializes only a new PostgreSQL volume and keeps the administrative password separate from the application role.

## Protection boundaries

PostgreSQL content fields are encrypted before SQL writes, using authenticated encryption. Searchable account identifiers use a separate deterministic encryption scheme. Equality and length can still be exposed for those identifiers. IDs, relationships, access states, timestamps, sizes and ordering remain database metadata. Application encryption does not encrypt the entire PostgreSQL disk, server WAL or infrastructure snapshots.

Media and file objects are encrypted using keys derived for their storage purpose. Authorization precedes decryption. This is server-side encryption: a server administrator controlling the running process and its keys can access content.

## Local and S3 objects

`FILE_STORAGE=local` keeps file objects in the data volume. `FILE_STORAGE=s3` uses the configured bucket, endpoint and prefix for file objects. Avatars, gallery images and other media still require the local `/data` volume. PostgreSQL and S3 do not make that volume disposable.

Storage markers bind the volume, database identity, encryption format and object backend. Preserve these markers, keys, schema, S3 prefix and the existing Compose project name during updates. Do not attach one installation's storage to another independent database.

## Keys and backups

Preserve both `APP_SECRET` and `DATA_ENCRYPTION_KEY`. Losing the storage key makes encrypted raw storage unreadable. Changing the encryption switch, key, database or object backend requires a separately validated migration; editing `.env` is not a conversion procedure.

Portable `.drgbackup` and `.drgpersonal` archives use a separate password and remain encrypted even when live storage encryption is disabled. They are logical exports, not raw copies of SQLCipher pages or PostgreSQL files. See [backup and restore](admin-backups.md) and [personal transfer](personal-data.md).
