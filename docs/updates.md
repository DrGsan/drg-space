# Updating DrG Space

## 5.0.1 reminder refresh

Unchanged background polls preserve reminder controls when the list response includes recoverable deleted lists. Real changes continue to update the workspace. Database schema, storage formats, keys, dependencies and `.env` are unchanged; no migration or new settings are required. Use the existing update procedure below.

## 5.0.0 self-hosted baseline

This release retains the existing application schema and storage formats. No migration, new clean database, or replacement of working keys is required for this baseline.

The working `.env` must not be replaced. New installations generate it from `.env.example`; existing installations retain all current keys, database connection/schema, S3 prefix and storage settings. The optional WEATHER_USER_AGENT identifies your deployment to weather providers; blank uses DrG Space and PUBLIC_URL. Existing configurations remain valid.

## 5.0.2 runtime-name transition

The preview uses Compose project `drg-space`, container `drg-space-app` and image `drg-space:local`. PostgreSQL application/client labels and exported backup filenames also use the product name. Backup format identifiers, JWT signatures/issuers, Redis key namespaces, database/schema names and S3 prefixes remain compatible. No database migration, clean database or secret regeneration is needed.

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
