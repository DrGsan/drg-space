# Contacts, spaces and collaboration

People and Groups manages contacts, invitations and shared spaces. Friendship creates a shared context for a pair; group membership provides access to the group's supported records. Account roles, space membership and individual page roles are distinct.

Invitations and membership changes are validated on the server. Changing a display name or locating a page ID does not change access. Removing a friendship or a space can remove its associated shared content; inspect the confirmation before proceeding. Revocation blocks new authorized reads and writes, including live connections.

Shared notes support block editing, note databases, discussion and mentions under their page permissions. Live collaboration reconnects using the versioned document protocol. Failed or conflicting writes retain recoverable drafts instead of silently replacing a newer remote version.

Local chat supports messages and attachments, with the configured retention and notification rules. Reading a message in its conversation reconciles its related notifications. Marking a notification read does not itself mark the source message read.

Private calls and group invitation rooms use different optional transports. See [calls](voice-calls.md). Independently hosted workspaces require explicit federation setup before cross-server collaboration; see [federation](federation.md).

Personal exports, full backups and deletion use their own ownership and integrity checks. Shared data is not an unrestricted per-account copy. See [personal transfer](personal-data.md) and [backup](admin-backups.md).
