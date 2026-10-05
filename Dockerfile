# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir uv==0.9.9 && uv sync --locked --no-dev --no-editable
RUN mkdir -p /data/uploads /data/jobs && chown -R 10001:10001 /data
USER 10001:10001
ENV AGENT_MODE=server AGENT_PUBLIC_URL=https://agent.glocalstorage.cn \
    AGENT_ROOT=/data/uploads AGENT_DATA_DIR=/data/jobs
EXPOSE 8768
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8768/healthz', timeout=3)"]
CMD ["/app/.venv/bin/glocal-agent", "serve", "--host", "0.0.0.0"]
