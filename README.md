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
| 播放列表不能排序 | 播放列表可按 标题 / 歌手 / 音源 / 时长 排序，升降序可切 |
| 下载文件无法归类 | 设置里“下载分类方式”：按音源 / 按歌手 / 歌手·专辑 / 全部平铺 |
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

> 适用于群晖 / 威联通 / 一般 Linux 服务器。全程只需要一台装了 Docker 的机器，
> 不用在上面装 Python。

### 0. 前置条件

- 已安装 **Docker** 和 **Docker Compose**（群晖在「套件中心」装 *Container Manager*；
  一般 Linux：`curl -fsSL https://get.docker.com | sh`）。
- 检查是否可用：

  ```bash
  docker version
  docker compose version   # 老版本是 docker-compose，命令换成带横杠的即可
  ```

### 1. 拿到代码

```bash
git clone https://github.com/SAGIRIxr/musicdl-webui.git
cd musicdl-webui
```

（群晖没装 git 的话，可在 GitHub 页面 *Code → Download ZIP* 下载解压，再进入该目录。）

### 2. 准备目录

仓库里的 `docker-compose.yml` 默认会用到两个相对目录，先建好：

```bash
mkdir -p music data
```

- `music/`：**下载目录**，也就是你的音乐库。想让 navidrome 收录，就把它指到 navidrome
  的音乐库同一个文件夹（见第 5 节）。
- `data/`：存 `config.json`（音源勾选 / Cookie / 代理 / 下载分类方式等），
  **重建容器不会丢配置**。

### 3. 启动

```bash
docker compose up -d --build
```

- 首次会本地构建镜像（装 ffmpeg + 依赖），约 3–5 分钟，之后启动是秒级。
- 起来后浏览器访问 **`http://<服务器IP>:8765`**（本机就是 `http://127.0.0.1:8765`）。

看日志 / 状态：

```bash
docker compose logs -f      # 实时日志，Ctrl+C 退出
docker compose ps           # 查看运行状态
```

### 4. 常用运维命令

```bash
docker compose restart              # 重启
docker compose down                 # 停止并删除容器（music/ data/ 数据都在，不受影响）
git pull && docker compose up -d --build   # 更新到最新版（拉代码 → 重建镜像）
```

### 5. 让下载的歌自动进 navidrome

关键就一句话：**musicdl-webui 的 `/music` 和 navidrome 的音乐库指向同一个宿主机目录**。
`docker-compose.yml` 里已经附了一段被注释的 navidrome 服务，去掉注释即可一起起：

```yaml
  navidrome:
    image: deluan/navidrome:latest
    container_name: navidrome
    restart: unless-stopped
    ports:
      - "4533:4533"
    volumes:
      - ./music:/music:ro          # ← 和上面 musicdl-webui 的 ./music 是同一个目录
      - ./navidrome-data:/data
    environment:
      - ND_SCANSCHEDULE=1m         # 每分钟扫一次，新歌很快出现
```

然后在设置里把 **下载分类方式** 选成「歌手 / 专辑」，下载的文件会按
`music/歌手/专辑/歌名.ext` 归好类，navidrome 扫描即入库，不用任何手动整理。

### 6. 环境变量（可在 `docker-compose.yml` 的 `environment` 里改）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MUSICDL_HOST` | `0.0.0.0` | 容器内监听地址，一般不用动 |
| `MUSICDL_PORT` | `8765` | 服务端口（改了记得同步改 `ports` 映射） |
| `MUSICDL_WORKDIR` | `/music` | 下载目录（容器内路径） |
| `MUSICDL_CONFIG` | `/data/config.json` | 配置文件路径 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 无 | 需要科学上网访问某些音源时设置 |

### 7. 不用 compose，直接 `docker run`

```bash
docker build -t musicdl-webui .
docker run -d --name musicdl-webui --restart unless-stopped \
  -p 8765:8765 \
  -v "$PWD/music:/music" \
  -v "$PWD/data:/data" \
  musicdl-webui
```

### 常见问题

- **端口被占用**：把 `-p 8765:8765` 或 compose 里的 `ports` 改成 `18765:8765`，
  再用 `http://<IP>:18765` 访问。
- **下载的文件在宿主机没权限 / navidrome 读不到**：给 `music/` 目录放开权限
  `chmod -R 777 music`（或按需设 owner）。群晖建议把 `./music` 换成共享文件夹的
  绝对路径，如 `/volume1/music:/music`。
- **某些音源搜不出来**：多为需要代理，按第 6 节设 `HTTP_PROXY`；
  群晖代理常写 `http://<宿主机IP>:7890`（不要用 `127.0.0.1`，容器内的 127 是它自己）。
- **“打开下载目录”按钮消失了**：这是正常的——服务端在容器里没有桌面，
  该按钮只在 Windows 本机运行时出现；文件请到「已下载文件」列表里下载或直接到 `music/` 取。
- **改了设置要重来**：设置存在 `data/config.json`，删掉这个文件即恢复默认。

## 设置说明（⚙️ 按钮）

- **音源选择**：40+ 音源按分类勾选，"默认五源" = 咪咕/网易云/QQ/酷我/千千
- **Cookie**：F12 → 网络 → 任意请求头里整串复制 Cookie 粘贴即可
  - 网易云：含 `MUSIC_U=…` 可解锁 VIP 音质
  - QQ 音乐：含 `qm_keyst=…`
- **基础**：下载目录、每源结果数、分页、重试、线程、超时、HTTP 代理
- **下载分类方式**：按音源 / 按歌手 / 歌手·专辑 / 全部平铺——决定歌曲在下载目录里怎么归类，
  喂 navidrome / Jellyfin 建议选「歌手 / 专辑」（`下载目录/歌手/专辑/歌名`）
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
