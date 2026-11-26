# Existing HTTPS reverse proxy

Set `PUBLIC_URL=https://notes.example.com`. Configure DNS for that hostname and terminate TLS on your reverse proxy. Forward to the application's HTTP port 8000 using the actual reachable server address; do not forward to a proxy's administration port.

The default Compose port is bound to 127.0.0.1. A proxy on another machine/container needs an explicitly configured reachable binding or a shared Docker network. Store that installation-specific change in an untracked Compose override.

Enable a permanent HTTP-to-HTTPS redirect for the public hostname at the TLS terminator (in Nginx Proxy Manager, enable **Force SSL**). From a client, request `http://notes.example.com/` once without following redirects. Expect a permanent redirect to `https://notes.example.com/`. The HTTPS URL should load the portal with a valid certificate.

Enable WebSocket forwarding and suitable upload/idle timeouts. Preserve Host, overwrite X-Forwarded-Proto with the visitor's actual scheme, and configure client forwarding headers. Set TRUSTED_PROXIES only to the actual proxy's addresses or narrow subnets. Never expose a broadly trusted forwarded-header path directly to untrusted clients.

When a trusted proxy forwards an explicitly plain HTTP request for the configured HTTPS hostname, Space also redirects GET and HEAD to the canonical HTTPS URL. Other HTTP methods and WebSocket upgrades are refused. Direct internal health checks can still use the application's HTTP port.

Reuse all overlay files and the existing Compose project name on every update. The application data volume, keys and schema must remain unchanged.
