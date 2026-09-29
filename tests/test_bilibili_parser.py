import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs

from linkexpand.bilibili_parser import CACHE,parse,public_reference
from linkexpand.metadata import Resource,PreviewError
from linkexpand.server import App


class BilibiliParserTests(unittest.TestCase):
    def setUp(self):CACHE.clear()

    def test_only_canonical_bv_link_leaves_machine(self):
        source='https://www.bilibili.com/video/BV1cSec6tEux/?trackid=private-tracking&spm_id_from=tracking#section'
        canonical='https://www.bilibili.com/video/BV1cSec6tEux/'
        self.assertEqual(public_reference(source),canonical)
        self.assertIsNone(public_reference('https://bilibili.com.attacker.org/video/BV1cSec6tEux/'))
        response={'code':200,'data':{'title':'标题','description':'简介','cover':'http://i1.hdslb.com/cover.jpg',
            'videos':[{'url':'https://cdn.example.com/full.mp4'}]}}
        with patch('linkexpand.bilibili_parser.fetch_resource',return_value=Resource('https://api.bugpk.com/',json.dumps(response).encode(),'application/json')) as fetch:
            result=parse(source);parse(source)
        fetch.assert_called_once()
        self.assertEqual(parse_qs(urlsplit(fetch.call_args.args[0]).query),{'url':[canonical]})
        self.assertNotIn('Cookie',fetch.call_args.kwargs['headers'])
        self.assertNotIn('Authorization',fetch.call_args.kwargs['headers'])
        self.assertEqual(result['preview']['image_url'],'https://i1.hdslb.com/cover.jpg')
        self.assertEqual(result['catalog']['resources'][0]['kind'],'video')

    def test_invalid_service_results_raise_fallback_errors(self):
        for response in [[],{}, {'code':403,'data':{}},{'code':200,'data':{'title':''}}]:
            with patch('linkexpand.bilibili_parser.fetch_resource',return_value=Resource('https://api.bugpk.com/',json.dumps(response).encode(),'application/json')):
                with self.assertRaises(PreviewError):parse('https://www.bilibili.com/video/BV1cSec6tEux/')

    def test_parser_success_skips_failed_visible_browser(self):
        app=App();url='https://www.bilibili.com/video/BV1cSec6tEux/'
        result={'preview':{'title':'Working interface'},'catalog':{'id':'media'}}
        with patch('linkexpand.server.get_preview',side_effect=PreviewError('HTTP 412')), \
             patch.object(app,'bilibili_public',return_value=result),patch.object(app,'real_browser') as browser:
            preview=app.create(url)
        browser.assert_not_called();self.assertEqual(preview['catalog']['id'],'media')

    def test_user_can_disable_third_party_parser(self):
        app=App();url='https://www.bilibili.com/video/BV1cSec6tEux/'
        with patch('linkexpand.server.get_preview',side_effect=PreviewError('HTTP 412')),patch.object(app,'bilibili_public') as parser, \
             patch.object(app,'real_browser',return_value={'preview':{'title':'Real browser'},'catalog':None}):
            self.assertEqual(app.create(url,use_parser=False)['title'],'Real browser')
        parser.assert_not_called()

    def test_preview_with_catalog_is_valid_http_json(self):
        app=App();url='https://www.bilibili.com/video/BV1cSec6tEux/'
        preview={'id':'preview','title':'Parsed video','url':url}
        result={'preview':preview,'catalog':{'id':'catalog','preview':preview,'resources':[]}}
        with patch('linkexpand.server.get_preview',side_effect=PreviewError('HTTP 412')),patch.object(app,'bilibili_public',return_value=result):
            response=json.loads(json.dumps(app.create(url)))
        self.assertEqual(response['catalog']['preview']['title'],'Parsed video')
