"""Packaged Windows EXE -> real browser input/paste -> downloads -> restart -> actual file paste."""
import argparse
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright,expect
from linkexpand.clipboard import set_formats
from packaged_smoke import verify_video_file_clipboard


class ClipboardBackup:
    def __init__(self):
        self.ole=C.OleDLL('ole32');self.pointer=C.c_void_p()
        self.ole.OleInitialize(None)
        self.ole.OleGetClipboard.argtypes=[C.POINTER(C.c_void_p)]
        self.ole.OleSetClipboard.argtypes=[C.c_void_p]
        self.ole.OleGetClipboard(C.byref(self.pointer))
        self.user=C.WinDLL('user32');self.changed=None
    def changed_by_test(self):self.changed=self.user.GetClipboardSequenceNumber()
    def close(self):
        if self.pointer.value:
            if self.changed==self.user.GetClipboardSequenceNumber():
                self.ole.OleSetClipboard(self.pointer);self.ole.OleFlushClipboard()
            table=C.cast(self.pointer,C.POINTER(C.POINTER(C.c_void_p))).contents
            C.WINFUNCTYPE(C.c_ulong,C.c_void_p)(table[2])(self.pointer)
        self.ole.OleUninitialize()


def stop_console(process):
    """Send Ctrl-C to this app's own console, leaving personal app processes alone."""
    if process.poll() is not None:return
    # Console control events must not reach this test's Python/Playwright process.
    # A separate helper stays attached until the target exits, then detaches.
    helper=r'''
import ctypes,sys,time
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.FreeConsole()
assert kernel.AttachConsole(int(sys.argv[1])),ctypes.get_last_error()
assert kernel.SetConsoleCtrlHandler(None,True)
assert kernel.GenerateConsoleCtrlEvent(0,0),ctypes.get_last_error()
time.sleep(2)
kernel.FreeConsole()
'''
    result=subprocess.run([sys.executable,'-c',helper,str(process.pid)],capture_output=True,timeout=10)
    if result.returncode:raise RuntimeError(f'Console helper failed: {result.stderr.decode(errors="replace")}')
    process.wait(timeout=20)


def wait_backend(base,process):
    for attempt in range(100):
        if process.poll() is not None:raise RuntimeError('The packaged application exited')
        try:
            with urllib.request.urlopen(base+'/api/health',timeout=1) as response:return json.load(response)
        except OSError:time.sleep(.3)
    raise RuntimeError('Packaged application failed to start')


def main():
    if os.name!='nt':raise RuntimeError('Run this acceptance test with Windows Python')
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'artifacts/windows-full-chain';root.mkdir(exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix='linkexpand-windows-chain-'));storage=folder/'downloads'
    with socket.socket() as reserved:reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    environment=dict(os.environ)
    for key in ['PYTHONHOME','PYTHONPATH','VIRTUAL_ENV']:environment.pop(key,None)
    windows=environment.get('SystemRoot','C:/Windows');environment['PATH']=os.pathsep.join([windows,str(Path(windows)/'System32')])
    command=[str(args.executable.resolve()),'--no-browser','--ui-test','--test-downloads-root',str(storage),'--port',str(port)]
    log=(root/'exe.log').open('w');process=None;backup=ClipboardBackup()
    try:
        def launch():return subprocess.Popen(command,stdout=log,stderr=log,env=environment,creationflags=subprocess.CREATE_NEW_CONSOLE)
        process=launch();health=wait_backend(base,process)
        assert health['version']=='0.3.6',health
        print('PASS: actual Windows portable EXE starts without Python on PATH.',flush=True)
        browser_path=Path(os.environ['LOCALAPPDATA'])/'ms-playwright/chromium-1228/chrome-win64/chrome.exe'
        with sync_playwright() as runtime:
            browser=runtime.chromium.launch(executable_path=str(browser_path),headless=False)
            try:
                context=browser.new_context(viewport={'width':1440,'height':1080},permissions=['clipboard-read','clipboard-write'],accept_downloads=True)
                errors=[];page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base);expect(page.locator('#copy-text')).to_be_disabled()
                target='https://www.bilibili.com/video/BV1cSec6tEux/'
                set_formats([(13,(target+'\0').encode('utf-16-le'))]);backup.changed_by_test()
                page.locator('#url-input').click();page.keyboard.press('Control+V')
                expect(page.locator('#card-title')).to_contain_text('iPhone 18',timeout=60000)
                expect(page.locator('#cover-image')).to_be_visible()
                assert page.locator('#cover-image').evaluate('image=>image.complete && image.naturalWidth>0')
                expect(page.locator('#video-download')).to_be_enabled()
                page.screenshot(path=str(root/'01-live-preview.png'),full_page=True)
                print('PASS: actual Ctrl-V -> Bilibili URL -> title, summary, image and complete-video option.',flush=True)
                page.locator('#edit-toggle').click();page.locator('#title-input').fill('Windows 界面验收 · iPhone 18');page.locator('#save-edit').click()
                expect(page.locator('#card-title')).to_have_text('Windows 界面验收 · iPhone 18')
                expect(page.locator('#video-download')).to_be_enabled()
                print('PASS: edit title keeps the image and download choice.',flush=True)
                receiver=context.new_page()
                receiver.route(base+'/qa-receiver',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='''<div id="paste" contenteditable="true" style="width:600px;min-height:500px"></div>
                    <script>window.filePaste=null;document.getElementById('paste').addEventListener('paste',async event=>{
                    if(event.clipboardData.files.length && !event.clipboardData.files[0].type.startsWith('image/')){event.preventDefault();const file=event.clipboardData.files[0];const bytes=await file.arrayBuffer();
                    const hash=await crypto.subtle.digest('SHA-256',bytes);window.filePaste={name:file.name,size:file.size,sha256:[...new Uint8Array(hash)].map(value=>value.toString(16).padStart(2,'0')).join('')};}});</script>'''))
                receiver.goto(base+'/qa-receiver')
                page.bring_to_front();page.locator('#copy-image').click();backup.changed_by_test()
                expect(page.locator('#toast')).to_contain_text('卡片图片已复制')
                receiver.bring_to_front();receiver.locator('#paste').click();receiver.keyboard.press('Control+V')
                expect(receiver.locator('#paste img')).to_have_count(1)
                assert receiver.locator('#paste img').evaluate('image=>image.complete && image.naturalWidth>0')
                print('PASS: actual card image copy pastes a visible image in the receiving editor.',flush=True)
                receiver.locator('#paste').fill('')
                page.bring_to_front();page.locator('#copy-text').click();backup.changed_by_test()
                expect(page.locator('#toast')).to_contain_text('图片、文字与真实链接已一起复制')
                backup.changed_by_test()
                receiver.bring_to_front();receiver.locator('#paste').click();receiver.keyboard.press('Control+V')
                expect(receiver.locator('#paste img')).to_have_count(1)
                expect(receiver.locator('#paste a').first).to_have_attribute('href',target)
                expect(receiver.locator('#paste')).to_contain_text('Windows 界面验收')
                assert receiver.locator('#paste img').evaluate('image=>image.complete && image.naturalWidth>0')
                receiver.screenshot(path=str(root/'02-actual-rich-paste.png'),full_page=True)
                print('PASS: actual rich paste preserves visible image, edited title, summary and clickable link.',flush=True)
                page.bring_to_front()
                for button,name in [('download','card.png'),('download-cover','source.png')]:
                    with page.expect_download() as event:page.locator('#'+button).click()
                    event.value.save_as(str(root/name));assert (root/name).read_bytes().startswith(b'\x89PNG')
                print('PASS: UI download buttons export card and source image.',flush=True)
                page.locator('.download-advanced').first.locator('summary').click()
                page.locator('#download-connections').select_option('8');page.locator('#download-speed').fill('2048')
                page.locator('#video-download').click();expect(page.locator('#video-status')).to_have_text('正在下载',timeout=45000)
                page.locator('#video-pause').click();expect(page.locator('#video-status')).to_have_text('已暂停',timeout=20000)
                stop_console(process)
                print('PASS: actual Ctrl-C closes only own app console.',flush=True)
                process=launch();wait_backend(base,process);page.reload()
                expect(page.locator('#video-status')).to_have_text('已暂停',timeout=30000)
                page.locator('#video-resume').click();expect(page.locator('#video-status')).to_have_text('下载完成',timeout=300000)
                path=Path(page.locator('#video-path').inner_text().removeprefix('已保存到：'))
                assert path.is_file() and path.stat().st_size==140922591
                digest=hashlib.sha256(path.read_bytes()).hexdigest();assert digest=='ecfcbb24a8b74f699bdbec3a5906fd36b38fd21de99de81dbf4d46a8ec6b9d6c'
                page.screenshot(path=str(root/'03-resumed-complete-download.png'),full_page=True)
                print('PASS: app reopen restores paused UI task; resume downloads the complete 141 MB video with expected SHA-256.',flush=True)
                page.locator('#video-copy').click();expect(page.locator('#toast')).to_contain_text('完整文件已复制')
                backup.changed_by_test();verify_video_file_clipboard(path)
                receiver.bring_to_front();receiver.locator('#paste').fill('');receiver.locator('#paste').click();receiver.keyboard.press('Control+V')
                receiver.wait_for_function('window.filePaste!==null',timeout=30000)
                pasted=receiver.evaluate('window.filePaste');assert pasted['size']==path.stat().st_size and pasted['sha256']==digest,pasted
                print(json.dumps({'PASS':'actual file paste recipient reads the whole video','size':pasted['size'],'sha256':pasted['sha256']}),flush=True)
                page.bring_to_front()
                print('Checking actual full-video export button.',flush=True)
                with page.expect_download() as event:page.locator('#video-save').click()
                saved=root/'另存完整视频.mp4';event.value.save_as(str(saved));assert hashlib.sha256(saved.read_bytes()).hexdigest()==digest
                print('PASS: full-video export is byte-identical.',flush=True)
                assert not errors,errors
                context.close()
            finally:browser.close()
        print('Closing own console after the receiving-browser test.',flush=True)
        stop_console(process)
        print('PASS: full Windows GUI and receiving-editor chain, restart/resume, file paste and export.',flush=True)
    finally:
        if process and process.poll() is None:
            try:stop_console(process)
            except Exception:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        log.close();backup.close()


if __name__=='__main__':main()
