from __future__ import annotations
import re

from affiliate_config import AMAZON_TRACKING_ID
from category_metadata import get_category

# Machine QA runs mechanical/structural checks against the RENDERED HTML plus the
# payload/evidence pair that produced it. It is intentionally separate from
# review_gate.py's content-integrity checks (which run first, before this QA pass).

LEAD_RANGE = (180, 230)
SUMMARY_RANGE = (130, 170)
HOW_TO_CHOOSE_RANGE = (190, 270)
MAIN_RANGE = (3000, 3400)
DESCRIPTION_TARGET_RANGE = (320, 400)
DESCRIPTION_HARD_MIN = 280
H3_LEN_RANGE = (6, 45)
NEAR_DUPLICATE_CONTAINMENT_THRESHOLD = 0.9
REPEATED_SENTENCE_MIN_LEN = 10

# Fixed, internal-ops, or template-filler phrases that must never appear in public copy.
# This list intentionally overlaps review_gate's jargon terms (defense in depth) and
# adds the literal boilerplate sentence fragments the old Python template used to
# stamp into every product card, so a regression back to fixed-template prose is caught.
BANNED_PHRASES = (
    '実在を確認', 'ストアを表示', 'identity', 'verified', 'fail-closed', 'fail closed',
    'discovery', 'retrieval', 'pipeline', 'evidence', 'legacy_asset', 'human_confirmation',
    'writer_status', 'qa_status', 'GROW-', 'observed_price', '観測価格',
    '価格は変動するため、最新の販売条件と仕様はリンク先で確認してください',
)

# Matches an explicit yen amount; must NOT match spec/unit tokens like "4000Pa",
# "1.0L", "1000W", "0.5kg" since those carry no currency symbol or 円 suffix.
FIXED_PRICE_PATTERN = re.compile(r'(?:[¥￥]\s*[0-9][0-9,]*|[0-9][0-9,]*\s*円)')

_TAG_PATTERN = re.compile(r'<[^>]+>')
_MAIN_PATTERN = re.compile(r'<main[^>]*>(.*?)</main>', re.S)


def strip_tags(html_fragment: str) -> str:
    return _TAG_PATTERN.sub('', html_fragment)


def main_text_length(html: str) -> int:
    match = _MAIN_PATTERN.search(html)
    if not match:
        return 0
    return len(strip_tags(match.group(1)))


def check_range(value: int, bounds: tuple[int, int]) -> bool:
    lo, hi = bounds
    return lo <= value <= hi


def find_banned_phrases(text: str) -> list[str]:
    lowered = text.lower()
    return [p for p in BANNED_PHRASES if p.lower() in lowered]


def find_fixed_prices(html: str) -> list[str]:
    visible = re.sub(r'<script\b.*?</script>|<style\b.*?</style>', ' ', html, flags=re.S | re.I)
    return FIXED_PRICE_PATTERN.findall(visible)


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) > 1 else {text}


def containment_similarity(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    inter = len(ba & bb)
    return max(inter / len(ba), inter / len(bb))


def find_near_duplicate_descriptions(descriptions: list[str]) -> list[tuple[int, int, float]]:
    hits = []
    for i in range(len(descriptions)):
        for j in range(i + 1, len(descriptions)):
            score = containment_similarity(descriptions[i], descriptions[j])
            if score >= NEAR_DUPLICATE_CONTAINMENT_THRESHOLD:
                hits.append((i, j, score))
    return hits


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r'(?<=。)', text) if s.strip()]


def find_repeated_sentences(descriptions: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    repeated = []
    for desc in descriptions:
        for sentence in _sentences(desc):
            s = sentence.strip()
            if len(s) < REPEATED_SENTENCE_MIN_LEN:
                continue
            seen[s] = seen.get(s, 0) + 1
    for s, count in seen.items():
        if count > 1:
            repeated.append(s)
    return repeated


def find_shared_openings_endings(descriptions: list[str], prefix_len: int = 12, suffix_len: int = 12):
    openings: dict[str, list[int]] = {}
    endings: dict[str, list[int]] = {}
    for idx, desc in enumerate(descriptions):
        openings.setdefault(desc[:prefix_len], []).append(idx)
        endings.setdefault(desc[-suffix_len:], []).append(idx)
    dup_openings = {k: v for k, v in openings.items() if len(v) > 1}
    dup_endings = {k: v for k, v in endings.items() if len(v) > 1}
    return dup_openings, dup_endings


def run_qa(html: str, payload: dict, evidence: dict) -> dict:
    results: dict = {}

    results['lead_len'] = len(payload['lead'])
    results['lead_in_range'] = check_range(results['lead_len'], LEAD_RANGE)
    results['summary_len'] = len(payload['summary'])
    results['summary_in_range'] = check_range(results['summary_len'], SUMMARY_RANGE)
    results['how_to_choose_len'] = len(payload['how_to_choose'])
    results['how_to_choose_in_range'] = check_range(results['how_to_choose_len'], HOW_TO_CHOOSE_RANGE)
    results['main_len'] = main_text_length(html)
    results['main_in_range'] = check_range(results['main_len'], MAIN_RANGE)

    descriptions = [p['description'] for p in payload['products']]
    desc_lens = [len(d) for d in descriptions]
    results['description_lens'] = desc_lens
    results['description_hard_min_pass'] = all(n >= DESCRIPTION_HARD_MIN for n in desc_lens)
    results['description_target_pass'] = all(check_range(n, DESCRIPTION_TARGET_RANGE) for n in desc_lens)

    h3_lens = [len(p['h3']) for p in payload['products']]
    results['h3_lens'] = h3_lens
    results['h3_len_pass'] = all(check_range(n, H3_LEN_RANGE) for n in h3_lens)

    banned_hits = {}
    all_prose = [payload['h1'], payload['lead'], payload['summary'], payload['how_to_choose'], payload['conclusion']]
    all_prose += [group['title'] for group in payload['comparison_groups']]
    all_prose += [group['angle'] for group in payload['comparison_groups']]
    all_prose += descriptions + [p['h3'] for p in payload['products']]
    for text in all_prose:
        hits = find_banned_phrases(text)
        if hits:
            banned_hits[text[:30]] = hits
    results['banned_phrase_hits'] = banned_hits
    results['banned_phrase_pass'] = not banned_hits

    near_dupes = find_near_duplicate_descriptions(descriptions)
    results['near_duplicate_descriptions'] = near_dupes
    results['description_uniqueness_pass'] = not near_dupes

    repeated = find_repeated_sentences(descriptions)
    results['repeated_sentences'] = repeated
    results['no_repeated_sentences_pass'] = not repeated

    dup_open, dup_end = find_shared_openings_endings(descriptions)
    results['duplicate_openings'] = dup_open
    results['duplicate_endings'] = dup_end
    results['distinct_openings_endings_pass'] = not dup_open and not dup_end

    category = get_category(evidence['category'])
    results['category_pass'] = (
        f'data-category="{category.slug}"' in html
        and f'href="{category.article_path}">{category.label}</a>' in html
    )
    results['nav_footer_pass'] = 'site-header' in html and '>Top<' in html and '<footer>' in html and 'All rights reserved' in html

    canonical_count = html.count(f'href="{evidence["canonical_url"]}"')
    results['canonical_count'] = canonical_count
    results['canonical_pass'] = canonical_count >= 1

    asins = re.findall(r'data-asin="([^"]+)"', html)
    results['asin_count'] = len(asins)
    results['asin_unique_count'] = len(set(asins))
    evidence_asins = {p['asin'] for p in evidence['products']}
    results['asin_pass'] = len(asins) == 6 and set(asins) == evidence_asins

    amazon_tag = AMAZON_TRACKING_ID
    amazon_link_count = len(re.findall(rf'amazon\.co\.jp/dp/[A-Z0-9]+\?tag={re.escape(amazon_tag)}', html))
    rakuten_link_count = html.count('>楽天</a>')
    yahoo_link_count = html.count('>Yahoo</a>')
    results['affiliate_counts'] = {
        'amazon': amazon_link_count, 'rakuten': rakuten_link_count, 'yahoo': yahoo_link_count,
    }
    results['affiliate_pass'] = amazon_link_count == 6 and rakuten_link_count == 6 and yahoo_link_count == 6

    missing_images = [p['image_url'] for p in evidence['products'] if f'src="{p["image_url"]}"' not in html]
    results['missing_product_images'] = missing_images
    results['image_identity_pass'] = not missing_images and evidence['eyecatch_image'] in html

    fixed_prices = find_fixed_prices(html)
    results['fixed_price_hits'] = fixed_prices
    results['fixed_price_pass'] = not fixed_prices

    results['overall_pass'] = all([
        results['lead_in_range'], results['summary_in_range'], results['how_to_choose_in_range'],
        results['main_in_range'], results['description_hard_min_pass'], results['h3_len_pass'],
        results['banned_phrase_pass'], results['description_uniqueness_pass'],
        results['no_repeated_sentences_pass'], results['distinct_openings_endings_pass'],
        results['nav_footer_pass'], results['category_pass'], results['canonical_pass'], results['asin_pass'],
        results['affiliate_pass'], results['image_identity_pass'], results['fixed_price_pass'],
    ])
    return results
