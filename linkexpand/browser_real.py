"""Visible Playwright fallback using the browser's own network stack."""
import asyncio
import base64
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, Error as BrowserError
from .capture import browser_executable
from .metadata import PreviewError, normalize_url, public_addresses


def profile_directory():
    root = (Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'LinkExpand' if os.name == 'nt'
            else Path.home() / 'Library/Application Support/LinkExpand' if sys.platform == 'darwin'
            else Path.home() / '.local/share/LinkExpand')
    return root / 'browser-profile'


def real_executable():
    if os.environ.get('LINK_EXPAND_BROWSER'): return browser_executable()
    if os.name == 'nt':
        for root in [os.environ.get('PROGRAMFILES(X86)', ''), os.environ.get('PROGRAMFILES', '')]:
            path = Path(root) / 'Microsoft/Edge/Application/msedge.exe'
            if path.is_file(): return str(path)
    return browser_executable()


def session_cookies(url, headers):
    """Transfer only cookies observed on this site's own document request."""
    if not isinstance(headers, dict): return []
    host = urlsplit(url).hostname
    records=headers.get('cookies')
    if isinstance(records,list):
        result=[]
        for item in records[:128]:
            if not isinstance(item,dict):continue
            domain=str(item.get('domain',''));scope=domain.lstrip('.')
            if not scope or not (host==scope or host.endswith('.'+scope)):continue
            name=item.get('name');value=item.get('value')
            if not isinstance(name,str) or not isinstance(value,str) or len(value)>16384 or re.search(r'[\x00-\x1f\x7f]',name+value):continue
            same={'no_restriction':'None','lax':'Lax','strict':'Strict'}.get(item.get('sameSite'),'Lax')
            cookie={'name':name,'value':value,'domain':domain,'path':str(item.get('path','/')),
                    'secure':bool(item.get('secure')),'httpOnly':bool(item.get('httpOnly')),'sameSite':same}
            if isinstance(item.get('expirationDate'),(int,float)) and item['expirationDate']>time.time():cookie['expires']=item['expirationDate']
            result.append(cookie)
        return result
    headers=headers.get('headers',headers)
    if not isinstance(headers,dict):return []
    domain = '.bilibili.com' if host == 'bilibili.com' or host.endswith('.bilibili.com') else host
    value = next((v for k, v in headers.items() if k.lower() == 'cookie' and isinstance(v, str)), '')
    if len(value) > 32768: raise PreviewError('网页登录信息过大。')
    if re.search(r'[\x00-\x1f\x7f]',value):return []
    cookies = []
    for entry in value.split(';'):
        name, separator, content = entry.strip().partition('=')
        if not separator or not re.fullmatch(r'[!#$%&\'*+.^_`|~\w-]+', name): continue
        if re.search(r'[\x00-\x1f\x7f]', content): continue
        cookies.append({'name': name, 'value': content, 'domain': domain, 'path': '/',
                        'secure': urlsplit(url).scheme == 'https', 'httpOnly': name=='SESSDATA' if domain=='.bilibili.com' else not re.search(r'csrf|xsrf',name,re.I), 'sameSite': 'Lax'})
    return cookies[:128]


async def expand(url, session=None, *, profile=None, executable=None, timeout=50, extra_args=None):
    url = normalize_url(url)
    initial = urlsplit(url)
    await asyncio.to_thread(public_addresses, initial.hostname, initial.port or (443 if initial.scheme == 'https' else 80))
    profile = Path(profile or profile_directory()); profile.mkdir(parents=True, exist_ok=True)
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    read_script = (root / 'browser-extension/page.js').read_text(encoding='utf-8')
    deadline = time.monotonic() + timeout
    resources = {}; tasks = set(); checked = {}
    async with async_playwright() as runtime:
        options = {'headless': False, 'viewport': {'width': 1280, 'height': 800}, 'accept_downloads': False,
                   'args': ['--no-first-run', '--no-default-browser-check'] + list(extra_args or []), 'timeout': 20000}
        path = executable or real_executable()
        if path: options['executable_path'] = path
        context = await runtime.chromium.launch_persistent_context(str(profile), **options)
        try:
            cookies = session_cookies(url, session)
            if cookies: await context.add_cookies(cookies)
            async def route_request(route):
                request = route.request; parts = urlsplit(request.url)
                if parts.scheme not in {'http', 'https'}:
                    await route.continue_(); return
                key = (parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))
                if key not in checked:
                    try: await asyncio.to_thread(public_addresses, *key); checked[key] = True
                    except PreviewError: checked[key] = False
                if not checked[key]: await route.abort(); return
                # Native browser TLS, HTTP headers, cookies, redirects and JavaScript.
                await route.continue_()
            await context.route('**/*', route_request)
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(5000)
            page.on('dialog', lambda dialog: dialog.dismiss())
            async def capture(response):
                try:
                    if response.request.method != 'GET' or response.status >= 400: return
                    headers = await response.all_headers(); mime = headers.get('content-type', '').split(';')[0].lower()
                    address = response.url
                    kind = ('hls' if re.search(r'\.m3u8(?:[?#]|$)', address, re.I) or 'mpegurl' in mime else
                            'dash' if re.search(r'\.mpd(?:[?#]|$)', address, re.I) or mime == 'application/dash+xml' else
                            'video' if mime.startswith('video/') else 'audio' if mime.startswith('audio/') else None)
                    if kind and len(resources) < 64:
                        observed = await response.request.all_headers()
                        resources[address] = {'url': address, 'kind': kind, 'mime': mime,
                            'headers': {key: value for key, value in observed.items() if key.lower() in {'referer','origin','user-agent','cookie','authorization'}}}
                except BrowserError: pass
            def observed(response):
                task = asyncio.create_task(capture(response)); tasks.add(task); task.add_done_callback(tasks.discard)
            page.on('response', observed)
            try: response = await page.goto(url, wait_until='domcontentloaded', timeout=25000)
            except BrowserError:
                if page.url == 'about:blank': raise PreviewError('Playwright 已打开真实浏览器，但网页加载失败。')
                response = None
            status = response.status if response else None
            data = None
            while time.monotonic() < deadline:
                try:
                    data = await page.evaluate(read_script + '\nlinkExpandPage(true)')
                    if not data.get('error') and data.get('preview', {}).get('title'):
                        await page.wait_for_timeout(2500)
                        data = await page.evaluate(read_script + '\nlinkExpandPage(false)')
                        if not data.get('error'): break
                except BrowserError:
                    if page.is_closed(): raise PreviewError('真实浏览器窗口已关闭。')
                await page.wait_for_timeout(1000)
            if not data or data.get('error') or not data.get('preview', {}).get('title'):
                detail = f'（HTTP {status}）' if status else ''
                raise PreviewError(f'Playwright 已实际打开真实浏览器，网页仍拒绝本次访问{detail}。这不能证明你的日常浏览器也需要验证；请启用扩展读取已经正常打开的同一页面。')
            if tasks: await asyncio.gather(*list(tasks), return_exceptions=True)
            preview = data['preview']
            if not preview.get('image_data'):
                # Prefer the actual cover element, otherwise capture this same real page.
                cover = page.locator('img')
                image = preview.get('image_url')
                found = False
                if image:
                    for index in range(min(await cover.count(), 40)):
                        node = cover.nth(index)
                        if await node.evaluate('(node, url) => (node.currentSrc || node.src) === url && node.naturalWidth >= 280', image):
                            try:
                                png = await node.screenshot(type='jpeg', quality=80, timeout=2000)
                                if len(png) <= 2_000_000:
                                    preview.update(image_data='data:image/jpeg;base64,' + base64.b64encode(png).decode(), visual_source='真实浏览器封面')
                                    found = True; break
                            except BrowserError: pass
                if not found:
                    png = await page.screenshot(type='jpeg', quality=75, timeout=5000)
                    preview.update(image_data='data:image/jpeg;base64,' + base64.b64encode(png).decode(), visual_source='真实浏览器网页截图')
            items = {item['url']: item for item in data.get('candidates', [])}
            items.update(resources)
            declared = {item['url'] for item in data.get('candidates', [])}
            candidates = [item for item in items.values() if not re.search(r'\.ts(?:[?#]|$)', item['url'], re.I)
                          and (not re.search(r'\.m4s(?:[?#]|$)', item['url'], re.I) or item['url'] in declared
                               or (urlsplit(item['url']).hostname or '').endswith('.bilivideo.com'))]
            videos = [item for item in candidates if item['kind'] == 'video']; audios = [item for item in candidates if item['kind'] == 'audio']
            if videos and audios:
                candidates = [videos[0], audios[0]] + [item for item in candidates if item['kind'] in {'hls', 'dash'}]
            return {'source': preview['url'], 'preview': preview, 'candidates': candidates[:64], 'method': 'playwright-visible'}
        finally:
            for task in list(tasks): task.cancel()
            await context.close()


def main():
    try:
        data = json.loads(sys.stdin.read(65536))
        result = asyncio.run(expand(data['url'], data.get('session')))
        print(json.dumps(result, ensure_ascii=True))
    except PreviewError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=True)); sys.exit(1)
    except Exception:
        print(json.dumps({'error': '真实浏览器回退失败。请确认已安装 Edge / Chrome，且 Link Expand 的独立浏览器窗口没有被关闭。'}, ensure_ascii=True)); sys.exit(1)


if __name__ == '__main__': main()
