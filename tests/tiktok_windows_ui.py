"""Real packaged Windows interface expands the supplied TikTok and downloads its full video."""
import argparse,hashlib,json,os,re,socket,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright,expect
from windows_full_chain import wait_backend,stop_console

URL='https://www.tiktok.com/@lianaparmezana/video/7669725994743336199'

def main():
 if os.name!='nt':raise RuntimeError('Run this test on Windows')
 parser=argparse.ArgumentParser();parser.add_argument('executable',type=Path);args=parser.parse_args()
 root=Path(__file__).resolve().parents[1]/'artifacts/tiktok-ui';root.mkdir(exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='linkexpand-tiktok-ui-') as folder:
  with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
  base=f'http://127.0.0.1:{port}';log=(root/'app.log').open('w')
  process=subprocess.Popen([str(args.executable.resolve()),'--no-browser','--ui-test','--test-downloads-root',folder,'--port',str(port)],creationflags=subprocess.CREATE_NEW_CONSOLE,stdout=log,stderr=log)
  try:
   assert wait_backend(base,process)['version']=='0.4.1'
   with sync_playwright() as runtime:
    browser=runtime.chromium.launch(executable_path=str(Path(os.environ['LOCALAPPDATA'])/'ms-playwright/chromium-1228/chrome-win64/chrome.exe'),headless=False,handle_sigint=False)
    try:
     context=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1080});page=context.new_page();page.goto(base)
     page.locator('#update-button').click()
     expect(page.locator('#toast')).to_contain_text('当前已是最新版',timeout=30000)
     print('PASS: real update button checks the public GitHub release without manual browser download.',flush=True)
     page.locator('#download-max-bytes').select_option('unlimited')
     page.locator('#url-input').fill(URL)
     expect(page.locator('#card-title')).to_contain_text('Liana',timeout=110000)
     expect(page.locator('#cover-image')).to_be_visible()
     assert page.locator('#cover-image').evaluate('img=>img.complete&&img.naturalWidth>0')
     expect(page.locator('#video-download')).to_be_enabled()
     page.screenshot(path=str(root/'preview.png'),full_page=True)
     print('PASS: actual packaged input -> TikTok caption, real image and grouped video choice.',flush=True)
     with page.expect_download() as image:page.locator('#download-cover').click()
     image.value.save_as(str(Path(folder)/'cover.png'))
     assert (Path(folder)/'cover.png').read_bytes().startswith(b'\x89PNG')
     page.locator('#video-download').click();expect(page.locator('#video-status')).to_have_text('下载完成',timeout=90000)
     assert page.evaluate('videoJob.quality')=='1080×1440'
     path=Path(page.evaluate('videoJob.path'));assert path.stat().st_size==2628739
     checksum=hashlib.sha256(path.read_bytes()).hexdigest()
     assert checksum=='ba6cebf82b9c126c21630db3aad2d423cf497fac5ac9c19cba73464760fbe266'
     page.screenshot(path=str(root/'complete.png'),full_page=True)
     print(json.dumps({'PASS':'actual packaged TikTok full download','bytes':path.stat().st_size,'quality':page.evaluate('videoJob.quality'),'sha256':checksum}),flush=True)
     context.close()
    finally:browser.close()
   stop_console(process);print('PASS: packaged application exits cleanly.',flush=True)
  finally:
   if process.poll() is None:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   log.close()

if __name__=='__main__':main()
