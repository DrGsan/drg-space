# Existing HTTPS reverse proxy

Set `PUBLIC_URL=https://notes.example.com`. Configure DNS for that hostname and terminate TLS on your reverse proxy. Forward to the application's HTTP port 8000 using the actual reachable server address; do not forward to a proxy's administration port.

The default Compose port is bound to 127.0.0.1. A proxy on another machine/container needs an explicitly configured reachable binding or a shared Docker network. Store that installation-specific change in an untracked Compose override.

Enable WebSocket forwarding and suitable upload/idle timeouts. Preserve Host, X-Forwarded-Proto and client forwarding headers. Set TRUSTED_PROXIES only to the actual proxy's addresses or narrow subnets. Never expose a broadly trusted forwarded-header path directly to untrusted clients.

Reuse all overlay files and the existing Compose project name on every update. The application data volume, keys and schema must remain unchanged.
