"""CEO: existing article recovery retains only the topic/theme, never prior copy."""
import json
from pathlib import Path
from unittest.mock import patch
import importlib.util

import legacy_article_recovery as legacy
import role_execution_writer as writer_role
import growth_recovery as recovery

SCRIPT=Path(__file__).resolve().parents[1]/'ops/reset_legacy_theme_only_20261125.py'
spec=importlib.util.spec_from_file_location('theme_only_reset',SCRIPT)
reset=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reset)


def _save(dir,name,value):
    (dir/name).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')


def test_reset_legacy_article_retains_theme_but_not_old_article_or_approval(tmp_path):
    jobs=tmp_path/'jobs';d=jobs/'one-theme';d.mkdir(parents=True)
    state={'slug':'one-theme','category':'dailygoods','source':'legacy_repair','status':'ACTIVE',
           'current_stage':'CHAT_REVIEW_PENDING','published_commit':'original-baseline',
           'last_failure_code':'old','next_retry_at':None}
    _save(d,'state.json',state)
    _save(d,'topic.json',{'slug':'one-theme','category':'dailygoods','product_theme':'水切りラック',
                          'comparison_axes':[{'id':'old'}]})
    _save(d,'payload.json',{'h1':'旧本文','products':[{'ref':'p1','h3':'旧商品'}]})
    _save(d,'evidence.json',{'products':[{'asin':'B000000001'}]})
    _save(d,'qa-certification.json',{'passed':True})
    _save(d,'chat-publish-approval.json',{'approved':True})
    _save(d,'legacy-repair.json',{'phase':'regular_repair', 'original_published_commit':'original-baseline'})
    with patch.object(recovery,'JOBS_ROOT',jobs),patch.object(recovery,'EVENTS_PATH',tmp_path/'events.jsonl'),\
         patch.object(reset,'job_dir',lambda slug:jobs/slug),patch.object(reset,'load_job',recovery.load_job),\
         patch.object(reset,'read_artifact',recovery.read_artifact),patch.object(reset,'write_artifact',recovery.write_artifact),\
         patch.object(reset,'_save_state',recovery._save_state):
        assert reset.reset([(state,'水切りラック')])==['one-theme']
        updated=recovery.load_job('one-theme')
        assert updated['current_stage']=='PLANNING_PENDING'
        assert updated['status']=='ACTIVE'
        assert updated['published_commit']=='original-baseline'
        assert recovery.read_artifact('one-theme','topic.json')=={'slug':'one-theme','category':'dailygoods','product_theme':'水切りラック'}
        assert not (d/'payload.json').exists()
        assert not (d/'evidence.json').exists()
        assert not (d/'qa-certification.json').exists()
        assert not (d/'chat-publish-approval.json').exists()
        assert (d/'rollback-ceo-theme-only-rebuild-20261125'/'payload.json').exists()
        assert recovery.read_artifact('one-theme','product-search-progress.json')['reuse_previous_products'] is False
        assert recovery.read_artifact('one-theme','legacy-theme-only-rebuild.json')['first_draft_completed'] is False
        assert reset.reset([(state,'水切りラック')])==['one-theme']


def test_writer_first_rebuild_draft_does_not_receive_old_payload_or_qa():
    topic={'slug':'one-theme','category':'dailygoods','product_theme':'水切りラック'}
    evidence={'products':[{'ref':'p1'}]}
    original={'products':[{'ref':'p1','h3':'古い商品'}]}
    recorded={}
    def read(slug,name,default=None):
        return {'topic.json':topic,'evidence.json':evidence,'payload.json':original,
                'gate-feedback.json':{'ceo_rework':'旧QA'},
                'legacy-theme-only-rebuild.json':{'first_draft_completed':False,'batch':'batch'}}.get(name,default)
    def invoke(*args,**kwargs):
        recorded.update(kwargs)
        return {'products':[{'ref':'p1','h3':'新商品'}]},{}
    with patch.object(writer_role,'read_artifact',side_effect=read),\
         patch('comparison_acceptance.product_issues',return_value=[]),\
         patch('editorial_plan.build_editorial_plan',return_value={}),\
         patch('writer_runtime.invoke_writer',side_effect=invoke),\
         patch('growth_recovery.load_job',return_value={'source':'legacy_repair'}),\
         patch.object(writer_role,'write_artifact') as write,\
         patch.object(writer_role,'record_execution'):
        assert writer_role.writer('one-theme')['writer_ready'] is True
    assert recorded['previous_payload'] is None
    assert recorded['gate_feedback']=={}
    assert any(call.args[1]=='legacy-theme-only-rebuild.json' and call.args[2]['first_draft_completed'] is True for call in write.call_args_list)
