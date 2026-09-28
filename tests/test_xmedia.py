import unittest

from linkexpand.metadata import PreviewError
from linkexpand.xmedia import parse_post, post_reference, syndication_token


class XMediaTests(unittest.TestCase):
    def data(self):
        return {'id_str': '1460323737035677698', '__typename': 'Tweet', 'text': '公开视频 &amp; 简介',
                'user': {'name': 'Demo', 'screen_name': 'demo'}, 'mediaDetails': [
                    {'type': 'photo', 'media_url_https': 'https://pbs.twimg.com/example.jpg'},
                    {'type': 'video', 'media_url_https': 'https://pbs.twimg.com/poster.jpg', 'video_info': {
                        'duration_millis': 11093, 'variants': [
                            {'content_type': 'application/x-mpegURL', 'url': 'https://video.twimg.com/a.m3u8'},
                            {'content_type': 'video/mp4', 'bitrate': 256000, 'url': 'https://video.twimg.com/vid/480x270/small.mp4'},
                            {'content_type': 'video/mp4', 'bitrate': 2176000, 'url': 'https://video.twimg.com/vid/1280x720/large.mp4'},
                            {'content_type': 'video/mp4', 'bitrate': 9000000, 'url': 'http://127.0.0.1/private.mp4'},
                        ]}}]}

    def test_status_url_variants_and_selected_attachment(self):
        for url in ['https://x.com/demo/status/1460323737035677698/video/2?x=1',
                    'https://twitter.com/i/web/status/1460323737035677698',
                    'https://mobile.twitter.com/demo/status/1460323737035677698']:
            self.assertEqual(post_reference(url)['id'], '1460323737035677698')
        self.assertIsNone(post_reference('https://attacker.example/demo/status/1460323737035677698'))
        self.assertIsNone(post_reference('https://x.com/demo'))

    def test_public_data_extracts_full_mp4s_in_quality_order(self):
        preview = parse_post(self.data(), 'https://x.com/demo/status/1460323737035677698/video/2')
        self.assertEqual(preview.description, '公开视频 & 简介')
        self.assertEqual(preview.selected_video, 2)
        self.assertEqual(len(preview.videos), 1)
        self.assertEqual(len(preview.videos[0]['variants']), 2)
        self.assertEqual(preview.videos[0]['variants'][0]['quality'], '1280×720')
        self.assertEqual(preview.videos[0]['duration_ms'], 11093)

    def test_video_object_fallback(self):
        data = self.data()
        del data['mediaDetails']
        data['video'] = {'durationMs': 1000, 'poster': 'https://pbs.twimg.com/a.jpg',
                         'variants': [{'type': 'video/mp4', 'src': 'https://video.twimg.com/vid/640x360/a.mp4'}]}
        preview = parse_post(data, 'https://twitter.com/demo/status/1460323737035677698')
        self.assertEqual(preview.selected_video, 1)
        self.assertEqual(len(preview.videos[0]['variants']), 1)

    def test_unavailable_or_mismatched_post_is_rejected(self):
        for data in [{}, {'__typename': 'TweetTombstone'}, {'id_str': '111111'}]:
            with self.assertRaises(PreviewError):
                parse_post(data, 'https://x.com/demo/status/1460323737035677698')

    def test_exact_token_uses_bundled_runtime_without_browser(self):
        self.assertRegex(syndication_token('1460323737035677698'), r'^[a-z0-9]+$')


if __name__ == '__main__':
    unittest.main()
