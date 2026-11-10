# Updating DrG Space

## 5.0.1 reminder refresh

Unchanged background polls preserve reminder controls when the list response includes recoverable deleted lists. Real changes continue to update the workspace. Database schema, storage formats, keys, dependencies and `.env` are unchanged; no migration or new settings are required. Use the existing update procedure below.

## 5.0.0 self-hosted baseline

This release retains the existing application schema and storage formats. No migration, new clean database, or replacement of working keys is required for this baseline.

The working `.env` must not be replaced. New installations generate it from `.env.example`; existing installations retain all current keys, database connection/schema, S3 prefix and storage settings. The optional WEATHER_USER_AGENT identifies your deployment to weather providers; blank uses DrG Space and PUBLIC_URL. Existing configurations remain valid.

## Updating an existing deployment

Before changing source repositories, confirm its currently deployed revision and schema compatibility. Keep the existing Compose project name and the exact overlay files. The following is an example for an installation whose original project name is `drg-site` and which uses an existing override:

```sh
git fetch origin
git switch main
git pull --ff-only origin main
docker compose -p drg-site -f compose.yaml -f compose.override.yaml config -q
docker compose -p drg-site -f compose.yaml -f compose.override.yaml up -d --build app
docker compose -p drg-site -f compose.yaml -f compose.override.yaml ps
```

Use your actual remote, project name and overlay files. Moving the source checkout into a differently named directory without preserving `-p` can create a new empty volume. Never remove existing volumes to make an update succeed.

## Backups and previous versions

Restore compatibility requires the same schema fingerprint. Keep the original encryption keys when recovering raw storage. Portable encrypted application backups have their own password. See [backup and restore](admin-backups.md).

Immutable public web resources from previous deployed builds are retained for up to seven days, within a 256 MiB budget, for open tabs. This cache is distinct from Docker build cache and must not be manually removed as routine build cleanup.
