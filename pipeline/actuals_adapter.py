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
JST = ZoneInfo('Asia/Tokyo')
UNIT_ID = 'sellemy'
OWNED_METRICS = {'pv', 'cost', 'published_article_count_daily', 'published_article_count'}


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


def explicit_cost_zero(now: datetime | None = None) -> list[dict]:
    now = (now or datetime.now(JST)).astimezone(JST)
    return [{'period': now.strftime('%Y-%m'), 'metric_id': 'cost', 'value': 0,
             'source': 'ceo_confirmed_sellemy_direct_cost_zero', 'updated_at': now.isoformat()}]


def build_actuals(publication: dict, ga4: dict, *, now: datetime | None = None) -> list[dict]:
    return publication_actuals(publication) + ga4_actuals(ga4) + explicit_cost_zero(now)


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
    projected = build_actuals(publication, ga4)

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
        'revenue_projected': False,
        'profit_projected': False,
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
