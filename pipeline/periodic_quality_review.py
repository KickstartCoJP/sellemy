from __future__ import annotations
import argparse, json, re, urllib.parse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS = Path.home() / "Library/Application Support/Sellemy/analytics"
OUT = ANALYTICS / "periodic-quality-review"
GA4_RAW = ANALYTICS / "ga4_daily_raw.json"
ADAPTIVE = Path.home() / "Library/Application Support/Sellemy/adaptive-publish/feedback.jsonl"
GSC_ROOT = Path.home() / "Library/Application Support/AIManagementOS/sellemy-google-index"
GSC_CREDS = GSC_ROOT / "search_console_credentials.json"
GSC_PROPERTY = "sc-domain:sellemy.jp"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
CONFIG = ROOT / "config" / "periodic_quality_review.json"
PUB_RE = re.compile(r'article:published_time" content="([^"]+)"')
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']')

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default

def article_catalog():
    rows = []
    for category in ("beauty", "dailygoods", "gadget"):
        for path in sorted((ROOT / "article" / category).glob("*.html")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            m = PUB_RE.search(text)
            published = m.group(1) if m else None
            slug = path.stem
            url = f"https://www.sellemy.jp/article/{category}/{path.name}"
            links = set()
            for href in HREF_RE.findall(text):
                if href.startswith("/article/"):
                    links.add("https://www.sellemy.jp" + href.split("#", 1)[0])
                elif href.startswith("https://www.sellemy.jp/article/"):
                    links.add(href.split("#", 1)[0])
            rows.append({"category": category, "slug": slug, "url": url, "path": str(path),
                         "published_at": published, "links": sorted(links)})
    indegree = Counter()
    known = {r["url"] for r in rows}
    for r in rows:
        for target in r["links"]:
            if target in known:
                indegree[target] += 1
    for r in rows:
        r["internal_inlinks"] = indegree[r["url"]]
        r["orphan_candidate"] = indegree[r["url"]] == 0
    return rows

def load_feedback():
    out = {}
    if not ADAPTIVE.exists():
        return out
    for line in ADAPTIVE.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        slug = row.get("slug")
        if slug:
            out[slug] = {
                "content_quality": row.get("content_quality"),
                "topic_novelty": row.get("topic_novelty"),
                "overall": row.get("overall"),
                "evaluated_at": row.get("evaluated_at"),
            }
    return out

def ga4_daily():
    doc = load_json(GA4_RAW, {})
    rows = {}
    for r in doc.get("rows", []):
        page = r.get("page_path") or ""
        if not page.startswith("/article/"):
            continue
        key = (str(r.get("date") or ""), page)
        rows[key] = {
            "page_views": r.get("page_views"),
            "sessions": r.get("sessions"),
            "active_users": r.get("active_users"),
            "affiliate_clicks": r.get("affiliate_clicks"),
        }
    return rows, doc.get("generated_at")

def gsc_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    info = load_json(GSC_CREDS, None)
    if not info:
        raise RuntimeError("search_console_credentials_missing")
    creds = Credentials.from_authorized_user_info(info, scopes=[GSC_SCOPE])
    creds.refresh(Request())
    return creds

def gsc_query(start: date, end: date, dimensions):
    import requests
    creds = gsc_credentials()
    site = urllib.parse.quote(GSC_PROPERTY, safe="")
    url = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": dimensions, "rowLimit": 25000, "dataState": "all"}
    headers = {"Authorization": f"Bearer {creds.token}"}
    quota_path = GSC_ROOT / "quota_project.txt"
    if quota_path.exists():
        quota = quota_path.read_text(encoding="utf-8").strip()
        if quota:
            headers["x-goog-user-project"] = quota
    resp = requests.post(url, headers=headers, json=body, timeout=45)
    resp.raise_for_status()
    return resp.json().get("rows", [])

def normalize_gsc(rows):
    out = {}
    for r in rows:
        keys = r.get("keys") or []
        if len(keys) < 2:
            continue
        d, page = keys[0], keys[1]
        out[(d.replace("-", ""), page)] = {
            "clicks": float(r.get("clicks") or 0),
            "impressions": float(r.get("impressions") or 0),
            "ctr": float(r.get("ctr") or 0),
            "position": float(r.get("position") or 0),
        }
    return out

def published_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None

def aggregate_window(article, days, ga4, gsc, today):
    pub = published_date(article.get("published_at"))
    if not pub:
        return {"state": "missing_published_at", "window_days": days}
    end = min(today, pub + timedelta(days=days - 1))
    if end < pub:
        return {"state": "not_started", "window_days": days}
    ga = {"page_views": 0, "sessions": 0, "active_users": 0, "affiliate_clicks": 0}
    click_known = False
    gs = {"clicks": 0.0, "impressions": 0.0, "position_weighted": 0.0}
    gsc_rows = 0
    cursor = pub
    path = urllib.parse.urlsplit(article["url"]).path
    while cursor <= end:
        k = (cursor.strftime("%Y%m%d"), path)
        row = ga4.get(k)
        if row:
            for key in ("page_views", "sessions", "active_users"):
                ga[key] += int(row.get(key) or 0)
            if row.get("affiliate_clicks") is not None:
                ga["affiliate_clicks"] += int(row.get("affiliate_clicks") or 0)
                click_known = True
        gr = gsc.get((cursor.strftime("%Y%m%d"), article["url"]))
        if gr:
            gs["clicks"] += gr["clicks"]
            gs["impressions"] += gr["impressions"]
            gs["position_weighted"] += gr["position"] * max(gr["impressions"], 1)
            gsc_rows += 1
        cursor += timedelta(days=1)
    affiliate_clicks = ga["affiliate_clicks"] if click_known else None
    affiliate_ctr = (affiliate_clicks / ga["page_views"]) if affiliate_clicks is not None and ga["page_views"] else (0.0 if affiliate_clicks is not None else None)
    return {
        "state": "ok", "window_days": days, "from": pub.isoformat(), "to": end.isoformat(),
        "ga4": {**ga, "affiliate_clicks": affiliate_clicks, "affiliate_ctr": affiliate_ctr},
        "search_console": {
            "state": "ok" if gsc_rows else "missing",
            "clicks": gs["clicks"] if gsc_rows else None,
            "impressions": gs["impressions"] if gsc_rows else None,
            "ctr": (gs["clicks"] / gs["impressions"]) if gs["impressions"] else (0.0 if gsc_rows else None),
            "average_position": (gs["position_weighted"] / gs["impressions"]) if gs["impressions"] else None,
        }
    }

def build_report(fetch_gsc=True, lookback_days=45):
    today = date.today()
    catalog = article_catalog()
    feedback = load_feedback()
    ga4, ga4_generated = ga4_daily()
    gsc_state = "missing"
    gsc_error = None
    gsc = {}
    if fetch_gsc:
        try:
            start = today - timedelta(days=int(lookback_days))
            gsc = normalize_gsc(gsc_query(start, today, ["date", "page"]))
            gsc_state = "ok"
        except Exception as exc:
            gsc_state = "source_unavailable"
            gsc_error = f"{type(exc).__name__}:{str(exc)[:300]}"
    articles = []
    for art in catalog:
        windows = {str(n): aggregate_window(art, n, ga4, gsc, today) for n in (7, 14, 30)}
        articles.append({
            "url": art["url"], "category": art["category"], "slug": art["slug"],
            "published_at": art["published_at"], "age_days": (today - published_date(art["published_at"])).days if published_date(art["published_at"]) else None,
            "internal_inlinks": art["internal_inlinks"], "orphan_candidate": art["orphan_candidate"],
            "quality_feedback": feedback.get(art["slug"], {"state": "missing"}),
            "windows": windows,
        })
    category_counts = Counter(a["category"] for a in articles)
    orphan_count = sum(1 for a in articles if a["orphan_candidate"])
    recent = sorted([a for a in articles if a["published_at"]], key=lambda x: x["published_at"], reverse=True)[:30]
    novelty = [a["quality_feedback"].get("topic_novelty", {}).get("score") for a in articles if isinstance(a["quality_feedback"].get("topic_novelty"), dict)]
    novelty = [float(v) for v in novelty if v is not None]
    quantified_30 = [a for a in articles if a["windows"]["30"]["state"] == "ok" and a["windows"]["30"]["ga4"]["page_views"] > 0]
    top_pv = sorted(quantified_30, key=lambda a: a["windows"]["30"]["ga4"]["page_views"], reverse=True)[:10]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grain": "article_url_x_day raw; 7/14/30 day post-publication windows derived",
        "sources": {
            "ga4": {"state": "ok" if ga4 else "missing", "generated_at": ga4_generated},
            "search_console_search_analytics": {"state": gsc_state, "error": gsc_error},
            "search_console_url_inspection": load_json(GSC_ROOT / "latest.json", {"state": "missing"}),
            "quality_feedback": {"state": "ok" if feedback else "missing"},
            "sns_attribution": {"state": "missing", "reason": "source/medium/campaign or UTM daily fact is not present in the current GA4 raw contract; no inferred lift emitted"},
        },
        "site_quality": {
            "article_count": len(articles),
            "category_counts": dict(category_counts),
            "orphan_candidate_count": orphan_count,
            "recent_30_category_counts": dict(Counter(a["category"] for a in recent)),
            "topic_novelty_average": round(sum(novelty) / len(novelty), 6) if novelty else None,
            "quantified_article_count_30d": len(quantified_30),
        },
        "top_articles_by_30d_pv": [{"url": a["url"], "pv": a["windows"]["30"]["ga4"]["page_views"],
                                     "affiliate_ctr": a["windows"]["30"]["ga4"]["affiliate_ctr"],
                                     "gsc_impressions": a["windows"]["30"]["search_console"]["impressions"],
                                     "gsc_position": a["windows"]["30"]["search_console"]["average_position"]} for a in top_pv],
        "articles": articles,
    }

def due(interval_days):
    latest = OUT / "latest.json"
    if not latest.exists():
        return True
    try:
        generated = datetime.fromisoformat(load_json(latest, {})["generated_at"].replace("Z", "+00:00"))
    except Exception:
        return True
    return datetime.now(timezone.utc) - generated >= timedelta(days=interval_days)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--offline", action="store_true")
    cfg = load_json(CONFIG, {})
    ap.add_argument("--interval-days", type=int, default=None)
    args = ap.parse_args()
    interval_days = int(args.interval_days or cfg.get("interval_days", 14))
    lookback_days = int(cfg.get("search_console_lookback_days", 45))
    if not args.force and not due(interval_days):
        print("PERIODIC_QUALITY_REVIEW_NOT_DUE")
        return
    report = build_report(fetch_gsc=not args.offline, lookback_days=lookback_days)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (OUT / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / f"review-{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PERIODIC_QUALITY_REVIEW_OK")
    print("ARTICLES", report["site_quality"]["article_count"])
    print("GSC", report["sources"]["search_console_search_analytics"]["state"])
    print("ORPHANS", report["site_quality"]["orphan_candidate_count"])
    print("QUANTIFIED_30D", report["site_quality"]["quantified_article_count_30d"])

if __name__ == "__main__":
    main()
