FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/Agonie0v0/musicdl"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates ffmpeg curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 musicdl && useradd --uid 10001 --gid musicdl --create-home musicdl \
    && mkdir -p /music /data /app/frontend /licenses && chown -R musicdl:musicdl /music /data /app
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py engine_api.py engine_worker.py ./
COPY legacy/app.py ./legacy/app.py
COPY --from=frontend /build/frontend/dist /app/frontend/dist
COPY LICENSE-musicsquare LICENSE-musicdl-reference /licenses/
ENV MUSICDL_BIND=0.0.0.0 MUSICDL_HOST=0.0.0.0 MUSICDL_PORT=8765 MUSICDL_DOWNLOAD_DIR=/music MUSICDL_DATA_DIR=/data MUSICDL_CONFIG=/data/config.json MUSICDL_COOKIE_SECURE=0
EXPOSE 8765
VOLUME ["/music", "/data"]
USER musicdl
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8765/api/health || exit 1
CMD ["python", "app.py"]
