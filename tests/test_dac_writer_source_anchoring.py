from writer_runtime import _prompt


def test_writer_instruction_excludes_title_only_product_specs_and_cross_card_claims():
    text = _prompt({'slug':'dac-source-gate'}, {'products':[{'ref':f'p{i}','amazon_title':f'Item {i}'} for i in range(1,7)],
                     'comparison_axes':[{'id':'a','label':'A'},{'id':'b','label':'B'}]},
                   previous_payload={'summary':'earlier'},
                   gate_feedback={'qa_failures': {'overall_pass': False},
                                  'independent_quality_gate': {'gate':'claim','issues':['unsupported adapter']}})
    assert '商品名・販売タイトルにだけ含まれる仕様' in text
    assert 'そのカードのproduct_refに属する検証済みexcerpt' in text
    assert 'QAが削除指定した文言・同じ主張の言い換え' in text
    assert 'unsupported adapter' in text
