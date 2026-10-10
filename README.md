# musicdl

音乐搜索、在线播放与批量下载工具。使用 React / TypeScript 界面，后端复用 musicdl 2.14.0 的完整搜索、解析和下载引擎。

## 功能

- 从原引擎动态读取音源，当前版本包含 57 个音源。
- 多音源并行搜索，已完成的结果即时显示，重复搜索短时缓存。
- 展示解析得到的音频格式和无损信息，支持在线播放与同步歌词。
- 批量下载、暂停、取消、重试、收藏、播放列表及文件管理。
- 支持歌单链接、分集选择和原版高级配置，包括分源 Cookie、代理与解析参数。

音源可用性取决于外部服务；部分音源需要平台账号、Cookie 或额外工具。平台 Cookie 不是所有搜索和下载的前置条件。曲目音质以解析结果为准。

## Docker 部署

镜像：`ghcr.io/agonie0v0/musicdl:latest`，支持 `linux/amd64` 和 `linux/arm64`，包含 FFmpeg。

公开镜像可以直接拉取，无需 GitHub 账号或登录 GHCR。使用 Docker Compose 部署：

```sh
git clone https://github.com/Agonie0v0/musicdl.git
cd musicdl
cp .env.example .env
# 编辑 .env，设置自己的用户名、密码和访问主机名
mkdir -p music data
```

容器以 UID/GID `10001:10001` 运行，挂载目录必须允许该用户读写。在 Linux 上为新建目录设置所有者后启动：

```sh
sudo chown 10001:10001 music data
docker compose up -d
```

也可以只下载本仓库的 `docker-compose.yml` 和 `.env.example`，放在同一目录后按以上步骤配置和启动。

默认访问 `http://127.0.0.1:8765`。允许其他设备访问时，在 `.env` 中设置 `MUSICDL_BIND=0.0.0.0`，并将服务器 IP 或域名加入 `MUSICDL_TRUSTED_HOSTS`，同时保留 `localhost,127.0.0.1`。主机端口通过 `MUSICDL_HOST_PORT` 设置。

| 挂载目录 | 用途 |
| --- | --- |
| `./music` → `/music` | 下载的音乐与歌词 |
| `./data` → `/data` | 数据库、配置和原曲目对象 |

更新与查看日志：

```sh
docker compose up -d --pull always
docker compose logs -f --tail 100
```

需要从源码构建镜像时：

```sh
docker build -t ghcr.io/agonie0v0/musicdl:latest .
docker compose up -d --pull never
```

## Windows

使用完整运行包 `musicdl-full-engine-windows.zip` 时，先完整解压，再双击 `start.bat`。包内包含 Python、依赖和已构建前端，无需额外安装开发工具。重复启动会打开现有服务；启动错误记录在 `logs/startup.log`。

从源码运行需要 Python 3.11+ 和 Node.js 24，双击 `start.bat` 会创建虚拟环境、安装依赖并构建前端。首次启动需要联网。

## Linux / macOS

安装 Python 3.11+、Node.js 24，以及所需的媒体工具（如 FFmpeg），然后：

```sh
sh start.sh
```

默认访问 `http://127.0.0.1:8765`。

## 配置和登录

复制 `.env.example` 为 `.env` 后修改配置。源码在本机回环地址运行时可以不配置登录；同时设置 `MUSICDL_USER` 和 `MUSICDL_PASS` 就启用认证，非回环监听必须配置认证。没有通用默认密码，示例密码请自行替换。

| 环境变量 | 说明 |
| --- | --- |
| `MUSICDL_USER` / `MUSICDL_PASS` | 登录账号和密码，必须同时设置 |
| `MUSICDL_TRUSTED_HOSTS` | 允许访问的域名/IP，以逗号分隔，不包含协议和端口 |
| `MUSICDL_COOKIE_SECURE` | HTTP 设为 `0`，HTTPS 设为 `1` |
| `MUSICDL_HOST` / `MUSICDL_PORT` | 服务监听地址和端口，默认 `127.0.0.1:8765` |
| `MUSICDL_BIND` / `MUSICDL_HOST_PORT` | Compose 的宿主机端口映射配置 |
| `MUSICDL_DOWNLOAD_DIR` | 音乐目录，源码默认 `musicdl_outputs`，Docker 为 `/music` |
| `MUSICDL_DATA_DIR` | 数据目录，源码默认 `data`，Docker 为 `/data` |
| `MUSICDL_CONFIG` | 原版配置路径，源码默认 `config.json`，Docker 为 `/data/config.json` |
| `MUSICDL_MAX_DOWNLOAD_TASKS` | 下载并发数 `1–4`，默认 `2`，重启生效 |

音源配置可在设置页面修改；“原版高级配置”保留原引擎的完整 JSON。数据目录可能包含平台凭据，备份时应妥善保存。

暂停后继续下载会从头开始。取消搜索会停止页面更新，但已经运行的音源请求可能继续完成并写入缓存。

## 开发

项目结构：

```text
frontend/          React 界面与前端检查
legacy/app.py      原版后端、音源配置及下载逻辑
engine_api.py      新版接口、搜索调度和数据管理
engine_worker.py   原引擎搜索与下载进程
app.py             应用入口
launcher.py        Windows 启动与日志
.github/workflows/ CI 和镜像发布
```

安装与检查：

```sh
python -m pip install -r requirements.txt pytest httpx
npm ci --prefix frontend
npm run build --prefix frontend
python -m pytest tests/test_engine_compat.py -q
python -m pytest legacy/tests/test_app.py -q
python -m pytest tests/test_launcher.py -q
npm run format:check --prefix frontend
npm test --prefix frontend
```

浏览器检查在 `frontend` 中执行 `npx playwright install chromium` 和 `npm run test:browser`。Windows 可通过 `MUSICDL_BROWSER_EXECUTABLE` 指定 Edge 或 Chrome 路径。

推送到 `main` 后，GitHub Actions 自动发布 `latest` 与对应提交的 `sha-...` 镜像标签，并执行容器启动检查。CI 检查原引擎兼容性与前端。容器启动检查通过不代表所有外部音源可用。

## 许可

原引擎来自 [CharlesPikachu/musicdl](https://github.com/CharlesPikachu/musicdl)，许可为 PolyForm Noncommercial 1.0.0。许可与来源说明见 [NOTICE.md](NOTICE.md)、[LICENSE-musicdl-reference](LICENSE-musicdl-reference) 和 [LICENSE-musicsquare](LICENSE-musicsquare)。
