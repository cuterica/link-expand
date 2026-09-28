"""Verify the Windows EXE independently of the source tree and Python PATH."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
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
        assert b'file:///' in html and preview['url'].encode() in html
        plain = read(13).decode('utf-16-le').split('\0', 1)[0]
        assert plain == preview['text']
    finally:
        user.CloseClipboard()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    environment = dict(os.environ)
    for name in ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV']:
        environment.pop(name, None)
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
                    assert json.load(response) == {'app': 'link-expand', 'version': '0.1.1'}
                assert 'v0.1.1' in html
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
                if os.name == 'nt':
                    request = urllib.request.Request(base + '/api/copy-rich',
                        data=json.dumps({'id': preview['id']}).encode(),
                        headers={'Content-Type': 'application/json', 'X-Local-Token': token})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        assert json.load(response)['ok']
                    verify_native_clipboard(preview)
                    print('PASS: native mixed text/image clipboard, readable PNG file, HTML links, Unicode text', flush=True)
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
