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
- **歌词保存方式**：单独 `.lrc` 文件 / 嵌入歌曲文件 / 两者都要
- **代理作用范围**：全部音源 / 仅指定音源——国内音源一般不用代理，只让 Spotify、
  YouTube、苹果等国际音源走代理，可勾选指定
所有配置保存在 `config.json`，保存即重建 musicdl 客户端，无需重启。

### 高级开关都是什么意思

> 一句话：**默认全部关着就能正常用**。只有当「某个音源搜不到 / 点了播放没声音 / 下载老失败」时，
> 才按下面对症逐个打开试试。

| 开关 | 打开后 | 什么时候开 |
|---|---|---|
| 搜索用浏览器指纹 `enable_search_curl_cffi` | 搜索请求模拟真实浏览器的 TLS 指纹 | 某个音源**搜不出结果**（常见于被 Cloudflare 拦） |
| 解析用浏览器指纹 `enable_parse_curl_cffi` | 获取播放 / 下载直链时模拟浏览器 | 能搜到但**点播放没声音 / 报错** |
| 下载用浏览器指纹 `enable_download_curl_cffi` | 下载文件时模拟浏览器 | **下载总是失败** |
| 随机 UA `random_update_ua` | 每次请求换一个浏览器标识 | 个别源**频繁失败**、疑似被反爬识别 |
| 自动找免费代理 `auto_set_proxies` | 连不上时自动抓公共代理重试 | 没有自己的代理又连不上时（**很慢很不稳**，有代理请直接填「HTTP 代理」） |
| 保持会话 `maintain_session` | 复用连接与 Cookie，略快 | 想快一点；偶发异常就关掉 |
| 严格分页限制 `strict_limit_search_size_per_page` | 严格按「每页大小」截断结果 | **建议保持勾选** |

### 专家选项：按音源写 JSON 覆盖（一般用不到）

给**单个音源**单独调参用，键是完整音源名（如 `"NeteaseMusicClient"`），留 `{}` 表示不覆盖。
普通使用完全可以跳过；改错只会让那个音源初始化失败。四个框各自的作用与例子：

- **`init_music_clients_cfg`** — 覆盖某音源的客户端参数（结果数、work_dir、cookies 等）。
  例：让网易云单独多返回一些结果 —
  ```json
  {"NeteaseMusicClient": {"search_size_per_source": 20}}
  ```
- **`clients_threadings`** — 给某音源单独设并发线程数。例：咪咕慢，降到 3 线程 —
  ```json
  {"MiguMusicClient": 3}
  ```
- **`requests_overrides`** — 给某音源单独设代理 / 超时 / 请求头。
  例：只给 Spotify 设 60 秒超时和单独代理 —
  ```json
  {"SpotifyMusicClient": {"timeout": 60, "proxies": {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}}}
  ```
- **`search_rules`** — 给某音源传特定搜索规则（少数源支持，一般留空 `{}`）。

> 提示：上面「基础」「代理作用范围」这些图形化设置已覆盖绝大多数需求，
> 只有要**对个别音源精细化调参**时才需要动这四个 JSON 框。

## musicdl 引擎升级（音源修复靠它，可独立更新）

各音源能不能用，取决于底层的 **musicdl** 引擎。平台改接口导致某个源失效时，
是 musicdl 作者更新解析代码来修——所以**升级 musicdl 就能拿到修复，不需要改本项目**。

**为什么升级不会把站搞崩？** 本项目为了做「平铺目录 / 歌词分离 / 日志面板」等增强，
patch 了一些 musicdl 内部方法。这些 patch 全部做了**兼容保护**：某个内部方法在新版本里
改名或消失时，只会**自动跳过那一项增强**（顶多少个锦上添花的功能，且会在日志/`/api/health` 标注），
**核心的搜索 / 播放 / 下载走的是 musicdl 公开接口，不受影响、照常启动**。因此 `requirements.txt`
里 musicdl 用的是 `>=`，不再钉死版本。

**怎么升级：**

| 场景 | 操作 |
|---|---|
| **网页一键（推荐）** | 设置 → 关于 musicdl 引擎 → **一键升级并重启**，自动完成升级+重启+刷新页面 |
| Docker / NAS | `docker compose up -d --build`（重建即拉最新 musicdl） |
| 本地手动 | `venv\Scripts\pip install -U musicdl`（Linux/mac：`venv/bin/pip install -U musicdl`） |
| 本地自动 | 设环境变量 `MUSICDL_AUTO_UPDATE=1` 再运行 `start.bat` / `start.sh`，每次启动自动升 |
| 想回退 | `pip install musicdl==2.13.1`，或把 `requirements.txt` 钉回某版本 |

> 「一键升级并重启」依赖服务能被自动拉起：本地用 **start 脚本**启动（脚本内置重启循环，
> 旧版脚本请更新）；Docker 由 `restart: unless-stopped` 自动拉起。手动 `python app.py` 裸跑的话，
> 重启后不会自己回来。

**怎么查看状态：** 设置弹窗底部「关于 musicdl 引擎」可看当前版本、点「检查新版本」；
接口层 `GET /api/health` 返回版本 + 各增强补丁是否生效，`GET /api/musicdl/latest` 查 PyPI 最新版。

> 小结：**日常升级 musicdl 尽管升**，坏不了站；只有极少数情况（musicdl 大改公开接口、或某个增强想恢复）才需要找我。

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
