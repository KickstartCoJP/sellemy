"""Existing persisted briefs must not override the new affiliate-first canon."""
from codex_provider import _stage_brief_text
from pathlib import Path


def test_old_runtime_brief_rules_filtered_and_current_strategy_projected(tmp_path):
    brief = tmp_path/'brief.md'
    brief.write_text('''## Fixed rules
- P0 436記事Recovery中はResponses APIのみを使用し、Codex / Claude / local fallbackは禁止。
- Evidence不足はBLOCKED + needs_product_reselection。Astraは不足Evidenceの代替にしない。
- 全段階の公開成立条件。6独立製品・根拠あるtopic固有比較・購入判断支援。
- 実際に存在する商品の同一性を確認する。
## Learned
- 具体的な推薦と読みやすさを優先し、形式検査をする。
''')
    for stage in ('writer','planning'):
        text = _stage_brief_text(brief,stage)
        assert '10,000-article production objective' in text
        assert 'no word/character floor' in text
        assert 'P0 436記事Recovery中はResponses APIのみ' not in text
        assert 'Evidence不足はBLOCKED' not in text
        assert '具体的な推薦と読みやすさを優先' not in text
        assert '実際に存在する商品の同一性' in text
