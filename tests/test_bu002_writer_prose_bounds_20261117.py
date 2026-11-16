"""Preserve existing mechanical minimums across all Writer revisions."""
from writer_runtime import _prompt
from qa import SUMMARY_MIN, HOW_TO_CHOOSE_MIN, DESCRIPTION_HARD_MIN


def test_writer_enforces_existing_part_minima_on_initial_and_claim_only_rewrite():
    evidence={'products':[{'ref':f'p{i}','amazon_title':f'Product {i}'} for i in range(1,7)],
              'comparison_axes':[{'id':'a','label':'A'},{'id':'b','label':'B'}]}
    topic={'slug':'example'}
    for prompt in (_prompt(topic,evidence),
                   _prompt(topic,evidence,previous_payload={'summary':'previous'},
                           gate_feedback={'independent_quality_gate':{'gate':'claim','issues':['Unsupported fragrance claim']}})):
        assert f'summaryは日本語{SUMMARY_MIN}字以上' in prompt
        assert f'how_to_chooseは日本語{HOW_TO_CHOOSE_MIN}字以上' in prompt
        assert f'descriptionは6件それぞれ{DESCRIPTION_HARD_MIN}字以上' in prompt
        assert '無根拠な機能や効能の創作は禁止' in prompt
