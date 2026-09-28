"""Isolated browser worker. All HTTP traffic goes through the pinned downloader."""

import asyncio
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

from PIL import Image, ImageStat
from playwright.async_api import async_playwright, Error as BrowserError

from .metadata import MAX_IMAGE, PreviewError, clean_text, fetch_resource


def browser_executable():
    configured = os.environ.get("LINK_EXPAND_BROWSER")
    if configured:
        return configured
    for name in ["google-chrome", "chromium", "chromium-browser", "msedge"]:
        executable = shutil.which(name)
        if executable:
            return executable
    if os.name == "nt":
        for root in [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                     os.environ.get("LOCALAPPDATA", "")]:
            for relative in ["Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"]:
                path = Path(root) / relative
                if path.is_file():
                    return str(path)
    return None


def frame_score(png):
    with Image.open(BytesIO(png)) as picture:
        picture = picture.convert("RGB")
        picture.thumbnail((160, 100))
        statistics = ImageStat.Stat(picture)
        brightness = sum(statistics.mean) / 3
        variation = sum(statistics.stddev) / 3
        return variation + picture.entropy() * 4 if 12 < brightness < 245 else -1


async def capture(url):
    deadline = time.monotonic() + 32
    async with async_playwright() as runtime:
        executable = browser_executable()
        browser = await runtime.chromium.launch(
            **({"executable_path": executable} if executable else {}),
            headless=True,
            args=["--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
                  "--disable-background-networking", "--disable-quic",
                  "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
        )
        context = await browser.new_context(viewport={"width": 1280, "height": 800},
                                            device_scale_factor=1, locale="zh-CN",
                                            service_workers="block", accept_downloads=False)
        slots = asyncio.Semaphore(6)
        count, transferred = 0, 0

        async def route_request(route):
            nonlocal count, transferred
            request = route.request
            count += 1
            if count > 90 or transferred > 36 * 1024 * 1024 or request.method not in {"GET", "HEAD"}:
                await route.abort()
                return
            try:
                async with slots:
                    remaining = deadline - time.monotonic()
                    if remaining < 1:
                        await route.abort()
                        return
                    headers = {}
                    if request.resource_type == "media":
                        # Bound media ranges rather than downloading an entire movie.
                        match = re.match(r"bytes=(\d+)-", request.headers.get("range", "bytes=0-"))
                        start = int(match.group(1)) if match else 0
                        headers["Range"] = f"bytes={start}-{start + MAX_IMAGE - 1}"
                    resource = await asyncio.to_thread(fetch_resource, request.url, MAX_IMAGE,
                                                       min(6, remaining), headers)
                    transferred += len(resource.body)
                    await route.fulfill(status=resource.status, body=resource.body,
                                        content_type=resource.content_type or "application/octet-stream",
                                        headers=resource.headers)
            except (PreviewError, BrowserError, OSError):
                try:
                    await route.abort()
                except BrowserError:
                    pass

        await context.route("**/*", route_request)
        await context.route_web_socket("**/*", lambda socket: socket.close())
        page = await context.new_page()
        page.set_default_timeout(2500)
        page.on("dialog", lambda dialog: dialog.dismiss())
        try:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except BrowserError:
                if page.url == "about:blank":
                    raise PreviewError("网页无法加载，已保留文字摘要。")
            await page.wait_for_timeout(1400)
            await page.add_style_tag(content="""
                [id*='cookie-banner'], [class*='cookie-banner'], [id*='consent-banner'],
                [class*='consent-banner'] { display:none!important; }
                * { caret-color: transparent!important; }
            """)
            title = clean_text(await page.title(), 180)
            description = await page.evaluate("""() => {
                const meta = document.querySelector('meta[property="og:description"], meta[name="description"]');
                if (meta?.content) return meta.content;
                const root = document.querySelector('article, main, [role="main"]') || document.body;
                return [...root.querySelectorAll('p')].filter(p => p.innerText.trim().length > 35)
                  .slice(0, 3).map(p => p.innerText).join(' ').slice(0, 500);
            }""")
            description = clean_text(description, 500)

            # Prefer a useful image discovered after JavaScript rendering.
            images = await page.locator("img").evaluate_all("""images => images.map((img, index) => ({
                index, width:img.naturalWidth, height:img.naturalHeight, src:img.currentSrc || img.src,
                visible:img.getBoundingClientRect().width > 200,
            })).filter(i => i.visible && i.width >= 320 && i.height >= 160 &&
                i.width / i.height < 3.4 && i.width / i.height > .55 &&
                !/(logo|icon|avatar|sprite|badge)/i.test(i.src))
                .sort((a,b) => b.width*b.height - a.width*a.height).slice(0, 3)""")
            for image in images:
                try:
                    png = await page.locator("img").nth(image["index"]).screenshot(type="png", timeout=2500)
                    if frame_score(png) > 15:
                        return {"png": png, "source": "网页图片", "title": title, "description": description}
                except BrowserError:
                    continue

            # Sample several moments and discard blank frames. Includes embedded videos.
            videos = []
            for frame in page.frames[:8]:
                try:
                    for index in range(min(await frame.locator("video").count(), 4)):
                        video = frame.locator("video").nth(index)
                        bounds = await video.bounding_box()
                        if bounds and bounds["width"] >= 260 and bounds["height"] >= 140:
                            videos.append((bounds["width"] * bounds["height"], video))
                except BrowserError:
                    continue
            if videos:
                _, video = max(videos, key=lambda item: item[0])
                best, score = None, -1
                for fraction in [0.2, 0.45, 0.7]:
                    if time.monotonic() > deadline - 3:
                        break
                    try:
                        ready = await video.evaluate("""async (video, fraction) => {
                            video.muted = true; video.preload = 'auto';
                            if (video.readyState < 2) {
                                // Do not restart an in-flight media download.
                                if (video.networkState === 0 || video.networkState === 3) video.load();
                                await Promise.race([new Promise(resolve => video.addEventListener('loadeddata', resolve, {once:true})),
                                    new Promise(resolve => setTimeout(resolve, 5000))]);
                            }
                            if (video.readyState < 2) return false;
                            if (Number.isFinite(video.duration) && video.duration > 1) {
                                video.currentTime = Math.min(video.duration - .1, video.duration * fraction);
                                await Promise.race([new Promise(resolve => video.addEventListener('seeked', resolve, {once:true})),
                                    new Promise(resolve => setTimeout(resolve, 1500))]);
                            }
                            video.pause(); video.controls = false; return video.readyState >= 2;
                        }""", fraction)
                        if not ready:
                            continue
                        png = await video.screenshot(type="png", timeout=2500)
                        candidate_score = frame_score(png)
                        if candidate_score > score:
                            best, score = png, candidate_score
                    except BrowserError:
                        continue
                if best and score > 15:
                    return {"png": best, "source": "视频截图", "title": title, "description": description}

            # Use the beginning of the main content rather than empty page margins.
            roots = page.locator("main, article, [role='main'], body > div")
            clip = None
            for index in range(min(await roots.count(), 3)):
                try:
                    root = roots.nth(index)
                    bounds = await root.bounding_box()
                    if bounds and bounds["width"] > 500 and bounds["height"] > 100:
                        await root.evaluate("node => node.scrollIntoView({block:'start'})")
                        bounds = await root.bounding_box()
                        dimensions = await page.evaluate("({x:scrollX,y:scrollY,w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight})")
                        x = max(0, bounds["x"] + dimensions["x"] - 28)
                        y = max(0, bounds["y"] + dimensions["y"] - 28)
                        width = min(1280, bounds["width"] + 56, dimensions["w"] - x)
                        height = min(800, max(width / 1.9, bounds["height"] + 56), dimensions["h"] - y)
                        clip = {"x": x, "y": y, "width": width, "height": height}
                        break
                except BrowserError:
                    continue
            png = await page.screenshot(type="png", animations="disabled", timeout=5000,
                                        **({"clip": clip} if clip else {}))
            return {"png": png, "source": "网页截图", "title": title, "description": description,
                    "warnings": ["视频无法播放，已使用网页截图。"] if videos else []}
        finally:
            await context.close()
            await browser.close()


def main():
    try:
        data = json.loads(sys.stdin.read(16384))
        result = asyncio.run(capture(data["url"]))
        result["png"] = base64.b64encode(result["png"]).decode("ascii")
        print(json.dumps(result))
    except PreviewError as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)
    except Exception:
        print(json.dumps({"error": "无法生成网页截图。请重新运行启动脚本安装浏览器，或换一个公开链接。"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
