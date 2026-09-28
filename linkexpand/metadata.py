"""Fetch public web pages with bounded downloads and extract preview metadata."""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from html.parser import HTMLParser
import http.client
import ipaddress
import re
import socket
import ssl
import sys
import time
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
import zlib

MAX_HTML = 2 * 1024 * 1024
MAX_IMAGE = 8 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; LinkExpand/0.1; local link preview)"


class PreviewError(ValueError):
    pass


def http_error_message(url: str, status: int) -> str:
    host=urlsplit(url).hostname or ''
    if status==412 and (host=='bilibili.com' or host.endswith('.bilibili.com')):
        return ('B 站安全风控拦截了本次访问（HTTP 412）。请在 Chrome 中打开此链接并完成网站验证；'
                '如果浏览器能正常播放，可用浏览器捕获扩展导入媒体后下载。直接粘贴网页地址尚不能可靠通过此验证。')
    if status in {401,403,429}:
        return '该网站限制访问或需要登录。可以使用手动编辑创建卡片。'
    return f'网页返回 HTTP {status}，请检查链接。'


def clean_text(value: str, limit: int = 500) -> str:
    # Remove control characters, collapse whitespace, and bound untrusted text.
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def concise_summary(value: str, limit=260) -> str:
    text = clean_text(value, 2000)
    if len(text) <= limit:
        return text
    excerpt = text[:limit - 1]
    boundary = max(excerpt.rfind("。"), excerpt.rfind("！"), excerpt.rfind("？"), excerpt.rfind(". "))
    return excerpt[:boundary + 1] if boundary > limit // 2 else excerpt.rstrip() + "…"


def normalize_url(value: str) -> str:
    value = value.strip()
    if len(value) > 4096 or re.search(r"[\s\x00-\x1f\x7f]", value):
        raise PreviewError("请输入一个完整的 http 或 https 链接。")
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError()
        if parts.username is not None or parts.password is not None:
            raise ValueError()
        port = parts.port
        if port not in {None, 80, 443}:
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        authority = host + (f":{port}" if port else "")
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="%/?@!$&'()*+,;=:-._~[]")
        return urlunsplit((parts.scheme.lower(), authority, path, query, ""))
    except (ValueError, UnicodeError):
        raise PreviewError("仅支持不含账号密码、使用标准端口的 http / https 链接。") from None


def public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )))
    except OSError:
        raise PreviewError("无法解析这个域名，请检查链接和网络。") from None
    if not addresses or any(
        not ipaddress.ip_address(address).is_global
        or ipaddress.ip_address(address).is_multicast for address in addresses
    ):
        raise PreviewError("只读取公开网页，不支持本机或内网地址。")
    return addresses


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self):
        # Connect to the validated address rather than resolving a second time.
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class PinnedHTTPSConnection(PinnedHTTPConnection):
    def connect(self):
        super().connect()
        try:
            context=ssl.create_default_context()
            if sys.platform=='darwin':
                import certifi
                context.load_verify_locations(cafile=certifi.where())
            self.sock = context.wrap_socket(
                self.sock, server_hostname=self.host
            )
        except Exception:
            self.sock.close()
            raise


@dataclass
class Resource:
    url: str
    body: bytes
    content_type: str
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


def fetch_resource(url: str, limit: int = MAX_HTML, timeout: float = 25, headers=None) -> Resource:
    current = normalize_url(url)
    deadline = time.monotonic() + timeout
    for _ in range(6):
        parts = urlsplit(current)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = public_addresses(parts.hostname, port)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PreviewError("网页响应超时，请稍后重试。")
        factory = PinnedHTTPSConnection if parts.scheme == "https" else PinnedHTTPConnection
        connection = factory(parts.hostname, port, addresses[0], min(8, remaining))
        try:
            target = parts.path + ("?" + parts.query if parts.query else "")
            connection.request("GET", target, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html, image/*;q=0.9, */*;q=0.5",
                "Accept-Encoding": "identity",
                **(headers or {}),
            })
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise PreviewError("网页跳转缺少目标地址。")
                current = normalize_url(urljoin(current, location))
                continue
            if response.status >= 400:
                raise PreviewError(http_error_message(current,response.status))
            length = response.getheader("Content-Length", "")
            if length.isdigit() and int(length) > limit:
                raise PreviewError("网页或图片过大，无法生成预览。")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PreviewError("网页响应超时，请稍后重试。")
                if connection.sock:
                    connection.sock.settimeout(min(8, remaining))
                chunk = response.read1(min(65536, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise PreviewError("网页或图片过大，无法生成预览。")
            body = b"".join(chunks)
            encoding = response.getheader("Content-Encoding", "identity").lower()
            if encoding == "gzip":
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                body = decoder.decompress(body, limit + 1)
                if len(body) > limit or decoder.unconsumed_tail:
                    raise PreviewError("网页解压后过大，无法生成预览。")
                if not decoder.eof:
                    raise PreviewError("网页压缩数据不完整。")
            elif encoding != "identity":
                raise PreviewError("该网站返回了不支持的压缩格式。")
            kept = {key.lower(): value for key, value in response.getheaders()
                    if key.lower() in {"content-range", "accept-ranges", "access-control-allow-origin"}}
            return Resource(current, body, response.getheader("Content-Type", ""), response.status, kept)
        except PreviewError:
            raise
        except (OSError, http.client.HTTPException, zlib.error):
            raise PreviewError("无法读取网页，请检查网络；也可以手动编辑卡片。") from None
        finally:
            connection.close()
    raise PreviewError("网页跳转次数过多。")


def decode_html(resource: Resource) -> str:
    header_match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", resource.content_type, re.I)
    meta_match = re.search(br"charset\s*=\s*[\"']?([\w-]+)", resource.body[:4096], re.I)
    encoding = (header_match.group(1) if header_match else
                meta_match.group(1).decode("ascii") if meta_match else "utf-8")
    try:
        codecs.lookup(encoding)
    except LookupError:
        encoding = "utf-8"
    try:
        return resource.body.decode(encoding, errors="replace")
    except (LookupError, ValueError):
        return resource.body.decode("utf-8", errors="replace")


class MetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title: list[str] = []
        self.paragraphs: list[str] = []
        self.base = ""
        self.in_title = False
        self.in_paragraph = False
        self.ignore_depth = 0
        self.paragraph_length = 0
        self.images: list[dict] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self.ignore_depth += 1
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content") or ""
            if content.strip():
                self.meta.setdefault(key, content)
        if tag == "base" and not self.base:
            self.base = values.get("href") or ""
        if tag == "title":
            self.in_title = True
        if tag == "p":
            self.in_paragraph = True
        if tag in {"img", "video"}:
            source = values.get("poster") if tag == "video" else (
                values.get("data-src") or values.get("data-original") or values.get("src"))
            srcset = values.get("srcset") or values.get("data-srcset") or ""
            if srcset:
                options = []
                for option in srcset.split(","):
                    match = re.match(r"\s*(\S+)\s+(\d+(?:\.\d+)?)([wx])", option)
                    if match:
                        options.append((float(match.group(2)), match.group(1)))
                if options:
                    source = max(options)[1]
            if source and not source.startswith("data:") and len(self.images) < 60:
                self.images.append({"url": source, "source": "视频封面" if tag == "video" else "网页图片",
                                    "priority": 850 if tag == "video" else 350})

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.ignore_depth = max(0, self.ignore_depth - 1)
        if tag == "title":
            self.in_title = False
        if tag == "p" and self.paragraph_length < 1000:
            self.in_paragraph = False
            self.paragraphs.append(" ")
            self.paragraph_length += 1

    def handle_data(self, data):
        if self.ignore_depth:
            return
        if self.in_title:
            self.title.append(data)
        if self.in_paragraph and self.paragraph_length < 1000:
            data = data[:1000 - self.paragraph_length]
            self.paragraphs.append(data)
            self.paragraph_length += len(data)


@dataclass
class Preview:
    url: str
    title: str
    description: str
    domain: str
    site_name: str
    image_url: str = ""
    image: bytes | None = field(default=None, repr=False)
    warnings: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list, repr=False)
    visual_source: str = ""
    summary_source: str = "网页摘要"
    videos: list[dict] = field(default_factory=list, repr=False)
    selected_video: int | None = None


def extract_metadata(resource: Resource) -> Preview:
    mime = resource.content_type.split(";", 1)[0].strip().lower()
    if mime and mime not in {"text/html", "application/xhtml+xml"}:
        raise PreviewError("这个链接不是网页。请使用手动编辑创建卡片。")
    parser = MetadataParser()
    parser.feed(decode_html(resource))
    meta = parser.meta
    domain = urlsplit(resource.url).hostname.removeprefix("www.")
    html_title = re.sub(r"<[^>]+>", "", "".join(parser.title))
    title = clean_text(meta.get("og:title") or meta.get("twitter:title") or html_title or domain, 180)
    description = concise_summary(meta.get("og:description") or meta.get("twitter:description") or
                                  meta.get("description") or "".join(parser.paragraphs))
    image = meta.get("og:image:secure_url") or meta.get("og:image") or meta.get("twitter:image") or meta.get("twitter:image:src")
    base = urljoin(resource.url, parser.base) if parser.base else resource.url
    preview = Preview(resource.url, title, description, domain,
                      clean_text(meta.get("og:site_name") or domain, 80),
                      urljoin(base, image.strip()) if image else "")
    seen = set()
    for candidate in ([{"url": preview.image_url, "source": "网页封面", "priority": 1000}] if image else []) + parser.images:
        candidate_url = urljoin(base, candidate["url"])
        if candidate_url in seen or re.search(r"(?:^|[/_.-])(logo|icon|avatar|badge|sprite|tracking)(?:[/_.-]|$)", candidate_url, re.I):
            continue
        seen.add(candidate_url)
        preview.candidates.append(dict(candidate, url=candidate_url))
    if not (meta.get("og:description") or meta.get("twitter:description") or meta.get("description")):
        preview.summary_source = "正文摘录"
    if not description:
        preview.warnings.append("网页没有提供摘要，可在下方补充。")
    return preview


def get_preview(url: str) -> Preview:
    url = normalize_url(url)
    from .xmedia import post_reference, preview_for_post
    if post_reference(url):
        return preview_for_post(url)
    is_video = re.search(r"\.(mp4|webm|mov|m4v)(?:$)", urlsplit(url).path, re.I)
    resource = fetch_resource(url, MAX_IMAGE, headers={"Range": "bytes=0-1023"} if is_video else None)
    mime = resource.content_type.split(";", 1)[0].strip().lower()
    if mime.startswith(("image/", "video/")):
        domain = urlsplit(resource.url).hostname.removeprefix("www.")
        title = clean_text(unquote(urlsplit(resource.url).path.rsplit("/", 1)[-1]), 180) or domain
        preview = Preview(resource.url, title, "", domain, domain, summary_source="媒体链接")
        if mime.startswith("image/"):
            preview.image = resource.body
            preview.visual_source = "链接图片"
        return preview
    return extract_metadata(resource)


def plain_text(preview: Preview) -> str:
    return "\n".join(part for part in [preview.title, preview.description, preview.url] if part)
