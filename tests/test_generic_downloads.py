import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from linkexpand.download_http import checked_headers,resource_url,scoped_headers,public_headers
from linkexpand.media_resolver import MediaHTML,candidate_resource,filename_from_headers,full_media_url
from linkexpand.metadata import PreviewError
from linkexpand.streaming import hls_media,hls_variants,dash_plan,substitute

class GenericTests(unittest.TestCase):
    def test_public_nonstandard_ports_and_url_validation(self):
        self.assertEqual(resource_url('https://example.com:8443/中文'), 'https://example.com:8443/%E4%B8%AD%E6%96%87')
        for value in ['file:///etc/passwd','https://user:pass@example.com','https://example.com:70000/','https://example.com/a\r\nX:x']:
            with self.assertRaises(PreviewError):resource_url(value)
    def test_credentials_do_not_cross_origin_or_enter_saved_headers(self):
        headers=checked_headers({'Cookie':'secret','Authorization':'Bearer token','Referer':'https://example.com/page'})
        self.assertIn('Cookie',scoped_headers(headers,'https://example.com/a.mp4','https://example.com/page'))
        self.assertNotIn('Cookie',scoped_headers(headers,'https://cdn.example.com/a.mp4','https://example.com/page'))
        self.assertNotIn('Authorization',public_headers(headers))
        for headers in [{'Host':'evil'},{'Range':'bytes=0-1'},{'Referer':'a\r\nb'}]:
            with self.assertRaises(PreviewError):checked_headers(headers)
    def test_html_video_jsonld_and_relative_links(self):
        parser=MediaHTML('https://example.com/pages/a')
        parser.feed('<title>Media</title><video src="../a.mp4"></video><source src="/b.webm" type="video/webm"><a href="/file.zip">Zip</a><script type="application/ld+json">{"@type":"VideoObject","contentUrl":"https://cdn.example.com/c.m3u8"}</script>')
        parser.extract_scripts()
        self.assertEqual({item['url'] for item in parser.candidates},{'https://example.com/a.mp4','https://example.com/b.webm','https://example.com/file.zip','https://cdn.example.com/c.m3u8'})
    def test_partial_player_query_becomes_whole_resource(self):
        self.assertEqual(full_media_url('https://example.com/a?range=0-999&token=abc'),'https://example.com/a?token=abc')
    def test_filename_and_candidate_keep_extension(self):
        self.assertEqual(filename_from_headers('https://example.com/api',{'content-type':'application/pdf'}),'api.pdf')
        self.assertEqual(filename_from_headers('https://example.com/a',{'content-disposition':"attachment; filename*=UTF-8''%E4%B8%AD%E6%96%87.zip"}),'中文.zip')
        item=candidate_resource({'url':'https://example.com/movie','kind':'video'},1,'https://example.com/',{},'https://example.com/')
        self.assertTrue(item['filename'].endswith('.mp4'))
        stream=candidate_resource({'url':'https://example.com/index.m3u8','kind':'hls'},1,'https://example.com/',{},'https://example.com/')
        self.assertEqual(stream['filename'],'index.mp4')
        webm=candidate_resource({'url':'https://example.com/videoplayback','kind':'video','mime':'video/webm'},1,'https://example.com/',{},'https://example.com/')
        self.assertEqual(webm['filename'],'videoplayback.webm')
    def test_hls_ranges_maps_keys_and_discontinuity(self):
        text='#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n#EXT-X-MAP:URI="init.mp4",BYTERANGE="20@0"\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x1\n#EXTINF:2,\n#EXT-X-BYTERANGE:100@20\na.mp4\n#EXT-X-DISCONTINUITY\n#EXTINF:3,\n#EXT-X-BYTERANGE:50\na.mp4\n#EXT-X-ENDLIST\n'
        track=hls_media(text,'https://example.com/list.m3u8')
        self.assertEqual(track['segments'][0]['range'],[20,119])
        self.assertEqual(track['segments'][1]['range'],[120,169])
        self.assertEqual(track['segments'][1]['sequence'],8)
        self.assertEqual(track['segments'][0]['init']['range'],[0,19])
        self.assertTrue(track['segments'][1]['discontinuity'])
    def test_hls_master_selects_quality_and_audio(self):
        data='#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",DEFAULT=YES,URI="audio.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=900,RESOLUTION=1280x720,AUDIO="aud"\nhigh.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=200,RESOLUTION=320x180\nlow.m3u8\n'
        variants=hls_variants(data,'https://example.com/main.m3u8')
        self.assertEqual(variants[0]['quality'],'1280x720')
        self.assertEqual(variants[0]['audio_url'],'https://example.com/audio.m3u8')
    def test_live_and_drm_are_not_reported_as_complete_downloads(self):
        with self.assertRaises(PreviewError):hls_media('#EXTM3U\n#EXTINF:2,\na.ts','https://example.com/a.m3u8')
        with self.assertRaises(PreviewError):hls_media('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="key"\n#EXTINF:2,\na.ts\n#EXT-X-ENDLIST','https://example.com/a.m3u8')
    def test_dash_duration_template_and_audio_tracks(self):
        text='<MPD mediaPresentationDuration="PT4S"><BaseURL>media/</BaseURL><Period><AdaptationSet mimeType="video/mp4"><SegmentTemplate timescale="1" duration="2" initialization="init-$RepresentationID$.mp4" media="$RepresentationID$-$Number%03d$.m4s"/><Representation id="v" bandwidth="1000" width="640" height="360"/></AdaptationSet><AdaptationSet mimeType="audio/mp4"><SegmentTemplate timescale="1" duration="2" initialization="init-a.mp4" media="a-$Number$.m4s"/><Representation id="a" bandwidth="100"/></AdaptationSet></Period></MPD>'
        with patch('linkexpand.streaming.document',return_value=('https://example.com/manifest.mpd',text)):
            plan=dash_plan('https://example.com/manifest.mpd')
        self.assertEqual(len(plan['tracks']),2)
        self.assertEqual(plan['tracks'][0]['segments'][0]['url'],'https://example.com/media/v-001.m4s')
        self.assertEqual(len(plan['tracks'][1]['segments']),2)
    def test_dash_timeline_negative_repeat(self):
        text='<MPD mediaPresentationDuration="PT6S"><Period><AdaptationSet contentType="video"><SegmentTemplate timescale="10" initialization="init.mp4" media="$Time$.m4s"><SegmentTimeline><S t="0" d="20" r="-1"/></SegmentTimeline></SegmentTemplate><Representation id="v" bandwidth="1000"/></AdaptationSet></Period></MPD>'
        with patch('linkexpand.streaming.document',return_value=('https://example.com/a.mpd',text)):
            plan=dash_plan('https://example.com/a.mpd')
        self.assertEqual([item['url'] for item in plan['tracks'][0]['segments']],['https://example.com/0.m4s','https://example.com/20.m4s','https://example.com/40.m4s'])
    def test_dash_multiple_periods_are_kept(self):
        body='<AdaptationSet contentType="video"><SegmentTemplate timescale="1" duration="2" initialization="init.mp4" media="$Number$.m4s"/><Representation id="v" bandwidth="1000"/></AdaptationSet>'
        text=f'<MPD mediaPresentationDuration="PT4S"><Period start="PT0S"><BaseURL>first/</BaseURL>{body}</Period><Period start="PT2S"><BaseURL>second/</BaseURL>{body}</Period></MPD>'
        with patch('linkexpand.streaming.document',return_value=('https://example.com/a.mpd',text)):
            plan=dash_plan('https://example.com/a.mpd')
        self.assertEqual([segment['url'] for segment in plan['tracks'][0]['segments']],['https://example.com/first/1.m4s','https://example.com/second/1.m4s'])
        self.assertTrue(plan['tracks'][0]['segments'][1]['discontinuity'])
    def test_dash_representation_mime_and_inherited_offset_timeline(self):
        text='<MPD mediaPresentationDuration="PT4S"><Period><AdaptationSet><SegmentTemplate timescale="10" presentationTimeOffset="500" initialization="init.mp4" media="$Time$.m4s"><SegmentTimeline><S t="500" d="20" r="-1"/></SegmentTimeline></SegmentTemplate><Representation id="v" mimeType="video/mp4" bandwidth="1000"><SegmentTemplate media="v-$Time$.m4s"/></Representation></AdaptationSet></Period></MPD>'
        with patch('linkexpand.streaming.document',return_value=('https://example.com/a.mpd',text)):
            plan=dash_plan('https://example.com/a.mpd')
        self.assertEqual([item['url'] for item in plan['tracks'][0]['segments']],['https://example.com/v-500.m4s','https://example.com/v-520.m4s'])

if __name__=='__main__':unittest.main()
