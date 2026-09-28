"""Small macOS launcher window; the existing browser interface stays unchanged."""

from pathlib import Path
import subprocess
import os
import signal
import sys
import threading
import tkinter as tk
from tkinter import ttk

from . import __version__
from .video_downloads import downloads_directory
from .capture import browser_executable


def run(server,url):
    root=tk.Tk();root.title('Link Expand '+__version__);root.resizable(False,False)
    frame=ttk.Frame(root,padding=24);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Link Expand 正在运行',font=('Helvetica',18,'bold')).pack(anchor='w',pady=(0,10))
    ttk.Label(frame,text=url).pack(anchor='w')
    ttk.Label(frame,text='在浏览器中展开链接和下载文件。退出会暂停未完成的下载。',wraplength=430).pack(anchor='w',pady=(8,16))
    controls=ttk.Frame(frame);controls.pack(fill='x')
    ttk.Button(controls,text='打开软件界面',command=lambda:subprocess.Popen(['open',url])).pack(side='left')
    def open_downloads():
        directory=downloads_directory();directory.mkdir(parents=True,exist_ok=True)
        subprocess.Popen(['open',str(directory)])
    ttk.Button(controls,text='打开下载文件夹',command=open_downloads).pack(side='left',padx=8)
    status=tk.StringVar(value='截图使用已安装的 Chrome / Edge，或免费的独立截图组件。')
    installer=[];installer_lock=threading.Lock()
    def stop_installer():
        with installer_lock:
            for process in installer:
                if process.poll() is not None:continue
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
    ttk.Label(frame,textvariable=status,wraplength=430).pack(anchor='w',pady=(16,8))
    def install_browser():
        install.configure(state='disabled');status.set('正在安装独立截图组件，请稍候…')
        def worker():
            folder=Path.home()/'Library'/'Application Support'/'LinkExpand';folder.mkdir(parents=True,exist_ok=True)
            try:
                with (folder/'browser-install.log').open('wb') as log:
                    with installer_lock:
                        if closing.is_set():return
                        process=subprocess.Popen([sys.executable,'--browser-install-worker'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
                        installer.append(process)
                    process.wait()
                    with installer_lock:
                        if process in installer:installer.remove(process)
                message='截图组件已就绪。' if process.returncode==0 else '截图组件安装失败；可以安装 Chrome / Edge 后再使用截图。'
            except OSError:message='截图组件无法安装，请使用已安装的 Chrome / Edge。'
            def finished():status.set(message);install.configure(state='normal')
            if not closing.is_set():
                try:root.after(0,finished)
                except (tk.TclError,RuntimeError):pass
        threading.Thread(target=worker,name='LinkExpand-browser-install',daemon=True).start()
    install=ttk.Button(frame,text='安装截图组件（可选）',command=install_browser);install.pack(anchor='w')
    if browser_executable():status.set('已检测到浏览器，截图组件可用。')
    closing=threading.Event()
    def close():
        if closing.is_set():return
        closing.set();stop_installer();server.shutdown();root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    root.createcommand('::tk::mac::Quit',close)
    ttk.Button(frame,text='退出',command=close).pack(anchor='e',pady=(12,0))
    serving=threading.Thread(target=server.serve_forever,name='LinkExpand-server',daemon=True);serving.start()
    try:root.mainloop()
    finally:
        if not closing.is_set():server.shutdown()
        stop_installer()
        serving.join(timeout=3)
