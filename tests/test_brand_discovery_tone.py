from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def test_editorial_guidance_prefers_encounters_not_banned_verbs():
    writer=(ROOT/'pipeline/writer_runtime.py').read_text()
    plan=(ROOT/'pipeline/editorial_plan.py').read_text()
    planning=(ROOT/'pipeline/planning_runtime.py').read_text()
    assert 'Sellemyは検索・比較・購入判断だけを急がせるサイトではなく' in writer
    assert '動詞自体は禁止ではなく' in writer
    assert '同じ語尾や同じ構文を連続させない' in writer
    assert 'モノとの出会いと発見を主役にする' in plan
    assert '「〇〇から選ぶ」「〇〇を探す」に収束することを避け' in planning


def test_renderer_avoids_forced_buying_or_searching_headings():
    renderer=(ROOT/'pipeline/renderer.py').read_text()
    assert '<strong>記事の切り口</strong>' in renderer
    assert '<h2>知っておきたいポイント</h2>' in renderer
    assert '<strong>比較軸から選ぶ</strong>' not in renderer
    assert '<h2>選ぶときのポイント</h2>' not in renderer
