"""Fetch one Amazon item page via isolated visible Chrome in Colima VM."""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path('/Users/kickstart/Library/Application Support/Sellemy/tmp')


def main() -> int:
    url = sys.argv[1]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False, executable_path='/usr/bin/google-chrome')
        try:
            page = browser.new_page()
            response = page.goto(url, wait_until='domcontentloaded', timeout=25000)
            status = response.status if response else None
            if status != 200 or not page.locator('#productTitle').count():
                print(json.dumps({'status_code': status, 'url': page.url}))
                return 2
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=OUTPUT_DIR,
                                             prefix='amazon-vm-item-', suffix='.html',
                                             delete=False) as stream:
                stream.write(page.content())
                path = Path(stream.name)
            os.chmod(path, 0o600)
            print(json.dumps({'status_code': status, 'url': page.url, 'html_path': str(path)}))
            return 0
        finally:
            browser.close()


if __name__ == '__main__':
    raise SystemExit(main())
