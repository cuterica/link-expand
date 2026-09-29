"""Bounded jobs for the explicitly paired, signed-in browser extension."""
from collections import OrderedDict
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time

from .metadata import PreviewError, normalize_url


def pairing_key():
    root = (Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'LinkExpand' if os.name == 'nt'
            else Path.home() / 'Library/Application Support/LinkExpand' if sys.platform == 'darwin'
            else Path.home() / '.local/share/LinkExpand')
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'browser-pairing-key'
    if not path.exists():
        fd, name = tempfile.mkstemp(prefix='.browser-pairing-', dir=root)
        try:
            with os.fdopen(fd, 'w') as output: output.write(secrets.token_urlsafe(32))
            try: os.link(name, path)
            except FileExistsError: pass
        finally: os.unlink(name)
    key = path.read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', key):
        raise PreviewError('浏览器配对文件无效，请删除 browser-pairing-key 后重新配对。')
    return key


class BrowserBridge:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.clients = {}
        self.jobs = OrderedDict()

    def _expire(self):
        now = self.clock()
        self.clients = {key: value for key, value in self.clients.items() if now - value['seen'] < 40}
        for job in self.jobs.values():
            if job['status'] in {'queued', 'running', 'fallback'} and now - job['created'] > (110 if job['status']=='fallback' else 65):
                job.update(status='error', error='浏览器读取超时，请确认浏览器仍在运行，或在网页中完成验证后重试。')

    def status(self):
        with self.lock:
            self._expire()
            return {'connected': bool(self.clients), 'browsers': sorted({v['browser'] for v in self.clients.values()})}

    def request(self, url, use_parser=True):
        url = normalize_url(url)
        with self.lock:
            self._expire()
            if not self.clients:
                raise PreviewError('浏览器未连接。请在 Edge / Chrome 扩展中完成一次配对并启用自动联动。')
            if sum(j['status'] in {'queued', 'running', 'fallback'} for j in self.jobs.values()) >= 4:
                raise PreviewError('浏览器正在处理其他链接，请稍后重试。')
            key = secrets.token_hex(12)
            self.jobs[key] = {'id': key, 'url': url, 'status': 'queued', 'created': self.clock(), 'client': None,'use_parser':bool(use_parser)}
            while len(self.jobs) > 32:
                terminal = next((k for k, j in self.jobs.items() if j['status'] not in {'queued', 'running', 'fallback'}), None)
                if terminal is None: break
                del self.jobs[terminal]
            return self.get(key)

    def poll(self, client, browser, active=None):
        if not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', client):
            raise PreviewError('浏览器标识无效。')
        with self.lock:
            self._expire()
            if client not in self.clients and len(self.clients) >= 8:
                raise PreviewError('已连接的浏览器过多。')
            self.clients[client] = {'seen': self.clock(), 'browser': 'Edge' if browser == 'Edge' else 'Chrome'}
            if active:
                job = self.jobs.get(active)
                return {'cancel': not job or job['client'] != client or job['status'] != 'running'}
            if any(j['client'] == client and j['status'] == 'running' for j in self.jobs.values()):
                return {'job': None}
            # When both are paired, use Windows' signed-in Edge by preference.
            if os.name == 'nt' and browser != 'Edge' and any(c['browser'] == 'Edge' for c in self.clients.values()):
                return {'job': None}
            for job in self.jobs.values():
                if job['status'] == 'queued':
                    job.update(status='running', client=client)
                    return {'job': {'id': job['id'], 'url': job['url']}}
            return {'job': None}

    def get(self, key):
        with self.lock:
            self._expire()
            job = self.jobs.get(key)
            if not job: raise PreviewError('浏览器任务已过期。')
            return {k: v for k, v in job.items() if k not in {'client', 'created'}}

    def accepts(self, key, client):
        with self.lock:
            self._expire()
            job = self.jobs.get(key)
            return bool(job and job['client'] == client and job['status'] in {'running','fallback'})

    def fallback(self,key,client):
        with self.lock:
            if not self.accepts(key,client) or self.jobs[key]['status']=='fallback':return None
            self.jobs[key].update(status='fallback',created=self.clock())
            return self.jobs[key]['url']

    def finish(self, key, client, result=None, error=None):
        with self.lock:
            if not self.accepts(key, client): return {'accepted': False}
            self.jobs[key].update(status='error' if error else 'complete', **({'error': str(error)[:500]} if error else {'result': result}))
            return {'accepted': True}

    def cancel(self, key):
        with self.lock:
            job = self.jobs.get(key)
            if job and job['status'] in {'queued', 'running','fallback'}: job['status'] = 'cancelled'
            return {'ok': True}
