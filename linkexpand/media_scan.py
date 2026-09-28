"""Optional bounded, isolated dynamic-page media discovery."""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

from playwright.async_api import async_playwright,Error as BrowserError

from .capture import browser_executable
from .download_http import checked_headers,resource_url,scoped_headers,document_body
from .metadata import PreviewError
from .owned_process import run_worker


async def inspect_page(url,headers):
    from .video_downloads import open_video
    from .media_resolver import classify
    deadline=time.monotonic()+18;captured={};count=0;transferred=0
    async with async_playwright() as runtime:
        executable=browser_executable()
        browser=await runtime.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}),
            args=['--proxy-server=http://127.0.0.1:9','--proxy-bypass-list=<-loopback>',
                  '--disable-background-networking','--disable-quic','--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        context=await browser.new_context(service_workers='block',viewport={'width':1280,'height':720},accept_downloads=False)
        slots=asyncio.Semaphore(4)
        def fetch(url_value,request_type,request_headers):
            extra={}
            kind=classify(url_value)
            if kind in {'video','audio'} or request_type=='media':
                match=re.match(r'bytes=(\d+)-',request_headers.get('range','bytes=0-'))
                start=int(match[1]) if match else 0
                extra['Range']=f'bytes={start}-{start+262143}'
            with open_video(url_value,extra,headers,url,allow_compressed=True) as response:
                mime=response.getheader('Content-Type','')
                media_kind=classify(url_value,mime)
                response_headers={key:value for key,value in response.getheaders()
                    if key.lower() in {'content-range','accept-ranges','access-control-allow-origin'}}
                if response.getheader('Content-Encoding','identity') not in {'identity',''}:
                    body=document_body(response,2*1024*1024)
                else:
                    chunks=[];size=0
                    while size<2*1024*1024:
                        chunk=response.read1(min(65536,2*1024*1024-size))
                        if not chunk:break
                        chunks.append(chunk);size+=len(chunk)
                    body=b''.join(chunks)
                return response.status,mime,body,response_headers,media_kind
        async def route_request(route):
            nonlocal count,transferred
            count+=1
            request=route.request
            if request.method not in {'GET','HEAD'} or count>75 or transferred>24*1024*1024 or time.monotonic()>deadline:
                await route.abort();return
            try:
                async with slots:
                    status,mime,body,response_headers,kind=await asyncio.to_thread(fetch,request.url,request.resource_type,request.headers)
                    transferred+=len(body)
                    if kind in {'hls','dash','video','audio'}:
                        copied={'Referer':request.headers.get('referer') or url,'User-Agent':request.headers.get('user-agent') or 'Mozilla/5.0'}
                        copied.update(scoped_headers(headers,request.url,url))
                        captured[request.url]={'url':request.url,'kind':kind,'headers':copied}
                    await route.fulfill(status=status,body=body,content_type=mime or 'application/octet-stream',headers=response_headers)
            except (PreviewError,OSError,BrowserError):
                try:await route.abort()
                except BrowserError:pass
        await context.route('**/*',route_request)
        await context.route_web_socket('**/*',lambda socket:socket.close())
        page=await context.new_page();page.set_default_timeout(1500)
        page.on('dialog',lambda dialog:dialog.dismiss())
        try:
            try:await page.goto(url,wait_until='domcontentloaded',timeout=12000)
            except BrowserError:pass
            for frame in page.frames[:6]:
                try:
                    values=await frame.locator('video,audio,source').evaluate_all("nodes=>nodes.map(n=>n.currentSrc||n.src).filter(s=>/^https?:/.test(s))")
                    for value in values:captured.setdefault(value,{'url':value,'kind':classify(value) or 'video','headers':headers})
                    await frame.locator('video,audio').evaluate_all("nodes=>nodes.slice(0,2).forEach(n=>{n.muted=true;n.play().catch(()=>{})})")
                except BrowserError:pass
            await page.wait_for_timeout(min(2500,max(0,(deadline-time.monotonic())*1000)))
            return list(captured.values())[:64]
        finally:
            await context.close();await browser.close()


def scan_page(url,headers=None):
    command=[sys.executable,'--media-scan-worker'] if getattr(sys,'frozen',False) else [sys.executable,'-m','linkexpand.media_scan']
    try:
        process=run_worker(command,json.dumps({'url':resource_url(url),'headers':checked_headers(headers)}),28)
        result=json.loads(process.stdout)
        if process.returncode or result.get('error'):raise PreviewError(result.get('error','动态网页识别失败。'))
        return result['resources']
    except (ValueError,OSError):raise PreviewError('动态识别组件无法运行，请确认已安装 Chrome / Edge。') from None
    except subprocess.TimeoutExpired:raise PreviewError('动态识别超时，已结束识别进程。可尝试浏览器捕获扩展。') from None


def main():
    try:
        data=json.loads(sys.stdin.read(65536))
        resources=asyncio.run(inspect_page(resource_url(data['url']),checked_headers(data.get('headers'))))
        print(json.dumps({'resources':resources}))
    except Exception:
        print(json.dumps({'error':'动态页面无法识别，请尝试浏览器捕获扩展或直接媒体地址。'}));sys.exit(1)

if __name__=='__main__':main()
