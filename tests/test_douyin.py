import json,unittest
from urllib.parse import quote
from linkexpand.metadata import Resource,PreviewError
from linkexpand.douyin import post_reference,parse_resource,browser_video
URL='https://www.douyin.com/jingxuan?modal_id=7686432847778982833'
ID='7686432847778982833'
class DouyinTests(unittest.TestCase):
 def item(self):return {'awemeId':ID,'desc':'A real clip','video':{'duration':65700,'originCover':'https://p3.douyinpic.com/cover.jpg','bitRateList':[
  {'width':1920,'height':1080,'bitRate':9000000,'playAddr':[{'src':'https://v3.zjcdn.com/video/low'}]},
  {'width':2560,'height':1440,'bitRate':2000000,'playAddr':[{'src':'https://v3.zjcdn.com/video/high'}]},
  {'width':2560,'height':1440,'bitRate':3000000,'audioFileId':'separate','playAddr':[{'src':'https://v3.zjcdn.com/video/video-only'}]}]}}
 def resource(self,item):return Resource(URL,('<script id="RENDER_DATA">'+quote(json.dumps({'app':{'videoDetail':item}}))+'</script>').encode(),'text/html')
 def test_modal_and_video_links_match_exact_item(self):
  self.assertEqual(post_reference(URL)['id'],ID)
  self.assertEqual(post_reference('https://www.douyin.com/video/'+ID)['id'],ID)
  self.assertIsNone(post_reference(URL.replace('www.douyin.com','douyin.com.evil.example')))
 def test_render_data_cover_highest_complete_variant_and_duration(self):
  preview=parse_resource(self.resource(self.item()));self.assertEqual(preview.title,'A real clip')
  self.assertEqual(preview.videos[0]['variants'][0]['quality'],'2560×1440')
  self.assertEqual(len(preview.videos[0]['variants']),2)
  self.assertEqual(preview.videos[0]['duration_ms'],65700)
  self.assertEqual(preview.image_url,'https://p3.douyinpic.com/cover.jpg')
 def test_related_video_is_not_used_as_requested_modal(self):
  item=self.item();item['awemeId']='1111111111111111111'
  with self.assertRaises(PreviewError):parse_resource(self.resource(item))
 def test_browser_group_excludes_feed_and_static_videos(self):
  candidates=[{'kind':'video','url':'https://lf.douyinstatic.com/animation.mp4'},
   {'kind':'video','url':'https://v3.zjcdn.com/video/low','douyin_id':ID,'width':1920,'height':1080},
   {'kind':'video','url':'https://v3.zjcdn.com/video/high','douyin_id':ID,'width':2560,'height':1440}]
  video=browser_video(URL,candidates);self.assertEqual(video['variants'][0]['width'],2560)
  self.assertEqual(len(video['variants']),2)
