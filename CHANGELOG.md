# Changelog

User-facing release history. Preview changes are consolidated into their stable release. Versions before 1.0.0 are retrospective development milestones, not claims of separately published releases. Historical behavior may differ from the current version; use the current installation and upgrade guides.

## [5.2.1-rc.1] — 2026-11-13 — Language settings and maintenance

- The task module name follows English interface settings.
- Floating navigation stays out of the way while mobile reminder settings are open.
- Updated compatibility checks cover administration, messaging, mobile controls and both interface languages.
- Note and image imports preserve their behavior while unused maintenance code is removed.

- **Installation:** Rebuild with the existing Compose settings and volumes.
- **Compatibility:** No schema, key, storage-format, dependency or environment changes; no migration is required.

## [5.2.0] — 2026-11-13 — Recoverable task deletion

- Delete a task and its subtasks together, with seven days to restore the branch.
- Project deletion pauses the remaining task recovery time; restoring the project resumes it.
- Expired task branches and their attachments are cleaned up together.

- **Installation:** Rebuild with the existing Compose settings and volumes.
- **Compatibility:** No schema, key, format, dependency or environment changes; no migration is required. Restore pending task branches before downgrading to a server without task recovery support.

## [5.1.2] — 2026-11-12 — Concurrent connections and chat history

- Keep notifications, chat and shared notes responsive while more clients are connected.
- Load chat history with fewer database requests while preserving access checks.

- **Installation:** Rebuild the image with the existing Compose settings, or reinstall requirements.lock.txt for a Python installation.
- **Compatibility:** Existing URLs, ports, environment settings, schema, storage formats and keys are preserved; no migration is required.

## [5.1.1] — 2026-11-12 — Note downloads and localized release notes

- Download file attachments from read-only and synchronized notes.
- Release titles and change lists follow the interface language.
- The project search clear button stays aligned and preserves other filters.

- **Installation:** Use the existing Compose settings and volumes; no new environment settings are required.
- **Compatibility:** No schema, key, storage-format or dependency changes; no migration from 5.1.0 is required.

## [5.1.0] — 2026-11-11 — Cancelled work and release availability

- Cancel tasks with an optional reason, preserve their content and reopen them later.
- Cancelled tasks have their own status and counter, leave unfinished views, and do not count towards epic completion.
- Owners and administrators can see an available stable GitHub release below the installed server version.
- Installed Web App names use DrG Space.
- The media gallery uses Media naming and a photo icon in navigation.

- **Installation:** Existing databases require the one-off tracker constraint expansion described in docs/updates.md before startup.
- **Compatibility:** Preserve keys, volumes, settings and task identities. Older clients retain a four-state projection; update the client to view cancelled work. Existing four-state full backups remain restorable.

## [5.0.2] — 2026-11-11 — Consistent runtime names

- Docker containers, images, Compose project and diagnostic labels use DrG Space names.
- Existing data volumes can be pinned as external volumes when changing the Compose project.
- Downloaded backups use the DrG Space filename; existing backup formats and tokens stay compatible.

- **Installation:** Follow the explicit project-rename procedure in docs/updates.md before starting an existing installation.
- **Compatibility:** Preserve volumes, keys, database/schema and object prefixes; no schema migration is required.

## [5.0.1] — 2026-11-11 — Stable reminder background refresh

- Unchanged reminder polls preserve list rows, the add field and the open properties panel when recently deleted lists exist.
- Real reminder edits, recovery and deletion still refresh the displayed data.

- **Installation:** Follow docs/updates.md with the existing Compose project and volumes.
- **Compatibility:** No schema, environment, dependency or storage-format changes; no migration is required.

## [5.0.0] — 2026-11-10 — DrG Space self-hosted baseline

- Speaker routing supports all participants and reconnects. Group calls require LiveKit; private and federated calls use TURN. Previous immutable web modules remain available to open tabs after deployment.
- Notes, projects, reminders, files, chat and dashboard in one self-hosted workspace.
- Empty private-vault directories are removed automatically during cleanup.
- Weather charts fit compact dashboard cards without clipping.
- Closing weather settings cancels pending city searches immediately.
- Weather provider identification uses the deployment settings.
- Russian and English interfaces for desktop browsers and installable mobile Web Apps.
- Optional integrations, encrypted storage and portable encrypted backups.

- **Installation:** See INSTALL.md.
- **Compatibility:** Existing storage formats and database schema are retained. Keep keys, volumes and Compose settings.

## [4.19.1] — 2026-11-10 — Aligned project cards

- Project names and avatars stay aligned when descriptions have different lengths.

## [4.19.0] — 2026-11-10 — Holiday calendar and themed effects

- Dashboard calendars show production-calendar dates in four sizes. Holiday decorations, greetings and particle effects follow the selected occasion.

## [4.18.0] — 2026-11-09 — Download limits and temporary sharing

- File links have independent deadlines and shared download limits. Confirmed disposal waits for active links and moves selected content to trash for seven days.

## [4.17.3] — 2026-11-09 — Wallpaper style controls

- Wallpaper controls appear in Glass style and retain the chosen background when styles change.

## [4.17.2] — 2026-11-08 — Reliable Notion blocks and attachments

- Synced blocks refresh their contents. Attachment errors identify the page, file and reason, with bounded retries and an explicit retry action.

## [4.17.1] — 2026-11-06 — Notion asset preservation and cleanup

- Recovery keeps page icons and covers. Missing assets are downloaded again, and unreferenced resources are cleaned automatically.

## [4.17.0] — 2026-11-06 — Notion page tree

- Select accessible pages through a collapsible tree with path-aware search. Overlapping synchronization roots are rejected.

## [4.16.1] — 2026-11-06 — Notion formatting and local ordering

- Inline code, callouts and synchronized blocks retain their formatting. New roots appear at the bottom and linked siblings can be reordered locally.

## [4.16.0] — 2026-11-06 — Voice messages and video notes

- Voice messages support seeking, waveforms, playback speed and sequential playback. Visible video notes play silently until selected.

## [4.15.2] — 2026-11-05 — Web App startup and note transfer

- Data operations show a retry screen that preserves the address and session. Transfer controls fit mobile layouts, and page counts use local tree data.

## [4.15.1] — 2026-11-03 — Fewer note catalog requests

- Catalog revision checks avoid retransmitting unchanged notes while preserving permission checks. Successful attachment downloads survive synchronization retries.

## [4.15.0] — 2026-11-03 — Notion synchronization and columns

- Optional Notion connections import a page subtree. Copies are read-only by default; experimental writes require explicit per-connection consent and conflict recovery.

## [4.14.0] — 2026-11-01 — Clock faces and live previews

- Analog and digital clocks offer six styles each, with full-size digital displays and live previews.

## [4.13.1] — 2026-11-01 — Less background database work

- Contact presence is loaded in batches. Fully read archive groups avoid unnecessary unread-count requests.

## [4.13.0] — 2026-11-01 — Dashboard motion

- Widget content and dashboard settings use smooth entry and exit transitions with reduced-motion support.

## [4.12.0] — 2026-11-01 — Family archive timeline

- Photos and videos form a date-based timeline for the selected conversation. The complete month index is available without scrolling through every page.

## [4.11.0] — 2026-11-01 — Reminder movement and expansion

- Reminder parents open correctly and move with their children. Manual ordering and reparenting are available within ordinary lists.

## [4.10.2] — 2026-10-31 — Note workspace selector

- The note workspace switcher preserves a compact layout and readable controls.

## [4.10.1] — 2026-10-31 — Reminder and weather refinements

- Reminder controls and larger weather icons improve compact layouts.

## [4.10.0] — 2026-10-31 — Holiday sign-in and decoration edges

- Before sign-in, holidays follow the device calendar. Account preferences apply after sign-in; edge decorations stay attached to the boundary.

## [4.9.3] — 2026-10-31 — Sliding bottom navigation

- The selected item moves smoothly between bottom navigation destinations.

## [4.9.2] — 2026-10-31 — Loading light animation

- A compact animated light indicates loading progress.

## [4.9.1] — 2026-10-31 — Personal names and error recovery

- Messages use account-specific contact names. Workspace failures provide a recoverable error screen.

## [4.9.0] — 2026-10-31 — Notion import and faster startup

- HTML and Markdown/CSV imports preserve nested pages and supported formatting. The shared editor, immutable asset caching and deferred JavaScript loading improve startup.

## [4.8.0] — 2026-10-30 — Task filters and notification reading

- Task views share persistent status and priority filters. New cards follow creation or status-transition time; reading chat clears related notifications.

## [4.7.0] — 2026-10-30 — Invitations and seasonal decoration

- Account invitations and holiday previews include clearer states. Sidebar contrast and decorative animations remain usable while scrolling.

## [4.6.0] — 2026-10-30 — Wallpaper categories

- Background selection groups wallpapers by category.

## [4.5.0] — 2026-10-30 — Recoverable project deletion

- Project deletion includes a recovery period and preserves the ability to restore supported content.

## [4.4.0] — 2026-10-30 — List recovery and world cities

- Reminder lists can be restored, and city selection uses an expanded geographic catalog.

## [4.3.0] — 2026-10-30 — Project task tracker

- Projects include structured task tracking with stable task identifiers.

## [4.2.0] — 2026-10-29 — Expense categories and currencies

- Shared bills support illustrated categories, currencies, pairs and receipt photographs.

## [4.1.0] — 2026-10-29 — Sign-in transitions and space avatars

- Authentication transitions are smoother, and a new space avatar collection is available.

## [4.0.6] — 2026-10-28 — Smooth clock seconds

- Analog second hands move smoothly.

## [4.0.5] — 2026-10-28 — Remote-server layout

- The Other servers section uses consistent spacing and empty-state presentation.

## [4.0.4] — 2026-10-28 — Distinct collaboration actions

- People and group actions use distinct icons.

## [4.0.3] — 2026-10-28 — Dashboard customization entry

- The dashboard customization action is easier to discover.

## [4.0.2] — 2026-10-28 — Web App task background

- Task screens retain the selected background in the installed Web App.

## [4.0.1] — 2026-10-28 — Management access and feed recovery

- A newly confirmed management session survives delayed access checks. Feed errors preserve previously loaded news and allow retrying.

## [4.0.0] — 2026-10-28 — Reminder invitations and Web App fixes

- Reminder-list invitations and mobile Web App behavior are improved.

## [3.21.1] — 2026-10-27 — Complete initial configuration

- New installations receive the full environment template while existing installations retain their configured values.

## [3.21.0] — 2026-10-27 — Russian and English interface

- Workspace screens and supported formatting use shared Russian and English catalogs.

## [3.20.0] — 2026-10-27 — Optional Redis and quieter polling

- Redis provides optional shared caching and coordination; background requests are reduced without making Redis mandatory.

## [3.19.1] — 2026-10-27 — Default Glass appearance

- Fresh installations use the Glass appearance and dunes background.

## [3.19.0] — 2026-10-27 — Glass, wallpapers and avatars

- The workspace gains configurable glass styling, wallpapers and avatar selection.

## [3.18.1] — 2026-10-26 — Federation security hardening

- Cross-server authorization and federation request validation are hardened.

## [3.18.0] — 2026-10-26 — Cross-server collaboration

- Federated servers exchange supported contacts, notes, databases and private calls.

## [3.17.2] — 2026-10-26 — Less archive polling

- Unchanged archive galleries and idle queues require less background work.

## [3.17.1] — 2026-10-25 — Sign-in and secret-vault recovery

- Authentication and secret-section access recover correctly from stale state.

## [3.17.0] — 2026-10-25 — Named sessions and devices

- Account security shows session names and device information.

## [3.16.0] — 2026-10-24 — Client API compatibility

- Existing note editing, search, file-transfer and passkey contracts remain compatible with updated clients; server storage formats are unchanged.

## [3.15.1] — 2026-10-24 — Settings API compatibility

- Account security and versioned dashboard settings remain compatible with updated clients.

## [3.15.0] — 2026-10-24 — Collaborative note compatibility

- Shared-note editing and voice-message contracts support updated clients without replacing stored data.

## [3.14.0] — 2026-10-24 — Workspace API compatibility

- Existing note, reminder, project, file and account endpoints remain compatible with updated clients.

## [3.13.0] — 2026-10-24 — Archive media and read state

- Family conversations expose media and reading state through the supported client API.

## [3.12.0] — 2026-10-23 — Delete chat messages for everyone

- Authors can remove their DrG messages for all participants; replies retain their own content without exposing deleted text.

## [3.11.0] — 2026-10-23 — Passkey client authorization

- Temporary single-use client authorization supports passkey sign-in alongside passwords and 2FA.

## [3.10.4] — 2026-10-23 — Download authorization refresh

- Downloads recover when an access token expires.

## [3.10.3] — 2026-10-22 — Markdown and Notion attachments

- Markdown import retains supported links and Notion attachments.

## [3.10.2] — 2026-10-22 — Compact archive status

- Family group connection status uses a compact presentation.

## [3.10.1] — 2026-10-22 — Animated chat media

- Conversations display supported GIF media.

## [3.10.0] — 2026-10-21 — Telegram reactions and shared-note refresh

- Telegram reactions and shared-note updates propagate through the workspace.

## [3.9.0] — 2026-10-21 — Archive connection status

- Family groups show their connection and synchronization state.

## [3.8.0] — 2026-10-21 — Send to Telegram conversations

- Authorized users can send messages into connected Telegram groups through the workspace.

## [3.7.1] — 2026-10-21 — Message date and spacing fixes

- Conversation dates and spacing are corrected.

## [3.7.0] — 2026-10-21 — Archive exports and completion notices

- Archive exports report progress and notify users when downloads are ready.

## [3.6.1] — 2026-10-21 — Archive administration controls

- Privileged archive operations are limited to Management.

## [3.6.0] — 2026-10-21 — Group copies and photo downloads

- Family group copies and photo downloads use supported archive operations.

## [3.5.0] — 2026-10-21 — Family archive administration

- Management provides controls for connected family archives.

## [3.4.0] — 2026-10-21 — Owner and additional administrators

- The installation distinguishes its owner from additional site administrators.

## [3.3.3] — 2026-10-21 — File menus and nested-page deletion

- File actions, subpage deletion and avatar presentation are refined.

## [3.3.2] — 2026-10-21 — Subpages and interface cleanup

- Nested note pages use the current compact interface.

## [3.3.1] — 2026-10-21 — Consistent secret-note interface

- Secret notes share the ordinary note layout while preserving their additional protection.

## [3.3.0] — 2026-10-21 — Secret-note trees and file controls

- Secret pages support hierarchical navigation and consistent file actions.

## [3.2.0] — 2026-10-21 — Secret notes and files

- Protected note and file storage adds a separate authentication boundary.

## [3.1.3] — 2026-10-21 — Responsive conversation widgets

- Dashboard conversations adapt to available space.

## [3.1.2] — 2026-10-21 — Reactions and media viewing

- Conversation reactions and media previews are refined.

## [3.1.1] — 2026-10-20 — Trash viewing and message layout

- Deleted items can be inspected, and conversation presentation is improved.

## [3.1.0] — 2026-10-20 — Trash, notifications and collaboration

- Workspace cleanup, notification state and shared editing are improved.

## [3.0.2] — 2026-10-20 — Trash compatibility and compact lists

- Existing trash data remains usable and lists become more compact.

## [3.0.1] — 2026-10-20 — Compact space-deletion state

- Spaces awaiting deletion use a compact status presentation.

## [3.0.0] — 2026-10-20 — Deletion with a recovery period

- Space deletion follows an explicit waiting period before final removal.

## [2.1.2] — 2026-10-20 — Sender avatars in conversations

- Chat messages display sender avatars consistently.

## [2.1.1] — 2026-10-20 — Avatar labels with personal names

- Avatar labels respect account-specific contact names.

## [2.1.0] — 2026-10-20 — Personal contact nicknames

- Users can assign personal names to contacts.

## [2.0.0] — 2026-10-20 — Shared data and space lifecycle

- Shared workspace data follows explicit space membership and lifecycle rules.

## [1.11.0] — 2026-10-20 — Space roles and account tables

- Space permissions and account administration use clearer roles and tables.

## [1.10.0] — 2026-10-20 — Member and team tables

- Membership and team information is presented in structured tables.

## [1.9.1] — 2026-10-20 — Team dialog and simple spaces

- Team controls and simplified workspace handling are refined.

## [1.9.0] — 2026-10-20 — Teams and note access

- Team membership participates in note access control.

## [1.8.2] — 2026-10-20 — Space photo previews

- Workspace photographs have a preview before selection.

## [1.8.1] — 2026-10-20 — Space name editing

- Workspace names use a dedicated editable field.

## [1.8.0] — 2026-10-20 — People and groups

- Contacts and groups have a dedicated workspace section.

## [1.7.0] — 2026-10-20 — Call window and audio controls

- Call presentation and audio management are improved.

## [1.6.0] — 2026-10-20 — Hidden text and quick formatting

- Notes support concealed text and faster formatting actions.

## [1.5.0] — 2026-10-20 — Subpages, Outline notices and diagrams

- Nested pages, Outline notifications and diagrams are supported.

## [1.4.0] — 2026-10-20 — File publications and Outline import

- File sharing, Outline import and collaborative note behavior are extended.

## [1.3.0] — 2026-10-20 — File uploads and navigation

- Drive uploads and folder navigation are improved.

## [1.2.2] — 2026-10-20 — Selected-folder contrast

- Selected folders remain readable across themes.

## [1.2.1] — 2026-10-20 — File loading and icons

- Drive loading transitions and icons are refined.

## [1.2.0] — 2026-10-20 — File workspace

- A dedicated file and folder workspace is available.

## [1.1.0] — 2026-10-19 — Release notes in About

- The About view displays user-facing release notes.

## [1.0.2] — 2026-10-19 — Profile placement

- Profile controls retain their established position.

## [1.0.1] — 2026-10-19 — Visible application version

- The sidebar displays the current application version.

## [1.0.0] — 2026-10-19 — First official stable release

- The existing personal workspace becomes the first officially numbered stable release.

## [0.53.0] — 2026-10-19 — Owner security center

- Owner security controls are grouped together.

## [0.52.0] — 2026-10-19 — Workspace branding

- The application receives its workspace name and logo.

## [0.51.0] — 2026-10-19 — Storage reconciliation

- Database references and stored objects are reconciled in both directions.

## [0.50.0] — 2026-10-19 — Page membership invitations

- Pages support explicit participant invitations.

## [0.49.0] — 2026-10-19 — Explicit note roles

- Note access uses three explicit permission roles.

## [0.48.0] — 2026-10-19 — Link previews and navigation settings

- Notes preview supported links and navigation can be configured.

## [0.47.0] — 2026-10-19 — Database lists and galleries

- Note databases support list, gallery and publication views.

## [0.46.0] — 2026-10-19 — Private external Notion images

- Imported external Notion images are handled as private resources.

## [0.45.0] — 2026-10-19 — Note history and S3 media

- Notes retain supported version history and media can use S3 storage.

## [0.44.0] — 2026-10-19 — Separate note attachments

- Note attachments are managed separately from general files.

## [0.43.0] — 2026-10-19 — Notion archive import

- Notion archives can be imported into notes.

## [0.42.0] — 2026-10-17 to 2026-10-19 — Conversation notification preferences

- Users can disable notifications for individual conversations.

## [0.41.0] — 2026-10-17 — Drive-style file navigation

- The file workspace uses folder-based drive navigation.

## [0.40.0] — 2026-10-16 to 2026-10-17 — Family photo integration

- The workspace integrates a separately operated family archive.

## [0.39.0] — 2026-10-16 — Contacts and management unlock

- Contacts and a shared management reauthentication flow are introduced.

## [0.38.0] — 2026-10-16 — Telegram stickers and voice metadata

- Telegram conversations display supported stickers and voice-message information.

## [0.37.0] — 2026-10-16 — Telegram conversation notifications

- Connected Telegram conversations participate in notifications.

## [0.36.0] — 2026-10-16 — Guest-call presentation

- Guest calls have configurable presentation and media controls.

## [0.35.0] — 2026-10-16 — Storage usage reporting

- Accounts display calculated storage use.

## [0.34.0] — 2026-10-16 — Family archive access

- Family archive viewing follows explicit access grants.

## [0.33.0] — 2026-10-16 — Mobile navigation and camera avatars

- Mobile navigation respects safe areas, and camera photographs are normalized for avatars.

## [0.32.0] — 2026-10-15 to 2026-10-16 — Passkey sign-in

- Accounts can sign in using supported passkeys.

## [0.31.0] — 2026-10-15 — Mobile navigation

- Mobile workspace sections receive dedicated navigation.

## [0.30.0] — 2026-10-15 — Guest group calls

- Guest invitations support group conversations in the historical call implementation.

## [0.29.0] — 2026-10-15 — Telegram archive connection

- The workspace connects to the separate Telegram archive service.

## [0.28.0] — 2026-10-15 — Administrator-prepared accounts

- Administrators can prepare member accounts.

## [0.27.0] — 2026-10-15 — Conversation management

- Conversation members and settings can be managed.

## [0.26.0] — 2026-10-15 — Video calls and screen sharing

- Calls support video, screen sharing and message-status refinements.

## [0.25.0] — 2026-10-15 — Private voice calls

- Contacts can make private voice calls.

## [0.24.0] — 2026-10-15 — Message editing

- Authors can edit supported chat messages.

## [0.23.0] — 2026-10-15 — Note attachments and Web App improvements

- Notes accept attachments and the installed Web App gains refinements.

## [0.22.0] — 2026-10-14 — Conversation replies

- Full-screen conversations support replies.

## [0.21.0] — 2026-10-14 — Web Push messages

- Supported browsers receive message notifications through Web Push.

## [0.20.0] — 2026-10-14 — Installed Web App icons

- The installed Web App receives application icons.

## [0.19.0] — 2026-10-14 — Reminder lists and children

- Reminders can be organized in lists and nested under parents.

## [0.18.0] — 2026-10-13 to 2026-10-14 — Compact notes workspace

- Notes use compact hierarchical navigation and block controls.

## [0.17.0] — 2026-10-13 — Threaded worker deployment

- Gunicorn supports coordinated workers sharing one installation's local storage.

## [0.16.0] — 2026-10-13 — File trash

- Files move through trash before final cleanup.

## [0.15.0] — 2026-10-13 — Weather providers

- Weather widgets support additional providers.

## [0.14.0] — 2026-10-13 — Flask server

- The application server moves to Flask.

## [0.13.0] — 2026-10-12 — Chat attachments and cleanup

- Conversations support attachments and unreferenced resources are cleaned.

## [0.12.0] — 2026-10-12 — Notifications, files and note databases

- Workspace notifications, file operations and note databases are extended.

## [0.11.0] — 2026-10-12 — Profiles, code and square widgets

- Profile controls, code formatting and square widgets are improved.

## [0.10.0] — 2026-10-11 to 2026-10-12 — Favorites, protected notes and shared bills

- The workspace adds favorites, protected notes and expense sharing.

## [0.9.0] — 2026-10-11 — PostgreSQL and default encryption

- PostgreSQL becomes optional and storage encryption is enabled by default.

## [0.8.0] — 2026-10-11 — Personal data transfer

- Accounts can export and import supported personal data.

## [0.7.0] — 2026-10-11 — Note databases

- Notes gain structured databases.

## [0.6.0] — 2026-10-11 — Shared spaces and collaborative notes

- Families, contacts and shared notes are introduced.

## [0.5.0] — 2026-10-11 — Administration and full backups

- The workspace adds administration and full backup operations.

## [0.4.0] — 2026-10-11 — Widgets, feeds and themes

- The dashboard gains widgets, RSS feeds and themes.

## [0.3.0] — 2026-10-11 — Authentication protection

- Sessions and sign-in protection form the authentication baseline.

## [0.2.0] — 2026-10-11 — Block pages and dashboard modules

- Notes use blocks and the dashboard gains configurable modules.

## [0.1.0] — 2026-10-11 — Personal workspace foundation

- The initial personal workspace establishes the application foundation.
