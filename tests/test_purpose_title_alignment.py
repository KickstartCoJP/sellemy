import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_existing_purpose_relation_titles_match_canonical_seed():
    features = json.loads((ROOT / 'config/purpose_features_seed.json').read_text(encoding='utf-8'))['features']
    relations = json.loads((ROOT / 'config/purpose_relation_seed.json').read_text(encoding='utf-8'))['relations']
    canonical = {feature['feature_id']: feature['title'] for feature in features}
    relevant = [row for row in relations if row['feature_id'] in canonical]
    assert len(relevant) == 117
    assert all(row['feature_title'] == canonical[row['feature_id']] for row in relevant)
