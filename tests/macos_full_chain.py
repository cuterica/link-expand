"""Real Mac app window -> native paste -> preview -> export -> full download -> file paste."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from linkexpand.macos_clipboard import Cocoa
from macos_clipboard_smoke import contents,restore


class UI:
    def __init__(self,base,token):self.base=base;self.token=token
    def post(self,path,data,timeout=35):
        request=urllib.request.Request(self.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Local-Token':self.token})
        try:
            with urllib.request.urlopen(request,timeout=timeout) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'{path} HTTP {error.code}: '+error.read().decode()) from None
    def eval(self,expression):
        result=self.post('/api/qa',{'action':'eval','code':'JSON.stringify('+expression+')'})
        return json.loads(result['value']) if result['value'] is not None else None
    def click(self,id):self.eval(f"(()=>{{document.getElementById({json.dumps(id)}).click();return true;}})()")
    def wait(self,expression,expected,timeout=45):
        deadline=time.monotonic()+timeout;last=None
        while time.monotonic()<deadline:
            last=self.eval(expression)
            if (expected(last) if callable(expected) else last==expected):return last
            time.sleep(.3)
        feedback=self.eval("({feedback:document.getElementById('feedback').textContent,video:document.getElementById('video-feedback').textContent})")
        raise AssertionError(f'GUI check failed: {expression}; got {last}; {feedback}')
    def snapshot(self,path):return self.post('/api/qa',{'action':'snapshot','path':str(path)})
    def export(self,button,path):
        self.click(button)
        message=self.wait("document.getElementById('toast').textContent",lambda value:'已保存到：' in value or '已另存到：' in value)
        output=Path(message.split('到：',1)[1].strip())
        assert output.is_file(),message
        path.write_bytes(output.read_bytes())
        return output


def main():
    if sys.platform!='darwin':raise RuntimeError('Run on the actual Mac.')
    parser=argparse.ArgumentParser();parser.add_argument('--app',type=Path);parser.add_argument('--port',type=int)
    parser.add_argument('--skip-video',action='store_true');parser.add_argument('--restart',action='store_true');parser.add_argument('--launch-services',action='store_true');parser.add_argument('--unlimited',action='store_true');args=parser.parse_args()
    folder=Path.home()/'Library/Application Support/LinkExpand/mac-chain-qa';folder.mkdir(exist_ok=True)
    with socket.socket() as reserved:reserved.bind(('127.0.0.1',0));port=args.port or reserved.getsockname()[1]
    base=f'http://127.0.0.1:{port}';process=None;log=None
    cocoa=Cocoa()
    with cocoa.pool():backup=contents(cocoa)
    try:
        if not args.port:
            command=[str(args.app/'Contents/MacOS/LinkExpand')] if args.app else [sys.executable,'-m','linkexpand.server','--native-window']
            command+=['--ui-test','--port',str(port)]
            if args.restart:
                storage=folder/f'download-restart-{time.time_ns()}';command+=['--test-downloads-root',str(storage)]
            if args.launch_services:
                if not args.app:raise RuntimeError('LaunchServices test requires a packaged .app')
                command=['open','-n','-W',str(args.app),'--args']+command[1:]
            environment=dict(__import__('os').environ)
            if args.app:
                for key in ['PYTHONPATH','PYTHONHOME','VIRTUAL_ENV']:environment.pop(key,None)
                environment['PATH']='/usr/bin:/bin:/usr/sbin:/sbin'
            log=(folder/'gui-process.log').open('w')
            process=subprocess.Popen(command,stdout=log,stderr=log,env=environment,start_new_session=True)
        for attempt in range(80):
            if process and process.poll() is not None:raise RuntimeError('Native app exited: '+(folder/'gui-process.log').read_text()[-1200:])
            try:
                with urllib.request.urlopen(base,timeout=1) as response:html=response.read().decode()
                break
            except OSError:time.sleep(.4)
        else:raise RuntimeError('Native app failed to start')
        token=re.search(r'name="local-token" content="([^"]+)"',html).group(1);ui=UI(base,token)
        ui.wait("document.readyState",'complete')
        ui.wait("!!document.getElementById('url-input')",True)
        ui.wait("!document.getElementById('native-quit').hidden",True)
        print('PASS: native application window loaded the actual interface, no Python on PATH for packaged run.',flush=True)
        if args.unlimited:
            ui.eval("(()=>{const select=document.getElementById('download-max-bytes');select.value='unlimited';select.dispatchEvent(new Event('change'));return true;})()")
            ui.wait("document.getElementById('download-limit-badge').textContent",lambda value:'无限制' in value)
            print('PASS: native unlimited selector and highest-quality badge.',flush=True)
        target='https://www.bilibili.com/video/BV1cSec6tEux/'
        with cocoa.pool():cocoa.set_payloads([('public.utf8-plain-text',target.encode())])
        ui.eval("(()=>{document.getElementById('url-input').focus();return true;})()")
        assert ui.post('/api/qa',{'action':'key','key':'v'})['handled']
        ui.wait("document.getElementById('url-input').value",target)
        ui.wait("document.getElementById('card-title').textContent",lambda value:'iPhone 18' in value,timeout=80)
        ui.wait("document.getElementById('cover-image').complete && document.getElementById('cover-image').naturalWidth>0",True)
        ui.wait("!document.getElementById('video-download').disabled",True)
        ui.snapshot(folder/'01-real-native-preview.png')
        print('PASS: real Command-V -> supplied Bilibili URL -> title/summary/cover/download option.',flush=True)
        ui.click('edit-toggle')
        ui.eval("(()=>{document.getElementById('title-input').value='Mac 原生编辑 · iPhone 18';return true;})()")
        ui.click('save-edit');ui.wait("document.getElementById('card-title').textContent",'Mac 原生编辑 · iPhone 18')
        ui.wait("!document.getElementById('video-download').disabled",True)
        print('PASS: editing title keeps preview image and full-video download options.',flush=True)
        ui.click('copy-image');ui.wait("document.getElementById('toast').textContent",lambda value:'卡片图片已复制' in value)
        with cocoa.pool():payload=dict(contents(cocoa)[0]);assert payload['public.png'].startswith(b'\x89PNG')
        ui.click('copy-text');ui.wait("document.getElementById('toast').textContent",lambda value:'一起复制' in value)
        pasted=ui.post('/api/qa',{'action':'native-paste'})
        assert pasted['attachments'] and target in pasted['links'] and 'iPhone 18' in pasted['text'],pasted
        print('PASS: actual native text view paste preserves embedded image, title, summary and clickable URL.',flush=True)
        ui.export('download',folder/'card-export.png');ui.export('download-cover',folder/'cover-export.png')
        print('PASS: real export buttons save card and source image to Downloads.',flush=True)
        if not args.skip_video:
            if args.restart:
                ui.eval("(()=>{document.getElementById('download-speed').value='2048';return true;})()")
            ui.click('video-download');ui.wait("document.getElementById('video-status').textContent",lambda value:value in {'正在下载','下载完成'},timeout=45)
            if ui.eval("document.getElementById('video-status').textContent")!='下载完成':
                ui.click('video-pause');ui.wait("document.getElementById('video-status').textContent",'已暂停',timeout=20)
                if args.restart:
                    ui.click('native-quit');process.wait(timeout=15);assert process.returncode==0
                    process=subprocess.Popen(command,stdout=log,stderr=log,env=environment,start_new_session=True)
                    for attempt in range(80):
                        try:
                            with urllib.request.urlopen(base,timeout=1) as response:html=response.read().decode()
                            break
                        except OSError:time.sleep(.3)
                    token=re.search(r'name="local-token" content="([^"]+)"',html).group(1);ui=UI(base,token)
                    ui.wait("document.readyState",'complete')
                    ui.wait("!!document.getElementById('video-status')",True)
                    ui.wait("document.getElementById('video-status').textContent",'已暂停',timeout=35)
                    if args.unlimited:ui.wait("document.getElementById('download-max-bytes').value",'unlimited')
                    print('PASS: quit and reopen native app restores paused real video task in the interface.',flush=True)
                    ui.eval("(()=>{document.getElementById('download-speed').value='0';return true;})()")
                ui.click('video-resume')
            ui.wait("document.getElementById('video-status').textContent",'下载完成',timeout=300)
            path=Path(ui.eval("document.getElementById('video-path').textContent").removeprefix('已保存到：'))
            assert path.is_file() and 1_000_000<path.stat().st_size<=500_000_000
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            ui.snapshot(folder/'02-complete-native-download.png')
            ui.click('video-copy');ui.wait("document.getElementById('toast').textContent",lambda value:'Finder' in value)
            with cocoa.pool():
                from urllib.parse import unquote,urlsplit
                payload=dict(contents(cocoa)[0]);copied=Path(unquote(urlsplit(payload['public.file-url'].decode().rstrip('\0')).path))
                assert copied.resolve()==path.resolve()
            received=ui.post('/api/qa',{'action':'file-paste','folder':str(folder/f'file-receiver-{time.time_ns()}')},timeout=95)
            assert hashlib.sha256(Path(received['paths'][0]).read_bytes()).hexdigest()==digest
            print('PASS: real native pasteboard file receiver copies the full file, with identical SHA-256.',flush=True)
            ui.export('video-save',folder/'完整视频另存.mp4')
            assert hashlib.sha256((folder/'完整视频另存.mp4').read_bytes()).hexdigest()==digest
            print(json.dumps({'PASS':'complete video through UI; pause/resume, native file copy and actual export button','bytes':path.stat().st_size,'sha256':digest}),flush=True)
        ui.click('native-quit')
        if process:
            process.wait(timeout=15);assert process.returncode==0,process.returncode
        print('PASS: Quit from the real interface stops the app cleanly.',flush=True)
    finally:
        if process and process.poll() is None:
            try:ui.post('/api/native/quit',{})
            except (OSError,RuntimeError,UnboundLocalError):pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        if log:log.close()
        with cocoa.pool():restore(cocoa,backup)


if __name__=='__main__':main()
