"""Verify the recurring copy-edit lesson reaches Writer, not other stages."""
import json
from pathlib import Path

from owner_brief_learning import load_brief_rules

ROOT = Path(__file__).resolve().parents[1]


def test_existing_writer_brief_reaches_summary_and_conclusion_only():
    writer = load_brief_rules('writer', root=ROOT)
    assert writer.count('要約は答えから書く') == 1
    assert writer.count('結びは要約・本文の判断軸を言い換えて繰り返さず') == 1
    assert '新しい情報のない' in writer
    assert '必要な条件・安全上の注意は残す' in writer
    assert '記事内の商品名・ブランド名・商品固有の特徴・参照を含めない' in writer
    assert '文字数・文数の強制NGを設けない' in writer
    for stage in ('planning', 'product_evidence', 'qa', 'designer', 'publish'):
        assert '要約は答えから書く' not in load_brief_rules(stage, root=ROOT)


def test_existing_brief_budget_and_learning_registry_are_unchanged():
    doc = json.loads((ROOT / 'config/growth-stage-briefs.json').read_text())
    assert len(doc['baseline']['writer']) == 3
    assert doc['rules']['writer'] == []
    assert all(len(rule) <= 250 for rule in doc['baseline']['writer'])
