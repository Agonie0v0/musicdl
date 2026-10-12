# musicdl

音乐搜索、在线播放与批量下载工具。使用 React / TypeScript 界面，后端复用 musicdl 2.14.0 的完整搜索、解析和下载引擎。

## 功能

- 从原引擎动态读取音源，当前版本包含 57 个音源。
- 多音源并行搜索，已完成的结果即时显示，重复搜索短时缓存。
- 展示解析得到的音频格式和无损信息，支持在线播放与同步歌词。
- 支持浅色、深色与跟随系统主题；专辑播放页、歌词点击跳转、收藏速览和最近搜索。
- 实时运行日志，包含原引擎搜索、歌单解析和下载进程的输出；支持按级别、活动、关键字或任务编号筛选、暂停更新与导出。
- 批量下载、暂停、继续、取消、重试、收藏、播放列表及文件管理。
- 支持歌单链接、分集选择和原版高级配置，包括分源 Cookie、代理与解析参数。

音源可用性取决于外部服务；部分音源需要平台账号、Cookie 或额外工具。平台 Cookie 不是所有搜索和下载的前置条件。曲目音质以解析结果为准。

搜索结果直接展示音源、曲目版本、文件大小和时长，并提供收藏、播放、下载、加入播放列表按钮。手机端分行显示这些信息和操作。文件大小来自音源解析结果；未提供时显示「未知」，不额外请求音频文件。

搜索结果的试听直接请求原音源，不需要先下载，也不会创建下载任务。试听沿用原引擎下载阶段的客户端、Cookie、代理及请求覆盖。“可试听”表示解析结果提供了 HTTP 地址或音频数据，不保证远端地址持续有效；请求失败会记录在播放日志中。

酷狗部分音频 CDN 的 HTTPS 证书与域名不匹配。对不携带 Cookie、认证头的酷狗音频 CDN 请求，试听支持原下载器的证书兼容重试：正常校验失败后，仅该次音频请求关闭证书校验，不跟随重定向，并记录警告。其他音源和含账号凭据的请求保持原有校验。需要严格校验时设置 `MUSICDL_PREVIEW_STRICT_TLS=1`，此时该类有问题的 CDN 会拒绝试听；这个开关不改变原下载器的行为。

结果支持无损、普通音质、MP3 和未知音质筛选，可叠加「仅可试听」及音源筛选。支持按大小、品质、时长升降序，以及曲名、歌手、音源排序；未知大小或品质排在最后，品质按无损与码率排序。

导入的平台歌单独立保存到「我的歌单」，不覆盖搜索结果。可查找、重命名、删除歌单或移出单首曲目，支持播放全部、加入待播列表、按版本批量下载。删除歌单不会删除下载文件或收藏；歌单数据保存在 `data/musicdl.db`，重启后保留。旧版没有保存歌单分组，之前的导入需要重新导入一次。

歌单详情会列出解析器明确报告的未导入曲目和原因，可直接换源搜索。日志中的「跳过曲目」表示原解析器未获取到该曲目的有效音频地址，不影响其他已导入曲目；版权、权限、网络或解析接口状态都可能导致这种结果，具体原因以音源返回的信息为准。显示的成功与跳过数量来自解析器实际返回的数据，不代表平台完整歌单的总数。

普通 HTTP 音频下载支持断点续传。暂停、网络中断或服务重启后保留临时数据，点击「继续下载」恢复；取消任务会清理临时文件。续传使用 Range / If-Range 校验文件一致性，音源不支持范围请求、缺少可用校验信息或文件已变化时从头下载。HLS 和音源专用解密下载仍使用原下载器，其续传能力取决于原下载器。完整传输后继续使用原引擎补充歌词和写入标签；未完成的文件不会出现在音乐文件列表中。

「音源与设置」保留原版下载目录、结果数、分页大小、重试次数、默认及分源线程数、请求超时、代理范围、Cookie、七项引擎开关，以及客户端参数、请求覆盖和搜索规则。普通设置和高级配置统一保存，已有分源参数不会被简化配置覆盖。

桌面端按 `Ctrl / Command + K` 聚焦搜索，在非输入状态按空格播放或暂停。主题与最近搜索保存在当前浏览器；点击歌词可跳转到对应播放位置。

遇到问题可打开「运行日志」，选择「错误与警告」，查看对应音源、任务编号和异常堆栈，或导出当前筛选日志。日志每秒更新，保留当前服务进程最近 800 条；服务重启后重新记录。Cookie、认证信息及音频签名地址会隐藏。

## Docker 部署

镜像：`ghcr.io/agonie0v0/musicdl:latest`，支持 `linux/amd64` 和 `linux/arm64`，包含 FFmpeg。

无需登录镜像仓库，也不需要创建 `.env`。新建一个文件夹，将下面的内容保存为 `docker-compose.yml`：

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
      # 将 192.168.1.100 改为运行本服务的电脑或服务器 IP
      MUSICDL_TRUSTED_HOSTS: 'localhost,127.0.0.1,192.168.1.100'
```

`container_name: musicdl` 将容器名称固定为 `musicdl`。已有部署添加这一行后，执行 `docker compose up -d`（或在 NAS 中重新部署 Compose 项目）即可应用，无需重新构建镜像。同一台主机部署多个实例时，请为每个实例填写不同的容器名称。

启动前修改两处：

1. 把 `请改成你自己的密码` 换成你的登录密码，保留两边的单引号。密码中如需使用 `$`，在 Compose 中写成 `$$`。
2. 把 `192.168.1.100` 换成服务器的 IP；在同一台电脑上访问时可以保留示例。通过域名访问时，将域名加入这行，用逗号分隔。

在这个文件所在目录执行：

```sh
mkdir -p music data
docker compose up -d
```

打开 `http://服务器IP:8765`，同一台电脑可打开 `http://localhost:8765`。用户名为 `admin`，密码为你刚填写的密码。需要换端口时，将 `8765:8765` 左边的数字改掉，例如 `8888:8765`，然后通过端口 `8888` 访问。

- `music` 文件夹保存下载的音乐和歌词。
- `data` 文件夹保存收藏、播放列表、下载记录和配置。SQLite 数据文件自动创建，无需安装或配置数据库。

更新、重建容器时保留这两个文件夹即可。镜像启动时自动调整数据目录和音乐根目录的所有者，再以普通用户（UID/GID `10001:10001`）运行应用，通常无需手动执行 `chown`。已有音乐库内的文件和子目录不会被递归修改；共享目录如有额外 ACL 限制，需要允许该用户写入。

### NAS 提示无法创建 `/data/engine`

`Permission denied: /data/engine` 表示容器运行用户无法写入挂载的数据目录，和网页登录密码无关。更新后的启动脚本会提前创建引擎目录，修复数据目录及其已有文件的所有者和所有者读写权限，并在切换到普通用户后实际验证 `/data`、`/data/engine` 和 `/music` 是否可写。

绿联等 NAS 如果通过共享文件夹 ACL 授权给特定用户，可让应用使用该用户的 UID/GID。在 NAS 的 SSH 终端运行 `id 用户名`，查看 `uid` 和 `gid` 的数字，然后在 Compose 的 `environment` 中添加（数字仅为示例，需替换为实际结果）：

```yaml
      PUID: '1000'
      PGID: '1000'
```

在 NAS 文件管理或共享文件夹权限中，给该用户对挂载的 `data`、`music` 目录授予读写权限，检查容器挂载未开启“只读”。保留镜像默认启动用户：入口以 root 准备目录后自动切换到 `PUID:PGID`，应用不会持续以 root 运行。

如果 NAS 界面或 Compose 设置了 `user: 'UID:GID'`，入口从开始就是普通用户，无法替其他用户修复旧数据目录权限。通常移除 `user`，改用上面的 `PUID/PGID`；必须保留 `user` 时，需要在宿主机提前给该用户授予数据目录（含旧 `data/engine`）的读写权限。`user` 与 `PUID/PGID` 同时设置时，数字必须一致。

权限修复不会递归改变已有音乐库中文件的所有者；共享音乐库的现有子目录也需要在 NAS 上授权。NAS 的额外 ACL 拒绝、只读挂载或不允许修改所有者的网络共享，仍需要在 NAS 端修正，单纯修改容器内权限无法绕过。

完成配置后保留数据目录并更新容器：

```sh
docker compose up -d --pull always
docker compose logs --tail 100
```

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

<details>
<summary>可选：下载并发、代理和容器限制</summary>

在同一目录创建 `compose.override.yml`，只添加需要的选项：

```yaml
services:
  musicdl-webui:
    environment:
      MUSICDL_MAX_DOWNLOAD_TASKS: "4"
      # HTTP_PROXY: http://你的代理地址:7890
      # HTTPS_PROXY: http://你的代理地址:7890
    security_opt:
      - no-new-privileges:true
    stop_grace_period: 30s
```

执行 `docker compose up -d` 应用修改。音乐目录也可以与其他音乐库服务共享。

</details>

## 企业微信应用与 Telegram 机器人

机器人复用网页的音源配置和下载队列，支持搜索、选择曲目、查询状态、取消、继续下载和重新获取链接。两端统一发送原文件下载链接，不上传音乐附件，不压缩或转码，也不设置单文件大小上限。手机直接从 musicdl 下载，实际容量与速度取决于服务器磁盘、带宽、代理和手机存储。企业微信内置浏览器若无法保存文件，可选择在系统浏览器中打开；保存到手机文件目录后，是否能导入播放器取决于手机和播放器。

默认不启用机器人。启用前准备一个手机和企业微信都可访问的 HTTPS 域名，将其反向代理到 musicdl 的 `8765` 端口，并将域名加入 `MUSICDL_TRUSTED_HOSTS`。下载与回调路径必须保留完整路径及查询参数，不能在这些路径额外添加网页登录、Basic Auth 或交互式验证。网页 `/api/` 仍保留原有认证；`/downloads/` 由单文件签名鉴权，`/bot/wecom` 由企业微信签名和 AES 加密鉴权。

### 配置方式

源码运行：在现有 `.env` 中添加下面需要的配置，更新依赖后重启。已有虚拟环境可执行 `python -m pip install -r requirements.txt`（Windows 使用 `.venv\Scripts\python.exe`）。不要覆盖已有登录与音源配置。

Docker：在 Compose 目录创建 `.env.bots`，填入下面配置；创建或合并 `compose.override.yml`：

```yaml
services:
  musicdl-webui:
    env_file:
      - .env.bots
    environment:
      # 请替换实际域名和服务器 IP，保留原有需要访问的主机名
      MUSICDL_TRUSTED_HOSTS: 'localhost,127.0.0.1,192.168.1.100,music.example.com'
      MUSICDL_COOKIE_SECURE: '1'
```

以下是机器人环境变量示例。只启用一个渠道时，另一个渠道的凭据保持为空：

```dotenv
MUSICDL_PUBLIC_URL=https://music.example.com
MUSICDL_LINK_TTL_SECONDS=86400
# 可选：只使用指定的已启用音源；留空使用网页设置中的全部音源
MUSICDL_BOT_SOURCES=netease,qq

MUSICDL_TG_TOKEN=你的BotFatherToken
MUSICDL_TG_ALLOWED_USERS=123456789,987654321
# 可选：独立 HTTP/HTTPS 代理；Docker 内须能访问该地址
MUSICDL_TG_PROXY=

MUSICDL_WECOM_CORP_ID=你的企业ID
MUSICDL_WECOM_AGENT_ID=1000002
MUSICDL_WECOM_SECRET=你的应用Secret
MUSICDL_WECOM_TOKEN=接收消息配置中的Token
MUSICDL_WECOM_AES_KEY=接收消息配置中的43位EncodingAESKey
MUSICDL_WECOM_ALLOWED_USERS=zhangsan,lisi
```

`MUSICDL_PUBLIC_URL` 必须是 HTTPS 域名地址，不带路径、用户名密码或查询参数。链接默认有效 24 小时，可通过 `MUSICDL_LINK_TTL_SECONDS` 配置为 60–604800 秒。配置缺少必要字段或白名单时会拒绝启动，并在启动日志中说明，不会自动开放给所有用户。凭据应保存在本机配置中，不提交到 Git。

从当前源码部署 Docker 时，先构建包含机器人模块的新镜像，再重新创建容器。本项目默认 Compose 只配置了 `image`，使用：

```sh
docker build -t ghcr.io/agonie0v0/musicdl:latest .
docker compose up -d --pull never
```

使用已发布且包含本功能的镜像时，可执行 `docker compose up -d --pull always`。保留 `data` 挂载，它包含机器人任务归属、待发送通知、命令去重记录和链接签名密钥；丢失数据目录会使旧链接和任务归属失效。运行单个服务进程，不要同时启动多个容器共享同一个 Telegram Token，也不要使用多个 Uvicorn worker。

### Telegram 设置

通过 BotFather 创建 Bot，将 Token 写入 `MUSICDL_TG_TOKEN`，将允许使用的账号的**数字用户 ID**写入 `MUSICDL_TG_ALLOWED_USERS`，不是用户名或群 ID。当前仅接受与 Bot 的私聊；用户先打开 Bot 并发送 `/start`。

机器人使用长轮询，不需要 Telegram 公网回调。如果同一 Token 曾配置 webhook，先通过 Telegram 的 `deleteWebhook` 接口移除，否则长轮询不能收消息。服务器必须能访问 `api.telegram.org`；有需要时配置独立代理，不影响音乐音源代理。运行日志中 Telegram 接收失败通常与 Token、网络或 webhook 冲突有关。

### 企业微信自建应用设置

1. 在企业微信管理后台创建自建应用，将使用者加入应用可见范围，记录企业 ID、AgentID 和应用 Secret。
2. 将应用所需的服务器公网出口 IP 配置为企业可信 IP，确保能够调用获取 Token 和发送消息接口。
3. 在应用的“接收消息 / 设置 API 接收”中生成 Token、EncodingAESKey，填写到环境变量，启动或重启 musicdl。
4. 接收消息 URL 填写 `https://music.example.com/bot/wecom`，使用与环境变量一致的 Token、EncodingAESKey，保存验证。
5. 在手机企业微信中打开该应用的会话发送“帮助”。`MUSICDL_WECOM_ALLOWED_USERS` 填企业通讯录中的成员 UserID。

回调先校验签名、解密、企业和应用身份，再立即确认接收；搜索与下载在后台执行，结果通过应用消息发送给发起人。只支持应用与成员的直接会话，不读取普通微信群或企业微信群消息。服务器时钟应保持准确，超过 10 分钟的消息回调会被拒绝。

### 命令和下载流程

| 中文命令 | Telegram 命令 | 说明 |
| --- | --- | --- |
| `帮助` | `/help` 或 `/start` | 显示命令 |
| `搜索 晴天 周杰伦` | `/search 晴天 周杰伦` | 最多返回 10 个可下载结果 |
| `下载 a1b2c3d4 2` | `/download a1b2c3d4 2` | 下载该次搜索的第 2 首；TG 也可点击结果按钮 |
| `状态` | `/status` | 查看自己的最近 10 个任务 |
| `取消 任务编号` | `/cancel 任务编号` | 取消自己的下载 |
| `继续 任务编号` | `/resume 任务编号` | 恢复暂停或重试失败任务 |
| `链接 任务编号` | `/link 任务编号` | 为已完成任务重新生成下载链接 |

搜索编号绑定发起人，有效期为 30 分钟；一次搜索最长等待 90 秒，超时会返回已取得的结果。含分集的结果需在网页展开选择，机器人列表不展示。每个用户最多同时有 5 个活动下载任务，实际并发仍受现有下载队列限制。下载完成后，TG 发送“下载到手机”按钮，企业微信发送可点击的下载卡片。

链接不需要网页登录，持有者在有效期内可以下载对应的单个文件，因此不要公开转发。支持 HTTP Range 和 HEAD，请确保反向代理透传 Range、If-Range 等请求头，不缓存下载响应，也不要记录包含签名的完整查询参数。链接失效不会删除音乐文件，可发送“链接 任务编号”重新获取；文件被删除或替换后旧链接失效。

下载与消息发送分别记录：发送失败会退避重试，不会重新下载音乐。服务重启后未完成的下载会按现有规则暂停，机器人通知用户通过“继续”恢复；待处理命令和通知保留。外部平台发送成功但响应丢失时可能出现重复通知。下载记录在网页被移除后，机器人不能再为该任务获取新链接。

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

Docker 用户直接修改 Compose 中的 `environment`，执行 `docker compose up -d` 应用更改。源码运行时可复制 `.env.example` 为 `.env` 后修改配置。本机回环地址运行时可以不配置登录；同时设置 `MUSICDL_USER` 和 `MUSICDL_PASS` 就启用认证，非回环监听必须配置认证。没有通用默认密码，示例密码请自行替换。

| 环境变量 | 说明 |
| --- | --- |
| `MUSICDL_USER` / `MUSICDL_PASS` | 登录账号和密码，必须同时设置 |
| `MUSICDL_TRUSTED_HOSTS` | 允许访问的域名/IP，以逗号分隔，不包含协议和端口 |
| `MUSICDL_COOKIE_SECURE` | HTTP 设为 `0`，HTTPS 设为 `1` |
| `MUSICDL_HOST` / `MUSICDL_PORT` | 服务监听地址和端口，默认 `127.0.0.1:8765` |
| `MUSICDL_DOWNLOAD_DIR` | 音乐目录，源码默认 `musicdl_outputs`，Docker 为 `/music` |
| `MUSICDL_DATA_DIR` | 数据目录，源码默认 `data`，Docker 为 `/data` |
| `PUID` / `PGID` | Docker 应用运行用户和组的数字 ID，默认 `10001:10001`；NAS 可设为有目录权限的用户 |
| `MUSICDL_CONFIG` | 原版配置路径，源码默认 `config.json`，Docker 为 `/data/config.json` |
| `MUSICDL_MAX_DOWNLOAD_TASKS` | 下载并发数 `1–4`，默认 `2`；Docker 中通过上方覆盖文件设置，重建容器生效 |

音源配置可在设置页面修改。音源名称右侧的复选框决定是否启用，支持全选和取消全选；至少选择一个音源才能保存。点击音源名称展开 Cookie、线程数和代理设置。数据目录可能包含平台凭据，备份时应妥善保存。

### 分源高级配置怎么用

这部分默认折叠，普通搜索、试听和下载不需要修改。只有需要对某个音源单独覆盖参数，或原引擎的某个解析器明确要求特定配置时，才展开修改。不知道填什么就保留现有值或 `{}`，不要照搬其他音源的参数。

每个编辑框都是独立的 **JSON 对象**，使用英文双引号，不支持注释或末尾多余的逗号。外层键是原引擎的完整音源类名，例如 `NeteaseMusicClient`、`QQMusicClient`、`MiguMusicClient`，不是“网易云”等显示名称。参数名和类型必须与当前安装的原引擎匹配。

| 编辑框 | 用途与优先级 |
| --- | --- |
| 分源客户端参数 `init_music_clients_cfg` | 覆盖该音源的全局客户端初始化参数，例如 `max_retries`、`maintain_session`。只影响列出的音源，其余音源继续使用基础设置。 |
| 分源线程数 `clients_threadings` | 原引擎对应音源的线程数，键为音源类名、值为整数；普通音源展开面板中的线程数也写入这里。未设置时使用默认线程数。它不是网页下载队列的同时下载任务数。 |
| 分源请求覆盖 `requests_overrides` | 覆盖指定音源的请求参数，例如 `timeout`、`headers`、`proxies`。分源代理和超时优先于全局设置；普通音源面板中的代理也写入这里。 |
| 分源搜索规则 `search_rules` | 直接传给该音源原解析器的 `rule` 参数。不同音源支持的键并不相同，没有通用的“音质规则”；只有确认该解析器支持的参数才能填写。通常保持 `{}`。 |
| 其他全局参数 | 放置基础表单未展示的原引擎全局客户端参数，直接填写参数对象，不需要外层音源类名。不要重复填写下载目录、结果数、重试次数或引擎开关等已有表单字段。 |

例如，只让网易云保持会话并重试 5 次，在“分源客户端参数”中填入：

```json
{
  "NeteaseMusicClient": {
    "maintain_session": true,
    "max_retries": 5
  }
}
```

只设置网易云为 3 个线程，在“分源线程数”中填入：

```json
{
  "NeteaseMusicClient": 3
}
```

只让网易云请求使用指定代理和 20 秒超时，在“分源请求覆盖”中填入：

```json
{
  "NeteaseMusicClient": {
    "timeout": 20,
    "proxies": {
      "http": "http://127.0.0.1:7890",
      "https": "http://127.0.0.1:7890"
    }
  }
}
```

代理必须能从 **musicdl 服务所在环境** 访问；Docker 中的 `127.0.0.1` 指容器自身。上面的代理地址只是格式示例，不代表已提供代理服务。

点击“保存设置”后重新初始化音源客户端。折叠高级配置不会关闭或清空其中的参数；修改普通设置也会保留其他高级字段。出现 JSON 错误时会展开高级区并提示，先修正格式再保存。需要恢复某个音源的默认值时，删除该音源的对应条目；不要为了修改一个音源覆盖整个已有对象，以免丢失其他配置。

### 下载队列与音乐文件管理

在搜索结果中点击下载、确认音质并加入队列后，服务会自动把文件保存到设置中的下载目录。队列显示“已完成”就代表文件已落盘，**不需要再点击“下载到本机”才能保存**。Docker 默认目录为容器内 `/music`；仓库 Compose 的 `./music:/music` 映射会让文件出现在 Compose 所在目录下的 `music` 文件夹。自定义目录也应位于已挂载到宿主机的路径中。

下载队列支持多选、批量暂停、继续和取消。暂停保留临时文件，支持续传的 HTTP 音源会尝试从已有字节继续；不支持续传的音源可能重新下载。取消会清理该任务的临时文件。进行中的任务须先暂停或取消，停止后才能移除记录。“清理已结束记录”移除已完成和已取消的记录；移除记录保留下载完成的音乐文件，但会删除未完成的临时文件。

音乐文件页面以歌曲库展示目录中的音频，读取歌曲标题、歌手、专辑、时长、格式和内嵌封面；没有标题标签时使用文件名。可切换文件夹，按歌曲、歌手或专辑搜索和排序，直接播放、播放全部或加入待播列表。已有音频放入挂载的音乐目录后，点击“刷新音乐库”即可显示。

点击歌曲的编辑按钮可修改标题、歌手和专辑，保存后写回音频标签；文件名、音频内容和其他标签保持不变。支持常见的 MP3、FLAC、M4A、OGG、WAV 等格式，无法解析或不支持写标签的文件会禁用编辑。编辑失败会保留原文件；NAS 文件须有写入权限。试听取决于浏览器的格式支持，不支持的格式可下载到本机播放。歌词优先读取同名 `.lrc` 文件，否则尝试读取内嵌歌词。

歌词等非音频内容在“附属文件”页管理。支持选择当前结果、单个或批量删除。删除会永久移除音乐库磁盘上的所选文件，操作前需要确认；只删除所选条目，歌词等旁文件需一并选中才会删除。

“下载到本机”（原“保存文件”）是把音乐库中**已经下载完成**的文件通过浏览器再下载到你当前使用的电脑。它不会重新向音源下载歌曲，也不影响服务器上的原文件。Docker、NAS 或远程服务器部署时通常需要这个入口；本机运行时可以直接使用设置中的下载目录，无需再下载一份。

取消搜索会停止页面更新，但已经运行的音源请求可能继续完成并写入缓存。

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
python -m pytest tests/test_docker_entrypoint.py -q
npm run format:check --prefix frontend
npm test --prefix frontend
```

浏览器检查在 `frontend` 中执行 `npx playwright install chromium` 和 `npm run test:browser`。Windows 可通过 `MUSICDL_BROWSER_EXECUTABLE` 指定 Edge 或 Chrome 路径。

推送到 `main` 后，GitHub Actions 自动发布 `latest` 与对应提交的 `sha-...` 镜像标签，并执行容器启动检查。CI 检查原引擎兼容性与前端。容器启动检查通过不代表所有外部音源可用。

## 许可

原引擎来自 [CharlesPikachu/musicdl](https://github.com/CharlesPikachu/musicdl)，许可为 PolyForm Noncommercial 1.0.0。许可与来源说明见 [NOTICE.md](NOTICE.md)、[LICENSE-musicdl-reference](LICENSE-musicdl-reference) 和 [LICENSE-musicsquare](LICENSE-musicsquare)。
