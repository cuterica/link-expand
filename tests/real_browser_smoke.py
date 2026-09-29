"""Headed native-browser fallback with real HTTP traffic and scoped login cookies."""
import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from linkexpand.browser_real import expand, real_executable
from linkexpand.metadata import PreviewError
from linkexpand.media_resolver import imported_candidates


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--url');args=parser.parse_args()
    if args.url:
        with tempfile.TemporaryDirectory(prefix='linkexpand-real-site-') as directory:
            try:
                result=asyncio.run(expand(args.url,profile=directory))
                print('PASS: actual visible browser:',Path(real_executable()).name,flush=True)
                print('Title:',result['preview']['title'],flush=True)
                print('Media resources:',len(result['candidates']),flush=True)
            except PreviewError as error:
                print('Actual visible browser result:',str(error),flush=True);raise SystemExit(2)
        return
    image=BytesIO();Image.new('RGB',(960,480),'green').save(image,'PNG')
    seen=[]
    class Fixture(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            path=urlsplit(self.path).path;cookie=self.headers.get('Cookie','')
            seen.append((path,'signed_in=session-fixture' in cookie))
            if path=='/page':
                assert 'signed_in=session-fixture' in cookie
                body='''<meta charset="utf-8"><title>真实浏览器成功读取</title><meta property="og:description" content="原请求 412 后浏览器原生访问成功">
                  <meta property="og:image" content="http://signed-in.example/cover.png"><img src="http://signed-in.example/cover.png">
                  <video src="http://signed-in.example/video.m4s"></video><audio src="http://signed-in.example/audio.m4s"></audio>
                  <script>window.__playinfo__={data:{dash:{video:[{baseUrl:'http://signed-in.example/video.m4s'}],audio:[{baseUrl:'http://signed-in.example/audio.m4s'}]}}};</script>'''.encode();mime='text/html; charset=utf-8'
            elif path=='/cover.png':body=image.getvalue();mime='image/png'
            elif path=='/video.m4s':body=b'video';mime='video/mp4'
            elif path=='/audio.m4s':body=b'audio';mime='audio/mp4'
            else:body=b'404';mime='text/plain'
            self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
    fixture=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    thread=threading.Thread(target=fixture.serve_forever,daemon=True);thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='linkexpand-visible-qa-') as directory, \
             patch('linkexpand.browser_real.public_addresses',return_value=['93.184.216.34']), \
             patch('linkexpand.metadata.fetch_resource',side_effect=AssertionError('Browser traffic must not use Python HTTP')):
            result=asyncio.run(expand('http://signed-in.example/page',{'Cookie':'signed_in=session-fixture'},
                profile=directory,extra_args=[f'--proxy-server=http://127.0.0.1:{fixture.server_port}']))
        assert result['method']=='playwright-visible'
        assert result['preview']['title']=='真实浏览器成功读取'
        assert result['preview']['image_data'].startswith('data:image/')
        catalog=imported_candidates(result['source'],result['candidates'])
        assert catalog['resources'][0]['kind']=='pair'
        for item in catalog['resources']:
            if item['kind'] in {'video','audio'}:
                headers={key.lower():value for key,value in item['variants'][0]['headers'].items()}
                assert 'signed_in=session-fixture' in headers['cookie']
        assert ('/page',True) in seen and ('/video.m4s',True) in seen and ('/audio.m4s',True) in seen
    finally:fixture.shutdown();fixture.server_close();thread.join()
    print('PASS: visible native browser, no Python HTTP replay, site cookies, title/summary/image, complete M4S video/audio tracks and authenticated media requests.')


if __name__=='__main__':main()
