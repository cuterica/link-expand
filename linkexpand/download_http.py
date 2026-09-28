"""URL and request context validation shared by generic resource discovery."""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit, urlunsplit
import zlib

from .metadata import PreviewError

SECRET_HEADERS = {'cookie', 'authorization'}
FORBIDDEN_HEADERS = {'host', 'connection', 'content-length', 'transfer-encoding', 'range',
                     'proxy-authorization', 'upgrade', 'accept-encoding'}


def resource_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 16384:
        raise PreviewError('请输入完整的 HTTP / HTTPS 资源地址。')
    value = value.strip()
    if re.search(r'[\s\x00-\x1f\x7f]', value):
        raise PreviewError('资源地址不能含空格或控制字符。')
    if '://' not in value:
        value = 'https://' + value
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {'http', 'https'} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        host = parts.hostname.encode('idna').decode().lower()
        if ':' in host:
            host = '[' + host + ']'
        authority = host + (f':{port}' if port is not None else '')
        return urlunsplit((parts.scheme.lower(), authority,
                          quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~"),
                          quote(parts.query, safe="%/?@!$&'()*+,;=:-._~[]"), ''))
    except (ValueError, UnicodeError):
        raise PreviewError('资源地址格式无效；请使用 HTTP / HTTPS，不要在地址里放账号密码。') from None


def origin(url):
    parts = urlsplit(resource_url(url))
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80)


def checked_headers(headers):
    if headers is None:
        return {}
    if not isinstance(headers, dict) or len(headers) > 24:
        raise PreviewError('请求头需要是 JSON 对象，最多 24 项。')
    result = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9!#$%&\'*+.^_`|~-]{1,64}', key):
            raise PreviewError('请求头名称无效。')
        if key.lower() in FORBIDDEN_HEADERS:
            raise PreviewError(f'请求头 {key} 由下载器管理，不能手动设置。')
        if not isinstance(value, str) or len(value) > 16384 or re.search(r'[\r\n\x00]', value):
            raise PreviewError('请求头值无效或过长。')
        result[key] = value
    return result


def scoped_headers(headers, target, credential_origin):
    return {key: value for key, value in headers.items()
            if key.lower() not in SECRET_HEADERS or origin(target) == origin(credential_origin)}


def public_headers(headers):
    return {key: value for key, value in headers.items() if key.lower() not in SECRET_HEADERS}


def document_body(response,limit):
    chunks=[];size=0
    while size<=limit:
        chunk=response.read1(min(65536,limit+1-size))
        if not chunk:break
        chunks.append(chunk);size+=len(chunk)
    if size>limit:raise PreviewError('网页或清单超过解析大小限制。')
    body=b''.join(chunks);encoding=response.getheader('Content-Encoding','identity').lower()
    if encoding in {'identity',''}:return body
    if encoding not in {'gzip','deflate'}:raise PreviewError('这个网页使用了不支持的压缩方式。')
    try:
        decoder=zlib.decompressobj(16+zlib.MAX_WBITS if encoding=='gzip' else zlib.MAX_WBITS)
        body=decoder.decompress(body,limit+1)
        if len(body)>limit or decoder.unconsumed_tail or not decoder.eof:raise ValueError()
        return body
    except (ValueError,zlib.error):raise PreviewError('网页解压失败或解压后超过限制。') from None
