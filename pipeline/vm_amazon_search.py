"""Amazon search in an isolated visible Chrome inside the Colima VM."""
from __future__ import annotations
import json
import sys
from discovery_adapters import parse_amazon_search_results
from playwright.sync_api import sync_playwright


def main() -> int:
    url = sys.argv[1]
    limit = int(sys.argv[2])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False, executable_path='/usr/bin/google-chrome')
        try:
            page = browser.new_page()
            response = page.goto(url, wait_until='domcontentloaded', timeout=25000)
            status = response.status if response else None
            if status != 200:
                print(f'Amazon VM Chrome HTTP {status}', file=sys.stderr)
                return 2
            rows = parse_amazon_search_results(page.content(), limit=limit)
            sys.stdout.write(json.dumps(rows, ensure_ascii=False))
            return 0
        finally:
            browser.close()


if __name__ == '__main__':
    raise SystemExit(main())
