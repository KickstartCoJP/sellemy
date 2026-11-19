"""CEO: Planner owns discovery axes; Writer receives fixed product assignments."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from writer_runtime import _prompt

ROOT = Path(__file__).resolve().parents[1]

def test_writer_is_not_taught_discovery_axis_design():
    topic={'slug':'bath-additives','reader_persona':{'purchase_goal':'daily bathing'}}
    evidence={'products':[{'ref':f'p{i}','amazon_title':f'Bath item {i}'} for i in range(1,7)],
              'comparison_axes':[{'id':'ingredients','label':'成分'}, {'id':'scent','label':'香り'}],
              'selection':{'axes':[{'id':'ingredients','product_refs':['p1','p2']},
                                   {'id':'scent','product_refs':['p3','p4']}]}}
    prompt=_prompt(topic,evidence)
    role=(ROOT/'config/writer-editorial-role.md').read_text()
    assert '3つの切り口は' not in prompt+role
    assert '切り口は商品を紹介するための整理' not in prompt+role
    assert '紹介切り口を整理する' not in prompt
    assert '切り口をまたいで' not in prompt
    assert '企画で確定した商品と割当を維持' in prompt
    assert '確定済み割当をそのまま使い' in prompt
    assert '商品に共通する用途・楽しみ方・特徴' in prompt
    assert '制作上の分類や企画の説明を本文に出さない' in prompt
    assert 'Evidence.selection.axes' in prompt
    assert '商品同士の比較・優劣・差異説明を必須' not in role
