from io import BytesIO
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from linkexpand.metadata import Preview, PreviewError, Resource, extract_metadata
from linkexpand.visuals import attach_visual, image_score, select_image


def picture(width=640, height=360, blank=False):
    canvas = Image.new("RGB", (width, height), "#285d73")
    if not blank:
        ImageDraw.Draw(canvas).rectangle((width//3, 0, width, height), fill="#e6c673")
    stream = BytesIO()
    canvas.save(stream, "PNG")
    return stream.getvalue()


class VisualTests(unittest.TestCase):
    def make_preview(self):
        return Preview("https://example.com/", "Title", "Summary", "example.com", "Example")

    def test_small_blank_and_corrupt_images_are_rejected(self):
        for image in [picture(32, 32), picture(blank=True), b"broken"]:
            self.assertEqual(image_score(image), -1)
        self.assertGreater(image_score(picture()), 0)

    def test_best_image_wins_over_small_first_image(self):
        preview = self.make_preview()
        preview.candidates = [{"url": "https://example.com/small.png", "source": "网页封面", "priority": 1000},
                              {"url": "https://example.com/good.png", "source": "网页图片", "priority": 350}]
        def fetch(url, *_args, **_kwargs):
            return Resource(url, picture(50, 50) if "small" in url else picture(), "image/png")
        with patch("linkexpand.visuals.fetch_resource", side_effect=fetch), patch("linkexpand.visuals.capture_page") as capture:
            attach_visual(preview)
        self.assertEqual(preview.visual_source, "网页图片")
        self.assertIn("good.png", preview.image_url)
        capture.assert_not_called()

    def test_missing_images_trigger_screenshot(self):
        preview = self.make_preview()
        with patch("linkexpand.visuals.capture_page", return_value={"image": picture(), "source": "网页截图"}) as capture:
            attach_visual(preview)
        capture.assert_called_once_with(preview.url)
        self.assertEqual(preview.visual_source, "网页截图")

    def test_failed_images_and_video_capture_fallback(self):
        preview = self.make_preview()
        preview.candidates = [{"url": "https://example.com/bad.png", "source": "网页封面", "priority": 1000}]
        with patch("linkexpand.visuals.fetch_resource", side_effect=PreviewError("bad image")), \
                patch("linkexpand.visuals.capture_page", return_value={"image": picture(), "source": "视频截图"}):
            attach_visual(preview)
        self.assertEqual(preview.visual_source, "视频截图")

    def test_capture_failure_keeps_text(self):
        preview = self.make_preview()
        with patch("linkexpand.visuals.capture_page", side_effect=PreviewError("截图超时")):
            attach_visual(preview)
        self.assertEqual(preview.description, "Summary")
        self.assertEqual(preview.warnings, ["截图超时"])

    def test_rendered_text_fills_missing_summary(self):
        preview = self.make_preview()
        preview.description = ""
        with patch("linkexpand.visuals.capture_page", return_value={"image": picture(), "source": "网页截图", "description": "Rendered article paragraph."}):
            attach_visual(preview)
        self.assertEqual(preview.description, "Rendered article paragraph.")
        self.assertEqual(preview.summary_source, "正文摘录")

    def test_image_candidates_include_srcset_and_video_poster(self):
        html = '''<title>Page</title><img src="/logo.png"><img src="/small.jpg" srcset="/medium.jpg 400w, /large.jpg 1200w">
            <video poster="/poster.jpg"></video>'''
        preview = extract_metadata(Resource("https://example.com/", html.encode(), "text/html"))
        self.assertEqual([item["url"] for item in preview.candidates],
                         ["https://example.com/large.jpg", "https://example.com/poster.jpg"])


if __name__ == "__main__":
    unittest.main()
