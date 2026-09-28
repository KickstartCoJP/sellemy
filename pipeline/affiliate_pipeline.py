from __future__ import annotations

import argparse
import base64
import calendar
import csv
import hashlib
import json
import os
import re
import sqlite3
import ssl
import urllib.parse
import urllib.request
import certifi
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

STATE_DIR = Path.home() / "Library" / "Application Support" / "Sellemy" / "analytics"
RAW_DIR = STATE_DIR / "affiliate-raw"
DB_PATH = STATE_DIR / "affiliate_actuals.sqlite3"
AUDIT_PATH = STATE_DIR / "affiliate_audit.jsonl"
START_DATE = "2025-01-01"
AMAZON_TRACKING_ID = "sellemy-22"
AMAZON_HISTORICAL_TRACKING_ID = "suzuron-22"
AMAZON_TRACKING_CUTOVER_DATE = "2026-09-18"
VC_CONFIG = Path.home() / '.config' / 'valuecommerce' / 'report-api.json'
VC_TOKEN_URL = 'https://api.valuecommerce.com/auth/v1/affiliate/token/'
VC_REPORT_URL = 'https://api.valuecommerce.com/report/v3/affiliate/transaction/'


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def audit(event: str, **detail: Any) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    row = {"at": stamp(), "event": event, **detail}
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS raw_files (
      provider TEXT NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL,
      source_kind TEXT NOT NULL, period_start TEXT, period_end TEXT,
      fetched_at TEXT NOT NULL, byte_count INTEGER NOT NULL,
      PRIMARY KEY(provider, sha256)
    );
    CREATE TABLE IF NOT EXISTS curated_orders (
      provider TEXT NOT NULL, provider_order_id TEXT NOT NULL,
      event_date TEXT NOT NULL, product_id TEXT, product_name TEXT,
      tracking_id TEXT, site_id TEXT, status TEXT,
      order_count INTEGER NOT NULL, gross_sales_yen INTEGER,
      commission_yen INTEGER, source_sha256 TEXT NOT NULL,
      source_row INTEGER NOT NULL, attribution_evidence TEXT,
      PRIMARY KEY(provider, provider_order_id, source_sha256, source_row)
    );
    CREATE TABLE IF NOT EXISTS provider_daily (
      provider TEXT NOT NULL, event_date TEXT NOT NULL, product_id TEXT NOT NULL,
      product_name TEXT, order_count INTEGER NOT NULL,
      gross_sales_yen INTEGER, commission_yen INTEGER,
      revenue_eligible INTEGER NOT NULL, exclusion_reason TEXT,
      source_count INTEGER NOT NULL, computed_at TEXT NOT NULL,
      PRIMARY KEY(provider, event_date, product_id)
    );
    CREATE TABLE IF NOT EXISTS fetch_runs (
      run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
      mode TEXT NOT NULL, status TEXT NOT NULL, detail_json TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS provider_watermarks (
      provider TEXT PRIMARY KEY, covered_from TEXT, covered_to TEXT,
      raw_file_count INTEGER NOT NULL, curated_row_count INTEGER NOT NULL,
      last_success_at TEXT, state TEXT NOT NULL
    );
    """)
    return db


def text(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def integer(value: Any) -> int | None:
    if value in (None, "", "-"):
        return None
    try:
        return int(Decimal(str(value).replace(",", "").replace("¥", "").strip()))
    except (InvalidOperation, ValueError):
        return None


def normalize_date(value: str) -> str:
    raw = value.strip().replace("/", "-").replace(".", "-")
    if len(raw) == 8 and raw.isdigit():
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return date.fromisoformat(raw[:10]).isoformat()


def dict_rows(path: Path) -> Iterable[tuple[int, dict[str, str]]]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    header_at = None
    for i, line in enumerate(raw):
        cols = next(csv.reader([line]))
        joined = "|".join(cols)
        if any(x in joined for x in ("発生日", "注文日", "トラッキングID", "注文番号", "注文ID", "成果発生")):
            header_at = i
            break
    if header_at is None:
        return
    reader = csv.DictReader(raw[header_at:])
    for n, row in enumerate(reader, start=header_at + 2):
        if any(v not in (None, "") for v in row.values()):
            yield n, {str(k or "").strip(): str(v or "").strip() for k, v in row.items()}


def record_raw(db: sqlite3.Connection, provider: str, path: Path, source_kind: str,
               period_start: str | None = None, period_end: str | None = None) -> str:
    digest = sha256(path)
    db.execute("""INSERT INTO raw_files
      (provider,sha256,path,source_kind,period_start,period_end,fetched_at,byte_count)
      VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(provider,sha256) DO UPDATE SET
        path=excluded.path, source_kind=excluded.source_kind,
        period_start=COALESCE(excluded.period_start,raw_files.period_start),
        period_end=COALESCE(excluded.period_end,raw_files.period_end),
        byte_count=excluded.byte_count""",
      (provider, digest, str(path), source_kind, period_start, period_end, stamp(), path.stat().st_size))
    return digest


def amazon_rows(path: Path, digest: str) -> list[tuple]:
    out = []
    for n, row in dict_rows(path):
        tracking = text(row, "トラッキングID", "Tracking ID", "tracking_id")
        day = text(row, "発送日", "注文日", "発生日", "Date", "date")
        if not day:
            continue
        try:
            normalized_day = normalize_date(day)
        except ValueError:
            continue
        product_id = text(row, "ASIN", "商品ASIN", "Product ASIN") or "__unknown__"
        name = text(row, "商品名", "Product Name", "タイトル")
        oid = text(row, "注文ID", "Order ID", "注文番号") or f"{normalized_day}:{product_id}:{n}"
        orders = integer(text(row, "発送済み商品", "注文済み商品", "注文数", "Items Shipped")) or 0
        sales = integer(text(row, "発送済み商品売上", "注文商品売上", "売上", "Revenue"))
        commission = integer(text(row, "紹介料合計", "発送済み商品の紹介料", "Earnings"))
        if normalized_day < AMAZON_TRACKING_CUTOVER_DATE and tracking == AMAZON_HISTORICAL_TRACKING_ID:
            evidence = "tracking_id_historical_exact"
        elif normalized_day >= AMAZON_TRACKING_CUTOVER_DATE and tracking == AMAZON_TRACKING_ID:
            evidence = "tracking_id_current_exact"
        else:
            evidence = "tracking_id_mismatch"
        out.append(("amazon", oid, normalized_day, product_id, name, tracking, None,
                    text(row, "ステータス", "Status"), orders, sales, commission, digest, n, evidence))
    return out


def rakuten_rows(path: Path, digest: str) -> list[tuple]:
    out = []
    for n, row in dict_rows(path):
        day = text(row, "発生日", "注文日", "date")
        if not day:
            continue
        try:
            normalized_day = normalize_date(day)
        except ValueError:
            continue
        product_id = text(row, "商品管理番号", "商品ID", "itemCode", "商品URL") or "__aggregate__"
        name = text(row, "商品名", "ショップ名")
        oid = text(row, "注文番号", "注文ID") or f"{normalized_day}:{product_id}:{n}"
        orders = integer(text(row, "売上件数", "注文件数", "sales")) or 0
        sales = integer(text(row, "売上金額", "購入金額", "amount"))
        commission = integer(text(row, "成果報酬", "報酬額", "rewards"))
        evidence = text(row, "サイト名", "サイトID", "掲載URL", "リンクID")
        evidence = "sellemy_positive" if "sellemy" in evidence.lower() else "no_sellemy_positive_evidence"
        out.append(("rakuten", oid, normalized_day, product_id, name, None, None,
                    text(row, "ステータス"), orders, sales, commission, digest, n, evidence))
    return out


def valuecommerce_rows(path: Path, digest: str) -> list[tuple]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("rows") or doc.get("orders") or doc.get("result") or []
    if isinstance(rows, dict):
        rows = rows.get("orders") or rows.get("items") or []
    out = []
    for n, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        day = text(row, "orderDate", "occurredAt", "date", "注文日", "発生日")
        if not day:
            continue
        try:
            normalized_day = normalize_date(day)
        except ValueError:
            continue
        site_id = text(row, "siteId", "site_id")
        product_id = text(row, "productId", "itemCode", "product_id", "programOid") or "__unknown__"
        name = text(row, "productName", "itemName", "product_name", "programName", "merchantName")
        oid = text(row, "orderId", "order_id", "注文番号", "transactionOid") or f"{normalized_day}:{product_id}:{n}"
        out.append(("valuecommerce", oid, normalized_day, product_id, name, None, site_id,
                    text(row, "status", "orderStatus", "approvalStatus"), integer(text(row, "quantity", "orderCount", "itemQuantity")) or 1,
                    integer(text(row, "sales", "amount", "orderAmount", "itemPriceTotal")),
                    integer(text(row, "commission", "reward", "commissionAmount", "affilPaymentNet")),
                    digest, n, "valuecommerce_site_id"))
    return out


def file_period(path: Path) -> tuple[str | None, str | None]:
    if path.suffix.lower() == '.json':
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
            start, end = doc.get('from'), doc.get('to')
            if start and end:
                return normalize_date(str(start)), normalize_date(str(end))
        except (ValueError, json.JSONDecodeError):
            pass
    match = re.search(r'(20\d{2})-(0[1-9]|1[0-2])', path.name)
    if match:
        year, month = map(int, match.groups())
        return f'{year:04d}-{month:02d}-01', f'{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}'
    match = re.search(r'(20\d{2})', path.name)
    if match:
        year = int(match.group(1))
        return f'{year:04d}-01-01', f'{year:04d}-12-31'
    return None, None


def ingest_all(db: sqlite3.Connection, raw_dir: Path = RAW_DIR) -> dict[str, int]:
    for stale in db.execute("SELECT provider,sha256,path FROM raw_files").fetchall():
        if not Path(stale["path"]).is_file():
            db.execute(
                "DELETE FROM raw_files WHERE provider=? AND sha256=?",
                (stale["provider"], stale["sha256"]),
            )
    counts = {"amazon": 0, "rakuten": 0, "valuecommerce": 0}
    for path in sorted(raw_dir.glob("*")):
        if not path.is_file():
            continue
        low = path.name.lower()
        if low.startswith("amazon-official-zero-") and path.suffix.lower() == ".json":
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("status") != "explicit_zero":
                continue
            period_start = str(doc["period_start"])
            period_end = str(doc["period_end"])
            record_raw(db, "amazon", path, "official_zero_report", period_start, period_end)
            db.execute(
                "DELETE FROM curated_orders WHERE provider=? AND event_date BETWEEN ? AND ?",
                ("amazon", period_start, period_end),
            )
            counts["amazon"] += 0
            continue
        if low.startswith("amazon") and path.suffix.lower() == ".csv":
            provider, parser = "amazon", amazon_rows
        elif low.startswith("rakuten") and path.suffix.lower() == ".csv":
            provider, parser = "rakuten", rakuten_rows
        elif low.startswith("valuecommerce") and path.suffix.lower() == ".json":
            provider, parser = "valuecommerce", valuecommerce_rows
        else:
            continue
        period_start, period_end = file_period(path)
        digest = record_raw(db, provider, path, "official_report", period_start, period_end)
        rows = parser(path, digest)
        if period_start and period_end:
            db.execute(
                "DELETE FROM curated_orders WHERE provider=? AND event_date BETWEEN ? AND ?",
                (provider, period_start, period_end),
            )
        db.executemany("""INSERT OR REPLACE INTO curated_orders
          (provider,provider_order_id,event_date,product_id,product_name,tracking_id,site_id,status,
           order_count,gross_sales_yen,commission_yen,source_sha256,source_row,attribution_evidence)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        counts[provider] += len(rows)
    db.commit()
    return counts


def reconcile(db: sqlite3.Connection) -> dict[str, Any]:
    db.execute("DELETE FROM provider_daily")
    db.execute("""INSERT INTO provider_daily
      SELECT provider,event_date,COALESCE(product_id,'__unknown__'),MAX(product_name),
        SUM(order_count),SUM(gross_sales_yen),SUM(commission_yen),
        CASE
          WHEN provider='amazon' AND attribution_evidence IN ('tracking_id_historical_exact','tracking_id_current_exact') THEN 1
          WHEN provider='valuecommerce' THEN 1
          WHEN provider='rakuten' AND attribution_evidence='sellemy_positive' THEN 1
          ELSE 0 END,
        CASE
          WHEN provider='amazon' AND attribution_evidence NOT IN ('tracking_id_historical_exact','tracking_id_current_exact') THEN 'tracking_id_not_sellemy'
          WHEN provider='rakuten' AND attribution_evidence<>'sellemy_positive' THEN 'no_sellemy_positive_evidence'
          ELSE NULL END,
        COUNT(*),?
      FROM curated_orders
      WHERE event_date>=?
      GROUP BY provider,event_date,COALESCE(product_id,'__unknown__'),attribution_evidence""", (stamp(), START_DATE))
    providers = ("amazon", "valuecommerce", "rakuten")
    for provider in providers:
        raw = db.execute("SELECT COUNT(*),MIN(period_start),MAX(period_end) FROM raw_files WHERE provider=?", (provider,)).fetchone()
        cur = db.execute("SELECT COUNT(*),MIN(event_date),MAX(event_date) FROM curated_orders WHERE provider=?", (provider,)).fetchone()
        covered_from = cur[1] or raw[1]
        covered_to = cur[2] or raw[2]
        state = "available" if raw[0] else "missing"
        db.execute("""INSERT OR REPLACE INTO provider_watermarks
          (provider,covered_from,covered_to,raw_file_count,curated_row_count,last_success_at,state)
          VALUES(?,?,?,?,?,?,?)""", (provider, covered_from, covered_to, raw[0], cur[0], stamp(), state))
    db.commit()
    vc = db.execute("""SELECT COUNT(*) FROM provider_daily
      WHERE provider='valuecommerce'""").fetchone()[0]
    yahoo = 0
    if yahoo and vc:
        raise RuntimeError("Yahoo and ValueCommerce double count guard failed")
    eligible = db.execute("SELECT COUNT(*),COALESCE(SUM(commission_yen),0) FROM provider_daily WHERE revenue_eligible=1").fetchone()
    return {"eligible_daily_rows": eligible[0], "commission_yen": eligible[1],
            "watermarks": [dict(x) for x in db.execute("SELECT * FROM provider_watermarks ORDER BY provider")]}


def nested_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = nested_value(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = nested_value(child, key)
            if found is not None:
                return found
    return None


def fetch_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=60, context=context) as response:
        return json.loads(response.read())


def fetch_valuecommerce(start: str, end: str) -> Path:
    if not VC_CONFIG.is_file():
        raise RuntimeError('ValueCommerce report API credential file is missing')
    cfg = json.loads(VC_CONFIG.read_text(encoding='utf-8'))
    signature = base64.b64encode(
        f"{cfg['client_key']}|{cfg['client_secret']}".encode()
    ).decode()
    token_url = VC_TOKEN_URL + '?' + urllib.parse.urlencode({'grant_type': 'client_credentials'})
    token_doc = fetch_json(token_url, {'Authorization': f'Bearer {signature}', 'Accept': 'application/json'})
    token = nested_value(token_doc, 'bearer_token')
    if not token:
        raise RuntimeError('ValueCommerce token response had no bearer_token')
    rows: list[dict] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({
            'criteria': 'o', 'from_date': start, 'to_date': end,
            'approval_status': 'p,a,c,i', 'via_ad': 'true', 'via_vpc': 'false',
            'limit': 1000, 'offset': offset,
        })
        doc = fetch_json(VC_REPORT_URL + '?' + query,
                         {'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
        page_rows = nested_value(doc, 'rowData') or []
        if isinstance(page_rows, dict):
            page_rows = [page_rows]
        rows.extend(x for x in page_rows if isinstance(x, dict))
        next_offset = nested_value(doc, 'nextOffset')
        if next_offset in (None, -1, '-1') or int(next_offset) <= offset:
            break
        offset = int(next_offset)
    result = {'provider': 'valuecommerce', 'site_id': str(cfg.get('site_id', '')),
              'from': start, 'to': end, 'rows': rows}
    path = RAW_DIR / f'valuecommerce-api-{start}-{end}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def run(mode: str, *, fetch: bool = False, start: str = START_DATE, end: str | None = None,
        db_path: Path = DB_PATH, raw_dir: Path = RAW_DIR) -> dict[str, Any]:
    end = end or date.today().isoformat()
    run_id = hashlib.sha256(f"{mode}:{start}:{end}:{stamp()}".encode()).hexdigest()[:20]
    db = connect(db_path)
    db.execute("INSERT INTO fetch_runs VALUES(?,?,NULL,?,'running','{}')", (run_id, stamp(), mode))
    try:
        fetched = []
        if fetch:
            cursor = date.fromisoformat(start)
            final = date.fromisoformat(end)
            while cursor <= final:
                month_end = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
                chunk_end = min(month_end, final)
                fetched.append(str(fetch_valuecommerce(cursor.isoformat(), chunk_end.isoformat())))
                cursor = chunk_end + timedelta(days=1)
        counts = ingest_all(db, raw_dir)
        result = {"run_id": run_id, "mode": mode, "range": [start, end],
                  "fetched": fetched, "ingested": counts, **reconcile(db)}
        db.execute("UPDATE fetch_runs SET completed_at=?,status='succeeded',detail_json=? WHERE run_id=?",
                   (stamp(), json.dumps(result, ensure_ascii=False), run_id))
        db.commit()
        audit("affiliate_pipeline_succeeded", **result)
        return result
    except Exception as exc:
        db.execute("UPDATE fetch_runs SET completed_at=?,status='failed',detail_json=? WHERE run_id=?",
                   (stamp(), json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), run_id))
        db.commit()
        audit("affiliate_pipeline_failed", run_id=run_id, error_type=type(exc).__name__, message=str(exc))
        raise
    finally:
        db.close()


def export_actuals(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    db = connect(db_path)
    rows = [dict(x) for x in db.execute("""SELECT provider,event_date,product_id,product_name,order_count,
      gross_sales_yen,commission_yen,revenue_eligible,exclusion_reason,source_count
      FROM provider_daily ORDER BY event_date,provider,product_id""")]
    db.close()
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("backfill", "daily"), default="daily")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--start-date")
    p.add_argument("--end-date")
    p.add_argument("--export-actuals", action="store_true")
    a = p.parse_args()
    start = a.start_date or (START_DATE if a.mode == "backfill" else (date.today() - timedelta(days=7)).isoformat())
    result = run(a.mode, fetch=a.fetch, start=start, end=a.end_date)
    if a.export_actuals:
        result["actuals"] = export_actuals()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
