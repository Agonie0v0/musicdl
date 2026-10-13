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

公网地址格式为 `https://music.example.com`，不含路径或查询参数。下载链接默认有效 24 小时，可设为 60–604800 秒。

### Telegram

1. 在 BotFather 创建机器人，将 Token 填入网页。
2. 填写允许使用者的数字用户 ID，多个用逗号分隔，不能填用户名或群 ID。
3. 按需选择“使用项目代理”，启用、保存并测试连接。
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

Telegram 也可点击消息按钮操作。机器人与网页共用下载队列，返回原文件链接，不通过聊天附件上传或转码。

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
python -m pytest tests/test_engine_compat.py tests/test_resumable_download.py -q
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
