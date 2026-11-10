FROM python:3.14.8-slim
WORKDIR /check
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock.txt ./constraints.txt
RUN pip install --no-cache-dir --constraint constraints.txt Flask PyJWT cryptography pydantic httpx pytest
COPY app/federation ./app/federation
COPY scripts/federation-smoke.py ./scripts/federation-smoke.py
COPY scripts/federation/docker_node.py ./scripts/federation/docker_node.py
COPY tests/test_federation_protocol.py tests/test_federation_probe.py ./tests/
CMD ["python", "scripts/federation/docker_node.py", "serve"]
