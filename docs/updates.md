# Updating DrG Space

## 5.3.2

Rebuild Space with the existing Compose files. Update Media to 1.14.8 for verified Telegram sender names on archived messages. The release uses the existing call settings and storage schema. No environment change or migration is required.

```sh
docker compose up -d --build --no-deps app
```

## 5.3.1

The notes catalog recovers after a short scheduled storage lock. The browser retries reads and keeps an existing list visible. Rebuild Space with the current Compose configuration and volumes. No database migration, environment change or storage conversion is required.

```sh
docker compose up -d --build --no-deps app
```

## 5.3.0

The optional Notes assistant needs `OPENAI_API_KEY` in the application environment and an account selected in Administration. `OPENAI_NOTES_MODEL` can override GPT-6.1 Terra, which defaults to medium reasoning. Existing installations can leave both variables unset. No database migration, storage-format change, or data-volume change is required.

```sh
docker compose up -d --build --no-deps app
```

## 5.2.28

Code blocks have a compact copy button and add searchable language selection and line wrapping to the block menu. Older code blocks retain their default wrapping. No database migration, storage-format change, or environment change is required. Rebuild Space with the same Compose files and data volumes:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.27

Unread counts recover after temporary storage failures. A federated local call action is retried once when maintenance prevented it from starting. No database migration, storage-format change, or environment change is required. Rebuild the application using the same Compose files and data volumes:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.26

Family groups remain visible when Media can return the group catalog but message previews are temporarily unavailable. If the whole Media service is unreachable, Space shows a status and refreshes automatically. Update Space first, then Media 1.14.7, which can return the catalog when object storage is unavailable.

Rebuild with the existing Compose files, environment, encryption keys and volumes. No migration or configuration change is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.25

People and Groups uses larger list text and solid backgrounds in the glass theme. During scheduled storage maintenance, presence and call polling can wait up to six seconds for the storage lock to clear.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No migration, configuration change or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.24

Calendar refreshes dated reminders when you open Home or return to the app. The smallest widget shows one reminder for the selected day, its time if set, and the number of other reminders. Select the row to open the full day. This update also includes the sign-in language selector spacing fix from 5.2.23.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No migration, configuration change or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.22

Section navigation crossfades between the previous and new workspace views. Browsers without View Transitions and devices set to Reduce Motion switch directly.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No database migration, new setting, storage change or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.21

The muted family-chat preview samples decoded frames at up to 30 frames per second. The separate viewer plays the original video stream at its native quality and cadence.

Rebuild Space with the existing Compose files and volumes. No migration is required.

## 5.2.20

Family chats draw decoded frames for the selected muted preview directly in the message tile. The same video stream supplies those frames. The existing poster covers the tile until a frame is drawn.

Rebuild Space with the existing Compose files and volumes. No migration is required.

## 5.2.19

Chat inboxes share one database session for message and call cursors, read markers, and the notification snapshot. The message cursor uses the room index. A new subscription schedules its own refresh and room-specific PostgreSQL signals wake inboxes for the affected room. Session revocation and the recovery checks still run independently.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No migration, new settings or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.18

Chat workers reuse recipient and room reads, batch inbox wake-ups, and use room-specific PostgreSQL signals. Message writes and shared room reads run outside the WebSocket event loop. Each outgoing event keeps its access checks, and periodic reconciliation covers reconnects and missed signals.

The conversation list shows ordinary chats and family groups together. A directly opened family conversation can show its history while the list and sending status load. The owner check for a personal note avoids the full workspace access index. The note catalog takes a write lock only when trash cleanup is needed.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No migration, new settings or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.17

The Notion page picker overlaps independent metadata requests within the existing workspace request budget. App workers sharing a data volume reuse the same encrypted catalog for up to five minutes. The catalog is scoped to the current authorization. Link coverage is read live each time.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No migration, new settings or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.16

Drive discards uploads when the connection closes before the request body is complete. This applies to local and S3 storage.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No migration, new settings or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.15

The note move dialog shows a collapsible tree of editable local destinations. Secret note icons appear in the sidebar after unlocking. New Outline collection pages start with their page links.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No migration, new settings or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.14

Use the standard note menu to create an editable local copy of a linked Notion page. The copy opens at the bottom of the personal notes list and has its own attachments. A page that has finished importing can be copied while unrelated pages from the same collection are still transferring.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No migration, new settings or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.13

Outline imports preserve collection hierarchy, page titles and icons, callouts, images inside tables and text, and attachment names. Import Markdown ZIPs with attachments, optionally accompanied by JSON ZIPs of the same collections without attachments to recover the collection emoji. The note attachment limit is 250 MiB per file.

Rebuild Space with the existing Compose files, environment, encryption keys and volumes. No migration, new settings or Media update is required. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.12

Drive resumes a private download from the requested byte range when the file is unchanged. If the file was replaced, the current copy is downloaded in full. During an S3 outage, Drive shows a retry action for the affected transfer. The new `GET /api/ready` endpoint checks database readiness separately from the existing `GET /api/health` process check.

Rebuild Space using the existing Compose files, environment, encryption keys and volumes. No migration, new settings or Media update is required. Keep any Compose overrides already used on the installation. For the base installation:

```sh
docker compose up -d --build --no-deps app
```

## 5.2.11

The pinned ASN.1 runtime is updated to pyasn1 0.6.5. Existing authentication, data encryption, client APIs and object storage formats are unchanged. Rebuild Space with the existing Compose files, environment and volumes. No schema migration, new keys or configuration changes are required. Browser regression checks now wait for completed navigation, permission responses and stable geometry; these test changes do not alter the interface.

## 5.2.10

Drive reuses completed 256-pixel WebP thumbnails for immutable source objects. The cache uses the existing storage cipher and is local to the shared data volume, including when originals use S3. Every private or shared response still checks current access before and after obtaining the thumbnail; responses keep `Cache-Control: no-store`. Original bytes, file API and user storage accounting are unchanged.

Only derived image data is cached: at most 256 MiB / 4096 entries, with a 256 KiB per-thumbnail limit and seven days since last use. At most two cold builds run across processes; requests for the same source share a completed result. Versioned format keys prevent reuse after renderer changes. Replacement creates a new immutable source identity; existing object garbage collection removes all cached variants with the retired source. Normal maintenance expires old entries, and full storage reconciliation removes cache-only orphans. Interrupted writes use atomic encrypted publication and a recoverable bounded index; a full/read-only cache falls back to authorized generation.

The disposable `file-thumbnails` directory inside `DATA_DIR` is excluded from portable backups and is not needed for restore or rollback. It can be rebuilt from original objects. Existing file-store binding, database schema, encryption keys, source object paths, dependencies, Compose settings and environment are unchanged. Rebuild with the same files and volumes; no migration is required. Media does not need an update for this release.

## 5.2.9

While scrolling, a video poster remains above the inline player until the first playback frame is presented. Pending or failed autoplay, seeking and scrolling away keep or restore the poster; this prevents a black flash while the original loads. Existing media geometry, autoplay limits, sources and request budgets are preserved. The poster handoff is frontend-only; it requires no additional Media changes once the archive is prepared for Media 1.14.4.

Media albums reserve a fixed mosaic from their file count before any preview loads. Images can arrive individually without resizing the album, neighbouring messages or other tiles. Mosaic previews use cover cropping; full-size originals remain available in the viewer. Single photos and video posters reserve natural frames from stored Media dimensions before thumbnail bytes arrive. This requires Media 1.14.4 or newer and its one-time preparation of old archive metadata. Older Media remains compatible through the previous geometry fallback. Full-size originals are unchanged. Older history uses the established near-edge pagination threshold, avoiding an extra page request on opening; while wheel, touch or keyboard scrolling continues, single-preview sizing and page insertion wait. Ready updates are applied shortly after the gesture settles. Unchanged background responses skip repeated layout work.

The initial conversation list waits for parallel native and Media catalogs, so family conversations do not appear in a later wave. Media 1.14.4 supplies optional last-message previews in its catalog with bounded cross-group metadata queries; older services retain the per-history fallback. An unavailable Media service leaves native conversations usable.

Rebuild Space with the existing Compose files and volumes. Space needs no schema, key, storage-format, dependency or environment changes. Update and prepare Media first to obtain stable single-preview geometry; follow its update guide, preserving the existing archive and session.

## 5.2.8

Call updates use participant-scoped reads and live inbox events, with the existing fast fallback during connection loss. A shared maintenance lease expires abandoned calls without depending on user traffic. Rebuild with the existing Compose files and volumes; no schema, environment, key, dependency or storage changes are required.

## 5.2.7: fewer background Media requests

Media conversations and their previews stop automatic polling while the page is hidden and cancel their active reads when chats are left. Hiding a page does not cancel user-initiated sends or delivery jobs. Returning to the page starts one fresh update. Known empty groups remain visible without loading their empty history; previews load with at most four history requests in flight. Missing metadata from an older Media service keeps the compatible history path.

There are no schema, key, storage-format, dependency, environment, port or volume changes. No migration, clean database, replacement configuration or Media update is required. Rebuild Space with the existing Compose overlays. Rolling back to 5.2.6 requires no data changes; restrictions for downgrading below 5.2.0 still apply.

## 5.2.6: more efficient Media conversations

Space batches local reactions, current reactor permissions and unique author profiles for the selected Media history page. The same request-local loading applies to a single message, including deleted-user fallbacks and current presence. No profile or access data is retained between requests. Message order, album boundaries, Telegram attribution, reaction controls and access checks are unchanged.

There are no schema, key, storage-format, dependency, environment, port or volume changes. No migration, clean database, replacement configuration or Media update is required. Rebuild Space with the existing Compose overlays. Rolling back to 5.2.5 requires no data changes; restrictions for downgrading below 5.2.0 still apply.

## 5.2.5: more efficient file browsing

File scope checks load selected entries and their ancestors instead of repeatedly scanning unrelated trees. Empty selections and trash cleanup with no expired entries avoid namespace walks. Note, task and chat attachments remain protected, including moved legacy attachments. PostgreSQL project locks continue to coordinate file access with project deletion.

There are no schema, key, storage-format, dependency, environment, port or volume changes. No migration or clean database is required. Rebuild with the existing Compose overlays. Rolling back to 5.2.4 requires no data changes; restrictions for downgrading below 5.2.0 still apply.

## 5.2.4: reactions below media

Photo and album reactions use a separate strip below the message background in built-in chats and Media history, including the installed Web App. Captions, quoted replies, documents and timestamps remain in the content bubble. Empty reaction strips reserve no space; multiple reactions wrap without overlapping the next message. Existing reaction actions, participants, permissions and synchronization are unchanged.

There are no schema, key, storage-format, dependency, environment, port or volume changes. No migration or clean database is required. Rebuild with the existing Compose overlays. Rolling back to 5.2.3 requires no data changes; restrictions for downgrading below 5.2.0 still apply.

## 5.2.3: easier access to sharing links

The file/folder sharing dialog opens on existing links when they are present. Each link has a readable address, copy/open/revoke actions, its expiry and the existing download-limit controls. Creating another link uses a separate tab and preserves the draft when switching tabs. An object with neither links nor disposal settings opens directly on the creation form. Clipboard restrictions leave the address selected for manual copying. Loading failures have a local retry; after a successful creation, a failed list refresh does not require resubmitting the form.

Existing permissions, links, download counters and confirmed disposal rules are unchanged, including preservation of later additions to a previously shared folder. No schema, key, storage-format, dependency, environment, port or volume changes are required. No migration or clean database is needed. Rebuild with the existing Compose overlays. Rolling back to 5.2.2 requires no data changes; restrictions for downgrading below 5.2.0 still apply.

## 5.2.2: stable Drive folder navigation

Drive keeps its navigation and tools mounted while opening folders. The current folder remains visible with a local loading status until the requested folder is ready. Folder navigation remains available while file changes are disabled; an error preserves the current view and restores its controls. Delayed responses cannot replace a more recently selected folder. Folder names, file contents, permissions, layout settings and storage are unchanged.

There are no schema, key, storage-format, dependency, environment, port or volume changes. No migration, clean database or replacement configuration is required. Rebuild with the existing Compose overlays. Rolling back to 5.2.1 requires no data changes; restrictions for downgrading below 5.2.0 still apply.

## 5.2.1: backup reliability and maintenance

Downloads cancelled before response headers now close their responses and release storage leases, preventing subsequent backups from waiting indefinitely. The task module title now follows English interface settings. Floating navigation no longer overlaps the mobile reminder properties panel. Normal note/image imports and task recovery behavior remain unchanged; the removed image repair helper had no application or CLI callers. Browser regression fixtures resolve built styles through the asset manifest, localization is included in the existing opt-in CI profile, and disposable Python/browser storage is cleaned up on normal shutdown. Supported legacy clients and archive fixtures are retained.

There are no schema, key, storage-format, dependency, environment, port or volume changes. No migration, clean database or replacement configuration is required. Rebuild with the existing Compose overlays. A rollback to 5.2.0 uses the same schema and seven-day recovery markers; the restrictions for downgrading below 5.2.0 still apply.

## 5.2.0: recoverable task deletion

Editors can explicitly delete an issue together with its complete subtree and restore that deletion batch for seven days. Completion and cancellation remain durable and never start deletion. The dialog confirms the issue key and the current branch/project version; newly added descendants require a fresh confirmation. Deleted issues disappear from ordinary reads, edits, attachments, notifications, counts and older clients. Recovery is available in the updated web/Web App and native iOS client; older clients still exclude deleted issues from ordinary reads.

Independent child deletions retain their own batch. Enclosing task deletion pauses their remaining time; restoring the parent does not restore those previously deleted children. Thirty-day project deletion pauses task recovery clocks, project restoration resumes the exact remaining time, and final project deletion removes every pending branch as well. Pending deletion of an enclosing shared workspace also pauses task clocks; cancelling it or detaching retained projects into their owners' personal space resumes the remaining time. Expired branches remove their comments, history, upload chunks, scoped file metadata and service markers; physical object deletion uses the retry queue and preserves referenced/foreign objects.

Existing schema, storage formats, encryption, keys, dependencies, environment settings, ports and volumes are unchanged; no migration or clean database is required. Rebuild using the existing Compose overlays. Full backups preserve original deadlines and pauses, including expiry after restoring an old snapshot; personal transfer copies owned retained content without deletion timers or other users' grants. Before downgrading to a server without task recovery support, restore all pending issue branches using this version: older servers do not understand these service markers. Do not blindly start an old image while pending task deletions exist.

## 5.1.2: concurrent connections and chat history

Gunicorn now uses its native ASGI worker for long-lived WebSockets. Flask HTTP routes retain their existing streaming interface through a bounded adapter. HTTP and socket URLs, ports and reverse-proxy Upgrade handling are unchanged; no extra service or container is required. `GUNICORN_THREADS` continues to bound the Flask HTTP pool per worker, while idle sockets do not reserve those threads.

Rebuild the application image using the existing update commands below. For a Python installation, reinstall `requirements.lock.txt`: Gunicorn is updated to 27.0.0 and a2wsgi 1.10.10 is added for bounded streaming adaptation. Do not run the new configuration with an old environment. Preserve existing Compose overlays, worker/thread environment values, data, keys and volumes. No schema, storage-format or key changes, new environment settings, clean database or migration are required.

Chat history batches author profiles, reactions, replies, mentions and attachment metadata within each authorized request. It retains live session checks and group membership filtering; no shared profile cache or additional retention is introduced.

## 5.1.1: note downloads and release language

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
