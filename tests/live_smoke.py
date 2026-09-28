"""Optional check against a running server, including real network and screenshot worker."""
import json
import re
import sys
import urllib.request

port = sys.argv[1] if len(sys.argv) > 1 else '8765'
base = 'http://127.0.0.1:' + port
with urllib.request.urlopen(base, timeout=10) as response:
    html = response.read().decode('utf-8')
token = re.search(r'name="local-token" content="([^"]+)"', html).group(1)
for url, source in [
    ('https://github.com', '网页封面'),
    ('https://example.com', '网页截图'),
    ('https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4', '视频截图'),
]:
    request = urllib.request.Request(base + '/api/preview', data=json.dumps({'url': url}).encode(),
                                     headers={'Content-Type': 'application/json', 'X-Local-Token': token})
    with urllib.request.urlopen(request, timeout=90) as response:
        preview = json.load(response)
    assert preview['visual_source'] == source, preview
    assert preview['visual'], preview
    with urllib.request.urlopen(base + preview['visual'], timeout=10) as response:
        assert response.read(8) == b'\x89PNG\r\n\x1a\n'
    print(json.dumps({'PASS': url, 'source': preview['visual_source'], 'warnings': preview['warnings']}), flush=True)
