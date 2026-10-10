# musicdl 产品上下文

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

桌面浏览器用户，搜索、试听音乐并批量下载无损音乐；兼容手机访问。

## Product Purpose

缩短搜索后第一首可以播放的匹配曲目的等待时间，并清楚展示音质版本。无损资源解析和下载独立推进。

## Operating Context

Windows、本地或 Docker/NAS 部署。运行时恢复原版 Python musicdl 完整引擎；Windows 包内置运行时。能力保持优先于语言迁移。

## Capabilities and Constraints

- 用户授权完全重构，已确认 output/design-preview-v2.html。
- React / TypeScript / Vite 前端，原版 musicdl 引擎与 FastAPI 适配层，SQLite。
- 完整注册表动态读取，当前 57 个音源；平台、聚合网关、下载站、电台的解析和下载逻辑均保留。
- 搜索显示无损 / MP3 等版本、来源和可试听状态；标注与验证证据分开。
- 实际 audio 事件驱动播放器、同步歌词、点击跳转、字号与跟随。
- 收藏、播放列表、歌单导入、批量音质选择、下载任务、文件浏览与设置。
- 按原音源自己的解析和权限逻辑处理，平台 Cookie 不是全局必填；完整 SongInfo 保留，不能试听不代表不能下载。
- 暂停后继续重新下载；FFmpeg 缺失时明确反馈嵌入限制。

## Evidence on Hand

frontend/src、app.py、engine_api.py、engine_worker.py 为当前实现；legacy/app.py 是正在复用的原后端；backend/src 是不再默认运行的 Rust 实验实现；output/design-preview-v2.html 为用户批准的视觉参考。output/restored-*.png 与 compatibility-*.json 为恢复原引擎后的实际证据；test-ui-* 仅为自动化夹具截图，不代表生产默认数据。

## Product Principles

- 首个资源就绪和点击后实际起播分别衡量。
- 搜索、试听、歌词和下载不制造不必要的等待。
- 明确区分源声明、实际资源、未知能力与账号权限。
- 保留批量选择和音质要求；单首失败不阻塞整批。

## Brand Commitments

保留产品名 musicdl，删除品牌 slogan。空状态给出下一步操作，不填充样例内容。

## Open Decisions

更广泛的公网音源对照、断点续传和部署环境验证待继续进行；禁止在未证明等价时用不完整的重写替换原解析器。
