from __future__ import annotations
import concurrent.futures, json, sqlite3, ssl, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUT = ROOT / "data" / "cutover" / "TASK-PJ6-PROD-CUTOVER-001"
CTX = ssl._create_unverified_context()

def fetch_image(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 SellemyCutoverAudit/1.0"})
        with urllib.request.urlopen(req, timeout=30, context=CTX) as response:
            head = response.read(32)
            content_type = response.headers.get("content-type", "")
            return {"url": url, "status": response.status, "content_type": content_type,
                    "bytes_sampled": len(head), "pass": response.status == 200 and content_type.startswith("image/") and bool(head)}
    except Exception as exc:
        return {"url": url, "status": 0, "error": f"{type(exc).__name__}: {exc}", "pass": False}

def main():
    before = json.load(open(CUT / "before" / "snapshot.json"))
    after = json.load(open(CUT / "after" / "snapshot.json"))
    bmap = {x["requested_url"]: x for x in before["urls"]}
    comparisons = []
    for a in after["urls"]:
        b = bmap[a["requested_url"]]
        checks = {
            "same_url": a["effective_url"] == b["effective_url"] == a["requested_url"],
            "http_200": a["http_status"] == 200,
            "title_preserved": a["title"] == b["title"] and bool(a["title"]),
            "h1_preserved": a["h1"] == b["h1"] and len(a["h1"]) == 1,
            "meta_preserved": a["meta_description"] == b["meta_description"] and bool(a["meta_description"]),
            "og_preserved": a["og"] == b["og"] and len(a["og"]) == 5,
            "jsonld_preserved": a["jsonld"] == b["jsonld"],
            "internal_links_preserved": a["internal_links"] == b["internal_links"],
            "canonical_added": a["canonical"] == a["requested_url"],
            "cards_6": a["product_cards"] == 6,
            "asins_6": len(a["asins"]) == 6,
            "affiliate_links": len(a["affiliate_links"]) >= 6,
            "fixed_price_0": len(a["fixed_price_hits"]) == 0,
        }
        comparisons.append({"url": a["requested_url"], "checks": checks, "pass": all(checks.values()),
                            "before_sha256": b["sha256"], "after_sha256": a["sha256"],
                            "after": {"cards": a["product_cards"], "asins": len(a["asins"]),
                                      "affiliate_links": len(a["affiliate_links"]), "images": len(a["images"])}})
    db = sqlite3.connect(ROOT / "data" / "sellemy.db")
    db.row_factory = sqlite3.Row
    products = [dict(x) for x in db.execute("select article_file,product_id,asin,amazon_url,image_url from products order by product_id")]
    identity = [{"product_id": x["product_id"],
                 "asin_in_url": bool(x["asin"] and x["asin"] in x["amazon_url"]),
                 "affiliate_tag": "tag=" in x["amazon_url"], "image_not_dummy": "dummy" not in x["image_url"].lower()}
                for x in products]
    urls = sorted({x["image_url"] for x in products})
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        image_results = list(pool.map(fetch_image, urls))
    validation = json.load(open(ROOT / "data" / "article_validation_set.json"))
    sitemap = after["supporting"]["sitemap.xml"]["text"]
    robots = after["supporting"]["robots.txt"]["text"]
    quality = json.load(open(ROOT / "data" / "pipeline_last_run.json"))
    quality_checks = {
        "writer_10_10": sum(x["writer_status"] == "pass" for x in quality["results"]) == 10,
        "fact_10_10": sum(x["fact_audit"] == "pass" for x in quality["results"]) == 10,
        "comparison_10_10": sum(x["comparison_audit"] == "pass" for x in quality["results"]) == 10,
        "seo_spam_10_10": sum(x["seo_spam_audit"] == "pass" for x in quality["results"]) == 10,
        "identity_60_60": sum(y["pass"] for x in quality["results"] for y in x["identity_audit"]) == 60,
    }
    report = {
        "task_id": "TASK-PJ6-PROD-CUTOVER-001", "production_commit": "50b2a50fd7d1f39af3b4bb98eacd137cde7617b8",
        "urls": comparisons, "images": image_results, "identity": identity, "quality": quality_checks,
        "supporting": {"sitemap_200": after["supporting"]["sitemap.xml"]["http_status"] == 200,
                       "sitemap_10_10": all("https://www.sellemy.jp" + x["url"] in sitemap for x in validation),
                       "robots_200": after["supporting"]["robots.txt"]["http_status"] == 200,
                       "robots_allows": "Allow: /" in robots},
    }
    report["pass"] = (all(x["pass"] for x in comparisons) and all(x["pass"] for x in image_results)
                      and all(all(v for k, v in x.items() if k != "product_id") for x in identity)
                      and all(quality_checks.values()) and all(report["supporting"].values()))
    (CUT / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "urls": sum(x["pass"] for x in comparisons),
                      "images": sum(x["pass"] for x in image_results), "identity": len(identity),
                      "quality": quality_checks, "supporting": report["supporting"]}, ensure_ascii=False))
    raise SystemExit(0 if report["pass"] else 1)

if __name__ == "__main__":
    main()
