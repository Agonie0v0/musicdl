# musicdl WebUI（我的音乐站 · 本地版）

基于 [CharlesPikachu/musicsquare](https://github.com/CharlesPikachu/musicsquare)（Apache-2.0）的界面，
后端换成本地 [musicdl](https://github.com/CharlesPikachu/musicdl)：
搜索 / 在线播放 / 下载全部走自己的 FastAPI 服务，不再依赖第三方公共 API，
并且所有 musicdl 设置项（含 Cookie）都能在网页里直接配置。

## 相比原演示站的改进

| 原演示站 | 本项目 |
|---|---|
| 只有网易云/QQ/酷我/JOOX 4 个第三方公共接口 | musicdl 全部 40+ 音源，按需启用 |
| JOOX Token、结果数等硬编码在 HTML 里 | 全部进 ⚙️ 设置弹窗，保存到 config.json 热生效 |
| 无法配置 Cookie（无 VIP 音质） | 每个音源一个输入框，整串 Cookie 粘贴即用（自动解析成 dict 注入搜索/下载/解析三处） |
| 下载=浏览器直接打开音频链接 | musicdl 后台任务下载：自动写标签、封面、歌词 .lrc，⬇️ 下载管理里看进度/取文件 |
| 收藏跨会话后播放链接失效 | 后端 `/api/resolve` 按 标题+歌手 自动重新解析 |
| 无代理/超时/线程等选项 | 代理、请求超时、线程数、curl_cffi 指纹、重试次数等均可配 |
| 深色霓虹主题 | 明亮清爽浅色主题，SVG 线性图标，桌面三栏 / 手机单列自适应 |
| 无音质选择 | 结果列表每行显示 格式+大小 徽标（FLAC 标金色），行内直接下载想要的版本 |
| 无任务/日志界面 | ⬇️ 弹窗：批量下载、下载任务（可折叠分组卡片+进度条）、文件管理、运行日志四合一 |
| 只能一首首下 | ⬇️ 弹窗内“批量下载”：勾选搜索结果 / 收藏，全选/反选，一次性下载并分组显示进度 |
| 搜索时不能再搜 | 搜索途中直接搜新词即接管（旧请求自动取消），无需刷新页面 |
| 缓存/空文件夹越堆越多 | 不再生成 `.pkl` 缓存与时间戳文件夹；“清理缓存”一键清掉历史残留 |
| 仅 Windows 本地 | 跨平台：Windows / Linux / macOS，含 Docker + NAS 部署、可直下 navidrome |

## 运行

**Windows：**

```bat
start.bat
```

**Linux / macOS：**

```bash
./start.sh
```

首次运行自动建 venv 装依赖，然后打开 http://127.0.0.1:8765
（建议 Python 3.11–3.13；部分依赖对 3.14 支持不全）

## Docker / NAS 部署（下载直接进 navidrome）

```bash
docker compose up -d
# 访问 http://<主机IP>:8765
```

- 下载目录 `/music` 挂到 **navidrome / Jellyfin 的音乐库同一个宿主机文件夹**即可，
  下好的歌以 `音源/歌名.ext` **平铺**进去（不再有时间戳套娃文件夹、不再散落 `.pkl` 缓存），
  navidrome 扫描即入库。`docker-compose.yml` 里附了可选的 navidrome 服务示例。
- 设置持久化：`config.json` 存到挂载的 `./data`，重建容器不丢配置。
- 环境变量：`MUSICDL_HOST`（默认容器内 `0.0.0.0`）、`MUSICDL_PORT`、
  `MUSICDL_WORKDIR`（下载目录）、`MUSICDL_CONFIG`（配置文件路径）。
- 远程部署时“打开下载目录”按钮会自动隐藏（服务端无桌面）。

## 设置说明（⚙️ 按钮）

- **音源选择**：40+ 音源按分类勾选，"默认五源" = 咪咕/网易云/QQ/酷我/千千
- **Cookie**：F12 → 网络 → 任意请求头里整串复制 Cookie 粘贴即可
  - 网易云：含 `MUSIC_U=…` 可解锁 VIP 音质
  - QQ 音乐：含 `qm_keyst=…`
- **基础**：下载目录、每源结果数、分页、重试、线程、超时、HTTP 代理
- **开关**：auto_set_proxies / random_update_ua / maintain_session / curl_cffi ×3 等
- **高级**：按音源 JSON 覆盖 `init_music_clients_cfg` / `clients_threadings` / `requests_overrides` / `search_rules`

所有配置保存在 `config.json`，保存即重建 musicdl 客户端，无需重启。

## 结构

```
app.py                  # FastAPI：搜索/流媒体代理/下载任务/文件/配置/缓存清理
static/index.html       # musicsquare 界面 + musicdl 数据层改造
static/index-v1-backup.html  # 第一版自研界面（备用）
Dockerfile              # 容器镜像（含 ffmpeg）
docker-compose.yml      # NAS/服务器一键部署，可挂 navidrome 库
start.bat / start.sh    # Windows / Linux·macOS 启动脚本
LICENSE-musicsquare     # 原界面 Apache-2.0 许可
config.json             # 网页保存的配置（运行时生成）
musicdl_outputs/        # 下载目录（可在设置中改；Docker 下为 /music）
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/meta` | 版本 + 全部音源（分类） |
| GET/PUT | `/api/config` | 读取 / 保存并热应用配置 |
| POST | `/api/search` | `{keyword, sources?}` 搜索（前端按源并发请求、渐进渲染） |
| GET | `/api/song/{id}` | 详情：歌词 / 封面 / 可播放性 |
| GET | `/api/stream/{id}` | 音频代理流（带 Range，解决防盗链/CORS） |
| POST | `/api/resolve` | `{source,title,artist}` 过期曲目重新解析 |
| GET | `/api/song/{id}/episodes` | FM 专辑展开剧集 |
| POST | `/api/download` | 创建下载任务（可一次多首，归为一个分组任务） |
| GET | `/api/tasks` · `/api/files` · `/api/file` | 任务 / 文件管理 |
| POST | `/api/tasks/clear-finished` | 清除已完成任务 |
| POST | `/api/cache/clear` | 清理 `.pkl` 缓存与空文件夹（保留音频文件） |
| POST | `/api/open-dir` | 打开下载目录（仅本机桌面；远程返回目录路径） |
| GET | `/api/logs` | 运行日志（拦截 musicdl LoggerHandle） |
