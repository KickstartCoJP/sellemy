from __future__ import annotations

import argparse
import calendar
import json
import shutil
import ssl
import tempfile
import time
import urllib.request
import zipfile
from datetime import date, timedelta
from urllib.parse import urljoin
from pathlib import Path
from typing import Iterable

import certifi
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options

FIREFOX = str(Path.home() / "Applications/FirefoxESR/Firefox.app/Contents/MacOS/firefox")
PROFILE_ROOT = Path.home() / "Library/Application Support/Sellemy/browser-profiles"
RAW_DIR = Path.home() / "Library/Application Support/Sellemy/analytics/affiliate-raw"


class CollectorError(RuntimeError):
    pass


class AuthenticationRequired(CollectorError):
    pass


def months(start: str, end: str) -> Iterable[str]:
    cursor = date.fromisoformat(start).replace(day=1)
    final = date.fromisoformat(end).replace(day=1)
    while cursor <= final:
        yield cursor.strftime("%Y-%m")
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)


def driver(profile_name: str, download_dir: Path) -> webdriver.Firefox:
    profile = PROFILE_ROOT / profile_name
    if not profile.is_dir():
        raise CollectorError(f"Firefox profile missing: {profile_name}")
    download_dir.mkdir(parents=True, exist_ok=True)
    runtime_profile = download_dir / "firefox-profile"
    shutil.copytree(
        profile,
        runtime_profile,
        ignore=shutil.ignore_patterns(".parentlock", "lock", ".startup-incomplete"),
    )
    opts = Options()
    opts.binary_location = FIREFOX
    opts.add_argument("-headless")
    opts.add_argument("-no-remote")
    opts.add_argument("-profile")
    opts.add_argument(str(runtime_profile))
    opts.set_preference("browser.download.folderList", 2)
    opts.set_preference("browser.download.dir", str(download_dir))
    opts.set_preference("browser.download.useDownloadDir", True)
    opts.set_preference("browser.helperApps.neverAsk.saveToDisk",
                        "text/csv,application/csv,application/octet-stream")
    return webdriver.Firefox(options=opts)
def wait_download(directory: Path, before: set[Path], timeout: int = 90) -> Path:
    deadline = time.time() + timeout
    while time.time() < deadline:
        candidates = {p for p in directory.iterdir() if p.is_file()}
        partial = [p for p in candidates if p.suffix in {".part", ".tmp"}]
        completed = sorted(candidates - before, key=lambda p: p.stat().st_mtime)
        if completed and not partial:
            return completed[-1]
        time.sleep(1)
    raise CollectorError("official report download timed out")


def install(downloaded: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".new")
    shutil.copy2(downloaded, temporary)
    if temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        raise CollectorError("provider returned an empty file")
    temporary.replace(destination)
    return destination


def collect_rakuten(start: str, end: str, raw_dir: Path = RAW_DIR) -> list[Path]:
    temp = Path(tempfile.mkdtemp(prefix="sellemy-rakuten-"))
    out: list[Path] = []
    browser = driver("rakuten-affiliate", temp)
    try:
        browser.get("https://affiliate.rakuten.co.jp/")
        if "login.account.rakuten.com" in browser.current_url:
            raise AuthenticationRequired("Rakuten Firefox session requires interactive reauthentication")
        cookie = "; ".join(f"{x['name']}={x['value']}" for x in browser.get_cookies())
        for month in months(start, end):
            url = f"https://affiliate.rakuten.co.jp/api/report/download/monthly?format=csv&date={month}"
            request = urllib.request.Request(url, headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0"})
            destination = raw_dir / f"rakuten-monthly-{month}.csv"
            temporary = temp / f"{month}.csv"
            context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(request, timeout=60, context=context) as response:
                temporary.write_bytes(response.read())
            out.append(install(temporary, destination))
    finally:
        browser.quit()
        shutil.rmtree(temp, ignore_errors=True)
    return out


def _first(browser: webdriver.Firefox, selectors: list[str]):
    for selector in selectors:
        try:
            element = browser.find_element(By.CSS_SELECTOR, selector)
            if element.is_displayed():
                return element
        except NoSuchElementException:
            pass
    return None
def collect_amazon(start: str, end: str, raw_dir: Path = RAW_DIR) -> list[Path]:
    temp = Path(tempfile.mkdtemp(prefix="sellemy-amazon-"))
    browser = driver("amazon-associates", temp)
    try:
        browser.get("https://affiliate.amazon.co.jp/home/reports")
        if "/ap/signin" in browser.current_url or browser.find_elements(By.CSS_SELECTOR, "input[type=password]"):
            raise AuthenticationRequired("Amazon Firefox session requires interactive reauthentication")

        launcher = browser.find_element(By.ID, "ac-report-download-launcher-osp")
        browser.execute_script("arguments[0].click()", launcher)
        time.sleep(1)
        from_value = date.fromisoformat(start).strftime("%m/%d/%Y")
        to_value = date.fromisoformat(end).strftime("%m/%d/%Y")
        date_values = (
            ("ac-daterange-custom-filter-report-download-timeInterval", "custom"),
            ("ac-daterange-val-from-report-download-timeInterval", from_value),
            ("ac-daterange-val-to-report-download-timeInterval", to_value),
            ("ac-daterange-cal-input-from-report-download-timeInterval", from_value),
            ("ac-daterange-cal-input-to-report-download-timeInterval", to_value),
        )
        for element_id, value in date_values:
            element = browser.find_element(By.ID, element_id)
            browser.execute_script(
                "arguments[0].value=arguments[1];"
                "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                element,
                value,
            )
        filter_element = browser.find_element(By.ID, "ac-daterange-custom-filter-report-download-timeInterval")
        browser.execute_script("arguments[0].setAttribute('data-ac-daterange-filter','custom')", filter_element)
        ok_button = browser.find_element(By.ID, "ac-daterange-ok-button-report-download-timeInterval-announce")
        browser.execute_script("arguments[0].click()", ok_button)
        time.sleep(1)

        formats = browser.find_elements(By.CSS_SELECTOR, "input[name=reportDownloadExportFormat]")
        csv_format = next((element for element in formats if (element.get_attribute("value") or "").upper() == "CSV"), None)
        if csv_format is None:
            raise CollectorError("Amazon official CSV format control was not found")
        browser.execute_script("arguments[0].click()", csv_format)

        snapshot_script = r"""
            return Array.from(document.querySelectorAll('tr')).map(function(row) {
                const link = row.querySelector("a[title='ダウンロード']");
                return {text: row.textContent.trim().replace(/\s+/g, ' '), href: link ? link.href : null};
            }).filter(function(row) { return row.text; });
        """
        prior_snapshot = browser.execute_script(snapshot_script)
        prior_rows = {row["text"] for row in prior_snapshot}
        prior_hrefs = {row["href"] for row in prior_snapshot if row["href"]}
        generate = browser.find_element(By.ID, "ac-reports-download-generate-osp-announce")
        browser.execute_script("arguments[0].click()", generate)

        deadline = time.time() + 180
        export_href = None
        zero_result = None
        polls = 0
        while time.time() < deadline:
            time.sleep(5)
            refresh = browser.find_element(By.ID, "ac-report-download-refresh-link-osp")
            browser.execute_script("arguments[0].click()", refresh)
            time.sleep(2)
            polls += 1
            snapshot = browser.execute_script(snapshot_script)
            new_rows = [row for row in snapshot if row["text"] not in prior_rows]
            candidates = [
                row for row in snapshot
                if row["href"] or "データがありませんでした" in row["text"]
            ]
            eligible_new = [
                row for row in new_rows
                if row["href"] or "データがありませんでした" in row["text"]
            ]
            result_rows = eligible_new
            export_href = next(
                (row["href"] for row in result_rows if row["href"]),
                None,
            )
            zero_result = next(
                (row["text"] for row in result_rows if "データがありませんでした" in row["text"]),
                None,
            )
            visible_zero = browser.execute_script("""
                return Array.from(document.querySelectorAll('body *')).some(function(element) {
                    const style = window.getComputedStyle(element);
                    return element.children.length === 0
                        && style.display !== 'none' && style.visibility !== 'hidden'
                        && element.textContent.includes('成果データがありません');
                });
            """)
            if visible_zero and polls >= 1:
                zero_result = "成果データがありません"
            if export_href is not None or zero_result is not None:
                break
        if zero_result is not None:
            destination = raw_dir / f"amazon-official-zero-{start}-{end}.json"
            payload = {
                "provider": "amazon",
                "period_start": start,
                "period_end": end,
                "status": "explicit_zero",
                "evidence": zero_result,
                "observed_url": browser.current_url,
            }
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            return [destination]
        if export_href is None:
            raise CollectorError("Amazon official report generation timed out")

        cookie = "; ".join(f"{item['name']}={item['value']}" for item in browser.get_cookies())
        request = urllib.request.Request(
            export_href,
            headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0"},
        )
        archive = temp / "amazon-report.zip"
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=60, context=context) as response:
            archive.write_bytes(response.read())
        with zipfile.ZipFile(archive) as package:
            csv_names = [name for name in package.namelist() if name.lower().endswith(".csv")]
            if len(csv_names) != 1:
                raise CollectorError(f"Amazon report ZIP contained {len(csv_names)} CSV files")
            extracted = temp / "amazon-report.csv"
            extracted.write_bytes(package.read(csv_names[0]))
        destination = raw_dir / f"amazon-official-{start}-{end}.csv"
        return [install(extracted, destination)]
    finally:
        browser.quit()
        shutil.rmtree(temp, ignore_errors=True)


def collect(providers: list[str], start: str, end: str) -> dict[str, object]:
    result: dict[str, object] = {"range": [start, end], "providers": {}}
    for provider in providers:
        fn = collect_amazon if provider == "amazon" else collect_rakuten
        try:
            files = fn(start, end)
            result["providers"][provider] = {"status": "succeeded", "files": [str(x) for x in files]}
        except AuthenticationRequired as exc:
            result["providers"][provider] = {"status": "authentication_required", "error": str(exc)}
        except Exception as exc:
            result["providers"][provider] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    return result
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", action="append", choices=("amazon", "rakuten"))
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", default=date.today().isoformat())
    args = parser.parse_args()
    providers = args.provider or ["amazon", "rakuten"]
    result = collect(providers, args.start_date, args.end_date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(row["status"] != "succeeded" for row in result["providers"].values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
