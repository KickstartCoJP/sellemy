from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import certifi

STATE_DIR = Path.home() / 'Library' / 'Application Support' / 'Sellemy' / 'analytics'
RAW_DIR = STATE_DIR / 'affiliate-raw'
AMAZON_CREDENTIALS = Path.home() / '.config' / 'amazon-associates' / 'creators-api.csv'
STATE_PATH = STATE_DIR / 'affiliate_source_state.json'
MARKETPLACE = 'www.amazon.co.jp'
AMAZON_PARTNER_TAG = 'sellemy-22'
ELIGIBILITY_PROBE_ASIN = 'B0009WCIDQ'


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _model_dict(value: Any) -> dict:
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json', by_alias=True)
    if hasattr(value, 'to_dict'):
        return value.to_dict()
    raise TypeError(f'unsupported SDK response: {type(value).__name__}')


def amazon_inventory() -> dict:
    if not AMAZON_CREDENTIALS.is_file():
        return {
            'provider': 'amazon', 'state': 'auth_not_configured', 'source': 'Amazon Creators API Reporting',
            'marketplace': MARKETPLACE, 'reports': None, 'report_count': None, 'updated_at': _stamp(),
        }
    from creatorsapi_python_sdk.api.default_api import DefaultApi
    from creatorsapi_python_sdk.api_client import ApiClient
    from creatorsapi_python_sdk.configuration import Configuration

    with AMAZON_CREDENTIALS.open(encoding='utf-8-sig') as f:
        row = next(csv.DictReader(f))
    configuration = Configuration(ssl_ca_cert=certifi.where())
    client = ApiClient(
        configuration=configuration,
        credential_id=row['Credential Id'], credential_secret=row['Secret'], version=row['Version'],
    )
    api = DefaultApi(client)
    response = api.list_reports(x_marketplace=MARKETPLACE, _request_timeout=20)
    data = _model_dict(response)
    reports = data.get('reports') or []
    eligibility = {'state': 'unknown'}
    try:
        from creatorsapi_python_sdk.models.get_items_request_content import GetItemsRequestContent
        probe = GetItemsRequestContent(partnerTag=AMAZON_PARTNER_TAG, itemIds=[ELIGIBILITY_PROBE_ASIN])
        api.get_items(x_marketplace=MARKETPLACE, get_items_request_content=probe, _request_timeout=20)
        eligibility = {'state': 'eligible_2xx', 'probe_asin': ELIGIBILITY_PROBE_ASIN}
    except Exception as exc:
        text = str(exc)
        if 'AssociateNotEligible' in text or 'eligibility requirements' in text:
            eligibility = {
                'state': 'associate_not_eligible', 'probe_asin': ELIGIBILITY_PROBE_ASIN,
                'http_status': 403, 'reason': 'AssociateNotEligible',
            }
        else:
            eligibility = {'state': 'probe_error', 'probe_asin': ELIGIBILITY_PROBE_ASIN, 'error_type': type(exc).__name__}
    sanitized = []
    for item in reports:
        if not isinstance(item, dict):
            continue
        sanitized.append({
            'filename': item.get('filename'), 'md5': item.get('md5'), 'size': item.get('size'),
            'lastModified': item.get('lastModified') or item.get('last_modified'),
        })
    return {
        'provider': 'amazon',
        'state': ('reports_available' if sanitized and eligibility['state'] == 'eligible_2xx'
                  else 'creators_api_ineligible' if eligibility['state'] == 'associate_not_eligible'
                  else 'api_verified_reports_empty'),
        'source': 'Amazon Creators API Reporting', 'marketplace': MARKETPLACE,
        'tracking_id': AMAZON_PARTNER_TAG, 'reports': sanitized, 'report_count': len(sanitized),
        'eligibility_probe': eligibility, 'updated_at': _stamp(),
        'zero_semantics': 'reports_empty_or_api_ineligible_is_missing_not_zero',
        'current_primary': 'Associates Central official report export',
        'future_primary': 'Creators API Reporting after eligibility probe returns 2xx',
        'historical_backfill_route': 'Associates Central official report export',
    }


def provider_auth_state() -> list[dict]:
    return [
        {
            'provider': 'rakuten', 'state': 'source_auth_pending',
            'source': 'Rakuten Affiliate official performance report/API/CSV',
            'updated_at': _stamp(), 'zero_semantics': 'source_unavailable_is_missing_not_zero',
        },
        {
            'provider': 'valuecommerce', 'state': 'source_auth_pending',
            'source': 'ValueCommerce official order/report API or export',
            'updated_at': _stamp(), 'zero_semantics': 'source_unavailable_is_missing_not_zero',
        },
    ]


def ingest_official_export(provider: str, source: Path) -> dict:
    provider = provider.lower().strip()
    if provider not in {'amazon', 'rakuten', 'valuecommerce'}:
        raise ValueError('unsupported provider')
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    digest = _sha256(source)
    dest = RAW_DIR / f'{provider}-{digest[:16]}{source.suffix.lower()}'
    if not dest.exists():
        shutil.copy2(source, dest)
    if _sha256(dest) != digest:
        raise OSError('official export copy verification failed')
    return {
        'provider': provider, 'source_file': str(source), 'raw_file': str(dest),
        'sha256': digest, 'ingested_at': _stamp(), 'parsed': False,
        'note': 'Raw official export stock only; no revenue is projected until provider-specific schema is validated.',
    }


def refresh_state() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    providers = [amazon_inventory(), *provider_auth_state()]
    state = {
        'generated_at': _stamp(), 'providers': providers,
        'revenue_actual_ready': False,
        'revenue_value': None,
        'profit_actual_ready': False,
        'profit_value': None,
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    readback = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    if readback != state:
        raise OSError('affiliate source state read-back mismatch')
    return state


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--refresh', action='store_true')
    p.add_argument('--import-provider', choices=['amazon', 'rakuten', 'valuecommerce'])
    p.add_argument('--import-file', type=Path)
    a = p.parse_args()
    if a.import_provider or a.import_file:
        if not a.import_provider or not a.import_file:
            raise SystemExit('--import-provider and --import-file are required together')
        print(json.dumps(ingest_official_export(a.import_provider, a.import_file), ensure_ascii=False, indent=2))
    if a.refresh or (not a.import_provider and not a.import_file):
        print(json.dumps(refresh_state(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
