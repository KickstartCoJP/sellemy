from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
from google.oauth2 import service_account

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "ga4.json"
AFFILIATE_EVENT_NAMES = {"affiliate_click", "click"}  # click = legacy Sellemy event name
ARTICLE_RE = re.compile(r"^/article/(beauty|dailygoods|gadget)/([^/]+)\.html$")


def load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = ("property_id", "credential_file", "output_file", "feedback_file")
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise ValueError(f"missing GA4 config: {', '.join(missing)}")
    return cfg


def _path(value: str) -> Path:
    return Path(value).expanduser()


def client_from_config(cfg: dict) -> BetaAnalyticsDataClient:
    creds = service_account.Credentials.from_service_account_file(
        _path(cfg["credential_file"]),
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    return BetaAnalyticsDataClient(credentials=creds)


def run_page_report(client: BetaAnalyticsDataClient, property_id: str, days: int) -> list[dict]:
    response = client.run_report(RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
        metrics=[Metric(name="screenPageViews"), Metric(name="sessions"), Metric(name="activeUsers")],
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        limit=100000,
    ))
    return [{
        "page_path": row.dimension_values[0].value,
        "page_title": row.dimension_values[1].value,
        "page_views": int(row.metric_values[0].value or 0),
        "sessions": int(row.metric_values[1].value or 0),
        "active_users": int(row.metric_values[2].value or 0),
    } for row in response.rows]


def run_affiliate_report(client: BetaAnalyticsDataClient, property_id: str, days: int) -> list[dict]:
    response = client.run_report(RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name="pagePath"), Dimension(name="eventName")],
        metrics=[Metric(name="eventCount")],
        date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
        limit=100000,
    ))
    rows = []
    for row in response.rows:
        event_name = row.dimension_values[1].value
        if event_name not in AFFILIATE_EVENT_NAMES:
            continue
        rows.append({
            "page_path": row.dimension_values[0].value,
            "event_name": event_name,
            "event_count": int(row.metric_values[0].value or 0),
        })
    return rows


def build_feedback(raw: dict) -> dict:
    views = {row["page_path"]: row["page_views"] for row in raw["pages"]}
    clicks: dict[str, int] = {}
    for row in raw["affiliate_click_events"]:
        clicks[row["page_path"]] = clicks.get(row["page_path"], 0) + row["event_count"]

    topic_metrics = []
    category_totals = {c: {"views": 0, "clicks": 0} for c in ("beauty", "dailygoods", "gadget")}
    for path, page_views in sorted(views.items()):
        match = ARTICLE_RE.match(path)
        if not match:
            continue
        category, slug = match.groups()
        page_clicks = clicks.get(path, 0)
        category_totals[category]["views"] += page_views
        category_totals[category]["clicks"] += page_clicks
        topic_metrics.append({
            "category": category,
            "intent_key": slug,
            "page_path": path,
            "page_views": page_views,
            "affiliate_clicks": page_clicks,
            "affiliate_ctr": round(page_clicks / page_views, 6) if page_views else 0.0,
        })

    for category, totals in category_totals.items():
        page_views, page_clicks = totals["views"], totals["clicks"]
        topic_metrics.append({
            "category": category,
            "intent_key": "*",
            "page_views": page_views,
            "affiliate_clicks": page_clicks,
            "affiliate_ctr": round(page_clicks / page_views, 6) if page_views else 0.0,
        })

    return {
        "source": "GA4",
        "generated_at": raw["generated_at"],
        "property_id": raw["property_id"],
        "range_days": raw["range_days"],
        "topic_metrics": topic_metrics,
        "product_metrics": [],
    }


def sync(days: int = 28) -> tuple[dict, dict]:
    cfg = load_config()
    client = client_from_config(cfg)
    generated_at = datetime.now(timezone.utc).isoformat()
    raw = {
        "source": "GA4",
        "generated_at": generated_at,
        "property_id": cfg["property_id"],
        "measurement_id": cfg.get("measurement_id"),
        "range_days": days,
        "pages": run_page_report(client, cfg["property_id"], days),
        "affiliate_click_events": run_affiliate_report(client, cfg["property_id"], days),
    }
    feedback = build_feedback(raw)
    for key, value in (("output_file", raw), ("feedback_file", feedback)):
        output = _path(cfg[key])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return raw, feedback


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=28)
    args = parser.parse_args()
    raw, feedback = sync(args.days)
    print("GA4_SYNC_OK")
    print(f"PAGES {len(raw['pages'])}")
    print(f"AFFILIATE_EVENT_ROWS {len(raw['affiliate_click_events'])}")
    print(f"TOPIC_METRICS {len(feedback['topic_metrics'])}")


if __name__ == "__main__":
    main()
