"""Discover dynamically inserted media through the bounded browser transport."""
import asyncio
import gzip
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import sys
import threading
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from linkexpand.media_scan import inspect_page

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path=='/page':
            body=gzip.compress(b'<html><title>Dynamic media</title><script>setTimeout(()=>{let v=document.createElement("video");v.src="/movie.mp4";document.body.append(v);fetch("/master.m3u8")},100)</script></html>')
            mime='text/html';compressed=True
        elif self.path=='/master.m3u8':
            body=b'#EXTM3U\n#EXTINF:2,\nsegment.ts\n#EXT-X-ENDLIST\n';mime='application/vnd.apple.mpegurl';compressed=False
        else:body=b'\0\0\0\x18ftypmp42'+b'x'*1024;mime='video/mp4';compressed=False
        self.send_response(200);self.send_header('Content-Type',mime)
        if compressed:self.send_header('Content-Encoding','gzip')
        self.send_header('Content-Length',str(len(body)));self.end_headers()
        try:self.wfile.write(body)
        except OSError:pass

def run():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with patch('linkexpand.video_downloads.public_addresses',return_value=['93.184.216.34']), \
             patch('linkexpand.video_downloads.PinnedHTTPConnection',side_effect=lambda *args:http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)):
            resources=asyncio.run(inspect_page(f'http://public.test:{server.server_port}/page',{}))
            assert any(item['url'].endswith('/movie.mp4') for item in resources),resources
            assert any(item['url'].endswith('/master.m3u8') and item['kind']=='hls' for item in resources),resources
    finally:server.shutdown();server.server_close();thread.join()
    print('PASS: compressed dynamic page, inserted video and fetched HLS manifest; isolated browser closed.')

if __name__=='__main__':run()
