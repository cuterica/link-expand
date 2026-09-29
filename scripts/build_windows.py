"""Build the existing application as a standalone Windows portable executable."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def notices(target):
    target.mkdir(parents=True, exist_ok=True)
    packages=["pillow", "playwright", "greenlet", "pyee", "typing-extensions", "pyinstaller"]
    if sys.platform=='darwin':packages+=['certifi','macholib','altgraph','pyobjc-core','pyobjc-framework-Cocoa','pyobjc-framework-WebKit']
    for package in packages:
        distribution = metadata.distribution(package)
        for item in distribution.files or []:
            if re.search(r"(?:^|/)(?:LICENSE|COPYING|NOTICE)[^/]*$", str(item), re.I):
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    destination = target / package / str(item).replace("/", "_")
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copy2(source, destination)
    for name in ['LICENSE.txt','LICENSE','Resources/LICENSE.txt']:
        python_license=Path(sys.base_prefix)/name
        if python_license.is_file():shutil.copy2(python_license,target/'Python-LICENSE.txt');break
    if sys.platform=='darwin':
        import tkinter
        library=Path(tkinter.Tcl().call('info','library'))
        for folder in [library,library.parent/'tk8.6',library.parent,Path('/Library/Frameworks/Tcl.framework/Resources'),Path('/Library/Frameworks/Tk.framework/Resources')]:
            source=folder/'license.terms'
            if source.is_file():shutil.copy2(source,target/(folder.name+'-license.terms'))
    (target / "README.txt").write_text(
        "Link Expand bundles Python, Pillow, Playwright and their dependencies.\n"
        "These files contain the licenses of the bundled components.\n"
        "Playwright's bundled Node.js driver also contains its own LICENSE and notices.\n"
        "Chromium, Chrome and Edge are not included in this distribution.\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if os.name != "nt":
        parser.exit(1, "Run this script with Windows Python to build a Windows executable.\n")
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    name = f"LinkExpand-{version}-Windows-x64"
    work = Path(os.environ["LOCALAPPDATA"]) / "LinkExpand" / "build"
    # Native storage keeps dependency analysis fast when the source is under WSL.
    stage = work / "source"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(ROOT / "linkexpand", stage / "linkexpand", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "packaging" / "entrypoint.py", stage / "entrypoint.py")
    notice_dir = work / "licenses"
    notices(notice_dir)
    numbers = tuple(int(part) for part in version.split(".")) + (0,)
    version_file = work / "version_info.txt"
    version_file.write_text(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0,
                   OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[StringFileInfo([StringTable('040904b0', [
    StringStruct('CompanyName', 'cuterica'),
    StringStruct('FileDescription', 'Link Expand - local link previews'),
    StringStruct('FileVersion', '{version}'),
    StringStruct('ProductVersion', '{version}'),
    StringStruct('ProductName', 'Link Expand'),
    StringStruct('OriginalFilename', '{name}.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])''', encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
               "--console", "--name", name, "--collect-all", "playwright",
               "--exclude-module", "tkinter",
               "--add-data", f"{stage / 'linkexpand' / 'static'};linkexpand/static",
               "--add-data", f"{ROOT / 'browser-extension'};browser-extension",
               "--add-data", f"{notice_dir};licenses", "--version-file", str(version_file),
               "--paths", str(stage), "--distpath", str(work / "dist"),
               "--workpath", str(work / "pyinstaller"), "--specpath", str(work),
               str(stage / "entrypoint.py")]
    subprocess.run(command, cwd=stage, check=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    exe = output / (name + ".exe")
    shutil.copy2(work / "dist" / exe.name, exe)
    guide = output / "使用说明.txt"
    guide.write_text(
        f"Link Expand {version} · Windows x64 免安装版\n\n"
        "双击 EXE，会自动打开现有的本地网页界面。无需安装 Python。\n"
        "将 URL 粘贴到输入框，即可自动展开摘要、图片或截图。\n"
        "B 站常规读取失败可用第三方公开 BV 链接解析；界面可关闭，只发送标准链接。\n"
        "仍失败时自动用 Playwright 打开真实浏览器；独立窗口不保证解除 412。\n"
        "通用下载支持普通文件、视频/音频、网页资源、HLS/DASH 点播。\n"
        "支持 1–16 连接、限速、失败重试、暂停续传与任务保存；每个任务最多 500 MB。\n"
        "完整文件保存到系统下载文件夹的 LinkExpand-videos；完成后可复制文件。\n"
        "HLS/DASH、分离音视频合并需要免费的 FFmpeg。已安装时自动查找；\n"
        "也可以把 ffmpeg.exe 放到本 EXE 旁边，或设置 LINK_EXPAND_FFMPEG。\n"
        "未安装时可用 winget install --id Gyan.FFmpeg -e（自行执行）。\n"
        "复杂播放器可安装 browser-extension 目录中的 Chrome / Edge 扩展：\n"
        "打开 chrome://extensions 或 edge://extensions，开启开发者模式，\n"
        "选择‘加载已解压的扩展程序’，选中 browser-extension 文件夹。\n"
        "在软件内复制扩展配对码；扩展内填本地地址和配对码，启用自动联动，\n"
        "授予网站访问权限后，软件输入链接即可自动读取预览和媒体。\n"
        "配对码跨软件重启保留。无图片时可授予调试权限截图自己的后台标签页。\n"
        "截图使用电脑中已安装的 Edge 或 Chrome，不包含浏览器安装包。\n"
        "使用期间请保留启动窗口；退出时关闭启动窗口，或按 Ctrl+C。\n"
        "如果 8765 端口已被占用，新启动会选择空闲端口；扩展内填实际本地地址。\n"
        "网页仍要求登录或验证码时，请在浏览器完成后重试；不处理 DRM。\n\n"
        "项目与更新：https://github.com/cuterica/link-expand\n"
        "第三方组件许可证在压缩包 licenses 目录及 EXE 内附带。\n",
        encoding="utf-8-sig",
    )
    extension = output / f"LinkExpand-browser-extension-{version}.zip"
    with zipfile.ZipFile(extension, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted((ROOT / "browser-extension").iterdir()):
            if file.is_file(): archive.write(file, file.name)
    portable = output / (name + ".zip")
    with zipfile.ZipFile(portable, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe, exe.name)
        archive.write(guide, guide.name)
        for file in sorted((ROOT / "browser-extension").iterdir()):
            if file.is_file(): archive.write(file, str(Path("browser-extension") / file.name))
        for file in sorted(notice_dir.rglob("*")):
            if file.is_file():
                archive.write(file, str(Path("licenses") / file.relative_to(notice_dir)))
    checksum = output / "SHA256SUMS.txt"
    checksum.write_text("".join(f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}\n"
                               for file in [exe, portable, extension]), encoding="ascii")
    print(f"Built: {exe}\nPortable: {portable}\nExtension: {extension}\nChecksums: {checksum}", flush=True)


if __name__ == "__main__":
    main()
