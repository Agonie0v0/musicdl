# musicdl-webui —— 供 NAS / Docker 部署，可把歌直接下进 navidrome/Jellyfin 库
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/Agonie0v0/musicdl" \
      org.opencontainers.image.licenses="Apache-2.0"

# ffmpeg：部分音源用 HLS 分片，写标签/转封面也需要
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 musicdl \
    && useradd --uid 10001 --gid musicdl --create-home --home-dir /home/musicdl musicdl \
    && mkdir -p /music /data /licenses \
    && chown -R musicdl:musicdl /app /music /data /opt/venv

COPY --chown=musicdl:musicdl app.py .
COPY --chown=musicdl:musicdl static ./static
COPY LICENSE-musicsquare /licenses/LICENSE-musicsquare

# 容器内默认对外监听；下载目录 /music 建议挂到 navidrome 的音乐库
ENV MUSICDL_HOST=0.0.0.0 \
    MUSICDL_PORT=8765 \
    MUSICDL_WORKDIR=/music \
    MUSICDL_FILE_ROOT=/music \
    MUSICDL_CONFIG=/data/config.json \
    MUSICDL_COOKIE_SECURE=1 \
    MUSICDL_ALLOW_SELF_UPDATE=0 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONIOENCODING=utf-8

EXPOSE 8765
VOLUME ["/music", "/data"]

USER musicdl

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/static/index.html', timeout=3)" || exit 1

CMD ["python", "app.py"]
