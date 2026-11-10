# Installation

## Requirements

- Docker Engine with Compose v2; Python 3.14 for the configuration generator.
- Persistent local storage for the application data volume.
- A domain and HTTPS reverse proxy when exposing the service outside localhost.

For development without Docker, install Python dependencies from `requirements.lock.txt`, Node.js 26 dependencies with `npm ci`, and build with `npm run build`. Generate `.env`, then start with `.venv/bin/gunicorn --config gunicorn.conf.py app.main:app` (load your `.env` into the process environment first).

## First start

Clone your repository into `drg-space`, then run:

```sh
cd drg-space
python3 scripts/init-env.py
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 app
```

The generator creates the complete configuration from `.env.example`, generates independent keys, writes mode 0600, and refuses to overwrite an existing `.env`. Wait for `healthy`, open http://localhost:8000, and use the locally stored `SETUP_TOKEN` for the first owner. Complete 2FA enrollment and save recovery codes.

The default port binds only to the server's localhost. For a remote server, an SSH tunnel is sufficient for initial setup:

```sh
ssh -N -L 8000:127.0.0.1:8000 user@example-server
```

## HTTPS

Set `PUBLIC_URL` to the exact public HTTPS origin. For built-in Caddy, also set `SITE_DOMAIN`, point DNS to the server, and use:

```sh
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

For an existing reverse proxy see [Nginx](docs/nginx-public.md); for an optional tunnel see [Cloudflare](docs/cloudflare-tunnel.md). Trust only the actual proxy addresses; do not disable TLS validation.

## Optional PostgreSQL

Set the generated PostgreSQL passwords in `.env` and use:

```sh
docker compose -f compose.yaml -f compose.postgres.yaml up -d --build
```

New installations explicitly use schema `drg_site`. Existing `.env` values and schema names must be preserved. Switching database engines is a data transfer, not a change to perform on an existing volume by guessing connection settings.

## Persistence and updates

Keep `.env`, encryption keys, volumes, Compose project name, and all overlay files. Existing technical names such as `drg-data`, `drg-site-app`, schema fallback `drg`, and the default S3 prefix are intentionally retained for compatibility. The repository name alone does not identify an existing Compose project.

Do not run `docker compose down -v` during an update. Never regenerate working keys. Read [updates](docs/updates.md) before changing a running deployment. Backups are described in [admin backups](docs/admin-backups.md).
