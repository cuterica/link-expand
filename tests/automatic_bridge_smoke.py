"""Real MV3 extension: a software URL uses an existing browser session automatically."""
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from playwright.sync_api import expect, sync_playwright
from linkexpand.server import App, Server

ROOT = Path(__file__).resolve().parents[1]


def main():
    app = App(); server = Server(('127.0.0.1', 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    target = 'https://signed-in.example/video'
    image = BytesIO(); Image.new('RGB', (960, 480), '#24764e').save(image, 'PNG')
    errors = []; requests = []
    try:
        with tempfile.TemporaryDirectory(prefix='linkexpand-auto-qa-') as directory, sync_playwright() as runtime:
            folder = Path(directory)
            extension = folder / 'extension'; shutil.copytree(ROOT / 'browser-extension', extension)
            # Installation's website permission grant is pre-granted only in this isolated test copy.
            manifest = json.loads((extension / 'manifest.json').read_text())
            manifest['host_permissions'] += ['http://*/*', 'https://*/*']
            manifest['permissions'].append('debugger')
            (extension / 'manifest.json').write_text(json.dumps(manifest))
            executable = os.environ.get('LINK_EXPAND_EXTENSION_TEST_BROWSER')
            context = runtime.chromium.launch_persistent_context(str(folder / 'profile'), headless=True,
                **({'executable_path': executable} if executable else {'channel': 'chromium'}),
                args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'])
            try:
                worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
                extension_id = worker.url.split('/')[2]
                context.add_cookies([{'name': 'signed_in', 'value': 'fixture-session', 'domain': 'signed-in.example', 'path': '/', 'secure': True}])
                def fixture(route):
                    url = route.request.url
                    requests.append(url)
                    if url == target:
                        assert 'signed_in=fixture-session' in route.request.headers.get('cookie', ''), 'Existing session was not used'
                        route.fulfill(content_type='text/html', body='''<html><head><meta charset="utf-8"><title>会话中的视频标题</title>
                          <meta property="og:description" content="自动流程读取的网页摘要">
                          <meta property="og:image" content="https://signed-in.example/cover.png"></head><body>
                          <h1>已登录</h1><img src="https://signed-in.example/cover.png"><video src="https://signed-in.example/full-video.mp4"></video>
                          <audio src="https://signed-in.example/full-audio.m4a"></audio></body></html>''')
                    elif url.endswith('/article'): route.fulfill(content_type='text/html', body='<meta charset="utf-8"><title>无图网页</title><main style="background:lightgreen;padding:50px"><h1>这是截图内容</h1><p>无封面时自动截取这个已登录浏览器的页面。</p></main>')
                    elif url.endswith('/cover.png'): route.fulfill(content_type='image/png', body=image.getvalue())
                    elif url.endswith('.mp4'): route.fulfill(content_type='video/mp4', body=b'video-fixture')
                    elif url.endswith('.m4a'): route.fulfill(content_type='audio/mp4', body=b'audio-fixture')
                    else: route.fulfill(status=404, body='Not found')
                context.route('https://signed-in.example/**', fixture)
                # Make pairing through the real extension message handler, once, before software input.
                popup = context.new_page(); popup.goto(f'chrome-extension://{extension_id}/popup.html')
                result = popup.evaluate('''async config => chrome.runtime.sendMessage({action:'bridge-config',enabled:true,...config})''',
                    {'base': base, 'token': app.bridge_token, 'screenshots': True})
                assert result.get('ok'), result
                user_tab = context.new_page(); user_tab.goto('data:text/html,<title>User tab preserved</title>')
                ui = context.new_page(); ui.on('pageerror', lambda error: errors.append(str(error))); ui.goto(base)
                expect(ui.locator('#browser-status')).to_contain_text('已连接', timeout=10000)
                with patch('linkexpand.server.get_preview', side_effect=AssertionError('Automatic flow must not fetch the blocked page')):
                    ui.locator('#url-input').fill(target)
                    expect(ui.locator('#card-title')).to_have_text('会话中的视频标题', timeout=45000)
                    expect(ui.locator('#card-description')).to_have_text('自动流程读取的网页摘要')
                    expect(ui.locator('#video-choice option').first).to_contain_text('PAIR')
                    expect(ui.locator('#copy-text')).to_be_enabled()
                    expect(ui.locator('#cover-image')).to_be_visible()
                    expect(ui.locator('#card-url')).to_have_attribute('href', target)
                ui.wait_for_timeout(1200)
                assert not [p for p in context.pages if p.url == target], 'Owned background tab was left open'
                assert user_tab.title() == 'User tab preserved'
                # An already working Bilibili page is reused despite tracking parameters.
                navigations=[]
                def bili_fixture(route):
                    navigations.append(route.request.url)
                    route.fulfill(content_type='text/html; charset=utf-8',body='''<meta charset="utf-8"><title>已正常打开的 B 站页面</title>
                      <meta property="og:description" content="直接复用当前页面"><video id="untouched"></video>
                      <script>window.__playinfo__={data:{dash:{video:[{baseUrl:'https://cdn.example.com/complete-video.m4s'}],audio:[{baseUrl:'https://cdn.example.com/complete-audio.m4s'}]}}};</script>''')
                context.route('https://www.bilibili.com/video/**',bili_fixture)
                working=context.new_page();working.goto('https://www.bilibili.com/video/BV1cSec6tEux/?spm_id_from=old')
                ui.locator('#url-input').fill('https://www.bilibili.com/video/BV1cSec6tEux/?spm_id_from=new')
                expect(ui.locator('#card-title')).to_have_text('已正常打开的 B 站页面',timeout=15000)
                expect(ui.locator('#video-choice option').first).to_contain_text('PAIR')
                assert len(navigations)==1,'The existing working page was reloaded or duplicated'
                assert not working.is_closed(),'A personal tab was closed'
                assert working.locator('#untouched').evaluate('video=>video.paused && !video.muted'),'A personal video was played or muted'
                working.close()
                ui.locator('#url-input').fill('https://signed-in.example/article')
                expect(ui.locator('#card-title')).to_have_text('无图网页', timeout=45000)
                expect(ui.locator('#preview-badge')).to_have_text('浏览器网页截图')
                expect(ui.locator('#cover-image')).to_be_visible()
                ui.wait_for_timeout(1200)
                assert not [p for p in context.pages if p.url.endswith('/article')], 'Screenshot tab was left open'
                # Cancellation also cleans up only the owned tab.
                ui.locator('#url-input').fill('https://signed-in.example/slow')
                ui.wait_for_timeout(1400)
                ui.locator('#url-input').fill('')
                ui.wait_for_timeout(2500)
                assert not [p for p in context.pages if p.url.endswith('/slow')], 'Cancelled tab was left open'
                assert user_tab.title() == 'User tab preserved'
                assert not errors, errors
                ui.screenshot(path=str(ROOT / 'artifacts' / 'automatic-browser-preview.png'), full_page=True)
            finally: context.close()
    finally: app.close(); server.shutdown(); server.server_close(); thread.join()
    print('PASS: real MV3 extension; software input uses signed-in profile; title/summary/image + video/audio pair; screenshot fallback, cancellation and owned-tab cleanup.')


if __name__ == '__main__': main()
