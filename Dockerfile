FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY app/ /srv/app/

# Run as an unprivileged user. config.json persists in /data; a volume mounted there must be writable by UID 10001.
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
 && mkdir -p /data && chown app:app /data
ENV CONFIG_PATH=/data/config.json HOME=/tmp
USER app

EXPOSE 8080
WORKDIR /srv/app

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:8080/health || exit 1

# Must stay at 1 worker: config, guard toggle, sessions, rate limits and stats live in process memory.
# Async I/O handles ~30 concurrent booth users fine.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
