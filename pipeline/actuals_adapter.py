from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS = Path.home() / 'Library' / 'Application Support' / 'Sellemy' / 'analytics'
PUBLICATION_METRICS = ANALYTICS / 'article_publication_metrics.json'
GA4_TOTALS = ANALYTICS / 'ga4_daily_totals.json'
GA4_RAW = ANALYTICS / 'ga4_daily_raw.json'
AFFILIATE_DB = ANALYTICS / 'affiliate_actuals.sqlite3'
JST = ZoneInfo('Asia/Tokyo')
UNIT_ID = 'sellemy'
OWNED_METRICS = {'pv', 'affiliate_click', 'ctr', 'revenue', 'orders', 'cost', 'profit', 'published_article_count_daily', 'published_article_count'}


def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding='utf-8'))


def publication_actuals(doc: dict) -> list[dict]:
    updated_at = str(doc['generated_at'])
    rows = []
    monthly_daily = defaultdict(int)
    monthly_cumulative = {}
    for item in doc['daily']:
        day = item['date']
        month = day[:7]
        daily = int(item['published_article_count_daily'])
        cumulative = int(item['published_article_count'])
        monthly_daily[month] += daily
        monthly_cumulative[month] = cumulative
        common = {
            'period': f'DAY_{day}', 'period_start': day, 'period_end': day,
            'period_label': '日次', 'source': 'sellemy_article_metadata', 'updated_at': updated_at,
        }
        rows.append({**common, 'metric_id': 'published_article_count_daily', 'value': daily})
        rows.append({**common, 'metric_id': 'published_article_count', 'value': cumulative})
    for month in sorted(monthly_daily):
        rows.append({'period': month, 'metric_id': 'published_article_count_daily',
                     'value': monthly_daily[month], 'source': 'sellemy_article_metadata', 'updated_at': updated_at})
        rows.append({'period': month, 'metric_id': 'published_article_count',
                     'value': monthly_cumulative[month], 'source': 'sellemy_article_metadata', 'updated_at': updated_at})
    return rows


def ga4_actuals(doc: dict) -> list[dict]:
    if doc.get('grain') != 'date' or doc.get('metric') != 'screenPageViews':
        raise ValueError('GA4 Actuals require date-grain screenPageViews stock')
    updated_at = str(doc['generated_at'])
    daily = {}
    for item in doc['rows']:
        value = item.get('page_views')
        if value is None:
            raise ValueError('GA4 daily total cannot be missing page_views')
        raw_date = str(item['date'])
        day = f'{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}'
        if day in daily:
            raise ValueError(f'duplicate GA4 daily total: {day}')
        daily[day] = int(value)
    rows = []
    monthly = defaultdict(int)
    for day in sorted(daily):
        value = daily[day]
        monthly[day[:7]] += value
        rows.append({'period': f'DAY_{day}', 'period_start': day, 'period_end': day,
                     'period_label': '日次', 'metric_id': 'pv', 'value': value,
                     'source': 'ga4_data_api_screenPageViews', 'updated_at': updated_at})
    for month in sorted(monthly):
        rows.append({'period': month, 'metric_id': 'pv', 'value': monthly[month],
                     'source': 'ga4_data_api_screenPageViews', 'updated_at': updated_at})
    return rows


def ga4_engagement_actuals(doc: dict) -> list[dict]:
    updated_at = str(doc['generated_at'])
    daily = defaultdict(lambda: {'views': 0, 'clicks': 0, 'available': False})
    for item in doc.get('rows', []):
        day = str(item['date'])
        daily[day]['views'] += int(item.get('page_views', 0) or 0)
        if item.get('affiliate_clicks') is not None:
            daily[day]['clicks'] += int(item['affiliate_clicks'])
            daily[day]['available'] = True
    rows = []
    monthly = defaultdict(lambda: {'views': 0, 'clicks': 0, 'available': False})
    for raw_day in sorted(daily):
        value = daily[raw_day]
        if not value['available']:
            continue
        day = f'{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:8]}'
        common = {'period': f'DAY_{day}', 'period_start': day, 'period_end': day,
                  'period_label': '日次', 'source': 'ga4_affiliate_click', 'updated_at': updated_at}
        rows.append({**common, 'metric_id': 'affiliate_click', 'value': value['clicks']})
        rows.append({**common, 'metric_id': 'ctr',
                     'value': round(value['clicks'] / value['views'], 8) if value['views'] else 0.0})
        month = day[:7]
        monthly[month]['views'] += value['views']
        monthly[month]['clicks'] += value['clicks']
        monthly[month]['available'] = True
    for month, value in sorted(monthly.items()):
        rows.append({'period': month, 'metric_id': 'affiliate_click', 'value': value['clicks'],
                     'source': 'ga4_affiliate_click', 'updated_at': updated_at})
        rows.append({'period': month, 'metric_id': 'ctr',
                     'value': round(value['clicks'] / value['views'], 8) if value['views'] else 0.0,
                     'source': 'ga4_affiliate_click', 'updated_at': updated_at})
    return rows


def affiliate_actuals(db_path: Path = AFFILIATE_DB, now: datetime | None = None) -> list[dict]:
    if not db_path.exists():
        return []
    import sqlite3
    now = (now or datetime.now(JST)).astimezone(JST)
    db = sqlite3.connect(db_path)
    raw = db.execute("""SELECT event_date,SUM(order_count),SUM(commission_yen)
      FROM provider_daily WHERE revenue_eligible=1 GROUP BY event_date ORDER BY event_date""").fetchall()
    watermarks = {
        provider: {'covered_from': covered_from, 'covered_to': covered_to, 'state': state}
        for provider, covered_from, covered_to, state in db.execute(
            "SELECT provider,covered_from,covered_to,state FROM provider_watermarks"
        ).fetchall()
    }
    db.close()
    rows = []
    monthly = defaultdict(lambda: {'orders': 0, 'revenue': 0})
    updated_at = now.isoformat()
    for day, orders, revenue in raw:
        common = {'period': f'DAY_{day}', 'period_start': day, 'period_end': day,
                  'period_label': '日次', 'source': 'affiliate_provider_reconciled', 'updated_at': updated_at}
        rows.append({**common, 'metric_id': 'orders', 'value': int(orders or 0)})
        rows.append({**common, 'metric_id': 'revenue', 'value': int(revenue or 0)})
        monthly[day[:7]]['orders'] += int(orders or 0)
        monthly[day[:7]]['revenue'] += int(revenue or 0)
    for month, value in sorted(monthly.items()):
        rows.append({'period': month, 'metric_id': 'orders', 'value': value['orders'],
                     'source': 'affiliate_provider_reconciled', 'updated_at': updated_at})
        rows.append({'period': month, 'metric_id': 'revenue', 'value': value['revenue'],
                     'source': 'affiliate_provider_reconciled', 'updated_at': updated_at})

    current_day = now.date().isoformat()
    current_month = now.strftime('%Y-%m')
    providers = ('amazon', 'rakuten', 'valuecommerce')
    current_coverage_complete = all(
        watermarks.get(provider, {}).get('state') == 'available'
        and (watermarks.get(provider, {}).get('covered_to') or '') >= current_day
        for provider in providers
    )
    if current_coverage_complete and current_month not in monthly:
        source = 'affiliate_provider_reconciled_explicit_zero'
        rows.append({'period': current_month, 'metric_id': 'orders', 'value': 0,
                     'source': source, 'updated_at': updated_at})
        rows.append({'period': current_month, 'metric_id': 'revenue', 'value': 0,
                     'source': source, 'updated_at': updated_at})
    return rows


def explicit_cost_zero(now: datetime | None = None) -> list[dict]:
    now = (now or datetime.now(JST)).astimezone(JST)
    return [{'period': now.strftime('%Y-%m'), 'metric_id': 'cost', 'value': 0,
             'source': 'ceo_confirmed_sellemy_direct_cost_zero', 'updated_at': now.isoformat()}]


def build_actuals(publication: dict, ga4: dict, *, ga4_raw: dict | None = None,
                  affiliate_db: Path = AFFILIATE_DB, now: datetime | None = None) -> list[dict]:
    rows = publication_actuals(publication) + ga4_actuals(ga4)
    if ga4_raw is not None:
        rows += ga4_engagement_actuals(ga4_raw)
    rows += affiliate_actuals(affiliate_db, now=now)
    rows += explicit_cost_zero(now)
    revenue_rows = [row for row in rows if row['metric_id'] == 'revenue']
    for revenue in revenue_rows:
        rows.append({**revenue, 'metric_id': 'profit',
                     'source': revenue['source'] + '+direct_cost_explicit_zero'})
    return rows


def _ai_os_root() -> Path:
    configured = os.environ.get('AI_MANAGEMENT_OS_ROOT')
    candidates = [Path(configured).expanduser()] if configured else []
    candidates += [Path.home() / 'ai-management-os', ROOT.parent / 'ai-management-os']
    for candidate in candidates:
        if (candidate / 'ai_management_os' / 'local' / 'unit_details.py').is_file():
            return candidate
    raise RuntimeError('AI Management OS root not found; set AI_MANAGEMENT_OS_ROOT')


def sync() -> dict:
    publication = _load(PUBLICATION_METRICS)
    ga4 = _load(GA4_TOTALS)
    ga4_raw = _load(GA4_RAW)
    projected = build_actuals(publication, ga4, ga4_raw=ga4_raw)

    ai_os = _ai_os_root()
    sys.path.insert(0, str(ai_os))
    from ai_management_os.local.store import DEFAULT_DB_PATH
    from ai_management_os.local.unit_details import UnitDetails

    details = UnitDetails(DEFAULT_DB_PATH)
    current = details.read()
    if not current.get('available'):
        raise RuntimeError('UnitDetails canonical unavailable')
    unit = next((x for x in current['units'] if x['unit_id'] == UNIT_ID), None)
    if unit is None:
        raise RuntimeError('Sellemy UnitDetails entry missing')

    snapshot = {'version': current['version'], 'source': dict(current['source']), 'units': []}
    for raw in current['units']:
        clone = json.loads(json.dumps(raw, ensure_ascii=False))
        if clone['unit_id'] == UNIT_ID:
            preserved = [row for row in clone['actuals'] if row['metric_id'] not in OWNED_METRICS]
            clone['actuals'] = preserved + projected
        snapshot['units'].append(clone)

    write = details.update(snapshot)
    readback = details.read()
    after = next(x for x in readback['units'] if x['unit_id'] == UNIT_ID)
    got = [row for row in after['actuals'] if row['metric_id'] in OWNED_METRICS]
    if got != projected:
        raise OSError('Sellemy Actuals projection read-back mismatch')

    pv_daily = [x for x in projected if x['metric_id'] == 'pv' and 'period_start' in x]
    pv_monthly = [x for x in projected if x['metric_id'] == 'pv' and 'period_start' not in x]
    pub_daily = [x for x in projected if x['metric_id'] == 'published_article_count_daily' and 'period_start' in x]
    cumulative_daily = [x for x in projected if x['metric_id'] == 'published_article_count' and 'period_start' in x]
    return {
        **write,
        'unit_id': UNIT_ID,
        'actual_count': len(projected),
        'publication_daily_rows': len(pub_daily),
        'publication_latest_daily': pub_daily[-1] if pub_daily else None,
        'publication_latest_cumulative': cumulative_daily[-1] if cumulative_daily else None,
        'ga4_daily_rows': len(pv_daily),
        'ga4_monthly_rows': len(pv_monthly),
        'ga4_total_pv': sum(x['value'] for x in pv_daily),
        'cost_current': next(x for x in projected if x['metric_id'] == 'cost'),
        'revenue_projected': any(x['metric_id'] == 'revenue' for x in projected),
        'profit_projected': any(x['metric_id'] == 'profit' for x in projected),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--sync', action='store_true')
    args = parser.parse_args()
    publication = _load(PUBLICATION_METRICS)
    ga4 = _load(GA4_TOTALS)
    if args.sync:
        print(json.dumps(sync(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(build_actuals(publication, ga4), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
