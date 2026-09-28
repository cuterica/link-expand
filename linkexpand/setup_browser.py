"""Use installed Chrome/Edge, or install Playwright Chromium on first run."""

from pathlib import Path
import subprocess
import sys

from playwright.sync_api import sync_playwright

from .capture import browser_executable


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if browser_executable():
        return
    with sync_playwright() as runtime:
        if Path(runtime.chromium.executable_path).is_file():
            return
    print("首次使用：正在安装截图浏览器，请稍候。", flush=True)
    if getattr(sys,'frozen',False):
        from playwright.__main__ import main as playwright_main
        sys.argv=['playwright','install','chromium'];playwright_main()
    else:sys.exit(subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"]))


if __name__ == "__main__":
    main()
