# Optional Cloudflare Tunnel

Use an account-managed Cloudflare Tunnel with a hostname you control, for example notes.example.com. Set PUBLIC_URL=https://notes.example.com. Configure the published HTTP route to http://app:8000 when using the bundled overlay network; leave the path unrestricted for the whole site.

Store the cloudflared token in an untracked local file with mode 0600, for example .cloudflare-tunnel.token. Set CLOUDFLARED_TOKEN_FILE to its path and use the file's actual uid/gid for CLOUDFLARED_UID/GID. Do not put token contents into Compose, Git, command history or support logs.

Include compose.cloudflare.yaml in every deployment/update command together with your existing project name and overlay files. It is an alternative to the built-in Caddy overlay. The default overlay uses a fixed internal tunnel address and subnet: check for conflicts before using it, and adjust the network and TRUSTED_PROXIES together if required. Trust only actual proxy addresses, not arbitrary caller-supplied forwarded headers.

Do not apply Cache Everything or other rules that override the application's Cache-Control to authenticated pages, API, service workers or private files. Verify WebSockets, long uploads, original client address handling and authorization through the full proxy chain.

See https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/ for current tunnel/network requirements. Your domain, token, network and routing are installation-specific and do not belong in this repository.
