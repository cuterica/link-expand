import base64
from io import BytesIO
from html.parser import HTMLParser
from pathlib import Path, PureWindowsPath
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from PIL import Image

from linkexpand.clipboard import cf_html, chat_xml, copy_rich
from linkexpand.metadata import Preview
from linkexpand.sharing import rich_html


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.links = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "img":
            self.images.append(values)
        if tag == "a":
            self.links.append(values)
        if tag == "script":
            self.scripts.append(values)


class SharingTests(unittest.TestCase):
    def preview(self):
        return Preview('https://example.com/?a=1&b=2', '标题 <script>alert(1)</script>',
                       '摘要 & 内容', 'example.com', 'Example')

    def test_card_has_clickable_links_and_embedded_image(self):
        image = b'example PNG content'
        parser = CardParser()
        parser.feed(rich_html(self.preview(), image))
        self.assertEqual(len(parser.images), 1)
        embedded = parser.images[0]['src'].split(',', 1)[1]
        self.assertEqual(base64.b64decode(embedded), image)
        self.assertTrue(parser.links)
        self.assertTrue(all(link['href'] == self.preview().url for link in parser.links))
        self.assertFalse(parser.scripts)
        self.assertEqual(parser.images[0]['width'], '480')

    def test_text_only_card_keeps_links(self):
        parser = CardParser()
        parser.feed(rich_html(self.preview(), None))
        self.assertFalse(parser.images)
        self.assertEqual(len(parser.links), 2)

    def test_windows_html_offsets_count_utf8_bytes(self):
        fragment = '<div>中文链接 <a href="https://example.com/">打开</a></div>'
        data = cf_html(fragment, 'https://example.com/')
        offsets = {key.decode(): int(value) for key, value in re.findall(
            br'(StartHTML|EndHTML|StartFragment|EndFragment):(\d+)', data)}
        self.assertEqual(data[offsets['StartFragment']:offsets['EndFragment']], fragment.encode('utf-8'))
        self.assertEqual(data[offsets['EndHTML']:], b'\0')
        self.assertTrue(data[offsets['StartHTML']:].startswith(b'<html>'))

    def test_chat_format_preserves_text_url_and_image_path(self):
        path = PureWindowsPath('C:/Users/中文用户/cache/a.png')
        text = '标题 ]]> 摘要\nhttps://example.com/'
        root = ET.fromstring(chat_xml(text, path).rstrip(b'\0'))
        elements = root.findall('EditElement')
        self.assertEqual(elements[0].attrib['filepath'], str(path))
        self.assertEqual(elements[1].text, '\n' + text)

    def test_preview_without_cover_still_copies_card_image(self):
        image = BytesIO()
        Image.new('RGB', (480, 180), 'white').save(image, 'PNG')
        record = {'preview': self.preview(), 'cover': None, 'png': image.getvalue()}
        with tempfile.TemporaryDirectory() as folder, \
                patch('linkexpand.clipboard.os', SimpleNamespace(name='nt', environ={'LOCALAPPDATA': folder})), \
                patch('linkexpand.clipboard.set_formats') as formats:
            copy_rich(record, 'abc123')
            payloads = dict(formats.call_args.args[0])
            tree = ET.fromstring(payloads['QQ_Unicode_RichEdit_Format'].rstrip(b'\0'))
            picture = tree.find("EditElement[@type='1']")
            self.assertIsNotNone(picture)
            self.assertEqual(Path(picture.attrib['filepath']).read_bytes(), record['png'])


if __name__ == '__main__':
    unittest.main()
