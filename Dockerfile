# musicdl-webui —— 供 NAS / Docker 部署，可把歌直接下进 navidrome/Jellyfin 库
FROM python:3.13-slim

# ffmpeg：部分音源用 HLS 分片，写标签/转封面也需要
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY static ./static

# 容器内默认对外监听；下载目录 /music 建议挂到 navidrome 的音乐库
ENV MUSICDL_HOST=0.0.0.0 \
    MUSICDL_PORT=8765 \
    MUSICDL_WORKDIR=/music \
    MUSICDL_CONFIG=/data/config.json \
    PYTHONIOENCODING=utf-8

EXPOSE 8765
VOLUME ["/music", "/data"]

CMD ["python", "app.py"]
