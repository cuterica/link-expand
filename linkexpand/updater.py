"""Verified updates from this project's public GitHub releases."""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from . import __version__
from .metadata import PreviewError,fetch_resource
from .video_downloads import DownloadManager

REPOSITORY='cuterica/link-expand'
LATEST='https://api.github.com/repos/'+REPOSITORY+'/releases/latest'


def version_tuple(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(value))
    if not match:raise PreviewError('更新版本号无效。')
    return tuple(int(part) for part in match.groups())


def update_directory():
    if sys.platform=='win32':root=Path(os.environ['LOCALAPPDATA'])/'LinkExpand'
    elif sys.platform=='darwin':root=Path.home()/'Library/Application Support/LinkExpand'
    else:root=Path.home()/'.local/share/LinkExpand'
    return root/'updates'


def select_release(data, system=None, architecture=None):
    system=system or sys.platform;architecture=architecture or platform.machine()
    if not isinstance(data,dict) or data.get('draft') or data.get('prerelease'):raise PreviewError('尚无可用的正式更新。')
    tag=data.get('tag_name');version_tuple(tag);version=tag.removeprefix('v')
    name=(f'LinkExpand-{version}-Windows-x64.exe' if system=='win32' else
          f'LinkExpand-{version}-macOS-{architecture}.zip' if system=='darwin' else '')
    asset=next((item for item in data.get('assets',[]) if isinstance(item,dict) and item.get('name')==name),None)
    if not asset:raise PreviewError('这个系统暂时没有对应的更新安装包。')
    digest=asset.get('digest','')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',digest):raise PreviewError('新版没有提供有效的 SHA-256 校验信息。')
    address=asset.get('browser_download_url','')
    expected=f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
    if address!=expected:raise PreviewError('更新安装包地址不属于本项目。')
    size=asset.get('size')
    if type(size) is not int or not 0<size<=256*1024*1024:raise PreviewError('更新安装包大小无效。')
    return {'version':version,'tag':tag,'name':name,'url':address,'sha256':digest[7:],'size':size}


def file_digest(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        while block:=source.read(1024*1024):digest.update(block)
    return digest.hexdigest()


class Updater:
    def __init__(self, executable=None, frozen=None):
        self.executable=Path(executable or sys.executable).resolve()
        self.supported=bool(getattr(sys,'frozen',False) if frozen is None else frozen) and sys.platform in {'win32','darwin'}
        self.lock=threading.RLock();self.release=None;self.job=None;self.manager=None;self.directory=None
        self.state='idle';self.error='';self.installing=False

    def check(self):
        with self.lock:
            if self.state in {'downloading','ready','installing'}:return self.status()
        resource=fetch_resource(LATEST,2*1024*1024,timeout=15,headers={'Accept':'application/vnd.github+json','User-Agent':'LinkExpand-Updater'})
        try:data=json.loads(resource.body)
        except ValueError:raise PreviewError('更新服务器没有返回有效数据。') from None
        release=select_release(data)
        available=version_tuple(release['version'])>version_tuple(__version__)
        with self.lock:
            self.release=release;self.state='available' if available else 'current';self.error=''
        return self.status()

    def status(self):
        with self.lock:
            task=self.job.snapshot() if self.job else None
            return {'state':self.state,'current_version':__version__,'latest_version':self.release['version'] if self.release else None,
                    'available':bool(self.release and version_tuple(self.release['version'])>version_tuple(__version__)),
                    'supported':self.supported,'progress':task.get('progress') if task else None,'error':self.error}

    def download(self):
        with self.lock:
            if not self.supported:raise PreviewError('请使用 Windows EXE 或 Mac 应用分发版的一键更新功能。')
            if self.state in {'downloading','ready','installing'}:return self.status()
            if not self.release or not self.status()['available']:raise PreviewError('请先检查更新；当前没有更高版本。')
            if not os.access(self.target().parent,os.W_OK):raise PreviewError('当前软件目录不可写，请把程序移到下载文件夹后重试。')
            self.directory=update_directory()/secrets.token_hex(12);self.directory.mkdir(parents=True)
            self.manager=DownloadManager(self.directory/'payload',limit=256*1024*1024)
            release=dict(self.release)
            video={'index':1,'post_id':release['tag'],'kind':'file','filename':release['name'],'variants':[{'url':release['url'],'quality':'更新安装包'}],
                   'options':{'connections':0,'max_bytes':256*1024*1024,'speed_limit':0}}
            state=self.manager.start(video,release['url']);self.job=self.manager.get(state['id'])
            self.state='downloading';self.error=''
        def watch():
            try:
                self.job.thread.join()
                if self.job.status!='complete':raise PreviewError(self.job.error or '更新下载未完成，请重试。')
                if self.job.file.stat().st_size!=release['size'] or file_digest(self.job.file)!=release['sha256']:
                    raise PreviewError('更新安装包校验失败，未安装。')
                with self.lock:self.state='ready'
            except (OSError,PreviewError) as error:
                with self.lock:self.error=str(error);self.state='error'
        threading.Thread(target=watch,name='LinkExpand-update-download',daemon=True).start()
        return self.status()

    def target(self):
        if sys.platform=='darwin':
            app=next((part for part in self.executable.parents if part.suffix=='.app'),None)
            if not app:raise PreviewError('无法确定当前 Mac 应用位置。')
            return app
        return self.executable

    def apply(self, port, restart_args=None):
        with self.lock:
            if self.state!='ready' or not self.job or not self.release:raise PreviewError('新版尚未下载并校验完成。')
            target=self.target()
            manifest={'platform':sys.platform,'parent_pid':os.getpid(),'source':str(self.job.file),'target':str(target),
                      'sha256':self.release['sha256'],'version':self.release['version'],'port':port,
                      'restart_args':restart_args or [],'directory':str(self.directory)}
            manifest_path=self.directory/'install.json';manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
            if sys.platform=='win32':
                helper=self.directory/'LinkExpand-update-helper.exe';shutil.copy2(self.executable,helper)
                subprocess.Popen([str(helper),'--update-worker',str(manifest_path)],creationflags=subprocess.CREATE_NO_WINDOW,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else:
                helper=self.directory/'LinkExpand-update-helper.app'
                subprocess.run(['/usr/bin/ditto',str(target),str(helper)],check=True,timeout=60)
                subprocess.Popen([str(helper/'Contents/MacOS/LinkExpand'),'--update-worker',str(manifest_path)],start_new_session=True,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.state='installing';self.installing=True
        return self.status()

    def close(self):
        if self.manager and not self.installing:self.manager.close()
