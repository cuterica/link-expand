"""Frozen entry point; reuse the existing server and screenshot worker unchanged."""

import sys

from linkexpand.capture import main as capture_main
from linkexpand.server import main as server_main
from linkexpand.media_scan import main as scan_main


if __name__ == "__main__":
    if sys.argv[1:] == ["--capture-worker"]:
        capture_main()
    elif sys.argv[1:] == ['--media-scan-worker']:
        scan_main()
    else:
        server_main()
