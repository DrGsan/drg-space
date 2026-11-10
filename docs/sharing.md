# Sharing pages and files

Sharing creates an explicit access grant. Page and shared-space roles control supported viewing and editing operations; site-level administration is a separate permission. Personal content does not become public merely because its identifier is known.

## Page publications

Review the publication's audience, access mode, password and expiry settings before creating a link. A publication can expose content to people outside the account's ordinary shared spaces. Revoking it blocks new access through that publication without deleting the underlying page.

## File and folder links

File links retain their own access deadlines and optional download limit. Sharing a folder creates a separate grant; moving individually shared files into it does not change their existing links. A link's limit is shared by its recipients rather than allocated separately to each visitor.

A file or ZIP download starts a counted delivery. Authorized continuation of that delivery does not create another charge. Preview does not consume a download; after the download limit is exhausted, both downloading and previewing the original are blocked. The public view shows remaining downloads only when a limit exists.

## Optional disposal

Automatic disposal is off by default and requires an explicit confirmation. The confirmed files are scheduled to enter trash after all qualifying active links have ended. Link expiry and storage disposal are different deadlines. A later longer-lived link can postpone disposal; it does not shorten another link's original term.

For a folder, only the confirmed current contents are included. New files are not silently added to that disposal set. An explicitly selected folder can also enter trash once the relevant links have ended and it contains no remaining active content. File trash lasts seven days before permanent cleanup.

The owner can inspect, change or cancel applicable disposal settings. Revoking a link does not itself cancel an already confirmed storage policy. Deadlines survive server downtime and are enforced from server state. See [retention](data-retention.md).

## Quotas and archives

Uploads and copies are checked against the account's quota and configured file limits. Folder uploads preserve supported paths after validation. ZIP and encrypted application exports have separate bounds. Password-protected portable backups are described in [administrative backup](admin-backups.md) and [personal transfer](personal-data.md).
