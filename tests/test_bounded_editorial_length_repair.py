import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from writer_runtime import revise_length_fields
from quality_fixtures import accepted_fixture

def test_repair_edits_prose_but_preserves_product_refs_and_groups():
    original,_,_=accepted_fixture()
    changes={'summary':'要点を照合しましょう。'*9,
             'how_to_choose':'機器の対応条件を確認しましょう。'*13,
             'conclusion':'購入時の仕様を確認しましょう。'*9,
             'products':[{'ref':p['ref'],'description':p['description']} for p in original['products']]}
    with patch('writer_runtime.require_local_provider_allowed'),\
         patch('writer_runtime.codex_generate_persistent',return_value=(changes,{'model':'test'})) as writer:
        revised,metadata=revise_length_fields(original,rendered_main_len=3433)
    assert metadata['model']=='test'
    assert writer.call_count==1
    repair_prompt=writer.call_args.args[3]
    assert 'SUMMARY, HOW_TO_CHOOSE AND CONCLUSION MUST CONTAIN NO PRODUCT NAMES OR BRAND NAMES' in repair_prompt
    assert 'remove them and replace' in repair_prompt
    assert [p['ref'] for p in revised['products']]==[p['ref'] for p in original['products']]
    assert revised['comparison_groups']==original['comparison_groups']
    assert revised['summary']==changes['summary']
    assert revised['conclusion']==changes['conclusion']
