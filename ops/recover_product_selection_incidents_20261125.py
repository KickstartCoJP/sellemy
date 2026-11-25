"""One-off, scoped recovery of the CEO-identified BU-002 selector incidents.

Execute only after this fix is approved/merged. No published article is edited.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from growth_recovery import (load_job,read_artifact,write_artifact,_save_state,_iso)

SLUGS=('autumn-humidifiers-6-picks','autumn-hair-loss-care-shampoo-6-picks',
       'autumn-hand-creams-6-picks','autumn-nail-colors-6-picks')
INCIDENT_CODES={'NEEDS_PRODUCT_RESELECTION','UNCHANGED_PRODUCT_SEARCH',
                'PRODUCT_REVIEW_CITATION_INVALID'}


def recover(slug:str,*,dry_run:bool=True)->str:
    if slug not in SLUGS:raise ValueError('outside CEO-approved incident scope')
    if read_artifact(slug,'selection-incident-recovery-20261125.json',{}):
        return f'{slug}: skip already recovered'
    state=load_job(slug)
    stage,status=state.get('current_stage'),state.get('status')
    code=state.get('last_failure_code')
    if (code not in INCIDENT_CODES or
        (status,stage) not in {('ACTIVE','WRITER_PENDING'),('FAILED','PRODUCT_FAILED')}):
        return f'{slug}: skip state={status}/{stage} code={code}'
    progress=read_artifact(slug,'product-search-progress.json',{}) or {}
    if not read_artifact(slug,'topic.json') or not progress.get('candidates'):
        return f'{slug}: skip missing topic or candidate history'
    old=read_artifact(slug,'product-attempt-fingerprint.json',{}) or {}
    if dry_run:return f'{slug}: eligible for scoped recovery ({code})'
    write_artifact(slug,'product-attempt-fingerprint-before-fix-20261125.json',old)
    write_artifact(slug,'product-attempt-fingerprint.json',{})
    if status=='FAILED':
        state['status']='ACTIVE'
        state['current_stage']='WRITER_PENDING'
        state['next_retry_at']=_iso()
        _save_state(state,event='CEO_SCOPED_SELECTOR_FIX_REOPENED',
                    detail={'previous_failure_code':code})
    write_artifact(slug,'selection-incident-recovery-20261125.json',{
        'reason':'CEO-approved BU-002 citation and selector implementation correction',
        'previous_status':status,'previous_stage':stage,
        'previous_failure_code':code,'previous_fingerprint':old})
    return f'{slug}: recovered for Product QA retry'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    for slug in SLUGS:print(recover(slug,dry_run=not args.apply))

if __name__=='__main__':main()
