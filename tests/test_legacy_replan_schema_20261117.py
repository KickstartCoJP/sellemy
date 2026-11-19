import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import planning_runtime


def test_replan_uses_single_candidate_schema_on_codex():
    seen = {}
    def fake_generate(command, model, schema, prompt, **kwargs):
        seen['schema'] = schema
        return {'candidates': []}, {'test': True}
    with patch.object(planning_runtime, 'codex_generate_persistent', side_effect=fake_generate):
        planning_runtime._invoke_planning('codex', ['/opt/homebrew/bin/codex'], 'test', 'prompt', 5,
                                          schema={'type': 'object', 'properties': {'candidates': {'type': 'array', 'minItems': 1, 'maxItems': 1}}})
    assert seen['schema']['properties']['candidates']['minItems'] == 1
    assert seen['schema']['properties']['candidates']['maxItems'] == 1
    assert planning_runtime.PLANNING_SCHEMA['properties']['candidates']['minItems'] == 1
    assert planning_runtime.PLANNING_SCHEMA['properties']['candidates']['maxItems'] == 1


def test_replan_passes_schema_to_provider():
    topic = {'slug':'test-existing-article','category':'dailygoods','query':'query','product_theme':'防水マット',
             'intent_key':'intent','reader_persona':{'reader':'read','knowledge_level':'some','purchase_goal':'goal','editorial_depth':'deep'},
             'comparison_axes':[{'id':str(i),'label':'axis','keywords':['word']} for i in range(3)],'signals':{}}
    with patch.object(planning_runtime, '_planning_provider', return_value=('codex',['codex'],'test', True)),\
         patch.object(planning_runtime, '_invoke_planning', return_value={'candidates':[topic]}) as invoke,\
         patch.object(planning_runtime, 'route', side_effect=lambda _name,_schema,_prompt,local: local()):
        result,_ = planning_runtime.replan_published_topic(slug='test-existing-article',category='dailygoods',product_theme_name='防水マット')
    assert result['slug'] == 'test-existing-article'
    assert 'title' not in result
    assert invoke.call_args.kwargs['schema']['properties']['candidates']['minItems'] == 1
    assert invoke.call_args.kwargs['schema']['properties']['candidates']['maxItems'] == 1
