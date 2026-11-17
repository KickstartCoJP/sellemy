from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def test_editorial_guidance_uses_natural_japanese_without_word_mandates():
    writer=(ROOT/'pipeline/writer_runtime.py').read_text()
    plan=(ROOT/'pipeline/editorial_plan.py').read_text()
    planning=(ROOT/'pipeline/planning_runtime.py').read_text()
    assert '自然な日本語で、商品固有の魅力と推薦理由を伝えてください' in writer
    assert '「選ぶ」「探す」' not in writer
    assert '出会いを楽しめる自然な一文' not in writer
    assert 'モノとの出会いと発見を主役にする' not in plan
    assert '記事タイトルは作らず' in planning


def test_renderer_avoids_forced_buying_or_searching_headings():
    renderer=(ROOT/'pipeline/renderer.py').read_text()
    assert '<strong>記事の切り口</strong>' in renderer
    assert '<h2>知っておきたいポイント</h2>' in renderer
    assert '<strong>比較軸から選ぶ</strong>' not in renderer
    assert '<h2>選ぶときのポイント</h2>' not in renderer


def test_product_selection_motivated_by_individual_discovery():
    source=(ROOT/'pipeline/product_selection.py').read_text()
    assert '同じ紹介切り口に合う特徴の異なる2商品' in source
    assert 'その商品だけの魅力・読者が興味を持つ理由' in source
    assert '「AよりBが優れる」のような比較を選定理由の定型にしない' in source


def test_planning_starts_from_three_types_of_appeal_not_comparison_axes():
    source=(ROOT/'pipeline/planning_runtime.py').read_text()
    assert 'まず一貫した商品テーマの中で' in source
    assert '朝の一杯を手軽に楽しむ' in source
    assert '価格・機能・サイズの優劣を機械的に3組へ分ける比較企画を初期値にしない' in source


def test_editorial_parts_focus_on_discovering_items():
    source=(ROOT/'pipeline/editorial_plan.py').read_text()
    assert '商品の用途や使う場面を自然に伝える' in source
    assert 'テーマに固有の魅力を中心に作る' in source
    assert 'その商品を知りたくなる固有の魅力' in source
    writer=(ROOT/'pipeline/writer_runtime.py').read_text()
    assert '各商品について、確かな特徴と使い道を説明し' in writer
    assert '必要なら商品間の差異も伝えてください' in writer
