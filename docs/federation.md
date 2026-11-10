# Federation

Federation connects independently operated workspaces after explicit trust setup. Ordinary login, notes, files and local collaboration do not require federation.

The application has separate site-level roles and shared-space permissions. Local authorization continues to apply to shared content; knowledge of a remote page or attachment identifier is not permission to read it. Revoked users lose new access. Restoration and account deletion also invalidate the relevant federation state.

Federation uses its own authenticated TLS endpoint, normally port 8444. Set FEDERATION_PUBLIC_URL only when enabling it; preserve the existing bind/address settings. The optional compose.federation.yaml exposes that endpoint, bound to localhost unless FEDERATION_BIND_ADDRESS is explicitly changed. Ordinary website HTTPS and federation TLS may use different endpoints.

Do not expose an unconfigured endpoint or trust arbitrary certificate headers. Verify the advertised hostname, routing, mutual authentication and access revocation between two disposable test instances before enabling federation on a working installation.

Shared notes use the regular block editor and permissions. File transfers and invitations are bounded and authenticated. Federated personal calls use the private WebRTC/TURN transport; group invitation rooms use LiveKit. See voice-calls.md for transport requirements.

Keep federation identities, private keys and working data with the existing volume. Do not copy another installation's identity into a new independent server. Backups are governed by the same schema/key compatibility requirements as the rest of the application.
