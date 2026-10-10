---
name: musicdl
description: 冷灰工作区中的音乐搜索、试听与下载操作界面
colors:
  accent: "#176b78"
  accent-hover: "#125965"
  soft: "#e8f3f4"
  ink: "#223747"
  muted: "#607581"
  secondary-text: "#536c79"
  line: "#e0e8eb"
  bg: "#f3f6f7"
  surface: "#ffffff"
  sidebar: "#eaf0f2"
  amber: "#8b601c"
  pending-bg: "#faf2e4"
  pending-text: "#886225"
  flac-bg: "#e6f2f2"
  flac-text: "#286e79"
  focus: "#71a8b3"
typography:
  headline:
    fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif'
    fontSize: "24px"
    fontWeight: 650
    letterSpacing: "-0.5px"
  title:
    fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif'
    fontSize: "28px"
    fontWeight: 650
    letterSpacing: "-0.5px"
  body:
    fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif'
    fontSize: "14px"
    lineHeight: 1.5
  label:
    fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif'
    fontSize: "12px"
    fontWeight: 550
  lyric:
    fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif'
    fontSize: "20px"
    fontWeight: 500
    lineHeight: 1.7
  lyric-active:
    fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif'
    fontSize: "25px"
    fontWeight: 650
    lineHeight: 1.7
rounded:
  badge: "4px"
  control: "5px"
  button: "6px"
  navigation: "7px"
  panel: "8px"
  album: "12px"
spacing:
  compact: "8px"
  control: "10px"
  small: "12px"
  mobile-gutter: "14px"
  medium: "18px"
  panel: "24px"
  desktop-gutter: "34px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.button}"
    padding: "10px 18px"
    typography: "{typography.label}"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
    textColor: "{colors.surface}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.button}"
    padding: "10px 18px"
    typography: "{typography.label}"
  button-icon:
    rounded: "{rounded.control}"
    width: "30px"
    height: "30px"
  navigation-active:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.accent}"
    rounded: "{rounded.navigation}"
    padding: "12px 13px"
  search-field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "8px 9px 8px 18px"
  badge-flac:
    backgroundColor: "{colors.flac-bg}"
    textColor: "{colors.flac-text}"
    rounded: "{rounded.badge}"
    padding: "3px 5px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "18px 18px 16px"
---

# Design System: musicdl

## Overview

**Creative North Star: "冷灰音乐工作台"**

这份记录固化用户批准的 output/design-preview-v2.html 在当前 React 前端中的实际实现。白色工作面、冷灰背景与青色操作强调形成清楚的任务层级：搜索结果保持密集，正在播放的曲目与歌词获得更大的文字与空间。产品名保持 musicdl，不附加品牌 slogan。

系统以操作效率和真实状态为中心。封面使用音源返回的图片，缺图或加载失败时显示音乐线条图标；空状态显示下一步操作，不填充演示曲目。颜色帮助识别选中、无损和待确认状态，文字同时说明其含义。

**Key Characteristics:**
- 冷灰背景、白色工作面与克制的青色强调。
- 紧凑结果行和稳定的底部播放控制。
- 系统无衬线字体、细线图标与明确的焦点描边。
- 源声明、资源就绪和真实进度分别呈现。

依据：frontend/src/style.css 的最终级联、App.tsx、components.tsx、PRODUCT.md，以及 output/production-search-desktop.png、production-player-desktop.png、production-search-mobile.png、production-player-mobile.png、production-playlist-mobile.png。截图用于核对真实布局，不作为默认内容。

## Colors

色彩以冷灰中性色承托低饱和青色，状态色使用浅底与深字。前置 YAML 是 token 值的规范来源；页面另有少量局部边框与状态色，保留在实现中。

### Primary
- **操作青 accent**：搜索、下载主按钮、活动导航、当前歌曲与歌词、范围输入的强调。
- **深操作青 accent-hover**：主按钮悬停。
- **浅青 soft**：结果计数与活动过滤项。
- **无损浅青 flac-bg / 无损深青 flac-text**：音源提供的 FLAC 标签；标签本身不表示下载前验证已通过。

### Secondary
- **提示琥珀 amber、pending-bg、pending-text**：音质待确认及权限类提示；不能取代具体的状态文案。

### Neutral
- **深灰蓝 ink**：标题、曲名与主要正文。
- **灰蓝 muted / secondary-text**：说明、元数据、未激活歌词与次要操作。
- **冷灰 bg、导航灰 sidebar、白色 surface**：工作区、导航区与结果/表单/对话框。
- **分隔灰 line**：面板边框与内容分隔。
- **焦点青 focus**：键盘焦点描边。

**The 状态证据 Rule.** 状态必须有文字含义；无损标注、试听就绪、账号限制与下载验证结果不能互相代替。

## Typography

**Display Font / Body Font:** Segoe UI，中文回退 Microsoft YaHei，再回退 sans-serif。没有外部字体依赖或独立展示字体。

文字采用系统界面的稳定字形。标题与活动歌词通过字重、字号和青色形成层级，时间使用等宽数字 tabular-nums，曲名和元数据在密集区域单行省略。

### Hierarchy
- **Headline**：桌面页标题（24px、650）；手机页标题（21px）。
- **Title**：桌面播放曲名（28px、650）；980px 以下为 24px，手机最终为 21px。
- **Body**：全局基准（14px、1.5）。结果曲名桌面为 13px、550，手机为 11px。
- **Label**：主次按钮（12px、550）；说明、来源、时间与徽标多为 9–12px，手机最小徽标和时间为 8px。这是当前密度事实，不是要求所有新正文采用小字号。
- **Lyric**：大屏普通/活动歌词（20/25px、500/650、1.7）；放大模式（23/29px）。1200px 以下默认（16/20px），980px 以下默认（15/18px）；手机默认（15/18px），放大（17/20px）。紧凑歌词最终为 13px、1.6，活动行加粗并着青色。

## Layout

桌面框架使用两列：侧栏（200px）和可伸缩工作区；1200px 以下侧栏（175px）。应用与工作区固定为 100dvh，页头和底部播放器不参与主内容滚动，只有工作区内容独立滚动。播放器最终是 flex 布局中的固定高度区域，不能仅根据早期 CSS 的 sticky 声明判断行为。

桌面工作区左右内边距（34px），1200px 以下（22px）。搜索区为结果列与试听面板（304px），间距（24px）；1550px 以上面板（326px），1200px 以下面板（260px）、间距（16px）；980px 以下试听侧面板隐藏，结果改为单列。表格固定布局，常规行高（69px）；1550px 以上最终行高（66px），手机（73px）。

播放页桌面为专辑/信息列和歌词列，总宽上限（1110px），列间距随视口变化。歌词拥有独立滚动容器。手机在 650px 以下改为横向可滚动导航与单列内容：左右工作区边距（14px），页头最终（77px），封面最终（144px），歌词容器最终（320px）。侧栏下方设置入口移至右上。

底部播放器桌面为曲目信息、播放控制/时间线、额外操作三列。手机最终为（105px / minmax(0, 1fr) / 28px），间距（10px），最小高度（79px）；曲目入口、上一首、播放/暂停、下一首、时间线与播放列表入口保留。音量、随机/循环快捷键与“词”快捷入口在手机隐藏；点曲目信息仍可展开播放页。

手机结果隐藏独立音源列和时长列、行内收藏、部分过滤与批量辅助操作；音源仍出现在曲目元信息中。设置表单的三列网格降为单列。下载行缩为封面、曲目信息和任务操作三列，状态/错误信息仍通过曲目说明显示。

## Elevation & Depth

层级主要由浅灰背景、白色面板和细边框形成。普通结果容器与导航没有明显阴影。搜索框只有极弱阴影（0 3px 12px #00000003），专辑封面使用环境阴影（0 16px 30px #20354812），原生对话框使用浮层阴影（0 18px 60px #00000020）与灰蓝遮罩（#23384650）。通知使用小幅阴影（0 5px 20px #00000015）。这些值收录在 sidecar，不属于前置 YAML token schema。

## Shapes

主体采用小幅圆角：标签（4px）、图标按钮与一般控件（5px）、主次按钮（6px）、导航和选择条（7px）、工作面与搜索框（8px）。专辑封面和对话框使用更宽的圆角（12px），手机专辑封面（9px）。播放按钮、行内试听与用户头像为圆形。分隔线通常为（1px）。

线条图标继承文字颜色，常规尺寸（19px）和描边（1.7）；具体工具栏按密度缩小。封面保持正方形并 object-fit: cover，缺图使用音乐图标。

## Components

### Buttons and inputs

主按钮是青底白字，次按钮是白底细边框；悬停通过颜色和背景变化反馈，不引入装饰位移。禁用控件当前以（0.5）透明度和默认光标表现。图标按钮拥有可读标签与 title，默认（30px）方形操作区，密集行内控件更小。

按钮、input、select 使用焦点描边（3px）与外偏移（3px）。搜索框内输入单独取消 outline，焦点能力在输入规则上与其他表单不同；记录当前事实，不将其复制为新字段规则。表单输入为浅底、细边框和小圆角，textarea 可纵向缩放。

### Navigation, chips and rows

活动导航为白底青字并加粗；来源、过滤与歌词跟随状态使用浅青填充。音质徽标按无损、普通版本与待确认区分，同时显示文字。结果复选框与批量操作保持独立；当前歌曲名变青，选中行使用极浅青背景。待播列表与下载队列是两种独立业务状态，保持各自明确名称。

### Lyrics and playback

歌词基于真实 audio 时间决定活动行；点击带时间的歌词跳转。跟随开启时直接居中活动行，在活动行、跟随开关、字号、歌词数据或容器大小变化后重新居中。关闭跟随后允许独立阅读；字号改变会重排并重新计算。没有定时模拟歌词或平滑装饰滚动。无歌词时保留真实返回文本或下一步说明。

底部播放器常驻，曲目信息可展开播放页。未选择歌曲时播放控件禁用；加载态使用静态白点，按钮的可读标签反映真实播放/暂停状态。试听质量与下载版本单独选择。

### Panels, dialogs and notices

搜索试听面板含歌词、音质版本和待播列表切换，下方展示下载入口与队列摘要。音质选择使用原生 radio，选中版本浅青底与强调边框。对话框为原生 dialog.showModal()，宽（440px）、视口上限（100vw - 32px）、高度上限（85dvh）；质量列表内部滚动。可关闭对话框支持关闭按钮与 Escape，登录对话框不允许 Escape 退出。

通知为深灰蓝底白字，定位在播放器上方，包含关闭按钮和 role=status。空状态、音源失败、权限不足与资源未知保持对应文字；不制造进度。当前 CSS 没有动画时间 token；减少动态效果偏好下明确保留即时滚动。

## Do's and Don'ts

### Do:
- Do 保留冷灰背景、白色工作面与青色操作强调。
- Do 让播放器保持可见，并在手机保留播放列表入口。
- Do 将真实音源状态、资源权限和版本验证证据写清楚。
- Do 用系统字体、细线图标、明确焦点和可读操作标签。
- Do 在缺少数据时提供下一步操作与真实空状态。

### Don't:
- Don't 添加品牌 slogan 或虚构演示曲目与进度。
- Don't 把无损标签当成已验证资源，或把试听版本冒充下载版本。
- Don't 用颜色独自表达权限、错误或待确认状态。
- Don't 为普通操作添加装饰动画、渐变卡片或醒目阴影。
- Don't 将当前密集徽标的小字号推广到长正文或新核心操作。


当前功能适配（2026-10-10）：音源列表从原引擎注册表动态读取，当前 57 项；设置页保留完整原版配置 JSON 编辑入口。搜索结果展示原解析器返回的版本；非 HTTP/专用流曲目显示可下载而非可试听，分集提供展开操作。视觉布局与 tokens 不变。
