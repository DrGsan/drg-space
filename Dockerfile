# syntax=docker/dockerfile:1
FROM node:26.11.1-alpine AS frontend
WORKDIR /build
COPY package.json package-lock.json build.mjs ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -s /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; npm install --global npm@12.2.0 --no-audit --no-fund && npm ci --no-audit --no-fund
COPY scripts/i18n-check.mjs scripts/dependency-notices.mjs ./scripts/
COPY web ./web
COPY locales ./locales
COPY app ./app
RUN npm run build
FROM python:3.14.8-slim
WORKDIR /app
COPY requirements.lock.txt ./
RUN --mount=type=secret,id=system_ca \
    if [ -s /run/secrets/system_ca ]; then export PIP_CERT=/run/secrets/system_ca; fi; pip install --no-cache-dir --upgrade pip==26.2.1 && pip install --no-cache-dir -r requirements.lock.txt && useradd --uid 10001 --create-home appuser && mkdir /data && chown appuser /data
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser locales ./locales
COPY --chown=appuser:appuser VERSION CHANGELOG.md LICENSE NOTICE ./
COPY --chown=appuser:appuser gunicorn.conf.py ./
COPY --chown=appuser:appuser scripts/backup.py scripts/security.py scripts/storage_audit.py ./scripts/
COPY --from=frontend --chown=appuser:appuser /build/app/static ./app/static
USER appuser
ENV DATA_DIR=/data
EXPOSE 8000 8444
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app.main:app"]
