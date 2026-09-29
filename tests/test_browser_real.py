import unittest
from unittest.mock import patch

from linkexpand.browser_real import session_cookies
from linkexpand.metadata import PreviewError
from linkexpand.server import App
from linkexpand.browser_bridge import BrowserBridge


class RealBrowserTests(unittest.TestCase):
    def test_cookie_transfer_is_scoped_and_preserves_signed_values(self):
        cookies=session_cookies('https://www.bilibili.com/video/BVtest/',{'Cookie':'SESSDATA=a%2Cb%3D; bili_jct=token; bad'})
        self.assertEqual(cookies[0]['value'],'a%2Cb%3D')
        self.assertTrue(all(item['domain']=='.bilibili.com' and item['secure'] for item in cookies))
        other=session_cookies('https://example.org/page',{'cookie':'session=test'})
        self.assertEqual(other[0]['domain'],'example.org')
        self.assertFalse(session_cookies('https://example.org/',{'Cookie':'invalid\x00name=bad; safe=x\n'}))

    def test_http_failure_falls_back_and_returns_download_catalog(self):
        app=App()
        preview={'id':'id','title':'Native browser','url':'https://example.com/'}
        with patch('linkexpand.server.get_preview',side_effect=PreviewError('HTTP 412')), \
             patch.object(app,'real_browser',return_value={'preview':preview,'catalog':{'id':'catalog'}}) as fallback:
            result=app.create('https://example.com/')
        self.assertEqual(result['title'],'Native browser')
        self.assertEqual(result['catalog']['id'],'catalog')
        fallback.assert_called_once_with('https://example.com/')

    def test_real_cookie_attributes_and_unrelated_domains(self):
        records={'cookies':[{'name':'SESSDATA','value':'session','domain':'.bilibili.com','path':'/','secure':True,'httpOnly':True,'sameSite':'lax'},
                            {'name':'bili_jct','value':'csrf','domain':'.bilibili.com','path':'/','secure':True,'httpOnly':False,'sameSite':'lax'},
                            {'name':'unrelated','value':'secret','domain':'.example.org','path':'/'}]}
        cookies=session_cookies('https://www.bilibili.com/video/BVtest/',records)
        self.assertEqual(len(cookies),2)
        self.assertTrue(cookies[0]['httpOnly']);self.assertFalse(cookies[1]['httpOnly'])

    def test_fallback_status_timeout_cancel_and_late_result(self):
        now=[0.0];bridge=BrowserBridge(lambda:now[0]);client='fallback-fixture-client'
        bridge.poll(client,'Edge');job=bridge.request('https://example.com/');bridge.poll(client,'Edge')
        self.assertEqual(bridge.fallback(job['id'],client),'https://example.com/')
        self.assertEqual(bridge.get(job['id'])['status'],'fallback')
        self.assertTrue(bridge.poll(client,'Edge',job['id'])['cancel'])
        self.assertIsNone(bridge.fallback(job['id'],client))
        bridge.cancel(job['id']);self.assertFalse(bridge.finish(job['id'],client,{'preview':{}})['accepted'])
