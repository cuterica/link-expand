import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from linkexpand.video_downloads import DownloadManager

VIDEO = b'\x00\x00\x00\x18ftypmp42' + bytes(range(256)) * 4096


class VideoHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    requests = []
    interrupted = False

    def log_message(self, *args):
        pass

    def do_GET(self):
        marker = self.path
        requested = self.headers.get('Range')
        self.requests.append((marker, requested, self.headers.get('If-Range')))
        if marker == '/huge.mp4':
            self.send_response(206)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Range', 'bytes 0-0/500000001')
            self.send_header('Content-Length', '1')
            self.end_headers()
            return
        ranged = requested and marker not in {'/single.mp4', '/unknown.mp4'}
        match = re.fullmatch(r'bytes=(\d+)-(\d+)', requested or '')
        start, end = (int(match[1]), int(match[2])) if ranged else (0, len(VIDEO)-1)
        data = VIDEO[start:end+1]
        self.send_response(206 if ranged else 200)
        self.send_header('Content-Type', 'video/mp4')
        if marker != '/unknown.mp4':
            self.send_header('Content-Length', str(len(data)))
        self.send_header('ETag', '"video-v1"')
        if ranged:
            offset = start + 1 if marker == '/wrong.mp4' and end > 0 else start
            self.send_header('Content-Range', f'bytes {offset}-{end}/{len(VIDEO)}')
        self.end_headers()
        try:
            for offset in range(0, len(data), 8192):
                self.wfile.write(data[offset:offset+8192])
                self.wfile.flush()
                if marker == '/retry.mp4' and end > 0 and not type(self).interrupted and offset >= 16384:
                    type(self).interrupted = True
                    self.connection.shutdown(2)
                    return
                time.sleep(.002)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), VideoHandler)
        cls.server.daemon_threads = True
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addresses = patch('linkexpand.video_downloads.public_addresses', return_value=['93.184.216.34'])
        self.connections = patch('linkexpand.video_downloads.PinnedHTTPConnection', side_effect=lambda *args:
            http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2))
        self.addresses.start()
        self.connections.start()
        self.manager = DownloadManager(self.root, self.root / 'state', limit=2_000_000, chunk_size=65536)
        VideoHandler.requests = []
        VideoHandler.interrupted = False

    def tearDown(self):
        self.manager.close()
        self.connections.stop()
        self.addresses.stop()
        self.directory.cleanup()

    def video(self, name='video', variants=None):
        return {'index': 1, 'post_id': '1460323737035677698', 'author': 'demo', 'duration_ms': 1000,
                'variants': variants or [{'url': f'http://video.twimg.com/{name}.mp4', 'quality': '1280×720'}]}

    def start(self, video=None):
        state = self.manager.start(video or self.video(), 'https://x.com/demo/status/1460323737035677698')
        return self.manager.get(state['id'])

    def wait(self, job, states=('complete', 'error', 'cancelled', 'paused')):
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            if job.status in states and (not job.thread or not job.thread.is_alive()):
                return job.snapshot()
            time.sleep(.01)
        self.fail(f'Job did not settle: {job.snapshot()}')

    def test_parallel_ranges_produce_exact_full_file(self):
        job = self.start()
        state = self.wait(job)
        self.assertEqual(state['status'], 'complete', state)
        self.assertEqual(job.file.read_bytes(), VIDEO)
        self.assertEqual(state['sha256'], hashlib.sha256(VIDEO).hexdigest())
        self.assertGreater(len(VideoHandler.requests), 4)
        self.assertTrue(any(request[2] == '"video-v1"' for request in VideoHandler.requests))

    def test_size_limit_selects_smaller_quality(self):
        video = self.video(variants=[{'url': 'http://video.twimg.com/huge.mp4', 'quality': '1920×1080'},
                                    {'url': 'http://video.twimg.com/video.mp4', 'quality': '640×360'}])
        job = self.start(video)
        state = self.wait(job)
        self.assertEqual(state['status'], 'complete', state)
        self.assertEqual(state['quality'], '640×360')

    def test_oversized_known_file_is_rejected_without_output(self):
        job = self.start(self.video('huge'))
        state = self.wait(job)
        self.assertEqual(state['status'], 'error')
        self.assertIn('超过', state['error'])
        self.assertFalse(job.part.exists())
        self.assertFalse(list(self.root.glob('*.mp4')))

    def test_unknown_size_is_stopped_at_limit(self):
        self.manager.limit = 32768
        job = self.start(self.video('unknown'))
        state = self.wait(job)
        self.assertEqual(state['status'], 'error')
        self.assertFalse(job.part.exists())
        self.assertLessEqual(state['downloaded'], 32768)

    def test_non_range_server_uses_single_connection(self):
        job = self.start(self.video('single'))
        state = self.wait(job)
        self.assertEqual(state['status'], 'complete', state)
        self.assertFalse(state['resumable'])
        self.assertEqual(job.file.read_bytes(), VIDEO)

    def test_retry_resumes_failed_segment(self):
        job = self.start(self.video('retry'))
        state = self.wait(job)
        self.assertEqual(state['status'], 'complete', state)
        self.assertEqual(job.file.read_bytes(), VIDEO)
        self.assertTrue(VideoHandler.interrupted)

    def test_invalid_content_range_never_publishes_file(self):
        job = self.start(self.video('wrong'))
        state = self.wait(job)
        self.assertEqual(state['status'], 'error')
        self.assertFalse(list(self.root.glob('*.mp4')))

    def test_pause_restart_and_resume_keep_saved_progress(self):
        job = self.start()
        deadline = time.monotonic()+5
        while time.monotonic() < deadline and job.snapshot()['downloaded'] < 32768:
            time.sleep(.005)
        job.pause()
        state = self.wait(job)
        self.assertEqual(state['status'], 'paused', state)
        self.assertGreater(state['downloaded'], 0)
        saved_id = job.id
        self.manager.close()
        self.manager = DownloadManager(self.root, self.root/'state', limit=2_000_000, chunk_size=65536)
        restored = self.manager.get(saved_id)
        self.assertGreater(restored.snapshot()['downloaded'], 0)
        restored.launch()
        state = self.wait(restored)
        self.assertEqual(state['status'], 'complete', state)
        self.assertEqual(restored.file.read_bytes(), VIDEO)

    def test_cancel_removes_partial_file(self):
        job = self.start()
        time.sleep(.03)
        job.pause(cancel=True)
        state = self.wait(job)
        self.assertEqual(state['status'], 'cancelled', state)
        self.assertFalse(job.part.exists())

    def test_generic_file_and_secret_headers_are_not_persisted(self):
        video=self.video();video['kind']='file';video['filename']='archive.zip'
        video['credential_origin']='http://video.twimg.com/'
        video['variants'][0]['headers']={'Cookie':'private-cookie','Authorization':'Bearer private-token'}
        job=self.start(video);state=self.wait(job)
        self.assertEqual(state['status'],'complete',state)
        self.assertTrue(job.filename.endswith('.zip'))
        self.assertEqual(job.file.read_bytes(),VIDEO)
        saved=job.state_file.read_text()
        self.assertNotIn('private-cookie',saved)
        self.assertNotIn('private-token',saved)


if __name__ == '__main__':
    unittest.main()
