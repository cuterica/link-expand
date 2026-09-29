import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch

from linkexpand.download_tuning import DownloadTuner,TransferMeter
from linkexpand.video_downloads import DownloadTransport,open_video,RetryableHTTP


class KeepAliveHandler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    peers=[]
    def log_message(self,*args):pass
    def do_GET(self):
        self.peers.append(self.client_address)
        body=b'x'*4096
        self.send_response(429 if self.path=='/busy' else 200)
        self.send_header('Content-Length',str(len(body)))
        if self.path=='/busy':self.send_header('Retry-After','2')
        self.end_headers()
        try:self.wfile.write(body);self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),KeepAliveHandler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        KeepAliveHandler.peers=[]
        self.dns=patch('linkexpand.video_downloads.public_addresses',return_value=['93.184.216.34']);self.dns.start()
        self.factory=patch('linkexpand.video_downloads.PinnedHTTPConnection',side_effect=lambda *args:http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2));self.factory.start()
        self.transport=DownloadTransport()
    def tearDown(self):
        self.transport.close();self.server.shutdown();self.server.server_close();self.thread.join()
        self.factory.stop();self.dns.stop()
    def test_complete_responses_reuse_the_real_tcp_connection(self):
        for _ in range(3):
            with open_video('http://public.example/file',transport=self.transport) as response:
                self.assertEqual(len(response.read()),4096)
        self.assertEqual(len(set(KeepAliveHandler.peers)),1)
        self.assertEqual(self.transport.statistics['new_connections'],1)
        self.assertEqual(self.transport.statistics['reused_connections'],2)
        self.assertEqual(self.transport.statistics['dns_resolutions'],1)
    def test_unread_response_is_discarded_before_next_request(self):
        with open_video('http://public.example/file',transport=self.transport) as response:response.read(1)
        with open_video('http://public.example/file',transport=self.transport) as response:response.read()
        self.assertEqual(len(set(KeepAliveHandler.peers)),2)
        self.assertEqual(self.transport.statistics['reused_connections'],0)
    def test_busy_response_preserves_retry_after_and_reduces_parallelism(self):
        tuner=DownloadTuner();self.transport.on_error=tuner.backoff
        with self.assertRaises(RetryableHTTP) as error:
            with open_video('http://public.example/busy',transport=self.transport):pass
        self.assertEqual(error.exception.status,429)
        self.assertEqual(error.exception.retry_after,2)
        self.assertEqual(tuner.connections,2)


class TuningTests(unittest.TestCase):
    def test_connection_growth_is_rolled_back_without_real_speed_gain(self):
        tuner=DownloadTuner(now=0)
        tuner.tick(4_000_000,4);tuner.tick(8_000_000,8)
        self.assertEqual(tuner.connections,8)
        tuner.tick(12_000_000,12)
        self.assertEqual(tuner.connections,4)
    def test_growth_continues_when_aggregate_speed_improves(self):
        tuner=DownloadTuner(now=0)
        tuner.tick(4_000_000,4);tuner.tick(8_000_000,8);tuner.tick(16_000_000,12)
        self.assertEqual(tuner.connections,16)
        tuner.tick(24_000_000,16)
        self.assertEqual(tuner.connections,8)
    def test_user_speed_limit_stops_futile_connection_growth(self):
        tuner=DownloadTuner(now=0,speed_limit=1_000_000)
        tuner.tick(4_000_000,4);tuner.tick(8_000_000,8)
        self.assertEqual(tuner.connections,4)
    def test_manual_connection_count_is_preserved(self):
        tuner=DownloadTuner(maximum=8,automatic=False,now=0)
        tuner.tick(1_000_000,8);tuner.backoff(now=9)
        self.assertEqual(tuner.connections,8)
    def test_speed_display_drops_to_zero_after_a_stall(self):
        with patch('linkexpand.download_tuning.time.monotonic',return_value=0):meter=TransferMeter()
        with patch('linkexpand.download_tuning.time.monotonic',return_value=1):meter.add(1000)
        with patch('linkexpand.download_tuning.time.monotonic',return_value=20):self.assertEqual(meter.speed(),0)
