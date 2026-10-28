from unittest.mock import patch
import pytest
import product_candidate_registry as registry
from product_selection import select_six, ProductSelectionError


def test_ng_persists_and_cannot_be_selected(tmp_path):
    path = tmp_path / 'candidates.json'
    with patch.object(registry, 'REGISTRY', path):
        registry.set_ng('B000000001', 'independent QA failure')
        assert path.exists() and registry.read_registry()['B000000001']['ng'] is True
        assert registry.is_ng('B000000001')
        rows = []
        axes = [{'id':a, 'keywords':[a]} for a in ('small','quiet','safe')]
        for i in range(6):
            axis = axes[i//2]['id']
            rows.append({'asin':f'B{i:09d}', 'amazon_title':f'Model {i}',
                         'discovery_axis_id':axis, 'sources':[{'verified':True,'excerpt':axis}]})
        # Explicitly verify a flagged candidate is excluded before AI invocation.
        with patch('product_selection.subprocess.run') as ai:
            with pytest.raises(ProductSelectionError):
                select_six(rows, {'comparison_axes':axes}, {})
            ai.assert_not_called()
