"""A CEO reworked published-preview job must restart from theme, not stale article."""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'pipeline'))
import ceo_review_replanning as mod
from growth_recovery import read_artifact


SLUG = 'test-reviewed-sticker-safe-item'
THEME = '防水マット'


def _state(**overrides):
    return {'slug': SLUG, 'category': 'dailygoods',
            'source': 'runtime', 'status': 'ACTIVE',
            'current_stage': 'PLANNING_PENDING', 'published_commit': None, **overrides}


def _read(slug, key, default=None):
    return {
        'planning-restart-required.json': {'required_stage': 'PLANNING',
                                           'product_theme': THEME,
                                           'retain_slug': SLUG,
                                           'retain_category': 'dailygoods'},
        'ceo-review-decision.json': {'decision': '差戻し'},
        'topic.json': {'title': '古いタイトル'},
        'payload.json': {'h1': '古い原稿'},
    }.get(key, default)


def test_explicit_ceo_rework_replans_and_invalidates_earlier_artifacts():
    topic = {'slug': SLUG, 'category': 'dailygoods', 'product_theme': THEME}
    products = [{'ref': str(i)} for i in range(6)]
    writes = {}
    def capture(slug, name, val): writes[name] = val
    with patch.object(mod, 'load_job', return_value=_state()), \
         patch.object(mod, 'read_artifact', side_effect=_read), \
         patch.object(mod, 'write_artifact', side_effect=capture), \
         patch('planning_runtime.replan_published_topic', return_value=(topic, {'provider': 'mock'})) as planner, \
         patch('growth_role_business.collect_unique_products', return_value=(products, {'complete': True})), \
         patch('product_selection.select_six', return_value=(products, {'axes': []})), \
         patch('analytics_feedback.load_feedback', return_value={}), \
         patch('growth_role_business.build_evidence', return_value={'slug': SLUG, 'products': products}), \
         patch('semantic_review.independent_review', return_value={'status': 'ACCEPTED'}), \
         patch('comparison_acceptance.product_issues', return_value=[]), \
         patch('growth_recovery.replace_product_evidence') as replacement:
        result = mod.replan_one(SLUG)
    assert result['action'] == 'writer'
    assert result['product_qa'] == 'PASS'
    assert planner.call_args.kwargs == {'slug': SLUG, 'category': 'dailygoods', 'product_theme_name': THEME}
    assert 'title' not in writes['topic.json']
    assert writes['planning-restart-required.json']['required_stage'] == 'completed'
    replacement.assert_called_once()


def test_without_ceo_rework_remains_nonrunnable():
    for invalid in (
        {'decision': '承認'}, {'decision': None}
    ):
        def read(slug, key, default=None):
            val = _read(slug, key, default)
            if key == 'ceo-review-decision.json':
                return invalid
            return val
        with patch.object(mod, 'load_job', return_value=_state()), \
             patch.object(mod, 'read_artifact', side_effect=read), \
             patch('planning_runtime.replan_published_topic') as planner:
            try:
                mod.replan_one(SLUG)
            except ValueError as exc:
                assert 'not authorized' in str(exc)
            else:
                raise AssertionError('must fail closed')
            planner.assert_not_called()


def test_no_published_or_reviewer_pending_restart():
    for state in (_state(published_commit='a'*40), _state(current_stage='CHAT_REVIEW_PENDING'),
                  _state(source='legacy_repair')):
        with patch.object(mod, 'load_job', return_value=state), \
             patch.object(mod, 'read_artifact', side_effect=_read):
            try:
                mod.replan_one(SLUG)
            except ValueError:
                pass
            else:
                raise AssertionError('source/review/URL boundary bypass')


def test_planning_failure_holds_one_job_and_preserves_old_content():
    written={}
    def capture(_slug, name, val): written[name]=val
    with patch.object(mod, 'load_job', return_value=_state()), \
         patch.object(mod, 'read_artifact', side_effect=_read), \
         patch.object(mod, 'write_artifact', side_effect=capture), \
         patch('planning_runtime.replan_published_topic', side_effect=RuntimeError('provider unavailable')), \
         patch.object(mod, 'set_stage') as stage, \
         patch('growth_recovery.replace_product_evidence') as replacement:
        result=mod.replan_one(SLUG)
    assert result['action']=='hold'
    assert written['planning-restart-required.json']['required_stage']=='PLANNING'
    stage.assert_called_once()
    assert stage.call_args.args[1]=='PLANNING_HOLD'
    replacement.assert_not_called()


def test_route_requires_explicit_restart_marker():
    from role_stage_exec import _inspect
    state=_state()
    with patch('growth_recovery.load_job',return_value=state), \
         patch('growth_recovery.read_artifact',side_effect=_read):
        result=_inspect(SLUG,cache_only=False)
    assert result['action']=='replan'


def test_only_ceo_reworked_hold_is_schedulable_once():
    from growth_recovery import eligible_job
    base = _state(current_stage='PLANNING_HOLD', next_retry_at=None)
    def marker(slug, name, default=None):
        return _read(slug, name, default)
    with patch('growth_recovery.all_jobs', return_value=[base]), \
         patch('growth_recovery.read_artifact', side_effect=marker):
        assert eligible_job()['slug'] == SLUG
    def attempted(slug, name, default=None):
        if name == 'planning-restart-required.json':
            return {**_read(slug, name, default), 'attempted': True}
        return _read(slug, name, default)
    with patch('growth_recovery.all_jobs', return_value=[base]), \
         patch('growth_recovery.read_artifact', side_effect=attempted):
        assert eligible_job() is None
    with patch('growth_recovery.all_jobs', return_value=[{**base, 'source': 'legacy_repair'}]), \
         patch('growth_recovery.read_artifact', side_effect=marker):
        assert eligible_job() is None


def test_replan_commit_marker_is_written_before_external_calls():
    written = []
    def track(_slug, name, obj):
        written.append((name, dict(obj) if isinstance(obj, dict) else obj))
    with patch.object(mod, 'load_job', return_value=_state()), \
         patch.object(mod, 'read_artifact', side_effect=_read), \
         patch.object(mod, 'write_artifact', side_effect=track), \
         patch('planning_runtime.replan_published_topic', side_effect=RuntimeError('transient failure')), \
         patch.object(mod, 'set_stage'):
        mod.replan_one(SLUG)
    assert written[0][0] == 'planning-restart-required.json'
    assert written[0][1]['attempted'] is True
