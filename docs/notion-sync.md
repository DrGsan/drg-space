# Notion synchronization

Notion synchronization is optional. Enable it only after configuring the OAuth integration and granting access to the required pages. Local notes and archive import remain available without it.

## Server configuration

```dotenv
NOTION_SYNC_ENABLED=true
NOTION_OAUTH_CLIENT_ID=
NOTION_OAUTH_CLIENT_SECRET=
NOTION_OAUTH_REDIRECT_URI=
NOTION_WEBHOOK_READ_SECRET=
```

Configure one Notion OAuth application. A blank redirect setting derives the callback from `PUBLIC_URL` and `/api/notion-sync/oauth/callback`; the external callback must use HTTPS. OAuth credentials stay on the server. Per-account authorization tokens are stored in encrypted records, not supplied to the browser as installation credentials.

The optional webhook signing secret authenticates incoming events. Events schedule work; they are not trusted page contents. Duplicate events are coalesced, and periodic reconciliation remains a fallback.

## Linking pages

The owner connects a workspace, selects an accessible page from the searchable tree or supplies its link, and reviews the proposed transfer. Selection includes the page's subtree and later descendants. Overlapping links are rejected rather than merged or overwritten automatically.

A new connection creates a new personal copy. Existing unrelated imports are not automatically linked. Read-only mode follows Notion changes and prevents editing the linked copy. Its root can be ordered locally without changing the Notion hierarchy.

## Experimental writes and recovery

Two-way writing is disabled by default and requires the explicit switch for that connection. OAuth write capability alone does not authorize outbound changes. Supported block operations remain narrower than arbitrary Notion editing; pages, databases and attachments are not silently deleted.

Concurrent or uncertain writes stop for review. Recovery lets the owner compare versions, choose a direction and optionally preserve the replaced version as an independent copy. Unsupported reverse changes are explained instead of forced.

Removing access or a source page stops the connection without deleting the local copy. File failures identify the page, resource and reason, with bounded retries. Icons and covers may retain the last good resource with a warning. Incomplete content is not reported as a complete transfer. See [import formats](note-import-formats.md).
