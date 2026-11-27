FROM python:3.14.10-slim-trixie@sha256:22eafb370920ad8b429e0aec3fff433e0669968519f5d60469d83729c138b28b AS deps
WORKDIR /check
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock.txt ./requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt
FROM deps
COPY app ./app
COPY VERSION CHANGELOG.md ./
COPY docs/releases/federation-3.18.md ./docs/releases/
COPY scripts/federation/prototype_node.py scripts/federation/prototype_client.py scripts/federation/call_media_bridge.py ./scripts/federation/
COPY scripts/federation/prototype_gateway.py ./scripts/federation/
COPY scripts/federation/database_scenario.py ./scripts/federation/
COPY scripts/federation-smoke.py ./scripts/
COPY tests ./tests
CMD ["python", "scripts/federation/prototype_node.py", "serve"]
