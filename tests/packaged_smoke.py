"""Verify the Windows EXE independently of the source tree and Python PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET


def verify_native_clipboard(preview):
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32')
    kernel = ctypes.WinDLL('kernel32')
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user.RegisterClipboardFormatW.restype = wintypes.UINT
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = wintypes.LPVOID
    kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.restype = ctypes.c_size_t
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    for _ in range(15):
        if user.OpenClipboard(None):
            break
        time.sleep(.03)
    else:
        raise RuntimeError('Could not inspect the clipboard written by the EXE.')
    def read(format_id):
        handle = user.GetClipboardData(format_id)
        assert handle
        pointer = kernel.GlobalLock(handle)
        assert pointer
        try:
            return ctypes.string_at(pointer, kernel.GlobalSize(handle))
        finally:
            kernel.GlobalUnlock(handle)
    try:
        chat = read(user.RegisterClipboardFormatW('QQ_Unicode_RichEdit_Format')).rstrip(b'\0')
        tree = ET.fromstring(chat)
        picture = tree.find("EditElement[@type='1']")
        assert picture is not None and Path(picture.attrib['filepath']).is_file()
        assert Path(picture.attrib['filepath']).read_bytes().startswith(b'\x89PNG')
        text = tree.find("EditElement[@type='0']").text
        assert preview['url'] in text and preview['title'] in text
        html = read(user.RegisterClipboardFormatW('HTML Format'))
        assert b'data:image/png;base64,' in html and preview['url'].encode() in html
        plain = read(13).decode('utf-16-le').split('\0', 1)[0]
        assert plain == preview['text']
    finally:
        user.CloseClipboard()


def verify_video_file_clipboard(expected_path):
    if sys.platform=='darwin':
        from macos_clipboard_smoke import Cocoa,contents
        from urllib.parse import urlsplit,unquote
        cocoa=Cocoa()
        with cocoa.pool():
            payloads=dict(contents(cocoa)[0])
            path=unquote(urlsplit(payloads['public.file-url'].decode().rstrip('\0')).path)
            assert Path(path).resolve()==Path(expected_path).resolve()
        return
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32')
    kernel = ctypes.WinDLL('kernel32')
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = wintypes.LPVOID
    kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.restype = ctypes.c_size_t
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    assert user.OpenClipboard(None)
    try:
        handle = user.GetClipboardData(15)
        assert handle
        pointer = kernel.GlobalLock(handle)
        try:
            data = ctypes.string_at(pointer, kernel.GlobalSize(handle))
        finally:
            kernel.GlobalUnlock(handle)
        offset = int.from_bytes(data[:4], 'little')
        path = data[offset:].decode('utf-16-le').split('\0', 1)[0]
        assert Path(path).resolve() == Path(expected_path).resolve()
    finally:
        user.CloseClipboard()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--expected-version',default='0.3.61')
    args = parser.parse_args()
    executable = args.executable.resolve()
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    environment = dict(os.environ)
    for name in ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV']:
        environment.pop(name, None)
    if sys.platform=='darwin':environment['PATH']='/usr/bin:/bin:/usr/sbin:/sbin'
    if os.name == 'nt':
        windows = environment.get('SystemRoot', 'C:/Windows')
        environment['PATH'] = os.pathsep.join([windows, str(Path(windows) / 'System32')])
    with tempfile.TemporaryDirectory(prefix='link-expand-package-test-') as working:
        with open(Path(working) / 'startup.log', 'w+b') as output:
            process = subprocess.Popen([str(executable), '--no-browser', '--port', str(port)],
                                       cwd=working, env=environment, stdout=output, stderr=output,
                                       **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
            base = f'http://127.0.0.1:{port}'
            try:
                deadline = time.monotonic() + 45
                while True:
                    if process.poll() is not None:
                        output.seek(0)
                        raise RuntimeError(output.read().decode('utf-8', errors='replace'))
                    try:
                        with urllib.request.urlopen(base, timeout=2) as response:
                            html = response.read().decode('utf-8')
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise RuntimeError('Packaged application did not start.')
                        time.sleep(.3)
                assert 'Link Expand' in html
                with urllib.request.urlopen(base + '/api/health', timeout=5) as response:
                    assert json.load(response) == {'app': 'link-expand', 'version': args.expected_version}
                assert 'v'+args.expected_version in html
                token = re.search(r'name="local-token" content="([^"]+)"', html).group(1)
                for url, expected in [
                    ('https://github.com', '网页封面'),
                    ('https://example.com', '网页截图'),
                    ('https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4', '视频截图'),
                ]:
                    request = urllib.request.Request(base + '/api/preview',
                        data=json.dumps({'url': url}).encode(),
                        headers={'Content-Type': 'application/json', 'X-Local-Token': token})
                    with urllib.request.urlopen(request, timeout=90) as response:
                        preview = json.load(response)
                    assert preview['visual_source'] == expected, preview
                    assert 'href=' in preview['html'] and 'data:image/png;base64,' in preview['html']
                    for field in ['visual', 'image']:
                        with urllib.request.urlopen(base + preview[field], timeout=10) as response:
                            assert response.read(8) == b'\x89PNG\r\n\x1a\n'
                    print(json.dumps({'PASS': url, 'source': preview['visual_source']}), flush=True)
                if os.name == 'nt' or sys.platform=='darwin':
                    request = urllib.request.Request(base + '/api/copy-rich',
                        data=json.dumps({'id': preview['id']}).encode(),
                        headers={'Content-Type': 'application/json', 'X-Local-Token': token})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        assert json.load(response)['ok']
                    if os.name=='nt':verify_native_clipboard(preview)
                    else:
                        from macos_clipboard_smoke import Cocoa,contents
                        cocoa=Cocoa()
                        with cocoa.pool():
                            payloads=dict(contents(cocoa)[0])
                            assert payloads['public.utf8-plain-text'].decode()==preview['text']
                            assert preview['url'] in payloads['public.html'].decode()
                            assert payloads['public.png'].startswith(b'\x89PNG')
                            assert payloads['com.apple.flat-rtfd']
                    print('PASS: native mixed text/image clipboard, embedded image, HTML links, Unicode text', flush=True)
                def post(path, data):
                    request = urllib.request.Request(base + path, data=json.dumps(data).encode(),
                        headers={'Content-Type':'application/json','X-Local-Token':token})
                    with urllib.request.urlopen(request, timeout=40) as response:
                        return json.load(response)
                browser_url='https://www.bilibili.com/video/BV1cSec6tEux/'
                captured=post('/api/capture/import',{'source':browser_url,'candidates':[],
                    'preview':{'url':browser_url,'title':'已登录 Edge 页面导入测试','description':'元信息由浏览器提供','image_url':''}})
                assert captured['preview']['title']=='已登录 Edge 页面导入测试'
                request=urllib.request.Request(base+'/api/capture/preview',headers={'X-Local-Token':token})
                with urllib.request.urlopen(request,timeout=10) as response:
                    latest=json.load(response);assert latest['url']==browser_url
                print('PASS: packaged browser preview import without refetching the blocked Bilibili page',flush=True)
                request=urllib.request.Request(base+'/api/capabilities',headers={'X-Local-Token':token})
                with urllib.request.urlopen(request,timeout=10) as response:pairing=json.load(response)['browser_pairing_key']
                def paired_post(path,data):
                    request=urllib.request.Request(base+path,data=json.dumps(data).encode(),headers={
                        'Content-Type':'application/json','X-Local-Token':pairing,'Origin':'chrome-extension://'+'a'*32})
                    with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)
                client='packaged-bridge-fixture'
                paired_post('/api/browser/poll',{'client_id':client,'browser':'Edge' if os.name=='nt' else 'Chrome'})
                bridge_job=post('/api/browser/request',{'url':browser_url})
                claim=paired_post('/api/browser/poll',{'client_id':client,'browser':'Edge' if os.name=='nt' else 'Chrome'})
                assert claim['job']['id']==bridge_job['id']
                completed=paired_post('/api/browser/result',{'id':bridge_job['id'],'client_id':client,'source':browser_url,
                    'preview':{'url':browser_url,'title':'Automatic browser preview','description':'Existing browser session'},
                    'candidates':[{'url':'https://cdn.example.com/full.mp4','kind':'video','headers':{'Cookie':'fixture-private-cookie'}},
                                  {'url':'https://cdn.example.com/full.m4a','kind':'audio'}]})
                assert completed['accepted']
                request=urllib.request.Request(base+'/api/browser/jobs/'+bridge_job['id'],headers={'X-Local-Token':token})
                with urllib.request.urlopen(request,timeout=10) as response:bridge_result=response.read()
                assert b'fixture-private-cookie' not in bridge_result
                result=json.loads(bridge_result)
                assert result['status']=='complete' and result['result']['preview']['title']=='Automatic browser preview'
                assert result['result']['catalog']['resources'][0]['kind']=='pair'
                print('PASS: packaged automatic browser jobs, persistent pairing key, video/audio pair, private headers remain local',flush=True)
                bili=post('/api/preview',{'url':browser_url,'bili_parser':True})
                assert 'iPhone 18' in bili['title'],bili.get('title')
                assert bili['summary_source']=='B 站公开链接解析'
                assert bili['cover'] and bili['catalog']['resources']
                with urllib.request.urlopen(base+bili['cover'],timeout=10) as response:assert response.read(8)==b'\x89PNG\r\n\x1a\n'
                print('PASS: actual Bilibili URL, public parser fallback, title/summary/cover and complete-video catalog',flush=True)
                x_preview = post('/api/preview', {'url':'https://x.com/TwitterDev/status/1460323737035677698'})
                assert x_preview['videos']
                job = post('/api/video/start', {'id':x_preview['id'],'video_index':x_preview['selected_video']})
                deadline = time.monotonic()+90
                while job['status'] not in {'complete','error','cancelled'} and time.monotonic()<deadline:
                    time.sleep(.25)
                    request = urllib.request.Request(base+'/api/video/jobs/'+job['id'],headers={'X-Local-Token':token})
                    with urllib.request.urlopen(request,timeout=10) as response:job=json.load(response)
                assert job['status']=='complete',job
                assert 0 < job['downloaded'] <= 500_000_000
                with urllib.request.urlopen(base+'/downloads/'+job['id']+'.mp4',timeout=20) as response:
                    data=response.read()
                assert data[4:8]==b'ftyp'
                assert hashlib.sha256(data).hexdigest()==job['sha256']
                if os.name=='nt' or sys.platform=='darwin':
                    post('/api/video/copy',{'job_id':job['id']})
                    verify_video_file_clipboard(job['path'])
                print(json.dumps({'PASS':'X whole video and file clipboard','size':len(data),
                    'quality':job['quality'],'file':job['path']}),flush=True)
                catalog=post('/api/download/resolve',{'url':'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4','scan':True})
                generic=post('/api/download/start',{'catalog_id':catalog['id'],'index':catalog['resources'][0]['index'],'connections':8})
                deadline=time.monotonic()+60
                while generic['status'] not in {'complete','error','cancelled'} and time.monotonic()<deadline:
                    time.sleep(.25)
                    request=urllib.request.Request(base+'/api/video/jobs/'+generic['id'],headers={'X-Local-Token':token})
                    with urllib.request.urlopen(request,timeout=10) as response:generic=json.load(response)
                assert generic['status']=='complete',generic
                assert generic['filename'].endswith('.mp4')
                with urllib.request.urlopen(base+'/downloads/'+generic['id']+'/file',timeout=20) as response:
                    generic_data=response.read()
                assert hashlib.sha256(generic_data).hexdigest()==generic['sha256']
                print(json.dumps({'PASS':'generic direct media','filename':generic['filename'],'size':len(generic_data)}),flush=True)
            finally:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    process.terminate()
                process.wait(timeout=20)
    print('PASS: standalone EXE, bundled interface, images, web capture, video frames; no Python on PATH')


if __name__ == '__main__':
    main()
