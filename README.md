# musicdl

**多音源音乐搜索、在线播放与批量下载。** 支持电脑和手机，可部署到 Docker / NAS，也可在本机运行。

[快速部署](#快速部署) · [网页配置](#网页配置) · [进阶指南](docs/configuration.md) · [问题反馈](https://github.com/Agonie0v0/musicdl/issues)

## 核心功能

- **搜索与试听**：多音源并行搜索，支持音质筛选、在线播放、同步歌词和歌词跳转。
- **下载与管理**：批量下载、暂停续传、收藏、歌单导入、本地音乐库及音频标签编辑。
- **通知与机器人**：Telegram、企业微信私聊搜索和下载，完成后返回原文件下载链接。
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

1. 准备手机可访问的 **HTTPS 域名**，反向代理到服务的 `8765` 端口。
2. 打开 **通知与机器人**，填写公网地址、渠道凭据和用户白名单，启用并保存。
3. 测试连接后，向机器人发送歌名搜索，选择曲目下载；完成后收到下载链接。

Telegram 白名单填写**数字用户 ID**，仅支持私聊。企业微信还需配置应用可见范围、可信 IP 和消息回调，见[机器人配置指南](docs/configuration.md#机器人配置)。配置保存后生效，无需重启。

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
