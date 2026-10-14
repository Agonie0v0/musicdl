# 进阶配置与维护

[返回 README](../README.md)

## NAS 目录权限

容器默认以 `10001:10001` 运行应用。入口脚本先准备数据目录，再切换到普通用户；通常无需手动修改目录所有者。

如 NAS 共享目录只授权给特定用户，在 NAS 上执行 `id 用户名`，将实际 UID/GID 添加到 Compose：

```yaml
    environment:
      MUSICDL_USER: admin
      MUSICDL_PASS: '你的登录密码'
      PUID: '1000'
      PGID: '1000'
```

在 NAS 上授予该用户对 `data`、`music` 及已有子目录的读写权限，确认挂载未设为只读。保留默认启动用户，通常不要另设 Compose 的 `user`。必须指定 `user` 时，需预先修复宿主机目录权限，并与 `PUID/PGID` 一致。

入口不会递归改变已有音乐库的所有者；NAS 的 ACL 或网络共享限制需在宿主机处理。修改后执行 `docker compose up -d`，用 `docker compose logs --tail 100` 检查启动结果。

## 登录与 HTTPS

Docker 在 Compose 中配置登录账号和密码；源码运行可复制 [.env.example](../.env.example) 为 `.env`。本机回环监听可不启用登录，非回环监听必须配置账号密码。域名和 IP 无需加入白名单。

通过 HTTPS 反向代理访问时，添加 `MUSICDL_COOKIE_SECURE: '1'`；直接 HTTP 访问使用 `0`。反向代理应保留完整路径、查询参数及音频 Range 请求头。

| 环境变量 | 用途 |
| --- | --- |
| `MUSICDL_USER` / `MUSICDL_PASS` | 登录账号和密码，必须同时设置 |
| `MUSICDL_COOKIE_SECURE` | HTTP 为 `0`，HTTPS 为 `1` |
| `MUSICDL_HOST` / `MUSICDL_PORT` | 服务监听地址与端口，本机默认 `127.0.0.1:8765` |
| `MUSICDL_DOWNLOAD_DIR` | 音乐目录；Docker 默认 `/music` |
| `MUSICDL_DATA_DIR` | 数据目录；Docker 默认 `/data` |
| `MUSICDL_CONFIG` | 引擎配置文件；Docker 默认 `/data/config.json` |
| `MUSICDL_MAX_DOWNLOAD_TASKS` | 下载并发数 `1–4`，默认 `2` |
| `PUID` / `PGID` | Docker 应用用户和组，默认 `10001:10001` |

## 机器人配置

优先在网页 **通知与机器人** 中配置，保存后覆盖对应环境变量。Token、Secret 等凭据不回显，留空保留原值；不要把实际凭据提交到仓库。

公网地址支持 HTTP / HTTPS 域名或 IP，可带端口，如 `http://192.168.1.20:8765`、`https://music.example.com`；省略协议时自动补 `http://`，不含路径或查询参数。填写手机可访问的地址；企业微信回调还需平台可访问。下载链接默认有效 24 小时，可设为 60–604800 秒。

连接测试使用当前填写的渠道凭据和代理，无需先保存，也不要求先启用或填写公网地址、白名单。测试不保存配置、不发送消息；保存后配置才会生效。

### Telegram

1. 在 BotFather 创建机器人，将 Token 填入网页。
2. 填写允许使用者的数字用户 ID，多个用逗号分隔，不能填用户名或群 ID。
3. 按需选择“使用项目代理”，测试连接后启用并保存。
4. 与机器人私聊发送 `/start` 或 `/menu`，也可直接发送歌名。

使用长轮询接收命令，无需公网回调。如果此前配置过 webhook，先移除。一个 Token 只运行一个服务实例。

### 企业微信

1. 创建自建应用，配置可见成员，将 CorpID、AgentID、Secret 填入网页。
2. 在企业微信后台配置服务器出口的可信 IP。
3. 生成消息回调 Token 和 EncodingAESKey，填入网页后启用并保存。
4. 在企业微信后台将接收消息 URL 设为 `https://music.example.com/bot/wecom`，填写相同的 Token、EncodingAESKey，完成验证。
5. 打开应用会话发送“帮助”。白名单使用成员 UserID；留空允许应用可见成员。

这里只支持成员与应用的直接会话。企业微信平台要求的可信 IP 与本项目已移除的访问主机白名单无关。

### 命令与下载链接

| 操作 | 命令 |
| --- | --- |
| 菜单 | `帮助`、`菜单`、`/start`、`/menu` |
| 搜索 | 直接发送歌名，或 `/search 歌名 歌手` |
| 翻页 | `下一页`、`上一页`、`第2页` |
| 下载 | `下载 2` 或 `/download 2`，按搜索结果编号选择 |
| 查看进度 | `状态` 或 `/status` |
| 管理任务 | `取消 任务编号`、`继续 任务编号`、`链接 任务编号` |

Telegram 也可点击消息按钮操作。搜索先显示全部已启用音源按钮，各源并行执行，完成后更新当前卡片；切换到未完成的源显示“搜索中”。机器人音源留空跟随 Web 已启用音源，填写时必须使用已启用的音源 ID 或完整引擎名称。每源最多保留 50 首，每页 5 首。企业微信发送 `音源 2` 切换音源。

机器人与网页共用下载队列。Telegram 的 MP3 / M4A / FLAC 优先使用 `sendAudio`；FLAC 上传时保留原文件名和字节，并标记 `audio/flac`，客户端支持时可在聊天内播放。音频格式被明确拒绝后才改用 `sendDocument`，不转码。文件采用流式上传，不把整个附件读入内存，上传期间不阻塞搜索卡片更新。Telegram 文件均先尝试上传，超过 50 MB 也不提前拦截；接口明确拒绝文件大小后回退到签名下载链接，并在日志和通知中显示实际错误。已拒绝上传的文件在链接通知重试时不会重复上传。企业微信仍使用临时素材发送文件，超过 20 MB 时提供链接。网络错误保留通知重试，不重新下载曲目；`/file 任务编号` 可重新尝试发送文件，`/link 任务编号` 可获取链接。

Telegram 文档标注云端上传上限为 50 MB，官方 [Local Bot API Server](https://core.telegram.org/bots/api#using-a-local-bot-api-server) 的 `--local` 模式为 2000 MB。可在 Web 设置填写自建 API 地址（例如 `http://telegram-bot-api:8081`，或环境变量 `MUSICDL_TG_API_URL`）。本项目不会按这两个数值提前拒绝文件，以实际接口响应为准，这不代表已验证云端可突破 50 MB。切换服务器前按官方部署说明从云端 API `logOut`；本项目通过 multipart 上传，不需要两个服务共享音乐目录。仅使用 Telegram 文件交付时访问地址可留空，大小被拒绝后的下载链接需要配置访问地址。实测时可在“运行日志 → 通知与机器人”查看原文件大小、`sendAudio` / `sendDocument`、API 是否接受以及回退原因；上传被接受也需在实际客户端确认 FLAC 播放。

### 飞书、钉钉、Discord 下载通知

在“更多下载通知渠道”填入平台生成的 HTTPS 群 Webhook 并启用。飞书 / Lark 与钉钉可填写加签密钥；如果使用关键词校验，请允许 `musicdl`。Webhook 为凭据，界面不回显；留空保留原值，可勾选清除。配置检查只校验地址和格式，不向群发送测试消息，实际权限及平台安全设置在首次通知时验证。

通知启用后产生的新下载任务的完成、失败、取消状态，覆盖 Web 与私聊下载；首次启用不会推送历史任务。完成通知附签名下载链接，访问地址须能被群成员访问，链接持有者可以下载，请控制群成员范围与有效期。Webhook 通道仅通知结果；搜索、选曲和直接文件交付使用 Telegram 或企业微信。

`/downloads/` 和 `/bot/wecom` 使用各自的签名鉴权，反向代理不要在这些路径增加额外登录验证。透传 Range / If-Range，不缓存下载响应；链接持有者在有效期内可以下载对应文件，不要公开转发链接。

保留 `data` 目录以保留任务归属、待发送通知和链接密钥。运行单个服务进程，不要启动多个 Uvicorn worker。

## 音源高级配置

普通使用无需展开“分源高级配置”。每个字段填写独立 JSON 对象，外层键使用完整音源类名，例如 `NeteaseMusicClient`。

| 字段 | 用途 |
| --- | --- |
| `init_music_clients_cfg` | 分源初始化参数，覆盖对应全局参数 |
| `clients_threadings` | 分源线程数 |
| `requests_overrides` | 请求头、超时、独立代理等请求参数 |
| `search_rules` | 原引擎各音源专用的搜索和解析规则 |

例如，为网易云设置 20 秒请求超时，在“分源请求覆盖”中填写：

```json
{
  "NeteaseMusicClient": { "timeout": 20 }
}
```

已有其他音源条目应保留。旧版分源代理优先于项目代理，可在音源面板切换为项目代理或直连。部分音源缺少参数时会显示“暂不可用”，补全后再次保存；其他可用音源仍可使用。

## 下载与故障排查

### 搜索速度与运行占用

网页的 QQ、网易云搜索默认先显示候选歌曲，音质显示为“待确认”；点击播放或选择下载版本时才调用原引擎解析该曲目，保留原来的无损优先、Cookie、代理和解析器回退策略。候选不是可下载承诺，解析失败会明确提示换源或重试。其他音源继续使用原引擎的完整搜索。机器人仍搜索已解析的曲目，但会在一个音源全部完成前显示已有结果，已展示的下载编号不再随排序改变。

搜索进程按需创建，最多保留两个空闲进程供后续任务复用，默认空闲 20 秒回收。取消或超时会终止正在执行该任务的进程；相同查询的多个订阅者共享任务，只有最后一个订阅者退出才会取消。网页断开后保留 10 秒重连时间，创建后 30 秒未订阅的搜索会自动停止。

成功且非空的查询默认缓存 30 秒，缓存同时受 32 条查询和 8 MiB 序列化数据限制；关键词、音源、完整配置分别参与缓存键。空结果和失败不会缓存。网页连续提交同一个关键词会强制刷新；API 调用者可以在 `POST /api/search/jobs` 中传 `refresh: true`。缓存只短期复用解析结果，外部音频链接仍可能提前失效。

以下环境变量在服务重启后生效；Docker 部署填入 Compose 的 `environment`，本机运行填入 `.env`：

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `MUSICDL_SEARCH_WORKERS` | `4` | 同时执行的音源搜索/按需解析数，范围 1–6；低内存 NAS 可试 2 |
| `MUSICDL_SEARCH_WORKER_IDLE` | `20` | 空闲进程保留秒数；0 表示每个任务结束即退出 |
| `MUSICDL_SEARCH_BUDGET` | `120` | 一个音源或按需解析的总执行秒数，不含排队 |
| `MUSICDL_SEARCH_QUEUE_TIMEOUT` | `30` | 等待搜索槽位的最长秒数 |
| `MUSICDL_SEARCH_CACHE_TTL` | `30` | 查询缓存秒数；0 关闭已完成结果缓存，仍合并正在执行的相同查询 |
| `MUSICDL_SONG_CACHE_MB` | `32` | 完整曲目的内存缓存预算，按序列化大小估算，同时最多 3000 首；大曲目保留磁盘副本，按需读取 |
| `MUSICDL_LAZY_SEARCH` | `1` | QQ、网易网页搜索按需解析；0 恢复搜索时解析所有曲目 |

网页设置里的请求超时仍是单次网络请求超时，与上述总搜索预算不同。运行日志记录每源排队时间、执行时间和首条增量时间。候选列表出现时间与首条可播放时间也不同，评估时需要分别记录。

下载进度文件最多约每秒更新，运行中的进度数据库提交最多约每两秒一次；完成、失败和暂停等状态立即保存。网页有活动下载时约每 1.5 秒刷新，无活动任务时约每 15 秒刷新，后台标签页暂停下载列表请求。歌单解析池限制为一个进程，定期更换进程。曲目缓存预算不是整个服务的内存上限，进程、元数据和活动任务仍占用额外内存。

离线测量重复启动开销：`python tests/benchmark_search.py`。此脚本使用真实引擎和进程通信，但禁用音源网络查询，不能代表实际平台的搜索耗时。

暂停保留临时数据；取消清理临时文件。HTTP 音源支持 Range 且文件校验通过时可续传，否则从头下载。HLS 和专用解密音源取决于原下载器。移除下载记录保留已完成文件，音乐文件页的删除则会删除磁盘文件。

搜索、播放或通知失败时，在 **运行日志** 中按类别和错误级别筛选并导出。日志保留当前进程最近 800 条，重启后清空；Windows 启动问题查看 `logs/startup.log`。

部分酷狗 CDN 证书异常时，试听会对不携带账号凭据的请求执行兼容重试。需要严格证书校验时设置 `MUSICDL_PREVIEW_STRICT_TLS=1`；这不改变原下载器行为。

## 开发检查

```sh
python -m pip install -r requirements.txt pytest httpx
npm ci --prefix frontend
npm run build --prefix frontend
npm run format:check --prefix frontend
npm test --prefix frontend
python -m pytest tests/test_engine_compat.py tests/test_resumable_download.py tests/test_search_runtime.py -q
python -m pytest tests/test_bot_notifications.py -q
python -m pytest legacy/tests/test_app.py -q
python -m pytest tests/test_launcher.py tests/test_docker_entrypoint.py -q
```

浏览器检查：在 `frontend` 目录运行 `npx playwright install chromium`，再运行 `npm run test:browser`。Windows 可通过 `MUSICDL_BROWSER_EXECUTABLE` 指定 Chrome / Edge 路径。

源码构建 Docker：

```sh
docker build -t ghcr.io/agonie0v0/musicdl:latest .
docker compose up -d --pull never
```

推送 `main` 后，GitHub Actions 构建 `latest` 和对应提交的 `sha-...` 镜像，并检查容器启动。发布成功后再拉取更新；容器启动通过不代表所有外部音源可用。
