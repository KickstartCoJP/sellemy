from __future__ import annotations
import json,re,sqlite3,urllib.parse
from pathlib import Path
from discovery_adapters import AutositeDiscoveryAdapter

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"data"/"sellemy.db"
ARTICLES=ROOT/"data"/"article_validation_set.json"
IDENTITIES=ROOT/"data"/"product_identity_seed.json"
REPORT=ROOT/"data"/"product_discovery_last_run.json"
ASIN_RE=re.compile(r"^[A-Z0-9]{10}$")

def connect():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def amazon_asin(url):
    m=re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)",url or "",re.I)
    return m.group(1).upper() if m else None

def identity_check(item):
    required=("product_name","asin","image_url","amazon_effective_url")
    missing=[k for k in required if not item.get(k)]
    asin=str(item.get("asin") or "").upper()
    errors=[]
    if missing: errors.append("missing:"+",".join(missing))
    if asin and not ASIN_RE.fullmatch(asin): errors.append("invalid_asin")
    url_asin=amazon_asin(item.get("amazon_effective_url"))
    if asin and url_asin!=asin: errors.append("amazon_url_asin_mismatch")
    if item.get("image_url") and not urllib.parse.urlparse(item["image_url"]).scheme.startswith("http"):
        errors.append("invalid_image_url")
    return errors

def discover(apply=False):
    articles=json.loads(ARTICLES.read_text(encoding="utf-8"))
    catalog=json.loads(IDENTITIES.read_text(encoding="utf-8"))
    by_article={}
    for item in catalog: by_article.setdefault(item["article_file"],[]).append(item)
    results=[]; valid=0; adapter=AutositeDiscoveryAdapter()
    with connect() as c:
        for article in articles:
            candidates=by_article.get(article["file"],[])
            selected=[]
            for rank,item in enumerate(candidates,1):
                errors=identity_check(item)
                state="identity_ready" if not errors else "replacement_required"
                selected.append({"rank":rank,"product_name":item.get("product_name"),
                    "asin":item.get("asin"),"state":state,"errors":errors})
                if errors: continue
                valid+=1
                row=c.execute("SELECT product_id FROM products WHERE article_file=? AND canonical_name=?",
                    (article["file"],item["product_name"])).fetchone()
                if apply and row:
                    c.execute("""UPDATE products SET asin=?,amazon_url=?,image_url=?,
                        status=CASE WHEN status='replacement_required' THEN 'discovered' ELSE status END
                        WHERE product_id=?""",(item["asin"],item["amazon_effective_url"],
                        item["image_url"],row["product_id"]))
            match=re.search(r"！(.+?)(?:6選|トップ6|TOP6|$)",article["title"])
            discovery_query=(match.group(1) if match else article["title"]).strip()
            alternatives=adapter.search_cache(discovery_query,limit=10)
            results.append({"article_file":article["file"],"theme":article["title"],
                "section_conditions":article["section_titles"],"discovery_query":discovery_query,"source_adapters":["affiliate_catalog","autosite_amazon","autosite_yahoo"],
                "candidate_count":len(candidates),"adapter_candidate_count":len(alternatives),
                "identity_ready":sum(x["state"]=="identity_ready" for x in selected),
                "replacement_candidates":[x for x in alternatives if x.get("asin") and x.get("image_url")][:3],"items":selected})
        if apply:c.commit()
    report={"stage":"Product Discovery","mode":"apply" if apply else "dry_run","adapter_health":adapter.health(),
        "articles":len(results),"candidates":sum(x["candidate_count"] for x in results),
        "identity_ready":valid,"replacement_required":sum(len(x["items"]) for x in results)-valid,
        "results":results}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser();p.add_argument("--apply",action="store_true");a=p.parse_args()
    r=discover(a.apply)
    print(json.dumps({k:r[k] for k in ("mode","articles","candidates","identity_ready","replacement_required")},ensure_ascii=False))
