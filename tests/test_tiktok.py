import json
import unittest
from unittest.mock import patch
from linkexpand.metadata import Resource,PreviewError
from linkexpand.tiktok import post_reference,parse_resource,browser_video
from linkexpand.media_resolver import imported_candidates

URL='https://www.tiktok.com/@demo/video/7669725994743336199'
ID='7669725994743336199'

class TikTokTests(unittest.TestCase):
    def item(self):
        return {'id':ID,'desc':'Actual clip caption','author':{'uniqueId':'demo','nickname':'Demo'},'video':{
            'duration':14,'width':576,'height':768,'originCover':'https://p16.tiktokcdn-us.com/original.jpg',
            'bitrateInfo':[{'Bitrate':9000000,'PlayAddr':{'Width':576,'Height':768,'UrlList':['https://v16.tiktok.com/video/low']}},
                           {'Bitrate':2000000,'PlayAddr':{'Width':1080,'Height':1440,'UrlList':['https://v16.tiktok.com/video/high','https://v19.tiktok.com/video/high']}}]}}
    def resource(self,item=None,legacy=False):
        item=self.item() if item is None else item
        data={'ItemModule':{ID:item}} if legacy else {'__DEFAULT_SCOPE__':{'webapp.video-detail':{'itemInfo':{'itemStruct':item}}}}
        identifier='SIGI_STATE' if legacy else '__UNIVERSAL_DATA_FOR_REHYDRATION__'
        return Resource(URL,('<html><title>TikTok - Make Your Day</title><script id="'+identifier+'" type="application/json">'+json.dumps(data)+'</script></html>').encode(),'text/html')
    def test_exact_tiktok_video_urls_and_unrelated_hosts(self):
        self.assertEqual(post_reference(URL)['id'],ID)
        self.assertIsNone(post_reference(URL.replace('www.tiktok.com','tiktok.com.evil.example')))
        self.assertIsNone(post_reference('https://www.tiktok.com/@demo'))
    def test_hydration_supplies_caption_cover_and_highest_resolution(self):
        preview=parse_resource(self.resource())
        self.assertIn('Actual clip caption',preview.title)
        self.assertEqual(preview.description,'Actual clip caption')
        self.assertEqual(preview.image_url,'https://p16.tiktokcdn-us.com/original.jpg')
        self.assertEqual(preview.videos[0]['duration_ms'],14000)
        self.assertEqual(preview.videos[0]['variants'][0]['quality'],'1080×1440')
    def test_legacy_sigi_state_and_mismatched_item(self):
        self.assertEqual(parse_resource(self.resource(legacy=True)).videos[0]['post_id'],ID)
        wrong=self.item();wrong['id']='1111111111111111111'
        with self.assertRaises(PreviewError):parse_resource(self.resource(wrong))
    def test_browser_variants_are_grouped_and_static_animation_excluded(self):
        candidates=[{'url':'https://lf16-tiktok-web.tiktokcdn-us.com/static/loading.mp4','kind':'video'},
                    {'url':'https://v16.tiktok.com/video/high','kind':'video','tiktok_id':ID,'width':1080,'height':1440,'headers':{'Cookie':'ephemeral'}},
                    {'url':'https://v19.tiktok.com/video/low','kind':'video','tiktok_id':ID,'width':576,'height':768,'headers':{'Cookie':'other-ephemeral'}}]
        result=imported_candidates(URL,candidates)
        self.assertEqual(len(result['resources']),1)
        variants=result['resources'][0]['variants'];self.assertEqual(len(variants),2)
        self.assertEqual(variants[0]['quality'] if 'quality' in variants[0] else variants[0]['width'],1080)
        self.assertEqual(variants[0]['credential_origin'],variants[0]['url'])
    def test_metadata_routes_to_tiktok_data_not_generic_page_title(self):
        from linkexpand.metadata import get_preview
        with patch('linkexpand.metadata.fetch_resource',return_value=self.resource()):
            self.assertIn('Actual clip caption',get_preview(URL).title)

    def test_title_success_with_rejected_media_still_uses_real_browser(self):
        from linkexpand.server import App
        preview=parse_resource(self.resource());app=App()
        result={'preview':{'id':'ready','title':'Actual clip caption','url':URL},'catalog':{'id':'videos'}}
        with patch('linkexpand.server.get_preview',return_value=preview),patch('linkexpand.server.attach_visual'), \
             patch('linkexpand.video_downloads.probe_video',side_effect=PreviewError('HTTP 403')), \
             patch.object(app,'real_browser',return_value=result) as fallback:
            answer=app.create(URL)
        self.assertEqual(answer['catalog']['id'],'videos')
        fallback.assert_called_once_with(URL)
