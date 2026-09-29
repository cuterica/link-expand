"""Build an independent macOS app with the existing browser interface."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys

from build_windows import notices

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    args=parser.parse_args()
    if sys.platform!='darwin':parser.exit(1,'Build this package on macOS with macOS Python.\n')
    version=re.search(r'^version = "([^"]+)"',(ROOT/'pyproject.toml').read_text(),re.M)[1]
    arch=platform.machine()
    if arch not in {'arm64','x86_64'}:parser.exit(1,'Unsupported Mac architecture.\n')
    work=Path.home()/'Library'/'Application Support'/'LinkExpand'/'build'
    stage=work/'source'
    if stage.exists():shutil.rmtree(stage)
    shutil.copytree(ROOT/'linkexpand',stage/'linkexpand',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copy2(ROOT/'packaging'/'entrypoint.py',stage/'entrypoint.py')
    licenses=work/'licenses';notices(licenses)
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--name','LinkExpand',
             '--exclude-module','tkinter',
             '--target-architecture',arch,'--osx-bundle-identifier','com.cuterica.linkexpand','--collect-all','playwright',
             '--collect-data','certifi','--add-data',f'{stage/"linkexpand"/"static"}:linkexpand/static',
             '--add-data',f'{ROOT/"browser-extension"}:browser-extension',
             '--add-data',f'{licenses}:licenses','--paths',str(stage),'--distpath',str(work/'dist'),
             '--workpath',str(work/'pyinstaller'),'--specpath',str(work),str(stage/'entrypoint.py')]
    subprocess.run(command,cwd=stage,check=True)
    app=work/'dist'/'LinkExpand.app'
    info=app/'Contents'/'Info.plist'
    values=plistlib.loads(info.read_bytes());values.update(CFBundleShortVersionString=version,CFBundleVersion=version,
        LSMinimumSystemVersion='13.0',NSHighResolutionCapable=True)
    info.write_bytes(plistlib.dumps(values))
    # Ad-hoc signing verifies the app's own binaries; it is not Apple notarization.
    subprocess.run(['codesign','--force','--deep','--sign','-',str(app)],check=True)
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    name=f'LinkExpand-{version}-macOS-{arch}'
    bundle=work/'portable'/name
    if bundle.exists():shutil.rmtree(bundle)
    bundle.mkdir(parents=True)
    subprocess.run(['ditto',str(app),str(bundle/'LinkExpand.app')],check=True)
    shutil.copytree(ROOT/'browser-extension',bundle/'browser-extension')
    (bundle/'使用说明.txt').write_text(
        f'Link Expand {version} · macOS {arch}\n\n'
        '解压 ZIP，双击 LinkExpand.app；也可以将它拖到应用程序文件夹。无需 Python。\n'
        '使用 Mac 原生窗口承载原有界面；支持 Command+C/V/Q，不需另外打开浏览器。\n'
        'B 站常规读取失败可用第三方公开 BV 链接解析；界面可关闭，只发送标准链接。\n'
        '仍失败时自动用 Playwright 打开真实 Chrome；不安装 Edge。\n'
        '未做 Apple 商业签名与公证。首次启动如被拦截，请到系统设置 → 隐私与安全性 → 仍要打开。\n'
        '链接展开、选图、截图与图文复制；通用文件、视频、HLS/DASH 点播下载，大小可选 500 MB 或无限制；无限制优先最大分辨率及最高可用画质。\n'
        '截图优先使用已安装的 Chrome；没有时可在 Link Expand 菜单安装独立截图组件。\n'
        '图文复制提供嵌入图片和链接的 RTFD、HTML、PNG 和文字，接收软件决定粘贴格式。\n'
        '下载完成可复制整个文件；保存目录 ~/Downloads/LinkExpand-videos。\n'
        '图片和另存文件导出到 ~/Downloads/LinkExpand-exports，同名文件不覆盖。\n'
        'HLS/DASH、分离音视频合并需要免费的 FFmpeg（不内嵌）。可用 brew install ffmpeg，\n'
        '或把 ffmpeg 放到 ~/Library/Application Support/LinkExpand/tools/ffmpeg。\n'
        '附带 Chrome 联动扩展；按 browser-extension/README.md 安装配对一次，启用自动联动。\n'
        '软件输入链接后自动读取已登录浏览器的预览与媒体；配对码跨重启保留。\n'
        '无封面时可授予调试权限截取自己的后台标签页，不切换前台页面。\n'
        '下载任务不保存 Cookie/Authorization；独立回退浏览器可能保存本机登录状态。\n'
        '退出会暂停下载；截图与识别使用临时会话，关闭程序会清理自己的测试浏览器进程。\n'
        '项目与更新：https://github.com/cuterica/link-expand\n',encoding='utf-8')
    archive=output/(name+'.zip');archive.unlink(missing_ok=True)
    subprocess.run(['ditto','-c','-k','--sequesterRsrc','--keepParent',str(bundle),str(archive)],check=True)
    checksum=output/('SHA256SUMS-macOS-'+arch+'.txt')
    checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n',encoding='ascii')
    print(f'App: {app}\nPortable: {archive}\nChecksums: {checksum}',flush=True)

if __name__=='__main__':main()
