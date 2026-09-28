"""Frozen entry point; reuse the existing server and screenshot worker unchanged."""

import sys

from linkexpand.capture import main as capture_main
from linkexpand.server import main as server_main


if __name__ == "__main__":
    if sys.argv[1:] == ["--capture-worker"]:
        capture_main()
    else:
        server_main()
