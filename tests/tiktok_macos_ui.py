"""Actual native Mac app input -> TikTok cover -> full highest-resolution media."""
import argparse,hashlib,json,os,re,socket,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from macos_full_chain import UI
URL='https://www.tiktok.com/@lianaparmezana/video/7669725994743336199'

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--app',type=Path,required=True);args=parser.parse_args()
 root=Path.home()/'Library/Application Support/LinkExpand/tiktok-qa';root.mkdir(exist_ok=True)
 with tempfile.TemporaryDirectory(prefix='linkexpand-tiktok-mac-') as folder:
  with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
  base=f'http://127.0.0.1:{port}';log=(root/'app.log').open('w')
  process=subprocess.Popen([str(args.app/'Contents/MacOS/LinkExpand'),'--ui-test','--test-downloads-root',folder,'--port',str(port)],stdout=log,stderr=log,start_new_session=True)
  try:
   for _ in range(100):
    try:
     with urllib.request.urlopen(base,timeout=1) as response:html=response.read().decode()
     break
    except OSError:time.sleep(.3)
   token=re.search(r'name="local-token" content="([^"]+)"',html).group(1);ui=UI(base,token)
   ui.wait('document.readyState','complete')
   ui.wait("!!document.getElementById('url-input')",True)
   ui.eval("(()=>{const node=document.getElementById('url-input');node.value="+json.dumps(URL)+";node.dispatchEvent(new Event('input'));return true;})()")
   ui.wait("document.getElementById('card-title').textContent",lambda value:'Liana' in value,timeout=110)
   ui.wait("document.getElementById('cover-image').complete&&document.getElementById('cover-image').naturalWidth>0",True)
   ui.wait("!document.getElementById('video-download').disabled",True)
   ui.snapshot(root/'preview.png')
   print('PASS: actual Mac app TikTok input -> real caption, visible image and downloadable video.',flush=True)
   ui.click('video-download');ui.wait("document.getElementById('video-status').textContent",'下载完成',timeout=100)
   path=Path(ui.eval('videoJob.path'));quality=ui.eval('videoJob.quality')
   assert quality=='1080×1440' and path.stat().st_size==2628739
   checksum=hashlib.sha256(path.read_bytes()).hexdigest();assert checksum=='ba6cebf82b9c126c21630db3aad2d423cf497fac5ac9c19cba73464760fbe266'
   ui.snapshot(root/'complete.png')
   print(json.dumps({'PASS':'actual Mac packaged TikTok full video','bytes':path.stat().st_size,'quality':quality,'sha256':checksum}),flush=True)
   ui.click('native-quit');process.wait(timeout=20);assert process.returncode==0
   print('PASS: native app closes cleanly.',flush=True)
  finally:
   if process.poll() is None:process.terminate();process.wait(timeout=20)
   log.close()

if __name__=='__main__':main()
