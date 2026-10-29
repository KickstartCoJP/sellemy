from copy import deepcopy
from quality_fixtures import accepted_fixture
from growth_role_business import build_evidence


def test_reselected_source_ref_cannot_override_canonical_product_refs():
    _, evidence, _ = accepted_fixture()
    topic = {'slug': evidence['slug'], 'category': evidence['category'],
             'comparison_axes': evidence['comparison_axes']}
    products = deepcopy(evidence['products'])
    for product in products:
        product['ref'] = 'p5'
    rebuilt = build_evidence(topic, products)
    assert [p['ref'] for p in rebuilt['products']] == ['p1', 'p2', 'p3', 'p4', 'p5', 'p6']
