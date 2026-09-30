"""Select useful page images, then fall back to video or page captures."""

import base64
from io import BytesIO
import json
import os
import subprocess
import sys
import time
import warnings

from PIL import Image, ImageStat, UnidentifiedImageError

from .metadata import MAX_IMAGE, Preview, PreviewError, concise_summary, fetch_resource
from .owned_process import run_worker


def image_score(data: bytes, priority=0) -> float:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as picture:
                width, height = picture.size
                if width < 280 or height < 140 or not 0.55 <= width / height <= 3.4:
                    return -1
                picture = picture.convert("RGB")
                picture.thumbnail((160, 100))
                variation = sum(ImageStat.Stat(picture).stddev) / 3
                if variation < 3:
                    return -1
                area_score = min(width * height / 10000, 180)
                shape_score = max(0, 90 - abs(width / height - 1.9) * 50)
                return priority + area_score + shape_score + min(variation, 60)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning,
            Image.DecompressionBombError):
        return -1


def select_image(preview: Preview) -> bool:
    deadline = time.monotonic() + 18
    best_score, best = -1, None
    candidates = sorted(preview.candidates, key=lambda item: item["priority"], reverse=True)[:6]
    for candidate in candidates:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            break
        try:
            data = fetch_resource(candidate["url"], MAX_IMAGE, timeout=min(6, remaining),headers={'Referer':preview.url,'User-Agent':'Mozilla/5.0'}).body
            score = image_score(data, candidate["priority"])
            if score > best_score:
                best_score, best = score, (candidate, data)
            # A useful social cover/poster already has the highest source priority.
            if score > 1100:
                break
        except PreviewError:
            continue
    if best:
        candidate, preview.image = best
        preview.image_url = candidate["url"]
        preview.visual_source = candidate["source"]
        return True
    return False


def capture_page(url: str) -> dict:
    try:
        process = run_worker(
            ([sys.executable, "--capture-worker"] if getattr(sys, "frozen", False)
             else [sys.executable, "-m", "linkexpand.capture"]),
            json.dumps({"url": url}), 42,
        )
        result = json.loads(process.stdout)
        if process.returncode or "error" in result:
            raise PreviewError(result.get("error", "网页截图失败。"))
        result["image"] = base64.b64decode(result.pop("png"), validate=True)
        return result
    except subprocess.TimeoutExpired:
        raise PreviewError("网页截图超时，已保留文字摘要。") from None
    except (ValueError, OSError):
        raise PreviewError("无法启动截图浏览器，请重新运行启动脚本安装浏览器。") from None


def attach_visual(preview: Preview):
    if preview.image:
        return
    selected = select_image(preview)
    if selected and preview.description:
        return
    try:
        result = capture_page(preview.url)
        if not selected:
            preview.image = result["image"]
            preview.visual_source = result["source"]
        if not preview.description and result.get("description"):
            preview.description = concise_summary(result["description"])
            preview.summary_source = "正文摘录"
            preview.warnings = [item for item in preview.warnings if "没有提供摘要" not in item]
        if preview.title == preview.domain and result.get("title"):
            preview.title = result["title"][:180]
        preview.warnings.extend(result.get("warnings", []))
    except PreviewError as error:
        preview.warnings.append(str(error))
