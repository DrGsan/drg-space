# Updating DrG Space

## 5.1.1 preview: note downloads and release language

File blocks expose a compact action menu beside the filename, with Download available in read-only and synchronized notes. It uses the existing authorized file route, session refresh and native browser download; filenames and stored bytes are preserved. Missing stored objects are rejected by the access preflight. Existing Notion files do not need to be reimported.

Release notes use the account language, or the requested language for a system-language account. The English CHANGELOG remains the source document; both supported UI catalogs are required for the current release. Web builds and server startup reject a release with missing translations or English text that differs from the changelog. New releases must add matching entries in locales/ru/release_notes.json and locales/en/release_notes.json.

No schema, storage-format, key, dependency or environment changes are required when upgrading from 5.1.0. Preserve the existing Compose files and volumes and use the update commands below; no migration is needed.

## 5.1.0: cancellation and release availability

This release expands only the tracker status CHECK constraint to include `cancelled`. Task columns, encryption, object paths, keys, `.env`, dependencies and volumes are unchanged. No clean database is required. Do not start this code against an unmodified previous schema: startup deliberately refuses the previous storage fingerprint.

Before upgrading an existing installation, stop all application writers. Apply a reviewed, one-off offline migration to a disposable copy first, test rollback, and verify every table's rows and foreign keys. Expand `tracker_issues.status` from `('todo','work','review','done')` to `('todo','work','review','done','cancelled')`; preserve every column, index and referencing table. PostgreSQL can replace its status CHECK in a transaction. SQLite/SQLCipher requires rebuilding only this table in a transaction with foreign keys disabled for the rebuild, followed by integrity and foreign-key checks. Update only the `schema` storage metadata value to `app.database.SCHEMA_HASH` after successful verification. Use the existing encryption key and mode throughout. Apply the verified procedure to the stopped installation before using the existing Compose update commands below.

Installations already migrated for 5.1.0-rc.1 or 5.1.0-rc.2 use the same schema; promoting that preview to 5.1.0 requires no further migration.

Rollback of the constraint is safe only before cancelled rows are created; the reviewed migration must refuse a downgrade while any cancelled task exists. Four-state full backups are accepted only when their entire schema matches the known previous fingerprint; unrelated schema changes remain rejected. New backups containing cancelled work require this version or newer.

Cancellation does not change child statuses, remove content or mark work completed. Editors can cancel and reopen tasks; optional reasons remain in history after reopening. Cancelled children are counted separately and excluded from an epic's completion denominator.

Updated web and native clients request `include_cancelled=true` for task reads/writes only when the project advertises `cancellation_supported`. Older HTTP and federation clients receive a consistent four-state page without cancelled rows. Direct reads/edits of a cached cancelled task require an updated client. Active children of cancelled parents remain available. No personal token is needed for the public GitHub release hint: the server caches a confirmed stable release for six hours, retries failed checks after fifteen minutes, and hides expired or unavailable results. Only owners and administrators can use this endpoint; it never installs an update.

The gallery and its access settings use Media naming in Russian and English. Only display text and the navigation icon change; `family_photos`, service URLs, API paths and existing settings remain compatible.

## 5.0.1 reminder refresh

Unchanged background polls preserve reminder controls when the list response includes recoverable deleted lists. Real changes continue to update the workspace. Database schema, storage formats, keys, dependencies and `.env` are unchanged; no migration or new settings are required. Use the existing update procedure below.

## 5.0.0 self-hosted baseline

This release retains the existing application schema and storage formats. No migration, new clean database, or replacement of working keys is required for this baseline.

The working `.env` must not be replaced. New installations generate it from `.env.example`; existing installations retain all current keys, database connection/schema, S3 prefix and storage settings. The optional WEATHER_USER_AGENT identifies your deployment to weather providers; blank uses DrG Space and PUBLIC_URL. Existing configurations remain valid.

## 5.0.2 runtime-name transition

This release uses Compose project `drg-space`, container `drg-space-app` and image `drg-space:local`. PostgreSQL application/client labels and exported backup filenames also use the product name. Backup format identifiers, JWT signatures/issuers, Redis key namespaces, database/schema names and S3 prefixes remain compatible. No database migration, clean database or secret regeneration is needed.

**An existing installation must pin its volumes before changing the Compose project.** Preserve the working `.env`, every override, port binding and optional backend. Stop the existing deployment before starting the new one; never run two application instances against a SQLite volume.

Inspect the old containers and all selected Compose overlays. For an installation previously named `drg-site`, record the exact `/data` volume:

```sh
docker inspect drg-site-app --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}'
```

Add these deployment settings to the existing `.env`, substituting the actual inspected name:

```dotenv
DRG_SPACE_EXTERNAL_VOLUMES=true
DRG_SPACE_DATA_VOLUME=drg-site_drg-data
```

If the existing installation uses the bundled PostgreSQL or Caddy, also set `DRG_SPACE_POSTGRES_VOLUME`, `DRG_SPACE_CADDY_DATA_VOLUME` and `DRG_SPACE_CADDY_CONFIG_VOLUME` to their actual old volumes for each selected overlay. External mode requires the volumes to exist and refuses to create empty replacements. Leave database URLs, schema names, S3 prefixes, keys and passwords unchanged. Bind mounts in custom overlays must remain bound to the same host paths.

From the updated checkout, with the installation's exact overlays:

```sh
docker compose -p drg-space -f compose.yaml -f compose.override.yaml config -q
docker compose -p drg-space -f compose.yaml -f compose.override.yaml build app
docker compose -p drg-site -f compose.yaml -f compose.override.yaml stop app
docker compose -p drg-site -f compose.yaml -f compose.override.yaml rm -f app
docker compose -p drg-space -f compose.yaml -f compose.override.yaml up -d --no-build --wait app
```

The stop/remove commands remove containers, without deleting volumes. Include every originally used overlay, and stop/remove any originally bundled database/proxy services as part of the same controlled handover before starting their replacements. For custom installations use the actual previous project name. Never use `down -v`.

If DrG Media joins the application network, migrate it in the same maintenance window: set its `DRG_SPACE_NETWORK=drg-space_default`, preserve its archive volume and stop its old worker before starting the new worker. The Media update guide describes this companion transition. Reverse proxies using published host ports keep the same addresses; proxies or monitoring using Docker names must be updated. If TRUSTED_PROXIES contains the old Docker gateway as a /32 entry, inspect the new network gateway and replace only that entry before completing the handover. Keep the other trusted proxy addresses unchanged; do not widen trust to a whole subnet. Recreate the app after an environment change.

Retain previous images and the recorded old configuration until health, authentication, storage access and integrations are confirmed. A rollback stops the new containers and starts the previous images under the old project, using the same data, keys and overlays. Reuse the preserved volume names; do not copy/reset a working database merely to rename a service. Remove old networks only after confirming no containers still use them; data volumes keep their original physical names.

## Subsequent updates

After the transition, fetch the approved release and use the new project with all existing overlays:

```sh
git fetch origin
git switch main
git pull --ff-only origin main
docker compose -p drg-space -f compose.yaml -f compose.override.yaml config -q
docker compose -p drg-space -f compose.yaml -f compose.override.yaml up -d --build --wait app
docker compose -p drg-space -f compose.yaml -f compose.override.yaml ps
```

Use the installation's actual remote and overlays. External-volume settings remain part of its working `.env`; never replace it with the example. New installations use project-scoped managed volumes unless explicitly configured otherwise.

## Backups and previous versions

Restore compatibility requires the same schema fingerprint. Keep the original encryption keys when recovering raw storage. Portable encrypted application backups have their own password. See [backup and restore](admin-backups.md).

Immutable public web resources from previous deployed builds are retained for up to seven days, within a 256 MiB budget, for open tabs. This cache is distinct from Docker build cache and must not be manually removed as routine build cleanup.
