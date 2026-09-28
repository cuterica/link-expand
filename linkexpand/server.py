"""Loopback-only interface for local link summaries and visual previews."""

from __future__ import annotations

import argparse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
from urllib.parse import urlsplit
import urllib.request
import webbrowser

from . import __version__
from .cards import original_visual, render_card, thumbnail
from .clipboard import copy_rich
from .metadata import Preview, PreviewError, clean_text, get_preview, normalize_url, plain_text
from .sharing import rich_html
from .visuals import attach_visual

STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.fetch_slots = threading.BoundedSemaphore(2)
        self.records = OrderedDict()

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
                "visual": f"/assets/{key}/visual.png" if record["visual"] else None}

    def get_record(self, key):
        with self.lock:
            record = self.records.get(key)
        if not record:
            raise PreviewError("预览已过期，请重新生成。")
        return record

    def create(self, url):
        if not self.fetch_slots.acquire(blocking=False):
            raise PreviewError("正在处理其他链接，请稍后重试。")
        try:
            preview = get_preview(url)
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
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.respond_json(403, {"error": "不允许跨站访问。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in hosts}:
            self.respond_json(403, {"error": "不允许跨站访问。"})
            return False
        return True

    def authenticated(self):
        token = self.headers.get("X-Local-Token", "")
        if not hmac.compare_digest(token, self.server.app.token):
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
                self.respond_json(200, {'native_rich': os.name == 'nt', 'version': __version__})
        elif path == '/api/health':
            self.respond_json(200, {'app': 'link-expand', 'version': __version__})
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
            if not 0 < length <= 16384:
                raise PreviewError("请求内容过大或为空。")
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise PreviewError("仅支持 JSON 请求。")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise PreviewError("请求格式有误。")
            app = self.server.app
            path = urlsplit(self.path).path
            if path == "/api/preview":
                result = app.create(str(data.get("url", "")))
            elif path == "/api/manual":
                result = app.manual(data)
            elif path == "/api/edit":
                result = app.manual(data, app.get_record(str(data.get("id", ""))))
            elif path == '/api/copy-rich':
                key = str(data.get('id', ''))
                copy_rich(app.get_record(key), key)
                result = {'ok': True}
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
    args = parser.parse_args()
    app = App()
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
                webbrowser.open(existing_url)
                return
        except (OSError, ValueError):
            pass
        # An old server or a different program must not serve the new UI's requests.
        server = Server(('127.0.0.1', 0), app)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Link Expand 已启动：{url}\n按 Ctrl+C 退出。", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
