"""Independent bounded HTTP download engine: parallel ranges, retries and resume."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import hashlib
import http.client
import errno
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
from urllib.parse import urljoin, urlsplit

from .metadata import (PinnedHTTPConnection, PinnedHTTPSConnection, PreviewError,
                       normalize_url, public_addresses)
from .download_http import resource_url, checked_headers, scoped_headers, public_headers

MAX_VIDEO_BYTES = 500_000_000


class DownloadStopped(Exception):
    pass


class RangeUnsupported(PreviewError):
    pass


class RetryableHTTP(PreviewError):
    pass


@contextmanager
def open_video(url, headers=None, request_headers=None, credential_origin=None,allow_compressed=False):
    current = resource_url(url)
    request_headers = checked_headers(request_headers)
    credential_origin = credential_origin or current
    for _ in range(6):
        parts = urlsplit(current)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        address = public_addresses(parts.hostname, port)[0]
        factory = PinnedHTTPSConnection if parts.scheme == 'https' else PinnedHTTPConnection
        connection = factory(parts.hostname, port, address, 6)
        response=None
        try:
            connection.request('GET', parts.path + ('?' + parts.query if parts.query else ''), headers={
                'User-Agent': 'Mozilla/5.0', 'Accept-Encoding': 'identity',
                **scoped_headers(request_headers, current, credential_origin),
                **(headers or {})})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location:
                    raise PreviewError('视频跳转缺少地址。')
                current = resource_url(urljoin(current, location))
                continue
            if response.status in {429, 500, 502, 503, 504}:
                raise RetryableHTTP('视频服务器暂时繁忙，请重试。')
            if response.status not in {200, 206}:
                raise PreviewError(f'资源服务器返回 HTTP {response.status}，请检查地址或请求头后重试。')
            if not allow_compressed and response.getheader('Content-Encoding', 'identity') not in {'identity', ''}:
                raise PreviewError('视频响应使用了不支持的压缩格式。')
            response.resource_url = current
            yield response
            return
        finally:
            if response is not None:response.close()
            connection.close()
    raise PreviewError('视频服务器跳转次数过多。')


def probe_video(url, request_headers=None, credential_origin=None):
    with open_video(url, {'Range': 'bytes=0-0'}, request_headers, credential_origin) as response:
        content_range = re.fullmatch(r'bytes 0-0/(\d+)', response.getheader('Content-Range', ''))
        if response.status == 206 and not content_range:
            raise PreviewError('视频服务器返回了错误的分段信息。')
        length = response.getheader('Content-Length', '')
        size = int(content_range[1]) if content_range else int(length) if length.isdigit() else None
        if size is not None and size <= 0:
            raise PreviewError('视频文件为空。')
        etag = response.getheader('ETag', '')
        validator = etag if etag and not etag.startswith('W/') else response.getheader('Last-Modified', '')
        return {'url': url, 'size': size, 'range': bool(content_range), 'validator': validator,
                'mime':response.getheader('Content-Type','').split(';',1)[0].lower()}


def downloads_directory():
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                location = os.path.expandvars(winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')[0])
                return Path(location) / 'LinkExpand-videos'
        except OSError:
            return Path(os.environ['USERPROFILE']) / 'Downloads' / 'LinkExpand-videos'
    return Path.home() / 'Downloads' / 'LinkExpand-videos'


class DownloadJob:
    def __init__(self, manager, video, source, job_id=None, saved=None):
        self.manager, self.video, self.source = manager, video, source
        self.id = job_id or secrets.token_hex(12)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.stop_reason = ''
        self.thread = None
        self.status = 'paused'
        self.error = ''
        self.asset = None
        self.positions = []
        self.current = []
        self.plan = []
        self.downloaded = 0
        self.filename = ''
        self.quality = ''
        self.started = 0.0
        self.initial_bytes = 0
        self.sha256 = ''
        self.stream_state = {}
        self.selected_variant = None
        self.credentials_missing = False
        options=video.get('options') or {}
        self.workers=min(16,max(1,int(options.get('connections',manager.workers))))
        self.speed_limit=max(0,int(options.get('speed_limit',0)))
        self.rate_lock=threading.Lock();self.next_transfer=0.
        if saved:
            for name in ['asset', 'positions', 'plan', 'downloaded', 'filename', 'quality', 'sha256', 'stream_state', 'credentials_missing']:
                setattr(self, name, saved.get(name, getattr(self, name)))
            self.current = list(self.positions)
            self.status = 'complete' if saved.get('status') == 'complete' and self.valid_file() else 'paused'

    @property
    def part(self):
        return self.manager.storage / (self.id + '.part')

    @property
    def state_file(self):
        return self.manager.storage / (self.id + '.json')

    @property
    def file(self):
        return self.manager.root / self.filename if self.filename else None

    def valid_file(self):
        path = self.file
        try:
            return bool(path and not path.is_symlink() and path.is_file()
                        and 0 < path.stat().st_size <= self.manager.limit
                        and path.stat().st_size == self.downloaded)
        except OSError:
            return False

    def snapshot(self):
        with self.lock:
            completed = sum(self.current) if self.plan else self.downloaded
            total = self.asset.get('size') if self.asset else None
            elapsed = time.monotonic() - self.started if self.started else 0
            speed = max(0, completed - self.initial_bytes) / elapsed if elapsed > 0 and self.status == 'downloading' else 0
            return {'id': self.id, 'source': self.source, 'video_index': self.video['index'],
                    'status': self.status, 'downloaded': completed, 'total': total,
                    'progress': min(1, completed / total) if total else None, 'speed': int(speed),
                    'quality': self.quality, 'filename': self.filename, 'error': self.error,
                    'path': str(self.file) if self.status == 'complete' and self.valid_file() else None,
                    'resumable': bool(self.asset and self.asset['range'] and self.asset['validator']),
                    'sha256': self.sha256, 'kind': self.video.get('kind','video'),
                    'fragments': len(self.stream_state.get('completed',{})),
                    'credentials_missing': self.credentials_missing}

    def save(self):
        with self.lock:
            safe_video=dict(self.video)
            safe_video['variants']=[dict(variant,headers=public_headers(variant.get('headers',{})),
                audio_headers=public_headers(variant.get('audio_headers',{}))) for variant in self.video['variants']]
            missing=any(any(key.lower() in {'cookie','authorization'} for key in request_headers)
                for variant in self.video['variants'] for request_headers in [variant.get('headers',{}),variant.get('audio_headers',{})])
            data = {'id': self.id, 'video': safe_video, 'source': self.source, 'status': self.status,
                    'asset': self.asset, 'positions': self.positions, 'plan': self.plan,
                    'downloaded': sum(self.positions) if self.plan else self.downloaded,
                    'filename': self.filename, 'quality': self.quality, 'sha256': self.sha256,
                    'stream_state': self.stream_state, 'credentials_missing': missing or self.credentials_missing}
            temporary = self.state_file.with_suffix('.tmp')
            try:
                temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                temporary.replace(self.state_file)
            except OSError:
                raise PreviewError('无法保存下载进度，请检查磁盘空间和文件夹权限。') from None

    def check_stop(self):
        if self.stop.is_set():
            raise DownloadStopped()

    def throttle(self,size):
        if not self.speed_limit:return
        with self.rate_lock:
            now=time.monotonic();scheduled=max(now,self.next_transfer)
            self.next_transfer=scheduled+size/self.speed_limit
        if self.stop.wait(max(0,scheduled-now)):self.check_stop()

    def launch(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            if self.status == 'complete' and self.valid_file():
                return
            self.stop.clear()
            self.next_transfer=0.
            self.stop_reason = ''
            self.error = ''
            self.status = 'queued'
            self.thread = threading.Thread(target=self.run, daemon=True, name='LinkExpand-video-' + self.id)
            self.thread.start()

    def pause(self, cancel=False):
        with self.lock:
            if self.status == 'complete':
                return
            self.stop_reason = 'cancel' if cancel else 'pause'
            self.stop.set()
            if self.thread and self.thread.is_alive():
                self.status = 'cancelling' if cancel else 'pausing'
            else:
                self.status = 'cancelled' if cancel else 'paused'
                if cancel:
                    self.part.unlink(missing_ok=True)
                    shutil.rmtree(self.manager.storage/(self.id+'-segments'),ignore_errors=True)
                    self.stream_state={}
                    self.positions, self.current, self.plan = [], [], []
                    self.downloaded = 0
                self.save()

    def select_asset(self):
        oversized = False
        last_error = None
        for variant in self.video['variants']:
            self.check_stop()
            try:
                candidate = probe_video(variant['url'],self.request_headers(variant),self.video.get('credential_origin') or self.source)
            except (OSError, http.client.HTTPException, PreviewError) as error:
                last_error = error
                continue
            if candidate['size'] is not None and candidate['size'] > self.manager.limit:
                oversized = True
                continue
            self.quality = variant.get('quality') or '原始画质'
            self.selected_variant=variant
            author = re.sub(r'[^A-Za-z0-9_-]', '_', self.video.get('author') or 'x')[:40]
            dimensions = re.sub(r'[^0-9x]', '', self.quality.replace('×', 'x')) or 'video'
            self.filename = f'X_{author}_{self.video["post_id"]}_{self.video["index"]}_{dimensions}_{self.id[:6]}.mp4'
            if self.video.get('kind') in {'file','audio','video'} and self.video.get('filename'):
                from .media_resolver import safe_filename
                name=safe_filename(self.video['filename'])
                base=Path(name).stem or 'download';suffix=Path(name).suffix or ('.mp4' if self.video.get('kind')=='video' else '.bin')
                media_suffix={'video/webm':'.webm','audio/webm':'.webm','video/mp4':'.mp4','audio/mp4':'.m4a',
                              'audio/mpeg':'.mp3','audio/ogg':'.ogg','video/quicktime':'.mov'}.get(candidate.get('mime'))
                if media_suffix and self.video.get('kind') in {'video','audio'}:suffix=media_suffix
                self.filename=f'{base}_{self.id[:6]}{suffix}'
            if self.file.exists():
                self.filename = self.file.stem + '_' + secrets.token_hex(3) + self.file.suffix
            return candidate
        if oversized:
            raise PreviewError('可用的视频版本均超过 500 MB，已停止下载。')
        raise PreviewError(str(last_error) if last_error else '没有可下载的 MP4 视频。')

    def request_headers(self,variant=None):
        variant=variant or self.selected_variant or (self.video['variants'][0] if self.video.get('variants') else {})
        headers=dict(variant.get('headers') or {})
        if not headers and not self.video.get('kind'):headers['Referer']='https://x.com/'
        return headers

    def run_stream(self):
        from .streaming import stream_download
        self.positions,self.current,self.plan=[],[],[]
        self.started=time.monotonic();self.initial_bytes=self.downloaded
        self.filename=f'Media_{self.video["post_id"]}_{self.video["index"]}_{self.id[:6]}.mp4'
        if self.video.get('filename'):
            from .media_resolver import safe_filename
            self.filename=Path(safe_filename(self.video['filename'])).stem+'_'+self.id[:6]+'.mp4'
        if self.file.exists():self.filename=self.filename[:-4]+'_'+secrets.token_hex(3)+'.mp4'
        last_error=None
        for variant in self.video['variants']:
            self.check_stop();self.selected_variant=variant
            try:
                stream_download(self,variant);return
            except PreviewError as error:
                last_error=error
                if '超过' not in str(error):raise
                shutil.rmtree(self.manager.storage/(self.id+'-segments'),ignore_errors=True)
                self.stream_state={};self.downloaded=0
        raise last_error or PreviewError('没有可处理的流媒体清晰度。')

    def run(self):
        acquired = False
        try:
            while not self.manager.slots.acquire(timeout=.2):
                self.check_stop()
            acquired = True
            self.check_stop()
            self.status = 'resolving'
            if self.video.get('kind') in {'hls','dash','pair'}:
                self.run_stream()
                candidate=None
            else:
                candidate = self.select_asset()
            # Only resume the identical version of a resource.
            can_resume = bool(candidate and self.part.is_file() and candidate == self.asset and candidate['validator']
                          and self.plan and candidate['range'] and candidate['size'] == self.part.stat().st_size)
            if candidate:self.asset = candidate
            if candidate and not can_resume:
                self.part.unlink(missing_ok=True)
                self.positions, self.current, self.plan = [], [], []
                self.downloaded = 0
            if candidate and candidate['size'] and shutil.disk_usage(self.manager.root).free < candidate['size'] + 8 * 1024 * 1024:
                raise PreviewError('下载文件夹所在磁盘的剩余空间不足。')
            if candidate:
                self.status = 'downloading'
                self.started = time.monotonic()
                self.initial_bytes = sum(self.positions) if self.plan else self.downloaded
                self.save()
            if candidate and candidate['range'] and candidate['size']:
                try:
                    self.parallel()
                except RangeUnsupported:
                    self.check_stop()
                    self.asset['range'] = False
                    self.positions, self.current, self.plan = [], [], []
                    self.part.unlink(missing_ok=True)
                    self.downloaded = 0
                    self.sequential()
            elif candidate:
                self.sequential()
            self.check_stop()
            self.status = 'verifying'
            size = self.part.stat().st_size
            if not 0 < size <= self.manager.limit or (self.asset['size'] is not None and size != self.asset['size']):
                raise PreviewError('视频文件不完整或超过 500 MB，未保存成品。')
            with self.part.open('rb') as video:
                header=video.read(16)
                require_mp4=self.video.get('kind','video') in {'hls','dash','pair'} or self.file.suffix.lower() in {'.mp4','.m4a','.m4v','.mov'}
                if require_mp4 and header[4:8] != b'ftyp':
                    raise PreviewError('下载结果不是完整 MP4 视频。')
                if self.file.suffix.lower()=='.webm' and header[:4]!=b'\x1aE\xdf\xa3':raise PreviewError('下载结果不是完整 WebM 媒体。')
                video.seek(0)
                digest = hashlib.sha256()
                while chunk := video.read(1024 * 1024):
                    self.check_stop()
                    digest.update(chunk)
            self.sha256 = digest.hexdigest()
            self.check_stop()
            # Hard links publish without overwriting any existing user file.
            os.link(self.part, self.file)
            self.part.unlink()
            self.downloaded = size
            self.asset['size'] = size
            self.status = 'complete'
            shutil.rmtree(self.manager.storage/(self.id+'-segments'),ignore_errors=True)
            self.stream_state={}
            self.save()
        except DownloadStopped:
            self.status = 'cancelled' if self.stop_reason == 'cancel' else 'paused'
            if self.status == 'cancelled':
                self.part.unlink(missing_ok=True)
                shutil.rmtree(self.manager.storage/(self.id+'-segments'),ignore_errors=True)
                self.stream_state={}
                self.positions, self.current, self.plan = [], [], []
                self.downloaded = 0
            try:
                self.save()
            except PreviewError:
                pass
        except Exception as error:
            self.error = str(error) if isinstance(error, PreviewError) else '视频下载失败，可以继续重试。'
            self.status = 'error'
            if isinstance(error, PreviewError) and ('超过' in str(error) or '不是完整 MP4' in str(error)):
                self.part.unlink(missing_ok=True)
                self.positions, self.current, self.plan = [], [], []
                self.downloaded = 0
            try:
                self.save()
            except PreviewError:
                pass
        finally:
            if acquired:
                self.manager.slots.release()

    def parallel(self):
        total = self.asset['size']
        if not self.plan:
            chunk_size = min(self.manager.chunk_size, max(65536, math.ceil(total / self.workers)))
            self.plan = [(start, min(total - 1, start + chunk_size - 1)) for start in range(0, total, chunk_size)]
            self.positions = [0] * len(self.plan)
            self.current = list(self.positions)
            with self.part.open('wb') as partial:
                partial.truncate(total)
            self.save()
        abort = threading.Event()
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix='LinkExpand-range') as pool:
            futures = [pool.submit(self.segment, index, abort) for index in range(len(self.plan))]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                abort.set()
                for future in futures:
                    future.cancel()
                raise

    def segment(self, index, abort):
        start, end = self.plan[index]
        position = self.positions[index]
        for attempt in range(3):
            self.check_stop()
            if abort.is_set() or position == end - start + 1:
                return
            offset = start + position
            headers = {'Range': f'bytes={offset}-{end}'}
            if self.asset['validator']:
                headers['If-Range'] = self.asset['validator']
            try:
                with self.part.open('r+b', buffering=0) as partial, open_video(self.asset['url'], headers,self.request_headers(),self.video.get('credential_origin') or self.source) as response:
                    if response.status == 200:
                        raise RangeUnsupported('视频服务器不支持这次分段请求，改为单连接下载。')
                    expected = f'bytes {offset}-{end}/{self.asset["size"]}'
                    if response.getheader('Content-Range', '') != expected:
                        raise PreviewError('视频内容已改变或分段响应错误，请重新下载。')
                    partial.seek(offset)
                    try:
                        while position <= end - start:
                            self.check_stop()
                            if abort.is_set():
                                return
                            chunk = response.read1(min(65536, end - start + 1 - position))
                            if not chunk:
                                raise OSError('Incomplete range')
                            self.throttle(len(chunk))
                            written = partial.write(chunk)
                            if written != len(chunk):
                                raise OSError('Incomplete disk write')
                            position += len(chunk)
                            with self.lock:
                                self.current[index] = position
                    finally:
                        partial.flush()
                        os.fsync(partial.fileno())
                        with self.lock:
                            self.positions[index] = position
                            self.current[index] = position
                        self.save()
                return
            except (OSError, http.client.HTTPException, RetryableHTTP) as error:
                if isinstance(error, OSError) and error.errno in {errno.ENOSPC, errno.EACCES, errno.EROFS, getattr(errno, 'EDQUOT', -1)}:
                    raise PreviewError('磁盘写入失败，请检查剩余空间和权限。') from None
                if attempt == 2:
                    raise PreviewError('网络中断，已保存下载进度，可点击继续下载。') from None
                self.stop.wait(.3 * (attempt + 1))

    def sequential(self):
        self.downloaded = 0
        for attempt in range(3):
            self.check_stop()
            try:
                with open_video(self.asset['url'],request_headers=self.request_headers(),credential_origin=self.video.get('credential_origin') or self.source) as response, self.part.open('wb') as partial:
                    length = response.getheader('Content-Length', '')
                    if length.isdigit() and int(length) > self.manager.limit:
                        raise PreviewError('视频超过 500 MB，已停止下载。')
                    self.downloaded = 0
                    while True:
                        self.check_stop()
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        if self.downloaded + len(chunk) > self.manager.limit:
                            raise PreviewError('视频超过 500 MB，已停止下载。')
                        self.throttle(len(chunk))
                        partial.write(chunk)
                        self.downloaded += len(chunk)
                    partial.flush()
                    os.fsync(partial.fileno())
                return
            except (OSError, http.client.HTTPException, RetryableHTTP) as error:
                if isinstance(error, OSError) and error.errno in {errno.ENOSPC, errno.EACCES, errno.EROFS, getattr(errno, 'EDQUOT', -1)}:
                    raise PreviewError('磁盘写入失败，请检查剩余空间和权限。') from None
                if attempt == 2:
                    raise PreviewError('网络中断，服务器不支持续传，请重试。') from None
                self.stop.wait(.3 * (attempt + 1))


class DownloadManager:
    def __init__(self, root=None, storage=None, limit=MAX_VIDEO_BYTES, workers=4, chunk_size=4*1024*1024):
        self.root = Path(root) if root else downloads_directory()
        self.storage = Path(storage) if storage else self.root / '.linkexpand-tasks'
        self.limit, self.workers, self.chunk_size = limit, workers, chunk_size
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.RLock()
        self.jobs = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self.storage.mkdir(parents=True, exist_ok=True)
        for file in sorted(self.storage.glob('*.json'), key=lambda file: file.stat().st_mtime, reverse=True)[:32]:
            try:
                if file.stat().st_size > 4 * 1024 * 1024 or not re.fullmatch(r'[a-f0-9]{24}', file.stem):
                    continue
                data = json.loads(file.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or not isinstance(data.get('source'), str) or not isinstance(data.get('video'), dict):
                    continue
                if data['id'] != file.stem or (data.get('filename') and Path(data['filename']).name != data['filename']):
                    continue
                resource_url(data['source'])
                for variant in data['video']['variants']:
                    resource_url(variant['url']);checked_headers(variant.get('headers'))
                    checked_headers(variant.get('audio_headers'))
                    if variant.get('audio_url'):resource_url(variant['audio_url'])
                stream=data.get('stream_state') or {}
                if not isinstance(stream,dict) or not isinstance(stream.get('completed',{}),dict):continue
                if len(stream.get('completed',{}))>20000:continue
                if any(not isinstance(record,dict) or not isinstance(record.get('size'),int)
                       or not 0<=record['size']<=self.limit or not re.fullmatch(r'[a-f0-9]{64}',str(record.get('sha256','')))
                       for record in stream.get('completed',{}).values()):continue
                asset = data.get('asset')
                if asset:
                    resource_url(asset['url'])
                    size = asset.get('size')
                    if size is not None and (not isinstance(size, int) or not 0 < size <= self.limit):
                        continue
                    if not isinstance(asset.get('range'), bool) or not isinstance(asset.get('validator'), str):
                        continue
                    if len(asset['validator']) > 512 or re.search(r'[\r\n]', asset['validator']):
                        continue
                plan, positions = data.get('plan') or [], data.get('positions') or []
                if len(plan) != len(positions) or len(plan) > 8192 or (plan and not asset):
                    continue
                expected_start = 0
                invalid = False
                for (start, end), position in zip(plan, positions):
                    if (not all(isinstance(value, int) for value in [start, end, position])
                            or start != expected_start or not 0 <= position <= end - start + 1 or end < start):
                        invalid = True
                        break
                    expected_start = end + 1
                if invalid or (plan and expected_start != asset['size']) or file.is_symlink():
                    continue
                job = DownloadJob(self, data['video'], data['source'], file.stem, data)
                if job.part.is_symlink():
                    continue
                self.jobs[job.id] = job
            except (OSError, ValueError, KeyError, TypeError, PreviewError):
                continue

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            raise PreviewError('找不到视频任务，请重新识别推文。')
        return job

    def start(self, video, source):
        with self.lock:
            for job in self.jobs.values():
                if job.video['post_id'] == video['post_id'] and job.video['index'] == video['index'] and job.status != 'cancelled':
                    if job.status in {'paused', 'error'}:
                        job.video, job.source = video, source
                        job.credentials_missing=False
                        options=video.get('options') or {}
                        job.workers=min(16,max(1,int(options.get('connections',self.workers))))
                        job.speed_limit=max(0,int(options.get('speed_limit',0)))
                    job.launch()
                    return job.snapshot()
            if len(self.jobs) >= 32:
                inactive = [job for job in self.jobs.values() if job.status in {'complete', 'cancelled', 'error'}]
                if not inactive:
                    raise PreviewError('未完成任务过多，请先完成或取消旧任务。')
                previous = inactive[0]
                previous.state_file.unlink(missing_ok=True)
                previous.part.unlink(missing_ok=True)
                del self.jobs[previous.id]
            job = DownloadJob(self, video, source)
            self.jobs[job.id] = job
            job.launch()
            return job.snapshot()

    def list(self):
        with self.lock:
            return [job.snapshot() for job in self.jobs.values()]

    def close(self):
        jobs = list(self.jobs.values())
        for job in jobs:
            if job.thread and job.thread.is_alive():
                job.pause()
        deadline = time.monotonic() + 9
        for job in jobs:
            if job.thread and job.thread.is_alive():
                job.thread.join(max(0, deadline - time.monotonic()))
