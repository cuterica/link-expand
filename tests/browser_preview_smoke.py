"""Import metadata from an authenticated page while backend page fetch stays 412."""
from io import BytesIO
from pathlib import Path
import sys
import threading
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from playwright.sync_api import sync_playwright,expect
from linkexpand.capture import browser_executable
from linkexpand.metadata import Resource,PreviewError
from linkexpand.server import App,Server

def main():
    url='https://www.bilibili.com/video/BV1cSec6tEux/'
    picture='https://i0.hdslb.com/bfs/archive/fixture.jpg'
    out=BytesIO();Image.new('RGB',(960,480),'#227b54').save(out,'PNG')
    def fetch(value,*args,**kwargs):
        assert value==picture,'Blocked page must not be fetched to import preview'
        return Resource(picture,out.getvalue(),'image/png')
    app=App();server=Server(('127.0.0.1',0),app)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with patch('linkexpand.server.get_preview',side_effect=PreviewError('B 站安全风控拦截（HTTP 412）')), \
             patch('linkexpand.metadata.fetch_resource',side_effect=fetch),sync_playwright() as runtime:
            browser=runtime.chromium.launch(executable_path=browser_executable(),headless=True)
            try:
                context=browser.new_context();page=context.new_page();errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                base=f'http://127.0.0.1:{server.server_port}'
                page.goto(base);page.locator('#url-input').fill(url)
                expect(page.locator('#feedback')).to_contain_text('HTTP 412')
                response=context.request.post(base+'/api/capture/import',headers={'X-Local-Token':app.token,'Origin':'chrome-extension://'+'a'*32},
                    data={'source':url,'candidates':[], 'preview':{'url':url,'title':'Edge 已登录的视频标题','description':'已打开页面的摘要','image_url':picture}})
                assert response.ok,response.text()
                page.goto(base+'/#browser-preview')
                expect(page.locator('#card-title')).to_have_text('Edge 已登录的视频标题')
                expect(page.locator('#preview-badge')).to_have_text('浏览器封面')
                expect(page.locator('#copy-text')).to_be_enabled()
                expect(page.locator('#card-url')).to_have_attribute('href',url)
                assert page.locator('#cover-image').evaluate('image=>image.complete && image.naturalWidth>0')
                page.wait_for_timeout(1100)
                assert 'HTTP 412' not in page.locator('#feedback').text_content()
                assert not errors,errors
                folder=Path(__file__).resolve().parents[1]/'artifacts';folder.mkdir(exist_ok=True)
                page.screenshot(path=str(folder/'browser-import-preview.png'),full_page=True)
                context.close()
            finally:browser.close()
    finally:app.close();server.shutdown();server.server_close();thread.join()
    print('PASS: backend 412 remains; imported browser title, description, cover, real URL and copy actions work without refetch.')

if __name__=='__main__':main()
