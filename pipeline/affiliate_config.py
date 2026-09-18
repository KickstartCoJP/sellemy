from __future__ import annotations

AMAZON_TRACKING_ID = 'sellemy-22'


def amazon_url(asin: str) -> str:
    value = str(asin or '').strip().upper()
    if not value:
        raise ValueError('ASIN is required')
    return f'https://www.amazon.co.jp/dp/{value}?tag={AMAZON_TRACKING_ID}'
