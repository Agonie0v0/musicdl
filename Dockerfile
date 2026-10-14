FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM debian:bookworm-slim AS telegram-api
ARG TELEGRAM_BOT_API_REF=e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1
ARG TELEGRAM_BUILD_JOBS=4
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates cmake g++ make gperf libssl-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init . && git remote add origin https://github.com/tdlib/telegram-bot-api.git \
    && git fetch --depth 1 origin "$TELEGRAM_BOT_API_REF" && git checkout --detach FETCH_HEAD \
    && git submodule update --init --recursive --depth 1 \
    && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/telegram \
    && cmake --build build --target install --parallel "$TELEGRAM_BUILD_JOBS" \
    && strip /opt/telegram/bin/telegram-bot-api

FROM python:3.14-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/Agonie0v0/musicdl"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates ffmpeg curl libssl3 zlib1g libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 musicdl && useradd --uid 10001 --gid musicdl --create-home musicdl \
    && mkdir -p /music /data /app/frontend /licenses && chown -R musicdl:musicdl /music /data /app
WORKDIR /app
ENV HOME=/home/musicdl
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py engine_api.py engine_worker.py search_runtime.py provider_compat.py local_library.py resumable_download.py bot_notifications.py telegram_local.py error_messages.py docker-entrypoint.py ./
COPY legacy/app.py ./legacy/app.py
COPY --from=frontend /build/frontend/dist /app/frontend/dist
COPY LICENSE-musicsquare LICENSE-musicdl-reference /licenses/
COPY --from=telegram-api /opt/telegram/bin/telegram-bot-api /usr/local/bin/telegram-bot-api
COPY --from=telegram-api /src/LICENSE_1_0.txt /licenses/LICENSE-telegram-bot-api
RUN telegram-bot-api --version
ENV HOME=/data/home MUSICDL_BIND=0.0.0.0 MUSICDL_HOST=0.0.0.0 MUSICDL_PORT=8765 MUSICDL_DOWNLOAD_DIR=/music MUSICDL_DATA_DIR=/data MUSICDL_CONFIG=/data/config.json MUSICDL_COOKIE_SECURE=0
EXPOSE 8765
VOLUME ["/music", "/data"]
ENTRYPOINT ["python", "/app/docker-entrypoint.py"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8765/api/health || exit 1
CMD ["python", "app.py"]
