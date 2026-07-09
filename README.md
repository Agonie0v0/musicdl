# musicdl WebUI

给 [CharlesPikachu/musicdl](https://github.com/CharlesPikachu/musicdl) 做的 Web 界面：
浏览器里搜索 / 下载音乐，所有 musicdl 设置项均可在 WebUI 中配置并持久化。

## 功能

- **搜索**：关键词跨音源并发搜索；也支持粘贴歌单链接（如网易云歌单）批量解析
- **下载**：勾选歌曲创建后台下载任务，任务面板实时展示每首歌的状态
- **剧集**：喜马拉雅等 FM 音源的专辑可展开选择单集下载
- **文件**：浏览各下载目录中的文件，支持从浏览器下载 / 删除
- **设置**（保存到 `config.json`，保存后立即重建客户端生效）：
  - 音源启用/禁用（40+ 音源按分类勾选）
  - 基础项：`work_dir`、`search_size_per_source`、`search_size_per_page`、`max_retries`、默认线程数、HTTP 代理、请求超时
  - 开关：`auto_set_proxies`、`random_update_ua`、`maintain_session`、`strict_limit_search_size_per_page`、三个 `curl_cffi` 开关
  - 高级（按音源 JSON 覆盖）：`init_music_clients_cfg`（含 cookies 等全部客户端参数）、`clients_threadings`、`requests_overrides`、`search_rules`
- **日志**：拦截 musicdl 的 LoggerHandle，网页里直接看运行日志

## 运行

```bat
start.bat
```

首次运行会自动创建 venv 并安装依赖，然后打开 http://127.0.0.1:8765

手动方式：

```bat
py -3.13 -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python app.py
```

> 注意：musicdl 部分依赖（curl-cffi、av 等）对 Python 3.14 支持不全，建议 3.11–3.13。

## 结构

```
app.py              # FastAPI 后端：搜索/下载任务/文件管理/配置管理
static/index.html   # 前端单页应用（无构建，纯原生 JS）
config.json         # WebUI 保存的配置（首次保存后生成）
musicdl_outputs/    # 默认下载目录（可在设置中修改）
```

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/meta` | 版本 + 全部注册音源（按分类） |
| GET/PUT | `/api/config` | 读取 / 保存并应用配置 |
| POST | `/api/search` | 关键词搜索 `{keyword}` |
| POST | `/api/playlist` | 歌单解析 `{url}` |
| GET | `/api/song/{id}/episodes` | 展开剧集 |
| POST | `/api/download` | 创建下载任务 `{ids}` |
| GET | `/api/tasks` | 任务列表及状态 |
| GET | `/api/files` | 已下载文件列表 |
| GET/DELETE | `/api/file?d=&rel=` | 下载 / 删除文件 |
| GET | `/api/logs` | 运行日志 |
