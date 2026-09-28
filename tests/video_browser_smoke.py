"""One managed headless browser: exercise download, pause, resume and file save."""
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from playwright.sync_api import expect,sync_playwright
from linkexpand import __version__
from linkexpand.capture import browser_executable
from linkexpand.metadata import Preview
from linkexpand.server import App,Server
from linkexpand.video_downloads import DownloadManager

DATA=b'\x00\x00\x00\x18ftypmp42'+bytes(range(256))*16384
ARCHIVE=b'PK\x03\x04'+bytes(range(256))*1024
class MediaHandler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        if self.path=='/files-page':
            body=b'<html><title>Download fixtures</title><video src="/test.mp4"></video><a href="/archive.zip">Archive</a></html>'
            self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
        data=ARCHIVE if self.path=='/archive.zip' else DATA
        match=re.fullmatch(r'bytes=(\d+)-(\d+)',self.headers.get('Range',''))
        start,end=(int(match[1]),min(int(match[2]),len(data)-1)) if match else (0,len(data)-1)
        self.send_response(206 if match else 200)
        self.send_header('Content-Type','application/zip' if self.path=='/archive.zip' else 'video/mp4')
        self.send_header('Content-Length',str(end-start+1))
        self.send_header('ETag','"browser-fixture"')
        if match:self.send_header('Content-Range',f'bytes {start}-{end}/{len(data)}')
        self.end_headers()
        try:
            for offset in range(start,end+1,8192):
                self.wfile.write(data[offset:min(end+1,offset+8192)])
                self.wfile.flush()
                time.sleep(.01)
        except OSError:pass

def run():
    media=ThreadingHTTPServer(('127.0.0.1',0),MediaHandler)
    media.daemon_threads=True
    mt=threading.Thread(target=media.serve_forever,daemon=True);mt.start()
    with tempfile.TemporaryDirectory() as folder:
        app=App();app._downloads=DownloadManager(Path(folder)/'videos')
        server=Server(('127.0.0.1',0),app)
        st=threading.Thread(target=server.serve_forever,daemon=True);st.start()
        video={'index':1,'post_id':'1460323737035677698','author':'demo','duration_ms':11093,
               'variants':[{'url':'http://video.twimg.com/test.mp4','quality':'1280×720'}]}
        def preview(url):
            p=Preview(url,'X 视频测试','测试完整下载与续传。','x.com','X / 推特')
            p.videos=[video];p.selected_video=1
            return p
        def visual(p):
            image=Image.new('RGB',(960,480),'#23563d')
            output=BytesIO();image.save(output,'PNG');p.image=output.getvalue();p.visual_source='视频封面'
        try:
            with patch('linkexpand.server.get_preview',side_effect=preview),patch('linkexpand.server.attach_visual',side_effect=visual), \
                 patch('linkexpand.video_downloads.public_addresses',return_value=['93.184.216.34']), \
                 patch('linkexpand.video_downloads.PinnedHTTPConnection',side_effect=lambda *args:http.client.HTTPConnection('127.0.0.1',media.server_port,timeout=3)), \
                 sync_playwright() as runtime:
                browser=runtime.chromium.launch(executable_path=browser_executable(),headless=True)
                try:
                    context=browser.new_context(viewport={'width':1440,'height':1100},accept_downloads=True)
                    page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    page.locator('#url-input').fill('https://x.com/demo/status/1460323737035677698')
                    expect(page.locator('#video-download')).to_be_enabled()
                    expect(page.locator('#video-choice')).to_contain_text('1280×720')
                    page.locator('#video-download').click()
                    expect(page.locator('#video-task')).to_be_visible()
                    page.locator('#video-pause').click()
                    expect(page.locator('#video-status')).to_have_text('已暂停',timeout=10000)
                    page.locator('#video-resume').click()
                    expect(page.locator('#video-status')).to_have_text('下载完成',timeout=15000)
                    expect(page.locator('#video-path')).to_contain_text('.mp4')
                    assert len(list((Path(folder)/'videos').glob('*.mp4')))==1
                    with page.expect_download() as event:page.locator('#video-save').click()
                    output=Path(__file__).resolve().parents[1]/'artifacts'/'video-ui-download.mp4'
                    event.value.save_as(str(output))
                    assert hashlib.sha256(output.read_bytes()).digest()==hashlib.sha256(DATA).digest()
                    page.locator('#download-url').fill(f'http://public.test:{media.server_port}/files-page')
                    page.locator('#download-resolve').click()
                    expect(page.locator('#video-choice')).to_contain_text('archive.zip')
                    page.locator('#video-choice').select_option(label='FILE · archive.zip')
                    page.locator('.download-advanced').first.locator('summary').click()
                    page.locator('#download-connections').select_option('8')
                    page.locator('#download-speed').fill('256')
                    page.locator('#video-download').click()
                    expect(page.locator('#video-path')).to_contain_text(re.compile(r'archive_[a-f0-9]+\.zip'),timeout=15000)
                    expect(page.locator('#video-status')).to_have_text('下载完成')
                    job=[item for item in app.downloads.jobs.values() if item.video.get('kind')=='file'][0]
                    assert job.workers==8 and job.speed_limit==256000
                    with page.expect_download() as event:page.locator('#video-save').click()
                    archive=output.parent/'generic-ui-archive.zip';event.value.save_as(str(archive))
                    assert archive.read_bytes()==ARCHIVE
                    page.locator('.download-advanced').last.locator('summary').click()
                    page.locator('#queue-refresh').click()
                    expect(page.locator('.queue-row')).to_have_count(2)
                    page.screenshot(path=str(output.parent/'video-ui.png'),full_page=True)
                    assert not errors,errors
                    context.close()
                finally:browser.close()
        finally:
            app.close();server.shutdown();server.server_close();st.join()
    media.shutdown();media.server_close();mt.join()
    print('PASS: X video pause/resume, HTML resource recognition, generic ZIP save, settings, queue and v'+__version__)

if __name__=='__main__':run()
