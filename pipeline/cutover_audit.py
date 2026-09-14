from __future__ import annotations
import argparse, hashlib, json, re, ssl, subprocess, urllib.parse, urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://www.sellemy.jp"
VALIDATION = ROOT / "data" / "article_validation_set.json"
CTX = ssl._create_unverified_context()

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 SellemyCutoverAudit/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as response:
            return response.status, response.geturl(), response.headers.get("content-type", ""), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, url, exc.headers.get("content-type", ""), exc.read()

def one(pattern, text, flags=re.I | re.S):
    match = re.search(pattern, text, flags)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""

def attrs(tag, text):
    return [dict(re.findall(r"""([:\w-]+)\s*=\s*["']([^"']*)["']""", raw))
            for raw in re.findall(fr"<{tag}\b([^>]*)>", text, re.I | re.S)]

def audit_html(url, body):
    text = body.decode("utf-8", "replace")
    metas = attrs("meta", text)
    links = attrs("link", text)
    anchors = attrs("a", text)
    images = attrs("img", text)
    canonical = next((x.get("href", "") for x in links if "canonical" in x.get("rel", "").lower()), "")
    description = next((x.get("content", "") for x in metas if x.get("name", "").lower() == "description"), "")
    og = {x.get("property", ""): x.get("content", "") for x in metas if x.get("property", "").startswith("og:")}
    jsonld = []
    for raw in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.S):
        try: jsonld.append(json.loads(raw))
        except json.JSONDecodeError: jsonld.append({"parse_error": True})
    hrefs = [x.get("href", "") for x in anchors]
    internal = sorted({urllib.parse.urljoin(url, x) for x in hrefs if x and not x.startswith(("#", "mailto:", "tel:", "javascript:")) and urllib.parse.urljoin(url, x).startswith(BASE)})
    affiliates = sorted({x for x in hrefs if ("amazon." in x or "amzn.to" in x) and ("tag=" in x or "amzn.to" in x)})
    cards = len(re.findall(r'<div\b[^>]*class=["\'][^"\']*item-card', text, re.I))
    asins = sorted(set(re.findall(r'data-asin=["\']([A-Z0-9]{10})["\']', text)))
    fixed = re.findall(r'(?:[¥￥]\s*[0-9][0-9,]*|[0-9][0-9,]*\s*円)', re.sub(r'<script\b.*?</script>|<style\b.*?</style>', ' ', text, flags=re.I|re.S))
    return {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "title": one(r"<title[^>]*>(.*?)</title>", text),
            "h1": re.findall(r"<h1\b[^>]*>(.*?)</h1>", text, re.I|re.S), "meta_description": description,
            "canonical": canonical, "og": og, "jsonld": jsonld, "internal_links": internal,
            "affiliate_links": affiliates, "product_cards": cards, "asins": asins,
            "images": sorted({x.get("src", "") for x in images if x.get("src")}), "fixed_price_hits": fixed}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["before", "after"])
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "data" / "cutover" / "TASK-PJ6-PROD-CUTOVER-001" / args.phase
    raw_dir = out / "html"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows = json.loads(VALIDATION.read_text(encoding="utf-8"))
    git = lambda *parts: subprocess.run(["git", *parts], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    report = {"task_id": "TASK-PJ6-PROD-CUTOVER-001", "phase": args.phase, "captured_at": stamp,
              "base_url": BASE, "urls": [], "supporting": {},
              "git": {"head": git("rev-parse", "HEAD"), "origin_main": git("rev-parse", "origin/main"),
                      "remote": git("remote", "get-url", "origin"), "status": git("status", "--porcelain=v1")}}
    for row in rows:
        url = urllib.parse.urljoin(BASE, row["url"])
        status, effective, content_type, body = fetch(url)
        (raw_dir / row["file"]).write_bytes(body)
        audit = audit_html(url, body)
        report["urls"].append({"requested_url": url, "effective_url": effective, "http_status": status,
                               "content_type": content_type, "article_file": row["file"], **audit})
    for name in ("sitemap.xml", "robots.txt"):
        status, effective, content_type, body = fetch(f"{BASE}/{name}")
        (out / name).write_bytes(body)
        report["supporting"][name] = {"http_status": status, "effective_url": effective,
                                      "content_type": content_type, "sha256": hashlib.sha256(body).hexdigest(),
                                      "bytes": len(body), "text": body.decode("utf-8", "replace")}
    out.joinpath("snapshot.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"phase": args.phase, "output": str(out), "urls": len(report["urls"]),
                      "statuses": [x["http_status"] for x in report["urls"]],
                      "sitemap": report["supporting"]["sitemap.xml"]["http_status"],
                      "robots": report["supporting"]["robots.txt"]["http_status"]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
