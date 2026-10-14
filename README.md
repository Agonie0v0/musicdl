# musicdl

**多音源音乐搜索、在线播放与批量下载。** 支持电脑和手机，可部署到 Docker / NAS，也可在本机运行。

[快速部署](#快速部署) · [网页配置](#网页配置) · [Telegram Local Bot API Server](#telegram-local-bot-api-server) · [进阶指南](docs/configuration.md) · [问题反馈](https://github.com/Agonie0v0/musicdl/issues)

## 核心功能

- **搜索与试听**：多音源并行搜索，支持音质筛选、在线播放、同步歌词和歌词跳转。
- **下载与管理**：批量下载、暂停续传、收藏、歌单导入、本地音乐库及音频标签编辑。
- **通知与机器人**：Telegram、企业微信按音源并行搜索，选曲后直接发送音频或原文件；飞书、钉钉和 Discord 支持下载结果通知。
- **统一代理**：在网页配置一次代理地址，音源和 Telegram 分别选择是否使用。
- **桌面与手机**：浅色 / 深色主题，手机顶栏可左右滑动，支持退出登录。

音源由原版 musicdl 引擎提供；可用性和音质取决于平台，部分音源需要 Cookie、代理或额外配置。

## 快速部署

推荐 Docker Compose。镜像支持 **amd64 / arm64**，包含 FFmpeg。

保存为 `docker-compose.yml`，先修改登录密码：

```yaml
services:
  musicdl-webui:
    container_name: musicdl
    image: ghcr.io/agonie0v0/musicdl:latest
    restart: unless-stopped
    init: true
    ports:
      - "8765:8765"
    volumes:
      - ./music:/music
      - ./data:/data
    environment:
      MUSICDL_USER: admin
      MUSICDL_PASS: '请改成你自己的密码'
```

```sh
mkdir -p music data
docker compose up -d
```

打开 **`http://服务器IP:8765`**，使用上面的账号密码登录。无需配置可信域名或 IP，也无需创建 `.env`。密码包含 `$` 时，在 Compose 中写成 `$$`。

| 目录 | 保存内容 |
| --- | --- |
| `./music` | 下载的音乐和歌词 |
| `./data` | 配置、收藏、歌单、下载记录和机器人数据 |

**更新时保留这两个目录。** 更新和查看日志：

```sh
docker compose up -d --pull always
docker compose logs -f --tail 100
```

## 网页配置

### 音源与代理

打开侧栏 **音源与设置**，选择音源、下载目录及下载偏好；需要账号的音源可展开填写 Cookie。

在 **项目代理** 中填写 HTTP / HTTPS 地址，保存后地址仍可查看和修改。音源可选择全部使用、仅指定音源或不使用；Telegram 在 **通知与机器人 → Telegram 代理** 中选择“使用项目代理”。仅给 Telegram 使用时，将音源范围设为“音源不使用代理”。

代理必须能从服务所在环境访问；Docker 中的 `127.0.0.1` 是容器自身。旧版独立代理继续保留，可手动切换为项目代理。

### Telegram / 企业微信

1. 打开 **通知与机器人**，填写手机可访问的 **HTTP / HTTPS 地址**，支持域名或 `IP:端口`；省略协议时使用 HTTP。
2. 填写渠道凭据，普通渠道可直接测试当前输入的连接，无需先保存。补全用户白名单后启用并保存；内置 Telegram 本地服务的首次测试例外，见下方说明。
3. 向机器人发送歌名搜索，点击音源切换结果（未完成的源显示搜索中），选择编号下载；完成后收到音频或原文件，超限时提供下载链接。

Telegram 白名单填写**数字用户 ID**，仅支持私聊。企业微信还需配置应用可见范围、可信 IP 和消息回调，见[机器人配置指南](docs/configuration.md#机器人配置)。配置保存后生效，无需重启。需要大文件上传时，按下面的 [Telegram Local Bot API Server](#telegram-local-bot-api-server) 指南配置内置服务。

Telegram 文档标注云端上传上限为 50 MB，官方 Local Bot API 的 `--local` 模式为 2000 MB。在通知设置中开启“使用内置 Telegram 本地服务（大文件上传）”并填写 my.telegram.org 的 API ID/API Hash，Docker 镜像会自动启动官方 `--local` 服务；也可以继续填写自建 API 地址。项目不按数值提前拦截，超过限制时根据接口响应回退到签名下载链接。MP3 / M4A / FLAC 优先使用 `sendAudio`，格式被拒绝时改用 `sendDocument`，始终保留原文件、不转码。失败通知和运行日志会先显示易懂原因，展开日志中的“查看原始信息”可查看平台原文。

飞书、钉钉和 Discord 在 **更多下载通知渠道** 中填写群 Webhook，通知新下载任务的完成、失败与取消，并附下载链接；群 Webhook 不提供搜索和选曲交互。飞书、钉钉支持加签密钥。

新下载文件使用 `歌曲名 - 歌手.扩展名`，重名时附任务标识以保留已有文件；歌词与音频保持同名。代理和搜索音源列表默认收起，展开勾选后点击“完成选择，收起”。

## Telegram Local Bot API Server

Telegram 官方云端 Bot API 的单文件上传上限是 50 MB。官方 Local Bot API Server 的 `--local` 模式把 Bot API 部署到 musicdl 所在机器，单文件上限提高到 2000 MB，并允许直接使用本地文件。它仍然需要连接 Telegram 网络；它不是离线服务，也不是 Telegram 客户端。musicdl 会继续通过 Bot Token 访问同一个机器人。

### 推荐方式：Docker 镜像内置服务

项目发布的 Docker 镜像已经内置官方 `telegram-bot-api` 程序。只需保留现有的 `./data` 挂载，不需要额外创建 API Server 容器，也不需要把 8081 映射到宿主机：musicdl 在容器内启动官方程序，并让它只监听 `127.0.0.1:8081`。

先更新到包含该程序的镜像并启动：

```sh
docker compose pull
docker compose up -d
docker compose logs -f --tail 100
```

`./data/telegram-bot-api` 用于保存官方程序的运行数据和临时文件。升级或重建镜像时不要删除 `./data`，否则可能需要重新建立本地服务状态。不要在同一容器或宿主机上再手动启动一个占用 8081 的 `telegram-bot-api`。

### 获取 Telegram 应用凭据

Local Bot API 需要 **API ID** 和 **API Hash**。这两项凭据属于你的 Telegram 应用，与 BotFather 生成的 Bot Token、Telegram 数字用户 ID 和手机号完全不同。

1. 使用 Telegram 账号登录 [my.telegram.org](https://my.telegram.org)。
2. 打开 **API development tools**；没有应用时按表单创建一个应用，已有应用直接查看。
3. 复制页面显示的 `api_id`（正整数）和 `api_hash`（32 位十六进制字符串）。
4. 将它们视为密钥保存，不要提交到 Git、截图公开或发到群聊。Bot Token 仍在 [@BotFather](https://t.me/BotFather) 创建机器人时获取。

API ID 不是 Bot ID，也不是允许使用机器人的用户 ID。musicdl 会在保存和启动前检查格式，但无法替 Telegram 判断凭据是否属于有效应用。

### 在 musicdl 网页中开启

建议按以下顺序操作：

1. 打开 **通知与机器人 → Telegram**，填写 BotFather 的 **Bot Token**、数字用户 ID 白名单，并勾选 Telegram 通知。
2. 勾选 **使用内置 Telegram 本地服务（大文件上传）**。
3. 填写 **Telegram API ID** 和 **Telegram API Hash**。勾选内置服务后，页面会隐藏自定义 Bot API 地址；代理仍按 Telegram 代理选项单独决定。
4. 先点击测试检查表单。如果内置服务尚未按这组凭据运行，测试只会检查官方程序和凭据格式，并提示“保存后启动并切换服务，再次测试可验证 Telegram 连接”；此时不会启动服务、注销云端或发送测试消息。
5. 点击保存。保存且 Telegram 已启用后，musicdl 会启动内置服务并切换到 `127.0.0.1:8081`。
6. 再次点击测试；这次会实际调用 Telegram 的 `getMe`。然后向机器人私聊发送 `/start` 或歌曲名，确认搜索和下载通知正常。
7. 最后用一首超过 50 MB 的文件验证大文件链路。页面状态“运行中”只表示本地进程已启动，不能代替真实网络和上传测试。

测试结果会显示在对应按钮下方。运行失败时，打开 **运行日志 → 通知与机器人** 查看中文原因；展开“查看原始信息”可以看到 Telegram 返回的原文。

### 环境变量方式（可选）

也可以在源码运行的 `.env` 或 Compose 的 `environment` 中预填：

```dotenv
MUSICDL_TG_TOKEN=从BotFather获取的Token
MUSICDL_TG_ALLOWED_USERS=123456789
MUSICDL_TG_LOCAL_ENABLED=1
MUSICDL_TG_LOCAL_API_ID=12345678
MUSICDL_TG_LOCAL_API_HASH=0123456789abcdef0123456789abcdef
```

网页中保存过的通知配置优先于环境变量；环境变量适合首次启动或没有网页访问权限的部署。源码运行时必须另外安装官方 `telegram-bot-api` 并让它位于 `PATH`，或者设置 `MUSICDL_TG_LOCAL_BINARY` 为程序的绝对路径。Windows 完整包和普通源码目录不保证自带该程序，Docker 镜像才保证内置。

### 网络、代理和数据目录

- 内置程序使用部署环境自己的网络出口连接 Telegram。网页里的项目 HTTP 代理只代理 Bot API 请求，不会自动变成内置程序连接 Telegram 的代理；如果服务器无法直连 Telegram，需要在宿主机、容器网络或出口层解决。
- 内置服务只监听容器或本机回环地址，通常不需要开放 8081、防火墙放行或反向代理。远程访问 musicdl 仍只需要网页使用的 8765 端口。
- 文件直接发送到 Telegram 时不要求配置公网地址。超过接口限制回退到签名下载链接时，`MUSICDL_PUBLIC_URL` 必须是手机或聊天成员可访问的 HTTP / HTTPS 地址。
- Local Bot API 与 musicdl 共用 Bot Token，但不需要与 `./music` 共享目录；官方程序只使用 `./data/telegram-bot-api`。
- 云端模式上传上限约 50 MB，`--local` 模式约 2000 MB；musicdl 不按数值提前拦截，以 Telegram 实际响应为准。

### 源码或自建 API Server（可选）

源码部署可以从[官方仓库](https://github.com/tdlib/telegram-bot-api)构建固定版本的程序。下面是 Debian / Ubuntu 的示例；Dockerfile 当前使用的固定提交与命令也保持一致。低内存机器可把并行度改成 `1`。需要其他系统时，使用官方[构建指令生成器](https://tdlib.github.io/telegram-bot-api/build.html)。

```sh
sudo apt-get update
sudo apt-get install -y git ca-certificates cmake g++ make gperf libssl-dev zlib1g-dev
git clone --recursive https://github.com/tdlib/telegram-bot-api.git
cd telegram-bot-api
git checkout e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1
git submodule update --init --recursive
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/telegram
sudo cmake --build build --target install --parallel 2
sudo ln -sf /opt/telegram/bin/telegram-bot-api /usr/local/bin/telegram-bot-api
telegram-bot-api --version
```

构建完成后，将 `telegram-bot-api` 放入 `PATH`，或设置：

```dotenv
MUSICDL_TG_LOCAL_BINARY=/opt/telegram/bin/telegram-bot-api
```

不需要手动启动它，也不要手动占用 8081；保存网页配置后由 musicdl 自动启动、监控和停止子进程。若程序安装在另一台机器或另一个容器，启动官方程序时必须使用 `--local`，然后关闭内置选项，在 **Telegram Bot API 地址** 中填写可达的 origin，例如 `http://telegram-bot-api:8081`。地址只写协议、主机和端口，不要附加 `/bot<TOKEN>`、`/getMe` 或其他路径；Docker 中的 `127.0.0.1` 指向当前容器自身。官方程序只接受 HTTP，远程 HTTPS 需要在前面加反向代理。Bot API Server 与 musicdl 不需要共享音乐目录，因为上传通过 multipart 请求完成。

### 切换服务器时的注意事项

已有云端 Telegram 配置时，在网页中保持 Telegram 启用并切换到内置模式，musicdl 会按条件对旧服务调用 `logOut`，然后启动本地服务。Telegram 官方规定：云端 `logOut` 成功后，机器人不能在随后 10 分钟内重新登录云端；这不影响继续登录本地服务。不要让同一个 Token 同时被多个长轮询或 webhook 实例使用。

如果 Token 正在其他程序中使用，或你通过环境变量首次启用本地模式，musicdl 无法替你管理那个外部实例。请先按[官方迁移说明](https://core.telegram.org/bots/api#moving-a-bot-to-a-local-server)处理 webhook、旧实例和机器人数据，再启动 musicdl。切回云端前同样要停止本地服务并确认没有其他实例占用 Token。

### 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 页面提示找不到官方程序 | 当前环境不是包含内置程序的 Docker 镜像，或源码部署未安装程序。更新镜像，或安装后把程序加入 `PATH` / 设置 `MUSICDL_TG_LOCAL_BINARY`。 |
| API ID/API Hash 格式错误 | API ID 必须是正整数，API Hash 必须是 32 位十六进制字符串；从 [my.telegram.org/apps](https://my.telegram.org/apps) 复制，不要填 Bot Token。 |
| 8081 已被占用 | 停止宿主机或容器中的其他 `telegram-bot-api`，或者关闭内置模式并填写一个独立的自建 API 地址。 |
| 服务已运行但测试超时 | 服务进程启动不代表能访问 Telegram。检查宿主机/容器出口、DNS、防火墙和代理；再看运行日志中的原始错误。 |
| 仍然收到“文件过大” | 当前请求可能仍使用云端或自建服务，确认网页状态已切到内置模式并重新测试；不同 API Server 的限制以实际响应为准。 |
| 收到 409 Conflict | 同一 Bot Token 被另一个长轮询或 webhook 实例使用，停止旧实例并清理 webhook 后重试。 |
| 收到 `chat not found` 或 `Forbidden` | 白名单必须填数字用户 ID，用户需要先私聊机器人发送 `/start`；机器人不能主动向未开始对话的用户发送消息。 |
| 切回云端暂时无法登录 | 云端 `logOut` 后官方限制约 10 分钟内不能重新登录云端；等待限制结束，并确认本地和其他实例都已停止。 |

## 本机运行

从源码运行需要 **Python 3.11+、Node.js 24**，建议安装 FFmpeg。首次启动会安装依赖并构建界面。

| 系统 | 启动方式 |
| --- | --- |
| Windows | 完整解压源码后，双击 `start.bat` |
| Linux / macOS | 在项目目录执行 `sh start.sh` |

默认访问 `http://127.0.0.1:8765`。如使用包含运行环境的 Windows 完整包，无需另装 Python 和 Node.js。

## 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 下载完成后文件在哪里？ | Docker 默认在宿主机 `music` 目录；“下载到本机”是再保存一份到浏览器所在设备。 |
| 音源搜索或播放失败？ | 先换源，再查看 **运行日志**；按提示检查 Cookie、代理和平台权限。 |
| NAS 提示 `Permission denied`？ | 检查挂载目录权限，按[目录权限说明](docs/configuration.md#nas-目录权限)配置 `PUID/PGID`。 |
| 需要 HTTPS、环境变量或高级参数？ | 查看[进阶配置与维护](docs/configuration.md)。 |

## 开发与许可

前端使用 React / TypeScript，后端复用 musicdl 的搜索、解析和下载引擎。[开发检查命令](docs/configuration.md#开发检查)

上游：[CharlesPikachu/musicdl](https://github.com/CharlesPikachu/musicdl)，采用 **PolyForm Noncommercial 1.0.0**。来源及完整许可见 [NOTICE.md](NOTICE.md)、[引擎许可](LICENSE-musicdl-reference) 和 [原仓库许可](LICENSE-musicsquare)。
