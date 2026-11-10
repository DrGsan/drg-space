# Security model

## Accounts and sessions

Initial setup requires `SETUP_TOKEN`. The first account becomes the owner. Passwords, 2FA, recovery codes and passkeys are handled by the authentication service. Administrative accounts require 2FA. Keep recovery codes outside the application and protect the installation secrets.

Browser requests use scoped sessions and CSRF protection. Authorization is checked on the server for pages, spaces, files, administrative actions and external integrations. Possessing an object ID or a link to a private page does not grant access. Role changes, revocation and account removal invalidate the relevant access state.

Management requires password reauthentication with an idle timeout. Background status polling does not extend that grant. Sensitive actions may require an additional confirmation or fresh 2FA. A secret vault prevents operations that would bypass its 2FA protection.

## Transport and proxies

Use HTTPS for public access. Set `PUBLIC_URL` to the external origin and trust only the actual reverse proxies through `TRUSTED_PROXIES`. Preserve WebSocket upgrades and correct forwarding headers. Broadly trusting caller-supplied forwarding headers can bypass address-based protections.

Never disable certificate verification to resolve a deployment problem. Federation has a separate authenticated TLS endpoint and identity. Notion, TURN, LiveKit, S3 and other integrations have separate credentials and trust boundaries.

## Storage and content

Storage encryption is enabled by default, but the running server controls the keys. It does not provide end-to-end encryption against its administrator. PostgreSQL retains some structural metadata in plaintext; see [storage](data-storage.md).

Uploaded and imported content is validated before use. Archive paths, block structures, file access and imported links are constrained. Rich content is sanitized; a note or import must not execute arbitrary scripts. File-type and size restrictions still apply when content originates from an external integration.

The security journal and incident controls support investigation and temporary restrictions. Journal access is privileged; diagnostic output must omit passwords, tokens, message content and raw media.

## Operational practice

Keep secrets out of Git, images, URLs and support logs. Use separate disposable environments for tests. Back up data and protect recovery material before upgrades. Report problems with the version, affected feature and a redacted reproduction; include the minimum information needed to investigate. See [backup](admin-backups.md), [retention](data-retention.md) and [deployment](../INSTALL.md).
