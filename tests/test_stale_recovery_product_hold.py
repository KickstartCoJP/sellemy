"""Regression: unchanged legacy product pool does not re-enter expensive search."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from role_stage_exec import _inspect
from growth_role_business import GrowthRuntimeError


def test_stale_pending_reselection_transitions_to_existing_hold():
    state = {'status': 'ACTIVE', 'current_stage': 'WRITER_PENDING',
             'last_failure_code': 'NEEDS_PRODUCT_RESELECTION'}
    evidence = {'products': [], 'product_acceptance': None}
    artifacts = {'evidence.json': evidence, 'topic.json': {}, 'selection.json': {},
                 'product-blocker.json': {'needs_product_reselection': True,
                                          'reasons': ['candidate pool exhausted; return to Planning']},
                 'product-attempt-fingerprint.json': {'signature': 'unchanged'}}
    called = []
    def hold(slug, reasons):
        called.append((slug, reasons))
        state['current_stage'] = 'PRODUCT_HOLD'
    with patch('growth_recovery.load_job', side_effect=lambda s: dict(state)), \
         patch('growth_recovery.read_artifact', side_effect=lambda s, name, default=None: artifacts.get(name, default)), \
         patch('growth_recovery.request_product_reselection', side_effect=hold):
        try:
            _inspect('stale-product', cache_only=True)
        except GrowthRuntimeError as exc:
            assert str(exc) == 'planning_restart_required for stale-product'
        else:
            raise AssertionError('recovery must stop after unchanged search was held')
    assert called == [('stale-product', ['candidate pool exhausted; return to Planning'])]


def test_new_product_search_without_prior_fingerprint_still_reselects():
    state = {'status': 'ACTIVE', 'current_stage': 'WRITER_PENDING',
             'last_failure_code': 'NEEDS_PRODUCT_RESELECTION'}
    artifacts = {'evidence.json': {'products': []}, 'topic.json': {}, 'selection.json': {},
                 'product-blocker.json': {'needs_product_reselection': True,
                                          'reasons': ['candidate pool exhausted; return to Planning']}}
    with patch('growth_recovery.load_job', return_value=state), \
         patch('growth_recovery.read_artifact', side_effect=lambda s, name, default=None: artifacts.get(name, default)):
        assert _inspect('new-product', cache_only=True)['action'] == 'product'
