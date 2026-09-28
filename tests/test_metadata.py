import gzip
import socket
import unittest
from unittest.mock import Mock, patch

from linkexpand.metadata import (MAX_HTML, PreviewError, Resource, decode_html,
                                 extract_metadata, fetch_resource, get_preview,
                                 normalize_url, public_addresses, http_error_message)


def resource(html, url="https://example.com/articles/a", content_type="text/html; charset=utf-8"):
    return Resource(url, html.encode("utf-8"), content_type)


class MetadataTests(unittest.TestCase):
    def test_bilibili_412_explains_rejected_request_and_real_browser_path(self):
        url='https://www.bilibili.com/video/BV1cSec6tEux/'
        connection=Mock();response=connection.getresponse.return_value;response.status=412
        with patch('linkexpand.metadata.public_addresses',return_value=['93.184.216.34']), \
             patch('linkexpand.metadata.PinnedHTTPSConnection',return_value=connection):
            with self.assertRaisesRegex(PreviewError,'安全风控.*HTTP 412.*浏览器捕获'):
                fetch_resource(url)
        self.assertNotIn('请检查链接',http_error_message(url,412))
        self.assertNotIn('B 站',http_error_message('https://www.bilibili.com.example.org/',412))
    def test_open_graph_wins_and_entities_are_decoded(self):
        preview = extract_metadata(resource('''<title>Fallback</title>
            <meta property="og:title" content="你好 &amp; world">
            <meta name="twitter:title" content="Twitter">
            <meta name="description" content="Fallback summary">
            <meta property="og:description" content="  One   two  ">
            <meta property="og:site_name" content="Example">
            <meta property="og:image" content="../cover.png">'''))
        self.assertEqual(preview.title, "你好 & world")
        self.assertEqual(preview.description, "One two")
        self.assertEqual(preview.image_url, "https://example.com/cover.png")
        self.assertEqual(preview.site_name, "Example")

    def test_twitter_fallback_and_base_url(self):
        preview = extract_metadata(resource('''<base href="https://cdn.example.com/assets/">
            <meta name="twitter:title" content="A title">
            <meta name="twitter:description" content="A summary">
            <meta name="twitter:image:src" content="image.png">'''))
        self.assertEqual(preview.image_url, "https://cdn.example.com/assets/image.png")
        self.assertEqual(preview.title, "A title")

    def test_plain_html_and_missing_metadata(self):
        preview = extract_metadata(resource("<title>A <b>title</b></title><p>Hello <strong>there</strong>.</p>"))
        self.assertEqual(preview.title, "A title")
        self.assertEqual(preview.description, "Hello there.")
        empty = extract_metadata(resource("<html></html>"))
        self.assertEqual(empty.title, "example.com")
        self.assertTrue(empty.warnings)

    def test_chinese_charset(self):
        html = '<meta charset="gbk"><title>中文标题</title>'.encode("gbk")
        preview = extract_metadata(Resource("https://example.com/", html, "text/html"))
        self.assertEqual(preview.title, "中文标题")
        value = Resource("https://example.com/", b"hello", "text/html; charset=unknown-nonsense")
        self.assertEqual(decode_html(value), "hello")

    def test_non_text_charset_falls_back(self):
        value = Resource("https://example.com/", b"hello", "text/html; charset=base64_codec")
        self.assertEqual(decode_html(value), "hello")

    def test_image_candidates_are_retained_for_selection(self):
        with patch("linkexpand.metadata.fetch_resource", return_value=
            resource('<title>Still useful</title><meta property="og:image" content="/bad.png">')):
            preview = get_preview("https://example.com/")
        self.assertEqual(preview.title, "Still useful")
        self.assertIsNone(preview.image)
        self.assertEqual(preview.candidates[0]["url"], "https://example.com/bad.png")

    def test_non_html_is_rejected(self):
        with self.assertRaises(PreviewError):
            extract_metadata(resource("binary", content_type="application/pdf"))

    def test_url_normalization(self):
        self.assertEqual(normalize_url("example.com/文章?a=你#fragment"),
                         "https://example.com/%E6%96%87%E7%AB%A0?a=%E4%BD%A0")
        self.assertEqual(normalize_url("https://例子.测试"), "https://xn--fsqu00a.xn--0zwm56d/")
        for url in ["file:///etc/passwd", "http://user:pass@example.com", "https://example.com:8080",
                    "https://example.com/\r\nCookie:bad", "", "https://"]:
            with self.subTest(url=url), self.assertRaises(PreviewError):
                normalize_url(url)

    def test_private_mixed_and_multicast_addresses_are_blocked(self):
        for addresses in [["127.0.0.1"], ["10.0.0.5"], ["::1"],
                          ["8.8.8.8", "192.168.0.1"], ["224.0.0.1"]]:
            answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 443)) for addr in addresses]
            with self.subTest(addresses=addresses), patch("socket.getaddrinfo", return_value=answers):
                with self.assertRaises(PreviewError):
                    public_addresses("example.com", 443)

    def test_redirect_to_private_network_is_blocked(self):
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 302
        response.getheader.return_value = "http://127.0.0.1/secret"
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
        with patch("socket.getaddrinfo", side_effect=[public, private]), \
                patch("linkexpand.metadata.PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(PreviewError):
                fetch_resource("https://example.com")
        connection.close.assert_called_once()

    def test_fetch_is_pinned_to_validated_ip(self):
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheaders.return_value = []
        response.getheader.side_effect = lambda key, default="": {"Content-Type": "text/html"}.get(key, default)
        response.read1.side_effect = [b"<title>Hi</title>", b""]
        with patch("linkexpand.metadata.public_addresses", return_value=["93.184.216.34"]), \
                patch("linkexpand.metadata.PinnedHTTPSConnection", return_value=connection) as factory:
            result = fetch_resource("https://example.com")
        self.assertEqual(factory.call_args.args[:3], ("example.com", 443, "93.184.216.34"))
        self.assertEqual(result.body, b"<title>Hi</title>")

    def test_oversized_gzip_is_bounded(self):
        compressed = gzip.compress(b"x" * (MAX_HTML + 1))
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheaders.return_value = []
        response.getheader.side_effect = lambda key, default="": {"Content-Encoding": "gzip"}.get(key, default)
        response.read1.side_effect = [compressed, b""]
        with patch("linkexpand.metadata.public_addresses", return_value=["93.184.216.34"]), \
                patch("linkexpand.metadata.PinnedHTTPSConnection", return_value=connection):
            with self.assertRaises(PreviewError):
                fetch_resource("https://example.com")


if __name__ == "__main__":
    unittest.main()
