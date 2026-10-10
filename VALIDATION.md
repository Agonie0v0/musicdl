# 完整原引擎恢复验证

2026-10-10，Windows 本机。当前默认实现为 React + 原 musicdl 2.14.0 引擎；2026-10-09 的纯 Rust 版本及其 0.3 秒级搜索数据不能用来代表当前完整解析链路。

## 回归检查

- `python -m pytest tests/test_engine_compat.py -q`：5 项通过。验证全部 57 个注册音源、完整配置往返、原搜索参数透传、原 SongInfo 的请求头/Cookie/预取字节持久化、真实原引擎进程下载生成 WAV、音质选择不匹配被拒绝、分集/HLS 可下载状态，以及 HTTP 原下载器的真实字节进度/暂停/临时文件清理。
- `python -m pytest legacy/tests/test_app.py -q`：原版 21 项测试通过。
- TypeScript/Vite 构建通过；浏览器自动化检查真实 WAV 播放、歌词跳转、收藏、待播列表、版本选择、队列、设置及响应式布局。
- 实际浏览器界面读取到 57 个音源，未截断成五个；生产页面没有记录到 pageerror。

## 公网验证（没有额外配置平台 Cookie）

隔离配置启用网易云、QQ、酷我、咪咕、GDStudio、歌曲宝；关键词“周杰伦”，每源 1 条，单请求超时 15 秒，测试 max_retries=0。保留引擎自身默认配置，不能将“未额外配置用户 Cookie”表述成“内部完全不使用任何 Cookie”。

这次搜索的第一条完整解析结果约 2.48 秒，六源处理完约 12.23 秒。酷我返回《晴天》FLAC，网易云返回《想你就写信 (Live)》FLAC，咪咕返回 MP3。QQ、GDStudio、歌曲宝本轮返回 0 条；未把空结果宣称为全站可用。

三条可试听资源的 HTTP Range 请求均为 206。酷我与网易云返回字节以 `fLaC` 开头，咪咕返回 MP3 ID3 数据。真实 Edge 播放酷我《晴天》解码时长 269.747 秒、加载 63 行歌词；可跳转播放。

随后通过原版 `MusicClient.download()` 完整下载酷我《晴天》，成功保存 55,481,753 字节，约 6.82 秒。Mutagen 解析确认：FLAC、44,100 Hz、24-bit、双声道、269.747 秒；队列多次报告真实递增字节。该下载位于隔离测试目录，不混入用户音乐库。

原始报告：

- `output/compatibility-live-report.json`
- `output/compatibility-browser-report.json`
- `output/compatibility-download-report.json`
- 截图 `output/restored-*.png`

## 限制

音源和解析不再删减，但未逐一验证 57 个外部网站的所有曲目、所有账号模式或特殊流协议。第三方接口失效时沿用原引擎的处理/修复渠道。取消搜索不保证立刻终止已在第三方库中运行的请求；下载暂停后继续从头下载。没有覆盖所有特殊协议的外部工具组合。

本次不是旧版同条件速度基准，不宣称固定倍数提速。Windows 全引擎运行包做独立启动检查；Docker 仅配置检查，未完成容器或 NAS 实机运行。
