import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from linkexpand.browser_bridge import BrowserBridge, pairing_key
from linkexpand.metadata import PreviewError


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.bridge = BrowserBridge(lambda: self.now)
        self.client = 'fixture-client-001'

    def connect(self):
        return self.bridge.poll(self.client, 'Edge')

    def test_offline_timeout_and_expired_heartbeat(self):
        with self.assertRaises(PreviewError): self.bridge.request('https://example.com/')
        self.connect()
        job = self.bridge.request('https://example.com/')
        self.bridge.poll(self.client, 'Edge')
        self.now += 66
        self.assertEqual(self.bridge.get(job['id'])['status'], 'error')
        self.assertFalse(self.bridge.status()['connected'])

    def test_claim_owner_cancel_and_no_late_result(self):
        self.connect()
        job = self.bridge.request('https://example.com/')
        claimed = self.connect()['job']
        self.assertEqual(claimed['id'], job['id'])
        self.assertFalse(self.bridge.finish(job['id'], 'different-client', {'preview': {}})['accepted'])
        self.bridge.cancel(job['id'])
        self.assertTrue(self.bridge.poll(self.client, 'Edge', job['id'])['cancel'])
        self.assertFalse(self.bridge.finish(job['id'], self.client, {'preview': {}})['accepted'])

    def test_finished_jobs_and_public_results(self):
        self.connect()
        job = self.bridge.request('example.com')
        self.connect()
        self.bridge.finish(job['id'], self.client, {'preview': {'title': 'Ready'}})
        result = self.bridge.get(job['id'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['result']['preview']['title'], 'Ready')
        self.assertNotIn('client', result)
        self.assertNotIn('created', result)

    def test_pairing_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder, patch('linkexpand.browser_bridge.Path.home', return_value=Path(folder)):
            first = pairing_key()
            self.assertEqual(first, pairing_key())
            self.assertEqual(len(first), 43)

