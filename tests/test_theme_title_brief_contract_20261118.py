"""CEO scope: theme-only Planner input; title only after Product; unbiased prose prompts."""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
from article_theme import product_theme
import planning_runtime as planner
import writer_runtime
import editorial_plan
import publish_payload


def _candidate():
    return {
        'slug': 'vitamin-c-face-lotion', 'category': 'beauty',
        'query': 'ビタミンC化粧水', 'product_theme': 'ビタミンC化粧水',
        'intent_key': 'vitamin-c-lotion-for-facial-skin',
        'reader_persona': {'reader': 'daily skincare reader', 'knowledge_level': 'general',
                           'purchase_goal': 'daily skincare purchase', 'editorial_depth': 'practical'},
        'comparison_axes': [{'id': f'a{i}', 'label': f'特徴{i}', 'keywords': ['化粧水']} for i in range(3)],
        'signals': {k: .8 for k in ('search_demand', 'purchase_intent', 'product_viability',
                                    'seasonality', 'profitability', 'evidence_availability')},
    }


def _evidence():
    return {
        'products': [{'ref': f'P{i}', 'amazon_title': f'化粧水{i}'} for i in range(6)],
        'comparison_axes': [{'id': f'a{i}', 'label': f'特徴{i}'} for i in range(3)],
        'selection': {'axes': []},
    }


def test_published_catalog_theme_extraction_is_short_and_title_free():
    articles = json.loads((ROOT / 'json/articles.json').read_text())
    assert len(articles) >= 450
    terms = [product_theme(article) for article in articles]
    assert all(terms) and all(len(t) <= 55 for t in terms)
    assert all(not any(token in t for token in ('6選', '比較', '出会い', '選び方')) for t in terms)
    by_slug = {row['slug']: product_theme(row) for row in articles}
    assert by_slug['microwave-steamer-containers-for-weeknight-meals'] == '電子レンジ用蒸し調理容器'
    assert by_slug['rechargeable-aa-aaa-batteries'] == '単3・単4充電池'


def test_planner_context_contains_themes_not_old_title_or_summary():
    with tempfile.TemporaryDirectory() as root:
        articles = Path(root) / 'articles.json'
        articles.write_text(json.dumps([
            {'slug': 'waterproof-mats-6-picks', 'title': '絶対に渡さない旧タイトル｜選び方',
             'product_theme': '防水マット', 'category': 'dailygoods',
             'summary': '絶対に渡さない旧要約'},
        ], ensure_ascii=False))
        seeds = Path(root) / 'seeds.json'
        seeds.write_text('{"categories": {}}')
        with patch.object(planner, 'ARTICLES', articles), patch.object(planner, 'SEEDS', seeds), \
             patch('amazon_search_retry.next_themes', return_value=[]):
            context = planner._context()
    data = json.dumps(context, ensure_ascii=False)
    assert context['existing_article_themes'] == ['防水マット']
    for marker in ('絶対に渡さない旧タイトル', '絶対に渡さない旧要約', 'waterproof-mats-6-picks'):
        assert marker not in data
    assert 'recent_topic_tokens' not in context
    assert not {'existing_slugs', 'recent_articles'} & context.keys()


def test_planner_output_has_no_article_title_field():
    item = planner.PLANNING_SCHEMA['properties']['candidates']['items']
    assert 'title' not in item['required'] and 'title' not in item['properties']
    planner.validate_candidate(_candidate())
    with_title = {**_candidate(), 'title': '防水マット6選｜選ぶ'}
    try:
        planner.validate_candidate(with_title)
    except planner.PlanningError:
        pass
    else:
        raise AssertionError('Planner output must reject an article title')


def test_replan_sends_only_theme_and_preserves_immutable_identity():
    seen = {}
    def route(_role, schema, prompt, _local):
        seen['prompt'] = prompt
        seen['schema'] = schema
        return {'candidates': [_candidate()]}, {}
    with patch.object(planner, 'route', side_effect=route):
        topic, _ = planner.replan_published_topic(
            slug='vitamin-c-face-lotion', category='beauty',
            product_theme_name='ビタミンC化粧水')
    assert topic['product_theme'] == 'ビタミンC化粧水'
    assert 'previous_title' not in seen['prompt']
    assert 'ビタミンC化粧水' in seen['prompt']
    assert seen['schema']['properties']['candidates']['maxItems'] == 1


def test_writer_does_not_receive_planner_or_old_article_title():
    topic = {**_candidate(), 'title': '旧タイトルはWriterへ渡さない'}
    text = writer_runtime._prompt(topic, _evidence())
    assert '旧タイトルはWriterへ渡さない' not in text
    assert 'ビタミンC化粧水' in text
    assert '出会いを楽しめる自然な一文' not in text
    assert '「選ぶ」「探す」' not in text
    plan = editorial_plan.build_editorial_plan(topic, _evidence())
    instructions = ' '.join(r['instruction'] for r in plan['requirements'])
    assert '出会い' not in instructions
    assert '「選ぶ」「探す」' not in instructions


def test_title_hash_is_independent_of_removed_planner_title():
    topic = _candidate()
    before = editorial_plan.build_editorial_plan(topic, _evidence())
    after = editorial_plan.build_editorial_plan({**topic, 'title': '旧記事のタイトル'}, _evidence())
    assert before['topic_hash'] == after['topic_hash']


def test_article_registry_persists_product_theme_from_topic():
    payload = {'slug': 'vitamin-c-face-lotion', 'category': 'beauty',
               'h1': '毎日の保湿に使いたい化粧水6選', 'summary': '商品紹介'}
    evidence = {'eyecatch_image': '/img/test.png'}
    with patch('growth_recovery.read_artifact', return_value=_candidate()):
        entry = publish_payload._article_entry(payload, evidence, existing=None,
                                              now=datetime.now(timezone.utc))
    assert entry['product_theme'] == 'ビタミンC化粧水'
    assert entry['title'] == payload['h1']


def test_all_staged_writer_lexical_sources_have_no_word_mandates():
    source = (ROOT / 'pipeline/writer_runtime.py').read_text()
    plan = (ROOT / 'pipeline/editorial_plan.py').read_text()
    assert '「選ぶ」「探す」「選び方」「探し方」を決まり文句として' not in source
    assert '出会いを楽しめる自然な一文で締め' not in source
    assert '「選ぶ」「探す」を含む決まり文句' not in plan
    assert 'モノとの出会いと発見を主役にする' not in plan


def test_full_responses_writer_input_does_not_reintroduce_title_or_word_mandate():
    """Check WRITER_ROLE + canon + accepted brief + topic/evidence after provider composition."""
    from responses_provider import _shared_prompt
    prompt = writer_runtime._prompt({**_candidate(), 'title': '削除すべき以前のh1'}, _evidence())
    with tempfile.TemporaryDirectory() as tmp:
        brief = Path(tmp) / 'writer-brief.md'
        brief.write_text('自然で具体的な推薦を書く。')
        with patch('responses_provider._runtime_paths', return_value=(None, brief)), \
             patch('responses_provider._ensure_brief', return_value=None), \
             patch('responses_provider._stage_brief_text', return_value='自然な日本語で商品を説明する。'):
            actual = _shared_prompt('writer', prompt)
    assert '削除すべき以前のh1' not in actual
    assert '出会いを楽しめる自然な一文で締め' not in actual
    assert '「選ぶ」「探す」を決まり文句として繰り返さない' not in actual
    assert '「選ぶ」「探す」を含む決まり文句' not in actual
    assert '商品' in actual
