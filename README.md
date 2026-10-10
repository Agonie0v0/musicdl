# musicdl

React/TypeScript 音乐界面 + 原版 musicdl 完整搜索、解析和下载引擎。

2026-10-10 修正：此前纯 Rust 版本只迁移五个音源并丢失多条解析路径，造成可下载资源减少。默认启动已切回完整原引擎，保留新版界面。`backend/` 中的 Rust 实验实现仅留作对照，不再作为默认服务。不能以更快地返回不可用结果代替原项目能力。

## 音源与解析

- 使用 `musicdl==2.14.0` 的完整注册表，当前 **57 个音源**；平台、聚合网关、下载站、电台等均从引擎动态读取，没有五源白名单。
- 搜索直接调用原版各源 `.search()`，完整传递线程数、请求覆盖及搜索规则；下载直接调用原版 `MusicClient.download()`。
- 原版 `SongInfo` 完整传递：请求头、Cookie、预取音频、分集、HLS 和专用流对象均保留。不能在线播放的结果仍可下载。
- 原版 `config.json` 优先，包括分源初始化、Cookie、代理范围、请求参数及解析配置。新版设置页提供“原版高级配置”编辑完整 JSON；普通设置不会丢弃高级参数。
- 歌单链接完整传给原版 `parseplaylist()`，不再先截断成 ID。支持展开分集后选择下载。
- 原解析器返回什么音质就展示什么音质，不凭平台元数据虚构多个可用版本。不同解析源返回的版本作为独立结果。平台 Cookie 不是全局必填条件；各音源按自身解析逻辑处理。

“保留全部音源”表示没有删减原引擎能力，不表示外部网站永久可用。部分平台仍可能需要自己的账号、Cookie 或外部媒体工具；引擎原有的默认配置保持不变。

## 搜索与界面

六个独立进程并行运行原音源搜索，按完成顺序通过 SSE 返回；同配置/关键词/音源合并重复请求，结果缓存 120 秒。慢源不阻塞已经完成的源。取消搜索立即停止页面结果更新；正在执行的原引擎请求可能继续到本次解析结束并填入缓存，不冒称能即时中断所有第三方请求。

保留同步歌词、歌词跳转/字号、收藏、播放列表、批量选择、暂停/取消/重试和文件管理。下载运行在独立进程，进度来自原引擎字节计数；暂停会终止该任务进程，继续从头下载。下载成功后才把文件从任务临时目录放入音乐库。原引擎没有上报字节的特殊协议显示不定进度，不伪造百分比。

## Windows

推荐使用 **musicdl-full-engine-windows.zip**，解压后双击 `start.bat`，访问 http://127.0.0.1:8765。包内包含 Python 运行时、原引擎全部基础依赖和已构建前端，无需另外安装 Python、Node.js 或 Rust。必须先完整解压再运行；重复启动会打开已运行的服务。失败时窗口保留，错误写入 `logs/startup.log`。请勿继续用上一版 `musicdl-rust-windows.zip` 或单独的 `musicdl-server.exe`。

源码启动需要 Python 3.11+ 和 Node.js 24：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
.\start.bat
```

Linux/macOS 使用 `./start.sh`。本次 Windows 包使用 Python 3.14.7；部分特殊流媒体下载器需要 FFmpeg 等外部程序，沿用原引擎要求。

## Docker / NAS

GitHub Actions 在每次推送到 `main` 后自动发布 `ghcr.io/agonie0v0/musicdl-full-engine:latest`，同时生成对应提交的 `sha-...` 标签。镜像支持 x86_64 和 ARM64 NAS。当前 GitHub 仓库及 GHCR 镜像包为私有；在飞牛 NAS 的终端先用具有 `read:packages` 权限的 GitHub classic token 登录 GHCR：

```sh
echo '你的 GitHub token' | docker login ghcr.io -u Agonie0v0 --password-stdin
```

在飞牛「Docker / Compose」中创建项目，放入本仓库的 `docker-compose.yml` 和修改后的 `.env`。将 `MUSICDL_BIND=0.0.0.0`、`MUSICDL_TRUSTED_HOSTS=NAS的局域网IP,localhost,127.0.0.1`，并把示例密码换成自己的密码。把 `./music` 和 `./data` 放在 NAS 的持久目录；容器以 UID/GID `10001:10001` 运行，这两个目录必须可写。启动后访问 `http://NAS的局域网IP:8765`。更新时重新拉取镜像并重建容器，数据仍保存在挂载目录。

命令行部署：

```sh
cp .env.example .env
# 在 .env 中设置自己的账号密码、NAS IP、MUSICDL_BIND=0.0.0.0
mkdir -p music data
docker compose up -d --pull always
```

镜像运行 Python 完整引擎与前端静态文件，包含 FFmpeg。Compose 默认只映射宿主机 127.0.0.1；局域网访问设置 `MUSICDL_BIND=0.0.0.0`，并将 NAS IP/域名加入 `MUSICDL_TRUSTED_HOSTS`。本机 Docker daemon 未运行，未完成 NAS 实机验证。

## 配置、登录和数据

默认本机访问可不设置登录。配置了 `MUSICDL_USER` 和 `MUSICDL_PASS` 就启用登录，用户名与密码必须同时设置；示例密码必须自行替换。非本地监听需要认证。

| 变量 | 用途 |
| --- | --- |
| MUSICDL_BIND / MUSICDL_HOST / MUSICDL_PORT | 默认 127.0.0.1:8765；HOST 优先，Compose BIND 为宿主机绑定 |
| MUSICDL_USER / MUSICDL_PASS | 服务登录账号，可使用原版 `_FILE` 密钥文件方式 |
| MUSICDL_TRUSTED_HOSTS | 明确允许访问的域名/IP |
| MUSICDL_COOKIE_SECURE | HTTP 使用 0，HTTPS 使用 1 |
| MUSICDL_DOWNLOAD_DIR / MUSICDL_FILE_ROOT | 受约束的音乐库目录；默认 musicdl_outputs |
| MUSICDL_DATA_DIR | 默认 data，保存 SQLite 与原曲目对象 |
| MUSICDL_CONFIG | 原版完整 config.json 路径；默认项目根目录，Docker 为 /data/config.json |
| MUSICDL_MAX_DOWNLOAD_TASKS | 下载并发 1–4，默认 2，重启生效 |

原版配置文件存在时完整优先加载；不存在时可导入此前 Rust 数据库里的基础音源偏好。SQLite 收藏、播放列表和记录继续使用原表结构。失效的旧曲目会按原音源重新查找完全匹配的歌曲，不会随意下载搜索列表第一首。数据目录包含音源凭据与原曲目对象，不要公开分享。

协议参考和许可证见 [NOTICE.md](NOTICE.md)。验证方法、成功及失败结果见 [VALIDATION.md](VALIDATION.md)。

## 开发检查

```sh
python -m pip install -r requirements.txt pytest httpx
python -m pytest tests/test_engine_compat.py -q
python -m pytest legacy/tests/test_app.py -q
cd frontend
npm run format:check
npm test
npm run build
npx playwright install chromium
npm run test:browser
```

Windows 浏览器测试可将 `MUSICDL_BROWSER_EXECUTABLE` 指向 Edge/Chrome。
