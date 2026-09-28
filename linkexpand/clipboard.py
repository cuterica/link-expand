"""Windows mixed text/image clipboard formats used by desktop chat clients."""

import ctypes
from ctypes import wintypes
from html import escape
import os
from pathlib import Path
import threading
import time

from .metadata import PreviewError, plain_text
from .sharing import rich_html

LOCK = threading.Lock()


def cf_html(fragment: str, source_url: str) -> bytes:
    prefix = b'<html><head><meta charset="utf-8"></head><body><!--StartFragment-->'
    suffix = b'<!--EndFragment--></body></html>'
    body = fragment.encode('utf-8')
    template = ('Version:1.0\r\nStartHTML:{start:010d}\r\nEndHTML:{end:010d}\r\n'
                'StartFragment:{first:010d}\r\nEndFragment:{last:010d}\r\n'
                f'SourceURL:{source_url}\r\n')
    start = len(template.format(start=0, end=0, first=0, last=0).encode('utf-8'))
    first = start + len(prefix)
    last = first + len(body)
    header = template.format(start=start, end=last + len(suffix), first=first, last=last).encode('utf-8')
    return header + prefix + body + suffix + b'\0'


def chat_xml(text: str, image_path: Path | None) -> bytes:
    image = (f'<EditElement type="1" imagebiztype="0" textsummary="" '
             f'filepath="{escape(str(image_path), quote=True)}" shortcut=""></EditElement>') if image_path else ''
    # CDATA is how native rich text represents literal URLs and message text.
    safe_text = text.replace(']]>', ']]]]><![CDATA[>')
    xml = ('<QQRichEditFormat><Info version="1001"></Info>' + image +
           f'<EditElement type="0"><![CDATA[{chr(10) if image else ""}{safe_text}]]></EditElement>'
           '</QQRichEditFormat>')
    return xml.encode('utf-8') + b'\0'


def set_formats(payloads):
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = [
        (user.CreateWindowExW, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
         wintypes.HINSTANCE, wintypes.LPVOID], wintypes.HWND),
        (user.DestroyWindow, [wintypes.HWND], wintypes.BOOL),
        (user.OpenClipboard, [wintypes.HWND], wintypes.BOOL),
        (user.CloseClipboard, [], wintypes.BOOL),
        (user.EmptyClipboard, [], wintypes.BOOL),
        (user.RegisterClipboardFormatW, [wintypes.LPCWSTR], wintypes.UINT),
        (user.SetClipboardData, [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE),
        (kernel.GlobalAlloc, [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL),
        (kernel.GlobalLock, [wintypes.HGLOBAL], wintypes.LPVOID),
        (kernel.GlobalUnlock, [wintypes.HGLOBAL], wintypes.BOOL),
        (kernel.GlobalFree, [wintypes.HGLOBAL], wintypes.HGLOBAL),
    ]
    for function, arguments, result in signatures:
        function.argtypes, function.restype = arguments, result
    owner = user.CreateWindowExW(0, 'STATIC', 'LinkExpandClipboard', 0, 0, 0, 0, 0, None, None, None, None)
    if not owner:
        raise PreviewError('无法打开图文剪贴板，请重试。')
    opened = False
    allocated = []
    try:
        for key, data in payloads:
            format_id = user.RegisterClipboardFormatW(key) if isinstance(key, str) else key
            handle = kernel.GlobalAlloc(0x0002, len(data))
            if not format_id or not handle:
                if handle:
                    kernel.GlobalFree(handle)
                raise PreviewError('无法准备图文剪贴板。')
            allocated.append([format_id, handle])
            pointer = kernel.GlobalLock(handle)
            if not pointer:
                raise PreviewError('无法写入图文剪贴板。')
            ctypes.memmove(pointer, data, len(data))
            kernel.GlobalUnlock(handle)
        for _ in range(15):
            if user.OpenClipboard(owner):
                opened = True
                break
            time.sleep(.03)
        if not opened or not user.EmptyClipboard():
            raise PreviewError('剪贴板正被其他程序占用，请重试。')
        for pair in allocated:
            if not user.SetClipboardData(pair[0], pair[1]):
                raise PreviewError('图文复制失败，请重试。')
            pair[1] = None  # The allocation now belongs to Windows.
    finally:
        if opened:
            user.CloseClipboard()
        for _, handle in allocated:
            if handle:
                kernel.GlobalFree(handle)
        user.DestroyWindow(owner)


def copy_rich(record, key: str):
    if os.name != 'nt':
        raise PreviewError('当前系统请使用浏览器图文复制。')
    preview, cover = record['preview'], record['cover'] or record['png']
    with LOCK:
        image_path = None
        cache = Path(os.environ['LOCALAPPDATA']) / 'LinkExpand' / 'clipboard'
        if cover:
            cache.mkdir(parents=True, exist_ok=True)
            image_path = cache / (key + '.png')
            image_path.write_bytes(cover)
        text = plain_text(preview)
        fragment = rich_html(preview, cover, image_path.as_uri() if image_path else None)
        set_formats([
            ('QQ_Unicode_RichEdit_Format', chat_xml(text, image_path)),
            ('HTML Format', cf_html(fragment, preview.url)),
            (13, (text + '\0').encode('utf-16-le')),
        ])
        # Keep referenced files after the program exits, so delayed pastes still work.
        if cache.is_dir():
            files = sorted(cache.glob('*.png'), key=lambda file: file.stat().st_mtime, reverse=True)
            for index, file in enumerate(files):
                if file != image_path and (index >= 48 or time.time() - file.stat().st_mtime > 7 * 86400):
                    try:
                        file.unlink()
                    except OSError:
                        pass
