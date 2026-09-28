import http.client
import json
import threading
import unittest
from unittest.mock import patch

from linkexpand.metadata import Preview
from linkexpand import __version__
from linkexpand.server import App, Server


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = App()
        cls.server = Server(("127.0.0.1", 0), cls.app)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, data=None, headers=None, method=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        request_headers = {"X-Local-Token": self.app.token, "Content-Type": "application/json"}
        request_headers.update(headers or {})
        connection.request(method or ("POST" if data is not None else "GET"), path,
                           body=json.dumps(data) if data is not None else None, headers=request_headers)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_session_token_inserted_into_html(self):
        status, body, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(self.app.token.encode(), body)
        self.assertNotIn(b"__LOCAL_TOKEN__", body)

    def test_running_backend_reports_current_version(self):
        status, body, _ = self.request('/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'app': 'link-expand', 'version': __version__})
        status, body, _ = self.request('/api/capabilities')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['version'], __version__)

    def test_api_rejects_missing_token_and_cross_site_origin(self):
        cases = [{"X-Local-Token": ""}, {"Origin": "https://attacker.example"},
                 {"Host": "attacker.example"}, {"Sec-Fetch-Site": "cross-site"}]
        for headers in cases:
            with self.subTest(headers=headers):
                status, _, _ = self.request("/api/manual", {"url": "https://example.com", "title": "Hi"}, headers)
                self.assertEqual(status, 403)

    def test_manual_edit_and_download_round_trip(self):
        status, body, _ = self.request("/api/manual", {"url": "https://example.com", "title": "中文标题", "description": "网页摘要"})
        self.assertEqual(status, 200)
        preview = json.loads(body)
        self.assertIn("https://example.com/", preview["text"])
        status, body, headers = self.request(preview["image"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))
        status, body, _ = self.request("/api/edit", {"id": preview['id'], "url": preview['url'],
            "title": "更新标题", "description": preview['description'], "site_name": preview['site_name']})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["title"], "更新标题")

    def test_invalid_requests_are_recoverable(self):
        for data in [[], {"url": "file:///x", "title": "Hi"}, {"url": "https://example.com", "title": ""}]:
            with self.subTest(data=data):
                status, _, _ = self.request("/api/manual", data)
                self.assertEqual(status, 400)

    def test_preview_uses_fetched_metadata(self):
        preview = Preview("https://example.com/", "Fetched title", "Summary", "example.com", "Example")
        with patch("linkexpand.server.get_preview", return_value=preview), patch("linkexpand.server.attach_visual"):
            status, body, _ = self.request("/api/preview", {"url": "https://example.com"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["title"], "Fetched title")

    def test_no_arbitrary_files_are_served(self):
        for path in ["/../../etc/passwd", "/static/../server.py", "/assets/invalid/card.png"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_removed_desktop_endpoints_are_not_available(self):
        for path in ["/api/paste", "/api/watch", "/api/copy"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, {"enabled": True})[0], 404)

    def test_video_actions_require_session_and_a_video(self):
        self.assertEqual(self.request('/api/video/start', {'id':'x'}, {'X-Local-Token':''})[0], 403)
        _, body, _ = self.request('/api/manual', {'url':'https://example.com','title':'No video'})
        preview = json.loads(body)
        self.assertEqual(self.request('/api/video/start', {'id':preview['id'],'video_index':1})[0],400)


if __name__ == "__main__":
    unittest.main()
