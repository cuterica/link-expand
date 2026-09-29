"""Real 512 MB transfer, restart, export and native file clipboard with controlled HTTP."""
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import urllib.request
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from linkexpand.server import App,Server
from linkexpand.video_downloads import DownloadManager
from packaged_smoke import verify_video_file_clipboard

SIZE=512_000_128
BLOCK=bytes(range(256))*256

class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        match=re.fullmatch(r'bytes=(\d+)-(\d+)',self.headers.get('Range',''))
        start,end=(int(match[1]),int(match[2])) if match else (0,SIZE-1)
        self.send_response(206 if match else 200)
        self.send_header('Content-Length',str(end-start+1));self.send_header('ETag','"large-fixture"')
        if match:self.send_header('Content-Range',f'bytes {start}-{end}/{SIZE}')
        self.end_headers()
        try:
            while start<=end:
                chunk=BLOCK[start%len(BLOCK):] if start%len(BLOCK) else BLOCK
                chunk=chunk[:end-start+1];self.wfile.write(chunk);start+=len(chunk)
        except (BrokenPipeError,ConnectionResetError):pass


def wait(job):
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        if job.status in {'complete','error','paused'} and not job.thread.is_alive():return job.snapshot()
        time.sleep(.01)
    raise AssertionError(job.snapshot())


def digest(stream):
    value=hashlib.sha256()
    while chunk:=stream.read(1024*1024):value.update(chunk)
    return value.hexdigest()


def run():
    clipboard=None
    if sys.platform=='win32':
        from windows_full_chain import ClipboardBackup
        clipboard=ClipboardBackup()
    elif sys.platform=='darwin':
        from macos_clipboard_smoke import Cocoa,contents,restore
        cocoa=Cocoa()
        with cocoa.pool():backup=contents(cocoa)
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Fixture);fixture.daemon_threads=True
    fixture_thread=threading.Thread(target=fixture.serve_forever,daemon=True);fixture_thread.start()
    with tempfile.TemporaryDirectory(prefix='linkexpand-large-file-') as folder:
        root=Path(folder);app=App();app._downloads=DownloadManager(root)
        server=Server(('127.0.0.1',0),app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def post(path,data):
            request=urllib.request.Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Local-Token':app.token})
            with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)
        try:
            with patch('linkexpand.video_downloads.public_addresses',return_value=['93.184.216.34']),patch('linkexpand.video_downloads.PinnedHTTPConnection',side_effect=lambda *args:http.client.HTTPConnection('127.0.0.1',fixture.server_port,timeout=8)):
                catalog=app.catalog({'source':'http://fixture.example/large.bin','title':'Large file','resources':[
                    {'index':1,'kind':'file','filename':'large.bin','post_id':'large-fixture','variants':[{'url':'http://fixture.example/large.bin','quality':'original'}]}]})
                state=post('/api/download/start',{'catalog_id':catalog['id'],'index':1,'max_bytes':500_000_000})
                job=app.downloads.get(state['id']);assert wait(job)['status']=='error' and '超过' in job.error,job.snapshot()
                assert not list(root.glob('*.bin'))
                print('PASS: 500 MB mode rejects a real 512,000,128-byte resource.',flush=True)
                state=post('/api/download/start',{'catalog_id':catalog['id'],'index':1,'max_bytes':None,'connections':8,'speed_limit':8_000_000})
                job=app.downloads.get(state['id'])
                deadline=time.monotonic()+10
                while job.snapshot()['downloaded']<65536 and time.monotonic()<deadline:time.sleep(.005)
                assert job.snapshot()['downloaded']>0;job.pause();assert wait(job)['status']=='paused'
                app.downloads.close();app._downloads=DownloadManager(root)
                job=app.downloads.get(state['id']);assert job.limit is None and job.status=='paused'
                job.speed_limit=0
                post('/api/video/resume',{'job_id':job.id,'max_bytes':None})
                state=wait(job);assert state['status']=='complete',state
                assert job.file.stat().st_size==SIZE and job.valid_file()
                with job.file.open('rb') as source:expected=digest(source)
                assert expected==state['sha256']
                print('PASS: unlimited download >500 MB survives manager restart, resumes and validates SHA-256.',flush=True)
                with urllib.request.urlopen(base+f'/downloads/{job.id}/file',timeout=30) as response:
                    assert response.headers['Content-Length']==str(SIZE)
                    assert digest(response)==expected
                print('PASS: export streams all 512,000,128 bytes with identical SHA-256.',flush=True)
                if sys.platform in {'win32','darwin'}:
                    post('/api/video/copy',{'job_id':job.id});verify_video_file_clipboard(job.file)
                    if clipboard:clipboard.changed_by_test()
                    print('PASS: native complete-file clipboard accepts a file larger than 500 MB.',flush=True)
                print(json.dumps({'platform':sys.platform,'bytes':SIZE,'sha256':expected,'max_bytes':None}),flush=True)
        finally:
            app.downloads.close();server.shutdown();server.server_close();thread.join()
            fixture.shutdown();fixture.server_close();fixture_thread.join()
            if clipboard:
                print('Restoring the original Windows clipboard.',flush=True)
                clipboard.close()
                print('PASS: original Windows clipboard restored.',flush=True)
            if sys.platform=='darwin':
                with cocoa.pool():restore(cocoa,backup)
    print('PASS: large-file acceptance and cleanup completed.',flush=True)

if __name__=='__main__':run()
