"""Native-browser regression: target metadata must not wait for a stalled player."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from linkexpand.browser_real import expand
from linkexpand.metadata import Resource


def main():
    identifier = '7686432847778982833'
    # Use a reserved fixture host: installed browsers upgrade real Douyin to HTTPS.
    target = 'http://douyin-fixture.example/jingxuan?modal_id=' + identifier
    video = 'http://v3.zjcdn.com/video/tos/target.mp4'
    item = {'awemeId': identifier, 'desc': 'Target metadata before player loading',
            'video': {'originCover': 'https://p3.douyinpic.com/cover.jpg', 'bitRateList': [
                {'width': 2560, 'height': 1440, 'playAddr': [{'src': video}]}]}}
    data = quote(json.dumps({'app': {'videoDetail': item}}))
    release = threading.Event(); stalled = threading.Event(); seen = []
    cover = BytesIO(); Image.new('RGB', (960, 480), 'green').save(cover, 'PNG')

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            path = urlsplit(self.path).path; seen.append(path)
            if path == '/jingxuan':
                body = ('<script id="RENDER_DATA" type="application/json">' + data + '</script>'
                        '<video autoplay src="' + video + '"></video>'
                        '<script src="/stalled-player.js"></script>').encode()
                mime = 'text/html; charset=utf-8'
            elif path == '/stalled-player.js':
                stalled.set(); release.wait(35); body = b''; mime = 'text/javascript'
            else: body = b''; mime = 'video/mp4'
            self.send_response(200); self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body))); self.end_headers()
            try: self.wfile.write(body)
            except OSError: pass

        def do_CONNECT(self):
            self.send_response(502); self.end_headers()

    fixture = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    thread = threading.Thread(target=fixture.serve_forever, daemon=True); thread.start()
    original_read = Path.read_text
    def fixture_reader(path, *args, **kwargs):
        text = original_read(path, *args, **kwargs)
        if path.name == 'page.js':
            text = text.replace(r'douyin\.com', r'douyin-fixture\.example')
        return text
    try:
        with tempfile.TemporaryDirectory(prefix='linkexpand-douyin-regression-') as profile, \
             patch('linkexpand.douyin.post_reference', return_value={'id': identifier}), \
             patch.object(Path, 'read_text', fixture_reader), \
             patch('linkexpand.browser_real.public_addresses', return_value=['93.184.216.34']) as dns, \
             patch('linkexpand.metadata.fetch_resource', return_value=Resource(
                 item['video']['originCover'], cover.getvalue(), 'image/png')):
            started = time.monotonic()
            result = asyncio.run(expand(target, profile=profile, timeout=15,
                extra_args=[f'--proxy-server=http://127.0.0.1:{fixture.server_port}']))
            elapsed = time.monotonic() - started
        assert stalled.is_set(), 'Fixture must block DOMContentLoaded'
        assert elapsed < 15, f'Waited for the stalled player: {elapsed:.2f}s'
        assert result['preview']['title'] == item['desc']
        assert result['preview']['image_data'].startswith('data:image/png;')
        assert result['candidates'], 'Target metadata lost when DOM repeats the same video URL'
        assert result['candidates'][0]['douyin_id'] == identifier
        assert result['candidates'][0]['url'] == video
        assert '/video/tos/target.mp4' not in seen, 'Preview downloaded the player media'
        assert dns.call_count == 1, 'Concurrent same-host requests repeated DNS checks'
        print(json.dumps({'PASS': 'target metadata and cover without DOM/player wait or media transfer',
                          'seconds': round(elapsed, 2)}), flush=True)
    finally:
        release.set(); fixture.shutdown(); fixture.server_close(); thread.join()


if __name__ == '__main__': main()
