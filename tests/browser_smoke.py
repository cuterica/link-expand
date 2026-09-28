"""Optional end-to-end checks; requires a working Chrome/Chromium installation."""
import asyncio
from io import BytesIO
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

from linkexpand.capture import browser_executable, capture
from linkexpand.cards import thumbnail
from linkexpand.metadata import Preview, Resource
from linkexpand.server import App, Server

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)
image = Image.new("RGB", (960, 480), "#214f40")
draw = ImageDraw.Draw(image)
draw.rectangle((480, 0, 960, 480), fill="#b8cf99")
draw.ellipse((400, 40, 800, 440), fill="#5d8f70")
stream = BytesIO()
image.save(stream, "PNG")
IMAGE = stream.getvalue()


def fake_preview(url):
    if "slow" in url:
        time.sleep(1.8)
    return Preview(url, "测试标题 " + url.rsplit("/", 1)[-1],
                   "这是自动展开的网页摘要，用于验证界面、图片导出和复制操作。",
                   "fixture.example", "测试网站")


def fake_visual(preview):
    preview.image = IMAGE
    preview.visual_source = "网页图片"


def ui_check():
    app = App()
    server = Server(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    errors = []
    try:
        with patch("linkexpand.server.get_preview", side_effect=fake_preview), \
                patch("linkexpand.server.attach_visual", side_effect=fake_visual), sync_playwright() as runtime:
            executable = browser_executable()
            browser = runtime.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            context = browser.new_context(viewport={"width": 1440, "height": 1000},
                                          permissions=["clipboard-read", "clipboard-write"], accept_downloads=True)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            expect(page.locator('#app-version')).to_have_text('v0.1.1')
            assert page.locator("#watch-toggle").count() == 0
            assert page.locator("#paste-wechat").count() == 0
            page.screenshot(path=str(ARTIFACTS / "interface.png"), full_page=True)
            page.locator("#url-input").fill("https://fixture.example/first")
            expect(page.locator("#card-title")).to_contain_text("first")
            page.locator("#copy-image").wait_for(state="visible")
            expect(page.locator("#copy-image")).to_be_enabled()
            assert page.locator("#preview-badge").inner_text() == "网页图片"
            page.locator("#copy-text").click()
            expect(page.locator("#toast")).to_contain_text("图文与可点击链接已复制")
            assert "https://fixture.example/first" in page.evaluate("navigator.clipboard.readText()")
            types = page.evaluate("navigator.clipboard.read().then(items => items[0].types)")
            assert 'text/html' in types and 'text/plain' in types
            editor = context.new_page()
            editor.set_content('<div id="paste-target" contenteditable="true" style="width:520px;min-height:300px"></div>')
            editor.locator('#paste-target').click()
            editor.keyboard.press('Control+V')
            expect(editor.locator('#paste-target img')).to_have_count(1)
            expect(editor.locator('#paste-target a').first).to_have_attribute('href', 'https://fixture.example/first')
            assert editor.locator('#paste-target img').evaluate('img => img.complete && img.naturalWidth > 0')
            editor.screenshot(path=str(ARTIFACTS / 'rich-paste.png'), full_page=True)
            editor.close()
            page.locator("#copy-image").click()
            expect(page.locator("#toast")).to_contain_text("图片已复制")
            assert "image/png" in page.evaluate("navigator.clipboard.read().then(items => items[0].types)")
            with page.expect_download() as download:
                page.locator("#download-cover").click()
            download.value.save_as(str(ARTIFACTS / "downloaded-image.png"))
            with Image.open(ARTIFACTS / "downloaded-image.png") as downloaded:
                assert downloaded.size == (960, 480)
            page.locator("#url-input").fill("https://fixture.example/slow")
            page.wait_for_timeout(1050)
            page.locator("#url-input").fill("https://fixture.example/new")
            expect(page.locator("#card-title")).to_contain_text("new")
            page.wait_for_timeout(1100)
            assert page.locator("#url-input").input_value() == "https://fixture.example/new"
            page.locator("#edit-toggle").click()
            page.locator("#title-input").fill("手动修改 <script>alert(1)</script>")
            page.locator("#save-edit").click()
            expect(page.locator("#card-title")).to_contain_text("手动修改")
            assert page.locator("#card-title script").count() == 0
            page.locator("#edit-toggle").click()
            page.screenshot(path=str(ARTIFACTS / "generated-preview.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(ARTIFACTS / "mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            legacy = context.new_page()
            legacy.route('**/api/capabilities', lambda route: route.fulfill(
                status=404, content_type='application/json', body='{"error":"old server"}'))
            legacy.goto(f"http://127.0.0.1:{server.server_port}")
            expect(legacy.locator('#feedback')).to_contain_text('旧后台')
            legacy.locator('#url-input').fill('https://fixture.example/legacy')
            expect(legacy.locator('#card-title')).to_contain_text('legacy')
            expect(legacy.locator('#copy-image')).to_be_enabled()
            expect(legacy.locator('#copy-text')).to_be_disabled()
            legacy.close()
            context.close()
            browser.close()
        assert not errors, errors
        print("PASS: expansion, editing, image and rich clipboard, download, layout, visible version, old backend cannot silently copy")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


VIDEO_HTML = '''<!doctype html><html><head><title>Video fixture</title></head>
<body style="margin:0;background:#eef3eb"><main style="padding:32px"><h1>Video preview</h1>
<p>This is an example video page with enough article text to provide a useful summary for the preview.</p>
<video id="video" width="640" height="360" muted></video><canvas id="canvas" width="640" height="360" hidden></canvas>
<script>
const canvas = document.getElementById('canvas'); const ctx = canvas.getContext('2d');
const stream = canvas.captureStream(20); const chunks = [];
const recorder = new MediaRecorder(stream, {mimeType:'video/webm'});
recorder.ondataavailable = event => chunks.push(event.data);
recorder.onstop = () => { const video = document.getElementById('video');
video.src = URL.createObjectURL(new Blob(chunks, {type:'video/webm'})); video.load(); };
let frame=0; const paint = () => { frame++; ctx.fillStyle='#275c72';ctx.fillRect(0,0,640,360);
ctx.fillStyle='#e4c271';ctx.fillRect(160,50,370,260);ctx.fillStyle='#20394a';ctx.font='38px sans-serif';
ctx.fillText('VIDEO FRAME '+frame,180,180); };
paint(); recorder.start(); const timer = setInterval(paint,50);
setTimeout(() => { clearInterval(timer); recorder.stop();stream.getTracks().forEach(t=>t.stop()); },700);
</script></main></body></html>'''


def capture_checks():
    fixtures = [
        ("https://fixture.example/page", "<html><head><title>Plain article</title></head><body><main style='padding:40px;width:800px'><h1>A useful article</h1><p>This page contains no images. Its content should be captured in a browser screenshot with its title visible.</p></main></body></html>", "网页截图"),
        ("https://fixture.example/video", VIDEO_HTML, "视频截图"),
    ]
    for url, html, expected in fixtures:
        def fetch(request_url, *_args, **_kwargs):
            if request_url == url:
                return Resource(url, html.encode(), "text/html; charset=utf-8")
            raise ValueError("Unexpected fixture request")
        with patch("linkexpand.capture.fetch_resource", side_effect=fetch):
            result = asyncio.run(capture(url))
        assert result["source"] == expected, result["source"]
        assert result["png"].startswith(b"\x89PNG")
        (ARTIFACTS / ("video-capture.png" if "video" in url else "article-capture.png")).write_bytes(result["png"])
        print("PASS:", expected)


if __name__ == "__main__":
    ui_check()
    capture_checks()
