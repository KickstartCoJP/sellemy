"""Regression: the Writer draft is never rewritten by a separate length-only call."""
from unittest.mock import patch
from role_execution_writer import writer


def test_writer_saves_initial_draft_even_when_length_is_outside_limits():
    draft = {'status': 'READY', 'summary': 'short',
             'how_to_choose': 'short', 'products': [{'ref':f'p{i}', 'description':'short'} for i in range(1,7)]}
    artifacts = {'topic.json': {'slug':'test'}, 'evidence.json': {'products': []}}
    with patch('role_execution_writer.read_artifact',side_effect=lambda slug,name,default=None:artifacts.get(name,default)), \
         patch('role_execution_writer.write_artifact') as save, \
         patch('role_execution_writer.record_execution'), \
         patch('comparison_acceptance.product_issues',return_value=[]), \
         patch('editorial_plan.build_editorial_plan',return_value={'requirements':[]}), \
         patch('writer_runtime.invoke_writer',return_value=(draft,{'model':'writer'})) as invoke, \
         patch('writer_runtime.codex_generate_persistent',side_effect=AssertionError('unexpected second AI call')) as ai:
        result = writer('test')
    assert result['writer_ready'] is True
    assert invoke.call_count == 1
    ai.assert_not_called()
    assert [call.args[1] for call in save.call_args_list] == ['editorial-plan.json','payload.json']
    assert save.call_args_list[-1].args[2] is draft
    assert 'length_repair_attempted' not in result['metadata']
