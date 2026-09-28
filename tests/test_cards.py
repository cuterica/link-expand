from io import BytesIO
import unittest

from PIL import Image

from linkexpand.cards import original_visual, render_card, thumbnail
from linkexpand.metadata import Preview, plain_text


class CardTests(unittest.TestCase):
    def make_preview(self):
        return Preview("https://example.com/文章", "一张中文链接卡片", "这里是一段网页摘要。", "example.com", "示例网站")

    def test_text_and_image_cards_are_valid_pngs(self):
        preview = self.make_preview()
        image = Image.new("RGBA", (300, 300), (255, 0, 0, 128))
        stream = BytesIO()
        image.save(stream, "PNG")
        preview.image = stream.getvalue()
        with Image.open(BytesIO(original_visual(preview))) as original:
            self.assertEqual(original.size, (300, 300))
        cover = thumbnail(preview)
        self.assertIsNotNone(cover)
        text_png = render_card(preview)
        image_png = render_card(preview, cover)
        with Image.open(BytesIO(text_png)) as text_card, Image.open(BytesIO(image_png)) as image_card:
            self.assertEqual(text_card.format, "PNG")
            self.assertEqual(text_card.width, 960)
            self.assertEqual(image_card.height - text_card.height, 480)
            self.assertTrue(all(value > 100 for value in image_card.getpixel((100, 100))))

    def test_corrupt_or_unsupported_image_falls_back(self):
        preview = self.make_preview()
        preview.image = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
        self.assertIsNone(thumbnail(preview))
        self.assertTrue(preview.warnings)
        self.assertTrue(render_card(preview).startswith(b"\x89PNG"))

    def test_plain_text_has_clickable_url(self):
        preview = self.make_preview()
        self.assertEqual(plain_text(preview), "一张中文链接卡片\n这里是一段网页摘要。\nhttps://example.com/文章")


if __name__ == "__main__":
    unittest.main()
