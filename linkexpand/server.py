"""Loopback-only interface for local link summaries and visual previews."""

from __future__ import annotations

import argparse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit, quote
import urllib.request
import webbrowser

from . import __version__
from .cards import original_visual, render_card, thumbnail
from .clipboard import copy_rich, copy_video_file
from .metadata import Preview, PreviewError, clean_text, get_preview, normalize_url, plain_text
from .sharing import rich_html
from .visuals import attach_visual
from .video_downloads import DownloadManager, MAX_VIDEO_BYTES
from .browser_bridge import BrowserBridge, pairing_key

STATIC = Path(__file__).parent / "static"


def open_page(url):
    if sys.platform=='darwin':subprocess.Popen(['open',url])
    else:webbrowser.open(url)


class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.fetch_slots = threading.BoundedSemaphore(2)
        self.records = OrderedDict()
        self._downloads = None
        self.catalogs = OrderedDict()
        self.browser_preview_id = None
        self.bridge = BrowserBridge()
        self.bridge_token = pairing_key()
        self.real_browser_slot = threading.BoundedSemaphore(1)
        self.native_ui=None

    def real_browser(self,url,session=None):
        if not self.real_browser_slot.acquire(blocking=False):
            raise PreviewError('真实浏览器正在读取另一条链接，请稍后重试。')
        try:
            from .owned_process import run_worker
            command=([sys.executable,'--real-browser-worker'] if getattr(sys,'frozen',False)
                     else [sys.executable,'-m','linkexpand.browser_real'])
            process=run_worker(command,json.dumps({'url':normalize_url(url),'session':session}),75)
            data=json.loads(process.stdout)
            if process.returncode or data.get('error'):raise PreviewError(data.get('error','真实浏览器读取失败。'))
            preview=self.browser_preview(data['preview'],data['source'])
            catalog=None
            if data.get('candidates'):
                from .media_resolver import imported_candidates
                result=imported_candidates(data['source'],data['candidates']);result['preview']=preview
                catalog=self.catalog(result)
            return {'preview':preview,'catalog':catalog,'method':'playwright-visible'}
        except subprocess.TimeoutExpired:raise PreviewError('真实浏览器读取超时，已关闭软件自己的浏览器窗口。') from None
        except (OSError,ValueError) as error:
            if isinstance(error,PreviewError):raise
            raise PreviewError('无法启动真实浏览器回退，请确认已安装 Edge / Chrome。') from None
        finally:self.real_browser_slot.release()

    def bilibili_public(self,url):
        from .bilibili_parser import parse
        data=parse(url)
        preview=self.browser_preview(data['preview'],data['source'])
        catalog=data['catalog']
        if catalog:
            catalog['preview']=preview;catalog=self.catalog(catalog)
        return {'preview':preview,'catalog':catalog,'method':data['method']}

    def fallback_job(self,key,client,session=None):
        url=self.bridge.fallback(key,client)
        if not url:return {'accepted':False}
        def read():
            try:
                from .bilibili_parser import public_reference
                if self.bridge.get(key).get('use_parser',True) and public_reference(url):
                    try:result=self.bilibili_public(url)
                    except PreviewError:result=self.real_browser(url,session)
                else:result=self.real_browser(url,session)
                self.bridge.finish(key,client,result)
            except PreviewError as error:self.bridge.finish(key,client,error=error)
        threading.Thread(target=read,daemon=True).start()
        return {'accepted':True,'fallback':'playwright-visible'}

    def browser_preview(self,data,source):
        if not isinstance(data,dict):raise PreviewError('浏览器网页预览数据无效。')
        url=normalize_url(str(data.get('url','')))
        if url!=normalize_url(source):raise PreviewError('浏览器预览与当前页面地址不一致。')
        title=clean_text(str(data.get('title','')),180)
        if not title:raise PreviewError('浏览器没有提供页面标题。')
        domain=urlsplit(url).hostname.removeprefix('www.')
        from .metadata import concise_summary,fetch_resource,MAX_IMAGE
        preview=Preview(url,title,concise_summary(str(data.get('description',''))),domain,
                        clean_text(str(data.get('site_name','')),80) or domain,summary_source=clean_text(str(data.get('summary_source','浏览器网页摘要')),60))
        image=str(data.get('image_url',''))
        image_data=data.get('image_data')
        if image_data:
            import base64
            from io import BytesIO
            from PIL import Image
            if not isinstance(image_data,str) or not re.match(r'^data:image/(png|jpeg|webp);base64,',image_data):
                raise PreviewError('浏览器图片格式无效。')
            try:
                content=base64.b64decode(image_data.split(',',1)[1],validate=True)
                if len(content)>2_000_000:raise ValueError('Image too large')
                with Image.open(BytesIO(content)) as check:
                    if check.width*check.height>16_000_000:raise ValueError('Image too large')
                    check.verify()
                preview.image=content;preview.visual_source=clean_text(str(data.get('visual_source','浏览器封面')),40)
            except (ValueError,OSError,Image.DecompressionBombError) as error:raise PreviewError('浏览器图片无效或过大。') from error
        elif image:
            try:
                preview.image=fetch_resource(normalize_url(image),MAX_IMAGE,timeout=8,headers={'Referer':url,'User-Agent':'Mozilla/5.0'}).body
                preview.visual_source=clean_text(str(data.get('visual_source','浏览器封面')),60)
            except PreviewError:preview.warnings.append('浏览器的标题和摘要已导入，封面暂时无法读取。')
        visual=original_visual(preview);preview.image=visual;cover=thumbnail(preview);preview.image=None
        result=self.put(preview,cover,visual)
        with self.lock:self.browser_preview_id=result['id']
        return result

    def catalog(self,result):
        key=secrets.token_hex(12)
        with self.lock:
            self.catalogs[key]=result
            while len(self.catalogs)>24:self.catalogs.popitem(last=False)
        return {'id':key,'source':result['source'],'title':result['title'],
                'preview':result.get('preview'),
                'resources':[{'index':item['index'],'kind':item.get('kind','video'),
                              'filename':item.get('filename',''),'qualities':[variant.get('quality','') for variant in item['variants']],
                              'url':item['variants'][0]['url']} for item in result['resources']]}

    def get_catalog(self,key):
        with self.lock:result=self.catalogs.get(key)
        if not result:raise PreviewError('下载资源列表已过期，请重新识别。')
        return result

    @property
    def downloads(self):
        with self.lock:
            if self._downloads is None:
                self._downloads = DownloadManager()
            return self._downloads

    def close(self):
        from .owned_process import stop_workers
        stop_workers()
        if self._downloads:
            self._downloads.close()

    def put(self, preview, cover=None, visual=None):
        png = render_card(preview, cover)
        key = secrets.token_hex(12)
        record = {"preview": preview, "cover": cover, "png": png, "visual": visual}
        with self.lock:
            self.records[key] = record
            while len(self.records) > 24:
                self.records.popitem(last=False)
        return self.serialize(key, record)

    def serialize(self, key, record):
        preview = record["preview"]
        return {"id": key, "url": preview.url, "title": preview.title,
                "description": preview.description, "domain": preview.domain,
                "site_name": preview.site_name, "warnings": preview.warnings,
                "visual_source": preview.visual_source, "summary_source": preview.summary_source,
                "text": plain_text(preview), "image": f"/assets/{key}/card.png",
                "html": rich_html(preview, record["cover"] or record['png']),
                "cover": f"/assets/{key}/cover.png" if record["cover"] else None,
                "visual": f"/assets/{key}/visual.png" if record["visual"] else None,
                "videos": [{'index': video['index'], 'duration_ms': video['duration_ms'],
                            'qualities': [variant['quality'] for variant in video['variants']]}
                           for video in preview.videos], 'selected_video': preview.selected_video}

    def get_record(self, key):
        with self.lock:
            record = self.records.get(key)
        if not record:
            raise PreviewError("预览已过期，请重新生成。")
        return record

    def create(self, url, use_parser=True):
        if not self.fetch_slots.acquire(blocking=False):
            raise PreviewError("正在处理其他链接，请稍后重试。")
        try:
            try:preview = get_preview(url)
            except PreviewError as first_error:
                from .bilibili_parser import public_reference
                if use_parser and public_reference(url):
                    try:
                        result=self.bilibili_public(url);preview=dict(result['preview']);preview['catalog']=result['catalog']
                        return preview
                    except PreviewError:pass
                try:
                    result=self.real_browser(url)
                    preview=dict(result['preview']);preview['catalog']=result['catalog']
                    return preview
                except PreviewError as second_error:
                    raise PreviewError(f'{first_error} 自动回退结果：{second_error}') from None
            attach_visual(preview)
            visual = original_visual(preview)
            preview.image = visual
            cover = thumbnail(preview)
            preview.image = None
            return self.put(preview, cover, visual)
        finally:
            self.fetch_slots.release()

    def manual(self, data, original=None):
        url = normalize_url(str(data.get("url", "")))
        domain = urlsplit(url).hostname.removeprefix("www.")
        title = clean_text(str(data.get("title", "")), 180)
        if not title:
            raise PreviewError("请填写卡片标题。")
        preview = Preview(url, title, clean_text(str(data.get("description", "")), 500),
                          domain, clean_text(str(data.get("site_name", domain)), 80) or domain)
        if original:
            preview.visual_source = original["preview"].visual_source
            if url == original['preview'].url:
                preview.videos = original['preview'].videos
                preview.selected_video = original['preview'].selected_video
        preview.summary_source = "手动编辑"
        return self.put(preview, original["cover"] if original else None,
                        original["visual"] if original else None)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows must not allow multiple processes to bind the same app address.
    allow_reuse_address = os.name != 'nt'

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, fmt, *args):
        # Keep private URLs, tokens, and browser requests out of console logs.
        pass

    def allowed(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in hosts:
            self.respond_json(403, {"error": "仅允许本机访问。"})
            return False
        extension_origin=self.extension_origin()
        if extension_origin:return True
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.respond_json(403, {"error": "不允许跨站访问。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in hosts}:
            self.respond_json(403, {"error": "不允许跨站访问。"})
            return False
        return True

    def extension_origin(self):
        value=self.headers.get('Origin','')
        if urlsplit(self.path).path in {'/api/capture/import','/api/browser/poll','/api/browser/result'} and re.fullmatch(r'chrome-extension://[a-p]{32}',value):return value
        return None

    def do_OPTIONS(self):
        if not self.allowed():return
        origin=self.extension_origin()
        if not origin:
            self.respond_json(403,{'error':'不允许跨站请求。'});return
        self.respond(204,b'','text/plain',{
            'Access-Control-Allow-Headers':'Content-Type, X-Local-Token','Access-Control-Allow-Methods':'POST, OPTIONS'})

    def authenticated(self):
        token = self.headers.get("X-Local-Token", "")
        bridge_route=urlsplit(self.path).path in {'/api/capture/import','/api/browser/poll','/api/browser/result'}
        paired=bridge_route and hmac.compare_digest(token,self.server.app.bridge_token)
        if not paired and not hmac.compare_digest(token, self.server.app.token):
            self.respond_json(403, {"error": "会话已更新，请刷新页面。"})
            return False
        return True

    def respond(self, status, content, mime, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if self.extension_origin():self.send_header('Access-Control-Allow-Origin',self.extension_origin())
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def respond_json(self, status, data):
        self.respond(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if not self.allowed():
            return
        path = urlsplit(self.path).path
        app = self.server.app
        if path == "/":
            self.respond(200, (STATIC / "index.html").read_bytes().replace(
                b"__LOCAL_TOKEN__", app.token.encode()), "text/html; charset=utf-8")
        elif path in {"/app.js", "/style.css", "/favicon.svg"}:
            mime = {"/app.js": "text/javascript; charset=utf-8", "/style.css": "text/css; charset=utf-8",
                    "/favicon.svg": "image/svg+xml"}[path]
            self.respond(200, (STATIC / path[1:]).read_bytes(), mime)
        elif path == '/api/capabilities':
            if self.authenticated():
                self.respond_json(200, {'native_rich': os.name == 'nt' or sys.platform=='darwin', 'version': __version__,
                                       'copy_video': os.name == 'nt' or sys.platform=='darwin', 'platform':sys.platform,'max_video_bytes': MAX_VIDEO_BYTES,
                                       'ffmpeg':bool(__import__('linkexpand.streaming',fromlist=['ffmpeg_path']).ffmpeg_path()),
                                       'native_shell':self.server.app.native_ui is not None,'native_image_copy':sys.platform=='darwin',
                                       'browser_pairing_key':app.bridge_token})
        elif path=='/api/browser/status' or re.fullmatch(r'/api/browser/jobs/[a-f0-9]{24}',path):
            if not self.authenticated():return
            try:self.respond_json(200,app.bridge.status() if path=='/api/browser/status' else app.bridge.get(path.rsplit('/',1)[1]))
            except PreviewError as error:self.respond_json(404,{'error':str(error)})
        elif path == '/api/health':
            self.respond_json(200, {'app': 'link-expand', 'version': __version__})
        elif path=='/api/capture/preview':
            if not self.authenticated():return
            try:
                with app.lock:key=app.browser_preview_id
                if not key:raise PreviewError('尚未导入浏览器网页预览。请在已登录的 Edge / Chrome 中点击扩展里的“导入当前网页预览”。')
                self.respond_json(200,app.serialize(key,app.get_record(key)))
            except PreviewError as error:self.respond_json(404,{'error':str(error)})
        elif path == '/api/video/jobs' or re.fullmatch(r'/api/video/jobs/[a-f0-9]{24}', path):
            if not self.authenticated():
                return
            try:
                result = app.downloads.list() if path == '/api/video/jobs' else app.downloads.get(path.rsplit('/', 1)[1]).snapshot()
                self.respond_json(200, result)
            except PreviewError as error:
                self.respond_json(404, {'error': str(error)})
        elif path=='/api/download/catalogs':
            if not self.authenticated():return
            with app.lock:
                result=[{'id':key,'source':value['source'],'title':value['title'],
                         'count':len(value['resources'])} for key,value in app.catalogs.items()]
            self.respond_json(200,result)
        elif re.fullmatch(r'/downloads/[a-f0-9]{24}(?:\.mp4|/file)', path):
            try:
                job = app.downloads.get(path.split('/')[2].removesuffix('.mp4'))
                if job.status != 'complete' or not job.valid_file():
                    raise PreviewError('视频尚未下载完成。')
                with job.file.open('rb') as video:
                    size = os.fstat(video.fileno()).st_size
                    if size <= 0:
                        raise PreviewError('下载文件为空。')
                    self.send_response(200)
                    self.send_header('Content-Type', mimetypes.guess_type(job.filename)[0] or 'application/octet-stream')
                    self.send_header('Content-Length', str(size))
                    fallback=job.filename if job.filename.isascii() else 'download'+job.file.suffix
                    self.send_header('Content-Disposition', f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(job.filename,safe="")}')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    while chunk := video.read(65536):
                        self.wfile.write(chunk)
            except PreviewError as error:
                self.respond_json(404, {'error': str(error)})
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif re.fullmatch(r"/assets/[a-f0-9]{24}/(card|cover|visual)\.png", path):
            # Assets are session-local, unguessable IDs; no arbitrary file paths.
            _, _, key, filename = path.split("/")
            try:
                record = app.get_record(key)
                content = record[{"card.png": "png", "cover.png": "cover", "visual.png": "visual"}[filename]]
                if not content:
                    raise PreviewError("没有缩略图。")
                self.respond(200, content, "image/png")
            except PreviewError as error:
                self.respond_json(404, {"error": str(error)})
        else:
            self.respond_json(404, {"error": "页面不存在。"})

    def do_POST(self):
        if not self.allowed() or not self.authenticated():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            path = urlsplit(self.path).path
            if not 0 < length <= (3_000_000 if path=='/api/browser/result' else 65536):
                raise PreviewError("请求内容过大或为空。")
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise PreviewError("仅支持 JSON 请求。")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise PreviewError("请求格式有误。")
            app = self.server.app
            path = urlsplit(self.path).path
            if path=='/api/browser/request':
                result=app.bridge.request(str(data.get('url','')),data.get('bili_parser') is not False)
            elif path=='/api/browser/cancel':
                result=app.bridge.cancel(str(data.get('id','')))
            elif path=='/api/browser/poll':
                result=app.bridge.poll(data.get('client_id'),data.get('browser'),data.get('active'))
            elif path=='/api/browser/result':
                key=str(data.get('id',''));client=str(data.get('client_id',''))
                if not app.bridge.accepts(key,client):result={'accepted':False}
                elif data.get('error'):
                    result=app.fallback_job(key,client,data.get('session'))
                else:
                    try:
                        source=str(data.get('source',''))
                        # Redirects may change the URL; preview and resource origin must agree.
                        preview=app.browser_preview(data.get('preview'),source)
                        candidates=data.get('candidates',[])
                        if not isinstance(candidates,list):raise PreviewError('媒体资源格式无效。')
                        from .media_resolver import imported_candidates
                        catalog=None
                        if candidates:
                            imported=imported_candidates(source,candidates);imported['preview']=preview
                            catalog=app.catalog(imported)
                        result=app.bridge.finish(key,client,{'preview':preview,'catalog':catalog})
                    except PreviewError as error:result=app.bridge.finish(key,client,error=error)
            elif path == "/api/preview":
                result = app.create(str(data.get("url", "")),data.get('bili_parser') is not False)
            elif path == "/api/manual":
                result = app.manual(data)
            elif path == "/api/edit":
                result = app.manual(data, app.get_record(str(data.get("id", ""))))
            elif path == '/api/copy-rich':
                key = str(data.get('id', ''))
                copy_rich(app.get_record(key), key)
                result = {'ok': True}
            elif path=='/api/copy-image':
                from .clipboard import copy_image
                copy_image(app.get_record(str(data.get('id',''))));result={'ok':True}
            elif path=='/api/copy-text':
                from .clipboard import copy_text
                text=str(data.get('text',''))
                if len(text)>65536:raise PreviewError('复制文字过长。')
                copy_text(text);result={'ok':True}
            elif path=='/api/native/save-image':
                if not app.native_ui:raise PreviewError('此操作仅适用于 Mac 原生窗口。')
                record=app.get_record(str(data.get('id','')))
                asset=data.get('asset','card')
                if asset not in {'card','visual'}:raise PreviewError('图片类型无效。')
                payload=record['png'] if asset=='card' else record['visual']
                if not payload:raise PreviewError('没有可保存的图片。')
                result=app.native_ui.save('link-preview.png' if asset=='card' else 'link-image.png',data=payload)
            elif path=='/api/native/save-video':
                if not app.native_ui:raise PreviewError('此操作仅适用于 Mac 原生窗口。')
                job=app.downloads.get(str(data.get('job_id','')))
                if job.status!='complete' or not job.valid_file():raise PreviewError('下载文件尚未完成或已被移动。')
                result=app.native_ui.save(job.filename,source=job.file)
            elif path=='/api/native/open-url':
                if not app.native_ui:raise PreviewError('此操作仅适用于 Mac 原生窗口。')
                open_page(normalize_url(str(data.get('url',''))));result={'ok':True}
            elif path=='/api/native/quit':
                if not app.native_ui:raise PreviewError('此操作仅适用于 Mac 原生窗口。')
                from PyObjCTools import AppHelper
                AppHelper.callLater(.1,app.native_ui.stop);result={'ok':True}
            elif path=='/api/qa':
                if not app.native_ui or not app.native_ui.qa:
                    self.respond_json(404,{'error':'页面不存在。'});return
                result=app.native_ui.qa_action(data)
            elif path == '/api/video/start':
                record = app.get_record(str(data.get('id', '')))
                index = data.get('video_index')
                preview = record['preview']
                video = next((item for item in preview.videos if item['index'] == index), None)
                if not video:
                    raise PreviewError('请先展开含视频的 X / 推特链接，再选择视频。')
                video=dict(video)
                connections=data.get('connections',0);speed=data.get('speed_limit',0)
                if not isinstance(connections,int) or not 0<=connections<=16 or not isinstance(speed,int) or not 0<=speed<=100_000_000:
                    raise PreviewError('连接数或限速参数无效。')
                max_bytes=data.get('max_bytes',MAX_VIDEO_BYTES)
                if max_bytes is not None and (type(max_bytes) is not int or max_bytes!=MAX_VIDEO_BYTES):
                    raise PreviewError('下载大小请选择 500 MB 或无限制。')
                video['options']={'connections':connections,'speed_limit':speed,'max_bytes':max_bytes}
                result = app.downloads.start(video, preview.url)
            elif path=='/api/download/resolve':
                from .media_resolver import resolve
                if not app.fetch_slots.acquire(blocking=False):raise PreviewError('正在处理其他链接，请稍后重试。')
                try:
                    url=str(data.get('url',''))
                    try:result=app.catalog(resolve(url,data.get('headers'),data.get('scan') is True))
                    except PreviewError:
                        from .bilibili_parser import public_reference
                        if data.get('bili_parser') is False or not public_reference(url):raise
                        resolved=app.bilibili_public(url)
                        if not resolved['catalog']:raise PreviewError('已取得 B 站预览，但接口没有提供完整视频地址。')
                        result=resolved['catalog']
                finally:app.fetch_slots.release()
            elif path=='/api/capture/import':
                from .media_resolver import imported_candidates
                candidates=data.get('candidates')
                if not isinstance(candidates,list):raise PreviewError('捕获数据格式无效。')
                source=str(data.get('source',''));preview=None
                if data.get('preview') is not None:
                    if not app.fetch_slots.acquire(blocking=False):raise PreviewError('正在处理其他链接，请稍后重试。')
                    try:preview=app.browser_preview(data['preview'],source)
                    finally:app.fetch_slots.release()
                if candidates:
                    catalog=imported_candidates(source,candidates)
                    if preview:catalog['preview']=preview
                    result=app.catalog(catalog)
                elif preview:result={'resources':[],'preview':preview}
                else:raise PreviewError('请选择媒体资源，或导入当前网页预览。')
            elif path=='/api/download/start':
                catalog=app.get_catalog(str(data.get('catalog_id','')))
                item=next((resource for resource in catalog['resources'] if resource['index']==data.get('index')),None)
                if not item:raise PreviewError('请选择下载资源。')
                item=dict(item)
                connections=data.get('connections',0);speed=data.get('speed_limit',0)
                if not isinstance(connections,int) or not 0<=connections<=16 or not isinstance(speed,int) or not 0<=speed<=100_000_000:
                    raise PreviewError('连接数或限速参数无效。')
                max_bytes=data.get('max_bytes',MAX_VIDEO_BYTES)
                if max_bytes is not None and (type(max_bytes) is not int or max_bytes!=MAX_VIDEO_BYTES):
                    raise PreviewError('下载大小请选择 500 MB 或无限制。')
                item['options']={'connections':connections,'speed_limit':speed,'max_bytes':max_bytes}
                result=app.downloads.start(item,catalog['source'])
            elif path=='/api/download/catalog':
                key=str(data.get('id',''));catalog=app.get_catalog(key)
                result={'id':key,'source':catalog['source'],'title':catalog['title'],
                        'preview':catalog.get('preview'),
                        'resources':[{'index':item['index'],'kind':item.get('kind','video'),'filename':item.get('filename',''),
                                      'qualities':[variant.get('quality','') for variant in item['variants']]} for item in catalog['resources']]}
            elif path in {'/api/video/pause', '/api/video/resume', '/api/video/cancel', '/api/video/copy'}:
                job = app.downloads.get(str(data.get('job_id', '')))
                if path.endswith('/resume'):
                    if any(key in data for key in ['connections','speed_limit','max_bytes']):
                        connections=data.get('connections',0 if job.automatic else job.workers)
                        speed=data.get('speed_limit',job.speed_limit)
                        max_bytes=data.get('max_bytes',job.limit)
                        if type(connections) is not int or not 0<=connections<=16 or type(speed) is not int or not 0<=speed<=100_000_000:
                            raise PreviewError('连接数或限速参数无效。')
                        if max_bytes is not None and (type(max_bytes) is not int or max_bytes!=MAX_VIDEO_BYTES):
                            raise PreviewError('下载大小请选择 500 MB 或无限制。')
                        with job.lock:
                            if job.thread and job.thread.is_alive():raise PreviewError('请等待任务暂停后再修改下载设置。')
                            job.automatic=connections==0;job.workers=16 if job.automatic else connections
                            job.speed_limit=speed
                            job.limit=max_bytes
                            job.video['options']=dict(job.video.get('options') or {},connections=connections,speed_limit=speed)
                            job.save()
                    job.launch()
                elif path.endswith('/copy'):
                    if job.status != 'complete' or not job.valid_file():
                        raise PreviewError('视频尚未下载完成或文件已被移动。')
                    copy_video_file(job.file)
                else:
                    job.pause(cancel=path.endswith('/cancel'))
                result = job.snapshot()
            else:
                self.respond_json(404, {"error": "接口不存在。"})
                return
            self.respond_json(200, result)
        except PreviewError as error:
            self.respond_json(400, {"error": str(error)})
        except (ValueError, UnicodeError):
            self.respond_json(400, {"error": "请求格式有误。"})
        except Exception:
            self.respond_json(500, {"error": "生成失败，请重试或使用手动编辑。"})


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Link Expand · 本地链接预览")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument('--native-window',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--ui-test',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--test-downloads-root',type=Path,help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = App()
    if args.test_downloads_root:
        if not args.ui_test:parser.error('--test-downloads-root requires --ui-test')
        app._downloads=DownloadManager(args.test_downloads_root)
    preferred_port = args.port if args.port is not None else 8765
    try:
        server = Server(("127.0.0.1", preferred_port), app)
    except OSError as error:
        if args.port is not None or args.no_browser:
            parser.exit(1, f"无法启动：{error}。可使用 --port 8766 更换端口。\n")
        existing_url = f'http://127.0.0.1:{preferred_port}'
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(existing_url + '/api/health', timeout=2) as response:
                existing = json.load(response)
            if existing == {'app': 'link-expand', 'version': __version__}:
                open_page(existing_url)
                return
        except (OSError, ValueError):
            pass
        # An old server or a different program must not serve the new UI's requests.
        server = Server(('127.0.0.1', 0), app)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Link Expand 已启动：{url}\n按 Ctrl+C 退出。", flush=True)
    native_mac=sys.platform=='darwin' and not args.no_browser and (getattr(sys,'frozen',False) or args.native_window)
    if not args.no_browser and not native_mac:
        open_page(url)
    try:
        if native_mac:
            from .macos_window import run
            run(server,url,args.ui_test)
        else:server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()


if __name__ == "__main__":
    main()
