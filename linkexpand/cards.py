"""Render a portable PNG card with system fonts and optional cover image."""

from io import BytesIO
import os
from pathlib import Path
import warnings

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .metadata import Preview

Image.MAX_IMAGE_PIXELS = 24_000_000
WIDTH = 960


def font(size: int, bold=False):
    custom = os.environ.get("LINK_EXPAND_FONT")
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = [custom, str(windows / "msyhbd.ttc" if bold else windows / "msyh.ttc"),
                  "/System/Library/Fonts/PingFang.ttc",
                  "/System/Library/Fonts/STHeiti Medium.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in candidates:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default(size=size)


def wrap(text, face, width, max_lines):
    lines, current = [], ""
    # Character-aware wrapping supports both Chinese and long URLs.
    for character in text:
        if face.getlength(current + character) > width and current:
            lines.append(current.rstrip())
            current = character.lstrip()
        else:
            current += character
    if current:
        lines.append(current.rstrip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and face.getlength(lines[-1] + "…") > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def original_visual(preview: Preview) -> bytes | None:
    if not preview.image:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(preview.image)) as source:
                source.seek(0)
                source = ImageOps.exif_transpose(source)
                # Flatten transparent pictures onto the same white card background.
                rgba = source.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                cover = background.convert("RGB")
                cover.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
        output = BytesIO()
        cover.save(output, "PNG")
        return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError,
            Image.DecompressionBombWarning, Image.DecompressionBombError):
        preview.warnings.append("缩略图格式无法显示，已生成文字卡片。")
        return None


def thumbnail(preview: Preview) -> bytes | None:
    visual = original_visual(preview)
    if not visual:
        return None
    with Image.open(BytesIO(visual)) as picture:
        cover = ImageOps.fit(picture, (WIDTH, 480), method=Image.Resampling.LANCZOS)
        output = BytesIO()
        cover.save(output, "PNG")
        return output.getvalue()


def render_card(preview: Preview, cover: bytes | None = None) -> bytes:
    title_font, body_font = font(42, True), font(30)
    small_font, url_font = font(25), font(23)
    title_lines = wrap(preview.title, title_font, WIDTH - 108, 3)
    body_lines = wrap(preview.description, body_font, WIDTH - 108, 4)
    url_lines = wrap(preview.url, url_font, WIDTH - 108, 2)
    top = 480 if cover else 0
    height = top + 140 + len(title_lines) * 56 + len(body_lines) * 44 + len(url_lines) * 34
    canvas = Image.new("RGB", (WIDTH, height), "#ffffff")
    if cover:
        with Image.open(BytesIO(cover)) as picture:
            canvas.paste(picture, (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((24, top + 28, 30, height - 28), radius=3, fill="#36a77d")
    x, y = 54, top + 28
    site = wrap(preview.site_name, small_font, WIDTH - 108, 1)
    draw.text((x, y), site[0] if site else preview.domain, font=small_font, fill="#278562")
    y += 44
    for line in title_lines:
        draw.text((x, y), line, font=title_font, fill="#213a33")
        y += 56
    if body_lines:
        y += 10
        for line in body_lines:
            draw.text((x, y), line, font=body_font, fill="#62736c")
            y += 44
    y += 18
    for line in url_lines:
        draw.text((x, y), line, font=url_font, fill="#288361")
        y += 34
    output = BytesIO()
    canvas.save(output, "PNG", optimize=True)
    return output.getvalue()
