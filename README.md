# Link Expand · 本地链接展开

放入一个 URL，自动展开标题、简短摘要和画面，生成接近 Telegram 链接预览的卡片。适合先看内容，也可以复制或下载图片。

![Link Expand 界面](docs/screenshot.png)

## Windows 免安装软件

在 [Releases 下载 Windows 版](https://github.com/cuterica/link-expand/releases/latest)。下载 EXE 直接运行，或下载 ZIP 解压后运行其中的 EXE，无需安装 Python。

分发版使用本项目原有的本地网页界面和处理流程。启动后自动打开浏览器，粘贴 URL 即可自动展开。截图使用电脑中已安装的 Edge 或 Chrome；不附带浏览器，以减小分发体积。

使用期间保留启动窗口，退出时关闭它或按 Ctrl+C。默认地址为 <http://127.0.0.1:8765>。同版本已运行时，再次启动会打开原来的页面；旧版本或其他程序占用端口时，新版会选择空闲端口并打开自己的页面，避免接入旧后台。需要指定其他端口时，在命令行运行 `LinkExpand-0.1.1-Windows-x64.exe --port 8766`。

页面右上角显示 `v0.1.1`，右侧按钮为「复制图文与链接」。页面会核对后台版本；旧后台或版本不匹配时会明确提示，并禁用右侧复制，防止误以为图文复制已生效。

每个发布版本提供 `SHA256SUMS.txt`，可验证下载文件。ZIP 内附第三方组件许可证。软件尚未进行商业代码签名。

## 从源码启动

需要 Python 3.10 或更新版本。首次启动联网安装依赖。截图优先使用已安装的 Chrome / Edge；未安装时自动下载 Chromium。

**Windows：** 双击 `run.bat`，自动创建环境、安装依赖并打开页面。运行环境放在 `%LOCALAPPDATA%\LinkExpand\venv`，避免在 WSL 共享目录中加载依赖时变慢。项目在 WSL 目录时也可以从 Windows 资源管理器打开这个文件。

**macOS / Linux / WSL：**

```bash
bash run.sh
```

装有 uv 时使用 `uv.lock` 锁定依赖；否则使用 Python 的 venv 和 pip。Ubuntu 若缺少 venv，请安装与 Python 版本匹配的 `python3-venv` 包。Linux 的 Chromium 还需要系统图形库；缺少时按 Playwright 的启动提示安装依赖。

默认页面：<http://127.0.0.1:8765>。按 Ctrl+C 停止。端口被占用时执行 `run.bat --port 8766` 或 `bash run.sh --port 8766`。使用 `--no-browser` 可关闭自动打开界面。

## 自动展开流程

1. 把链接粘贴到输入框，稍后自动展开；也可以点击「展开链接」重新获取。
2. 摘要优先使用网页提供的 Open Graph / Twitter / description，没有时提取正文段落并截短。动态页面截图时也尝试读取渲染后的正文。
3. 画面优先选网页封面、视频封面、正文大图。按来源、实际尺寸、比例和画面变化评分，跳过明显的图标、头像和空白图片。
4. 没有合适图片时，启动独立浏览器读取动态图片；发现可播放视频时，在 20%、45%、70% 处取样，选择较清晰、非黑屏的画面。
5. 没有可用视频画面时，截取网页主内容区域。
6. 页面标明「网页封面」「网页图片」「视频截图」或「网页截图」。可下载整张卡片，也可以单独下载所选图片或截图，单独下载保留完整比例。

支持公开网页、直接图片链接和常见 `.mp4` / `.webm` 视频链接。视频加载、编码、流媒体和站点访问限制会影响截图；受限视频会退回网页截图。需要登录、验证码或 DRM 的内容无法保证展开，可使用「手动编辑」补充摘要。

摘要是网页描述或正文摘录，未接入大模型，不会凭空生成内容。初始卡片标注「预览示例」，实际预览使用抓取或手动填写的内容。

## 保存与编辑

- 「手动编辑」修改标题、摘要；同一 URL 保留原来的画面。
- 左侧「复制卡片」保持整张 PNG 图片和当前排版。
- 右侧「复制文字与链接」一次复制图片、标题、摘要和真实 URL。Windows 分发版同时写入桌面聊天使用的图文格式、HTML 富文本和纯文本格式，让支持图文粘贴的微信 / QQ 读取同一次复制中的图片与文字。图片资源保存在本机，便于客户端读取。文字中的 URL 保留为真实链接，图片中的 URL 仍属于图片像素。
- macOS / Linux 浏览器方式使用带内嵌 PNG 的 HTML 富文本，可粘贴到支持富文本的文档、邮件或编辑器。纯文本输入框只会保留文字和链接；接收软件决定如何呈现排版。
- 「下载卡片 PNG」导出整个预览；「下载图片 / 截图」导出画面。

复制图片需要浏览器支持 [Clipboard API](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/write)，建议使用 Chrome 或 Edge。

## 本地运行

服务仅监听 `127.0.0.1`，网页、摘要和图片不上传到第三方服务。只在输入链接后请求网页和它的公开资源。最近 24 张卡片在内存中保存，退出后清空；不记录 URL、标题和摘要日志。

Windows 图文复制所需的图片缓存位于 `%LOCALAPPDATA%\LinkExpand\clipboard`。为支持退出程序后粘贴，图片会暂时保留；后续复制会清理超过 7 天或最近 48 张之外的旧图片。

下载限制大小与时长；每次跳转都验证公网地址，并直接连接已验证的 IP。截图浏览器的 HTTP 请求也经同一个下载器处理，限制请求数和总大小，不共享个人浏览器的登录状态。不支持本机 / 内网地址、账号密码 URL 和非标准端口。

可以通过 `LINK_EXPAND_FONT` 指定中文 `.ttf` / `.ttc` 字体，通过 `LINK_EXPAND_BROWSER` 指定 Chrome / Edge / Chromium 可执行文件路径。

## 开发与验证

```bash
uv sync --locked
uv run python -m linkexpand.setup_browser
uv run python -m unittest discover -s tests -v
uv run python tests/browser_smoke.py
uv run link-expand --no-browser
```

Python 标准库负责本地 HTTP 服务、受限下载和网页解析；Pillow 生成 PNG；[Playwright](https://playwright.dev/python/docs/network) 在独立进程中生成动态页面与视频截图。界面使用原生 HTML / CSS / JavaScript，无前端构建步骤。

## 构建 Windows 分发版

在 Windows 上使用 Python 3.10 或以上版本，在 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

构建使用独立环境 `%LOCALAPPDATA%\LinkExpand\build-venv`，将 EXE、含说明与许可证的 ZIP、SHA-256 校验文件输出到 `dist`。只打包已有程序及依赖，不更换界面或截图实现。截图子进程在 EXE 中通过专用入口调用同一份 Playwright 代码。
