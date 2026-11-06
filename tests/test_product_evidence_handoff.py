"""Do not change independently accepted Product QA inputs while creating a job."""
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from quality_fixtures import accepted_fixture
from comparison_acceptance import digest, evidence_content
import growth_role_business as business
import role_stage_exec as stages


def test_approved_selection_survives_handoff_without_rebuilding_from_discovery():
    _, accepted_evidence, receipt = accepted_fixture()
    rows = accepted_evidence['products']
    topic = {'slug': accepted_evidence['slug'], 'category': accepted_evidence['category'],
             'comparison_axes': accepted_evidence['comparison_axes'], '_product_acceptance': receipt,
             '_accepted_selection_asins': [p['asin'] for p in rows],
             '_accepted_selection_metadata': {'axes': []}}
    with (patch.object(business, 'require_clean_current_main', return_value='fake-head'),
          patch('analytics_feedback.load_feedback', return_value={}),
          patch.object(business, 'select_viable_topic', return_value=(topic, rows, [])),
          patch.object(business, 'build_evidence', return_value=accepted_evidence),
          patch('growth_recovery.create_job', return_value={'slug': topic['slug']}) as create):
        stages._create_job(cache_only=True, candidates=[topic], provider=None, admission=None)
    saved = create.call_args.kwargs['evidence']
    assert saved['product_acceptance']['evidence_sha256'] == digest(evidence_content(saved))


def test_topic_selection_passes_exact_six_reviewed_records_not_discovery_rows():
    _, evidence, _ = accepted_fixture()
    discovery = [{k: v for k, v in p.items() if k != 'selection_reason'}
                 for p in evidence['products']]
    selected = [{**p, 'selection_reason': f'grounded reason {i}'}
                for i, p in enumerate(discovery)]
    topic = {'slug': 'handoff-check', 'comparison_axes': evidence['comparison_axes']}
    from unittest.mock import patch
    with (patch.object(business, 'rank_candidates', return_value=[topic]),
          patch.object(business, 'collect_unique_products', return_value=(discovery, {'complete': True})),
          patch.object(business, 'select_six', return_value=(selected, {'axes': []})),
          patch.object(business, 'build_evidence', return_value={'products': selected}),
          patch.object(business, 'independent_review', return_value={'review': {'status': 'ACCEPTED'}}),
          patch.object(business, 'receipt_issues', return_value=[])):
        chosen, passed, _ = business.select_viable_topic([topic], {}, cache_only=True)
    assert chosen['_accepted_selection_asins'] == [p['asin'] for p in selected]
    assert passed == selected
    assert all(p['selection_reason'] for p in passed)
    assert all('selection_reason' not in p for p in discovery)
