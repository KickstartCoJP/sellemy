"""CEO inspection for ten consecutive failed normal publication turns.

Uses existing Task/Event, not a second Role delivery or an article publication gate.
"""
from __future__ import annotations
import hashlib,os,json,subprocess
from pathlib import Path


class CEOFailureBridgeError(ValueError):pass

def submit_failed_ten_inspection(receipt:dict,*,ai_os_root:Path|None=None,ai_os_python:str|None=None)->dict:
    if receipt.get('schema')!='sellemy-failed-10-inspection/v1' or receipt.get('status')!='REQUIRES_CEO_INSPECTION':
        raise CEOFailureBridgeError('invalid inspection receipt')
    slugs=receipt.get('slugs')
    if (not isinstance(slugs,list) or not 1 <= len(slugs) <= 10
            or len(set(slugs)) != len(slugs)
            or int(receipt.get('failure_count') or 0) < 10):
        raise CEOFailureBridgeError('inspection requires ten failed turns and their source jobs')
    signature=hashlib.sha256(('|'.join(slugs)+'|'+str(int(receipt['failure_count'])//10)).encode()).hexdigest()[:20].upper()
    task_id='TASK-BU002-FAILED-TEN-CEO-'+signature
    ai_os_root=Path(ai_os_root or os.environ.get('SELLEMY_AI_OS_ROOT','/Users/kickstart/ai-management-os'))
    ai_os_python=ai_os_python or os.environ.get('SELLEMY_AI_OS_PYTHON',str(ai_os_root/'.venv/bin/python'))
    canonical=Path(os.environ.get('SELLEMY_AI_OS_CANONICAL_ROOT',str(Path.home()/'Library/Application Support/AIManagementOS/canonical')))
    folder=canonical/'tasks'/task_id
    def call(module,payload):
        completed=subprocess.run([ai_os_python,'-m',module],cwd=ai_os_root,input=json.dumps(payload,ensure_ascii=False),text=True,capture_output=True,timeout=180)
        if completed.returncode:
            raise CEOFailureBridgeError(f'{module} failed: {(completed.stderr or completed.stdout)[-1800:]}')
        return json.loads(completed.stdout)

    if not (folder/'task.json').exists():
        call('ai_management_os.actions.task_create',{'task_id':task_id,'task_name':'Sellemy品質NG 10連続：CEO検査',
             'project_id':'BU-002','creator_role_id':'sellemy-ops','owner_role_id':'sellemy-ops',
             'executor_role_id':'os-chief','message_id':'TASKCREATE-'+signature,
             'parent_task_id':None,'ceo_direct':False,'receiving_role_id':'os-chief'})
    for path in (folder/'history').glob('*.json'):
        try:
            if json.loads(path.read_text()).get('message_id')=='FAILED10-'+signature:
                return {'required':True,'duplicate':True,'task_id':task_id}
        except (OSError,ValueError):continue
    history_dir=folder/'history'
    if not list(history_dir.glob('*.json')):
        call('ai_management_os.actions.task_append',{'task_id':task_id,
            'message_id':'FAILED10-ASSIGN-'+signature,'actor_role_id':'sellemy-ops',
            'role_id':'os-chief','status':'in_progress',
            'detail':{'next_action':'品質失敗10回到達、CEO検査への引き継ぎ','next_responsibility_role_id':'os-chief','source':'sellemy-normal-quality-gate'}})
    detail={'next_action':'CEO検査依頼: Sellemy Growthで品質NGが10回連続。対象記事の各失敗履歴を点検し、Ownerに品質回復と再開条件を指示してください。既存の公開記事は変更せず、日次投稿数の一律上限を設けない。',
      'requested_to':'ceo','reason':'通常制作10ターンが連続failed。品質根本原因の検査と回復判断が必要','required_actions':['10件の原因分析と品質回復判断'],
      'failed_slugs':slugs,'failure_count':receipt['failure_count'],'source':'sellemy-normal-quality-gate',
      'resume_role_id':'os-chief','next_responsibility_role_id':'os-chief'}
    result=call('ai_management_os.actions.task_append',{'task_id':task_id,
        'message_id':'FAILED10-'+signature,'actor_role_id':'sellemy-ops',
        'role_id':'os-chief','status':'user_decision_required','detail':detail})
    if result.get('message_id')!='FAILED10-'+signature:
        raise CEOFailureBridgeError('failed inspection Task/Event readback')
    return {'required':True,'duplicate':False,'task_id':task_id,'message_id':result['message_id']}
