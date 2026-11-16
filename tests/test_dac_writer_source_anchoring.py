from writer_runtime import _prompt


def test_writer_instruction_allows_attributed_listing_facts_but_not_invented_performance():
    text = _prompt({'slug':'dac-source-gate'}, {'products':[{'ref':f'p{i}','amazon_title':f'Item {i}'} for i in range(1,7)],
                     'comparison_axes':[{'id':'a','label':'A'},{'id':'b','label':'B'}]},
                   previous_payload={'summary':'earlier'},
                   gate_feedback={'qa_failures': {'overall_pass': False},
                                  'independent_quality_gate': {'gate':'claim','issues':['unsupported adapter']}})
    assert '掲載タイトルから確認できる名称・型番・数量・寸法等' in text
    assert '性能・耐久性・効能などを断定しない' in text
    assert '商品refが違う根拠を転用せず' in text
    assert 'unsupported adapter' in text
