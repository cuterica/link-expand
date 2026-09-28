"""Resolve public X post media directly from X's embed data, without yt-dlp."""

from __future__ import annotations

from html import unescape
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode, urlsplit

import playwright

from .metadata import Preview, PreviewError, clean_text, concise_summary, fetch_resource, normalize_url

X_HOSTS = {'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com', 'm.twitter.com'}


def post_reference(url: str):
    parts = urlsplit(normalize_url(url))
    if parts.hostname not in X_HOSTS:
        return None
    match = re.fullmatch(r'/(?:[A-Za-z0-9_]+|i/web)/status/(\d{5,25})(?:/(?:video|photo)/(\d+))?/?', parts.path)
    if not match:
        return None
    return {'id': match[1], 'index': int(match[2]) if match[2] else None}


def syndication_token(post_id: str) -> str:
    # Use the already bundled JavaScript runtime for exact JS floating/base-36 conversion.
    node = Path(playwright.__file__).parent / 'driver' / ('node.exe' if os.name == 'nt' else 'node')
    script = 'process.stdout.write(((Number(process.argv[1])/1e15)*Math.PI).toString(36).replace(/(0+|\\.)/g,""))'
    try:
        result = subprocess.run([str(node), '-e', script, post_id], capture_output=True,
                                text=True, encoding='ascii', timeout=5, check=True,
                                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        if not re.fullmatch(r'[a-z0-9]+', result.stdout):
            raise ValueError()
        return result.stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        raise PreviewError('无法启动 X 视频解析组件，请重新运行或更新软件。') from None


def media_url(value: str) -> str:
    url = normalize_url(value)
    if urlsplit(url).hostname != 'video.twimg.com' or not urlsplit(url).path.lower().endswith('.mp4'):
        raise PreviewError('X 未提供可下载的完整 MP4 视频。')
    return url


def parse_post(data: dict, url: str) -> Preview:
    reference = post_reference(url)
    if not reference or not isinstance(data, dict) or data.get('__typename') == 'TweetTombstone' or not data.get('id_str'):
        raise PreviewError('这条 X 推文不可公开读取，可能需要登录、已删除或受访问限制。')
    if str(data['id_str']) != reference['id']:
        raise PreviewError('X 返回的推文与链接不一致，请重试。')
    author = data.get('user') or {}
    handle = clean_text(author.get('screen_name') or 'X', 50)
    name = clean_text(author.get('name') or handle, 80)
    preview = Preview(normalize_url(url), f'{name} (@{handle})',
                      concise_summary(unescape(data.get('text') or '')), 'x.com', 'X / 推特',
                      summary_source='推文正文')
    media = data.get('mediaDetails') or []
    if not media and data.get('video'):
        video = data['video']
        media = [{'type': 'video', 'media_url_https': video.get('poster'), 'video_info': {
            'duration_millis': video.get('durationMs'), 'variants': video.get('variants', [])}}]
    for index, item in enumerate(media, 1):
        if not isinstance(item, dict):
            continue
        poster = item.get('media_url_https') or item.get('media_url')
        if poster:
            preview.candidates.append({'url': poster, 'priority': 1000 if not preview.candidates else 850,
                                       'source': '推文图片' if item.get('type') == 'photo' else '视频封面'})
        if item.get('type') not in {'video', 'animated_gif'}:
            continue
        info = item.get('video_info') or {}
        variants, seen = [], set()
        for variant in info.get('variants', []):
            if (variant.get('content_type') or variant.get('type')) != 'video/mp4':
                continue
            try:
                candidate = media_url(variant.get('url') or variant.get('src') or '')
            except PreviewError:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            dimensions = re.search(r'/(\d+)x(\d+)/', urlsplit(candidate).path)
            width, height = (int(dimensions[1]), int(dimensions[2])) if dimensions else (0, 0)
            bitrate = int(variant.get('bitrate') or 0)
            variants.append({'url': candidate, 'bitrate': bitrate, 'width': width, 'height': height,
                             'quality': f'{width}×{height}' if dimensions else '原始画质'})
        variants.sort(key=lambda variant: (variant['bitrate'], variant['width'] * variant['height']), reverse=True)
        if variants:
            preview.videos.append({'index': index, 'post_id': reference['id'], 'author': handle,
                                   'duration_ms': int(info.get('duration_millis') or 0), 'variants': variants})
    if not preview.candidates:
        for photo in data.get('photos') or []:
            if photo.get('url'):
                preview.candidates.append({'url': photo['url'], 'priority': 1000, 'source': '推文图片'})
    if preview.candidates:
        preview.image_url = preview.candidates[0]['url']
    if reference['index'] and any(video['index'] == reference['index'] for video in preview.videos):
        preview.selected_video = reference['index']
    elif preview.videos:
        preview.selected_video = preview.videos[0]['index']
    return preview


def preview_for_post(url: str) -> Preview:
    reference = post_reference(url)
    if not reference:
        raise PreviewError('请使用 X / 推特的单条推文链接。')
    query = urlencode({'id': reference['id'], 'lang': 'en', 'token': syndication_token(reference['id'])})
    try:
        resource = fetch_resource('https://cdn.syndication.twimg.com/tweet-result?' + query, timeout=20,
                                  headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://x.com/'})
        data = json.loads(resource.body)
    except (PreviewError, ValueError):
        raise PreviewError('无法读取这条 X 推文。请确认它可以公开访问；需要登录、已删除或受限的内容暂不支持。') from None
    return parse_post(data, url)
