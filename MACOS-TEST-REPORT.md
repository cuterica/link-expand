# Mac 全流程验收：v0.3.5

测试日期：2026-09-29。设备：真实 Mac mini，Apple Silicon arm64，macOS 27.0。

验收对象是打包后的 `LinkExpand.app`，应用进程的 PATH 仅为 macOS 系统目录；通过源代码运行的结果没有替代应用包测试。另将最终 ZIP 解压，以 LaunchServices（`open -n -W`，和双击使用相同启动服务）打开解压后的应用。

界面使用系统 WebKit 的 Mac 原生窗口，沿用原有 HTML/CSS 和后台。测试操作实际窗口中的控件；Command-V 通过原生菜单／响应者链粘贴系统剪贴板内容。测试入口只有显式 `--ui-test` 时启用，并需要本机会话认证，正常启动不会启用。

| 环节 | 实测行为和结果 |
| --- | --- |
| 启动 | 从打包后的应用及 ZIP 解压后的应用正常打开原生窗口；无 Python 环境依赖 |
| 输入链接 | 将真实 B 站 URL 放入系统剪贴板，在原生窗口执行 Command-V，自动触发展开 |
| 预览 | BV1cSec6tEux 返回标题、摘要、真实封面和完整视频选项 |
| 编辑 | 在窗口修改标题并重新生成，封面与视频下载选项保留 |
| 复制卡片 | 点击左侧按钮，原生剪贴板提供有效 PNG |
| 复制图文 | 点击右侧按钮，真实 NSTextView 原生接收器执行粘贴，保留嵌入图片、标题、摘要和 NSLink 可点击 URL |
| 保存图片 | 点击实际导出按钮，卡片 PNG 和源图保存到 `~/Downloads/LinkExpand-exports`，同名文件不覆盖 |
| 完整下载 | 在原生窗口点击下载，真实获取 140,922,591 字节 MP4，未超过 500 MB |
| 暂停和续传 | 从界面暂停；退出应用、重新打开后，界面恢复暂停任务；继续下载并完成 |
| 复制文件 | 点击复制视频，原生 NSPasteboard 提供对应 NSURL 文件对象 |
| 文件接收 | 原生文件接收器通过 NSPasteboardReading 读取文件对象，使用 NSFileManager 复制实际文件，文件 SHA-256 与原下载一致 |
| 视频另存 | 点击实际另存按钮生成完整文件副本，SHA-256 与原下载一致 |
| 退出 | 从界面退出，应用正常返回 0，并关闭自己的后台服务 |
| HLS、加密 HLS、DASH | Mac 上使用受控、真实生成的音视频媒体下载、恢复分段、合并，并用 FFmpeg 实际解码；时长均为 4.04 秒且包含视频、音频 |
| 分离音视频合并 | 下载并合并独立轨道，Mac 本机解码验证视频、音频均完整 |
| 兼容回归 | 原有网页截图、视频截图、X 下载、通用文件下载、图文复制和版本检查通过 |

完整 B 站 MP4：1280×590，H.264 视频 + AAC 音频，1631.296 秒。校验值：

```text
ecfcbb24a8b74f699bdbec3a5906fd36b38fd21de99de81dbf4d46a8ec6b9d6c
```

全流程脚本：

```bash
python tests/macos_full_chain.py --app path/to/LinkExpand.app --restart
python tests/macos_full_chain.py --app path/to/unzipped/LinkExpand.app --launch-services --skip-video
python tests/streaming_smoke.py
```

发布附件中的 `macOS-validation-v0.3.5.zip` 包含实际窗口截图及测试日志。视频样本不包含在软件包或验证附件内。

边界：实际接收端是原生文本接收器和原生文件接收器，没有把“剪贴板里存在某个格式”当成粘贴成功。本机未授予跨应用辅助功能自动操作权限，因此没有宣称已自动操作 Finder 或微信／QQ 的实际窗口。第三方 B 站公开链接解析的可用性、画质依赖服务；用户可关闭该回退。
