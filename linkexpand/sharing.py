"""Self-contained rich clipboard card with live text, links, and embedded cover."""

import base64
from html import escape

from .metadata import Preview


def rich_html(preview: Preview, cover: bytes | None, image_uri: str | None = None) -> str:
    url = escape(preview.url, quote=True)
    title = escape(preview.title)
    description = escape(preview.description)
    site = escape(preview.site_name)
    image = ""
    if cover:
        source = image_uri or "data:image/png;base64," + base64.b64encode(cover).decode("ascii")
        image = (
            '<tr><td style="padding:0;">'
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
            f'<img src="{escape(source, quote=True)}" alt="{escape(preview.title, quote=True)}" '
            'width="480" style="display:block;border:0;width:480px;max-width:100%;height:auto;">'
            '</a></td></tr>'
        )
    summary = (f'<p style="margin:8px 0 12px;font-size:14px;line-height:1.7;color:#62736c;">'
               f'{description}</p>') if description else ""
    # Inline styling and a table preserve layout in rich text editors and mail clients.
    # The cover is embedded so it works without access to the local server.
    return (
        '<table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0" '
        'style="width:480px;max-width:100%;border-collapse:collapse;background:#f7faf4;'
        'font-family:Arial,Microsoft YaHei,sans-serif;text-align:left;">'
        f'<tbody>{image}<tr><td style="padding:16px 20px;border-left:3px solid #62aa81;'
        'word-break:break-word;overflow-wrap:anywhere;">'
        f'<p style="margin:0 0 6px;font-size:12px;color:#278562;">{site}</p>'
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        'style="font-size:18px;font-weight:bold;line-height:1.5;color:#213a33;text-decoration:none;">'
        f'{title}</a>{summary}'
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        'style="font-size:12px;line-height:1.6;color:#288361;word-break:break-all;">'
        f'{escape(preview.url)}</a></td></tr></tbody></table>'
    )
