"""CEO 2026-11-19: planning intent -> product recommendation -> writer -> independent QA."""
from unittest.mock import patch

import planning_runtime as planning
from editorial_plan import build_editorial_plan
from editorial_qa import make_input, review_prompt
from product_selection import select_six
from writer_runtime import _prompt


AXES = [
    {'id': 'small', 'label': '少量を小分け', 'keywords': ['小分け']},
    {'id': 'flat', 'label': '平らに保存', 'keywords': ['平ら']},
    {'id': 'bulky', 'label': 'かさのある食品', 'keywords': ['マチ']},
]
TOPIC = {
    'slug': 'freezer-bag-special', 'category': 'dailygoods', 'product_theme': '食品用冷凍保存袋',
    'query': '食品用 冷凍保存袋 おすすめ 小分け', 'intent_key': '食品用冷凍保存袋を用途で選ぶ',
    'reader_persona': {
        'reader': '食品を冷凍する家庭の人', 'knowledge_level': '入門',
        'purchase_goal': '用途に適した冷凍用の袋を選ぶ',
        'editorial_depth': '確かな根拠を提示する',
    },
    'comparison_axes': AXES,
    'signals': dict.fromkeys(
        ('search_demand', 'purchase_intent', 'product_viability',
         'seasonality', 'profitability', 'evidence_availability'), 0.6),
}
OLD = {**TOPIC, 'slug': 'prior-freezer-bags', 'query': '食品用 冷凍保存袋 おすすめ'}


def test_generic_existing_topic_not_reintroduced_by_rewording():
    assert planning._same_purchase_intent(TOPIC, OLD)
    assert planning._same_purchase_intent(
        {**TOPIC, 'product_theme': '蛇口直結型浄水器',
         'query': '蛇口直結型浄水器 おすすめ 選び方 カートリッジ交換'},
        {**OLD, 'product_theme': '蛇口直結型浄水器',
         'query': '蛇口直結型浄水器 おすすめ 選び方'})
    assert not planning._same_purchase_intent(
        {**TOPIC, 'query': '食品用 冷凍保存袋 業務用 大容量'},
        {**OLD, 'query': '食品用 冷凍保存袋 赤ちゃん用 少量'})


def test_distinct_product_class_or_reader_search_remains_eligible():
    assert not planning._same_purchase_intent(
        TOPIC, {**OLD, 'product_theme': '食品用密閉容器'})
    assert not planning._same_purchase_intent(
        {**TOPIC, 'query': '食品用冷凍保存袋 小分け'},
        {**OLD, 'query': '食品用冷凍保存袋 業務用 大量まとめ買い'})


def test_rank_candidates_uses_existing_active_topic_without_new_ledger(tmp_path):
    import json
    articles = tmp_path / 'articles.json'
    articles.write_text('[]')
    with patch.object(planning, 'ARTICLES', articles), patch.object(
        planning, '_active_topic_intents', return_value=[OLD]
    ):
        ranked = planning.rank_candidates([TOPIC], {'topic_metrics': [], 'product_metrics': []})
    assert ranked == []


def test_six_candidate_product_handoff_preserves_specific_evidence():
    candidates = []
    for i, axis in enumerate(AXES):
        for j in range(2):
            n = i*2+j
            candidates.append({
                'asin': f'B{n:09d}', 'discovery_axis_id': axis['id'],
                'amazon_title': f'モデル{n}', 'brand': f'製造元{n}',
                'sources': [{'verified': True, 'source_type': 'marketplace_item',
                             'excerpt': f'モデル{n}は{n+1}cmの容器'}],
            })
    selected, selection = select_six(candidates, TOPIC, {})
    assert selection['candidate_count'] == 6
    for product in selected:
        assert '企画軸' not in product['selection_reason']
        assert 'cm' in product['selection_reason']
        assert '紹介切り口' in product['selection_reason']


def test_editorial_brief_tracks_reader_goal_and_all_article_parts():
    evidence = {
        'products': [
            {'ref': f'p{i}', 'amazon_title': f'独立商品{i}',
             'selection_reason': f'商品{i}の具体的な特色と根拠'} for i in range(1, 7)],
        'comparison_axes': AXES,
        'selection': {'axes': []},
    }
    plan = build_editorial_plan(TOPIC, evidence)
    assert plan['search_intent'] == TOPIC['intent_key']
    assert plan['reader_value_goal'] == TOPIC['reader_persona']['purchase_goal']
    assert all(p['selection_reason'] for p in plan['products'])
    assert '読者が用途を絞れる' in next(x['instruction'] for x in plan['requirements'] if x['id']=='E06')
    prompt = _prompt(TOPIC, evidence)
    assert '商品名を入れ替えても成立する一般論' in prompt
    assert TOPIC['reader_persona']['purchase_goal'] in prompt
    article = {
        'h1':'タイトル','lead':'導入','summary':'概要','how_to_choose':'選び方',
        'conclusion':'結論',
        'comparison_groups':[{'title':str(i),'angle':'用途'} for i in range(3)],
        'products':[{'ref':f'p{i}','h3':'製品','description':'説明'} for i in range(1,7)]
    }
    review = review_prompt(make_input(article, brief=plan))
    assert 'all six product descriptions' in review
    assert 'editorial brief' in review
