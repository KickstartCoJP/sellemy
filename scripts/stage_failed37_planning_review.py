import json,hashlib
from pathlib import Path
job_root=Path.home()/'Library/Application Support/Sellemy/growth-recovery/jobs'
out=Path(__file__).resolve().parents[1]/'staging/failed37-planning'
out.mkdir(parents=True, exist_ok=True)
slugs=[]
for path in sorted(job_root.glob('*/failed37-planning-reevaluation-request.json')):
 job=path.parent;r=json.loads(path.read_text());slug=r['slug'];assert slug==job.name
 assert r['task_id']=='TASK-BU002-FAILED37-REPLAN-20261115'
 expected=r['original_artifact_sha256']
 for file,digest in expected.items():
  assert hashlib.sha256((job/file).read_bytes()).hexdigest()==digest,(slug,file)
 st=json.loads((job/'state.json').read_text());assert (st['status'],st['current_stage']) in [('FAILED','WRITER_FAILED'),('FAILED','PRODUCT_FAILED')]
 t=json.loads((job/'topic.json').read_text()); e=json.loads((job/'evidence.json').read_text());select=json.loads((job/'selection.json').read_text())
 assert t['slug']==slug and e['slug']==slug and len(t['comparison_axes'])==3 and len(e['products'])==6
 old_qa=json.loads((job/'qa.json').read_text())
 old_pass=r.get('qa_pass_fields_at_capture') or {}
 reasons={x.get('asin'):x.get('reason') for a in (select.get('axes') or []) for x in a.get('products',[]) if isinstance(x,dict)}
 products=[{'ref':p.get('ref'),'asin':p.get('asin'),'name':p.get('amazon_title'),'prior_individual_selection_reason':reasons.get(p.get('asin')),'verified_excerpt_sources':sum(1 for z in (p.get('sources') or []) if isinstance(z,dict) and z.get('verified') is True and str(z.get('excerpt') or '').strip() and str(z.get('url') or '').startswith('https://'))} for p in e['products']]
 def field(name):return (t.get('reader_persona') or {}).get(name,'')
 topic={'slug':slug,'category':t['category'],'title':t.get('title'),'query':t.get('query'),'reader_persona':t.get('reader_persona') or {},'introduction_angles':[{'id':a.get('id'),'label':a.get('label'),'keywords':a.get('keywords')} for a in t['comparison_axes']]}
 assessment={'schema':'sellemy-failed37-planning-review-input/v1','task_id':r['task_id'],'slug':slug,'job_state':'FAILED','failed_stage':st['current_stage'],'original_job_sha256':expected,'planning_candidate':topic,'original_product_inventory':products,'original_qa_pass_fields':old_pass,'independent_review_required':True,'independent_review_status':'PENDING','replanning_checks':{'reader_need':{'question':'What concrete product-selection need can be met?','observed_reader':field('reader'),'purchase_goal':field('purchase_goal'),'pass':None},'six_individual_recommendations':{'question':'Which products each meet the stated need, with source-backed individual value?','pass':None},'three_discovery_angles':{'question':'Are the 3 angles distinct discovery entrypoints without head-to-head rankings?','pass':None},'replan_outcome':{'instruction':'Approve existing theme only if reader intent and genuine product recommendations are independently verified; otherwise reselect Planning theme and products.','pass':None}},'product_qa':{'existing_independent_receipt':'STALE_CONTRACT','new_product_qa_required':True,'same_product_allowed_if_verified':True,'product_count':6,'verified_per_product':[p['verified_excerpt_sources'] for p in products]},'next_action':'independent_planning_qa_then_conditional_product_qa_then_writer_qa_then_ceo_gate','execution_allowed':False,'production_article_changed':False}
 (out/(slug+'.json')).write_text(json.dumps(assessment,ensure_ascii=False,indent=2)+'\n')
 slugs.append({'slug':slug,'failed_stage':st['current_stage'],'request_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'planning_review':'PENDING','product_qa':'RECHECK_REQUIRED'})
assert len(slugs)==37 and len(set(x['slug'] for x in slugs))==37
clusters=[
 {'family':'insulated_lunch_bags','slugs':['insulated-lunch-bags-for-daily-use','insulated-lunch-bags-for-daily-commutes','insulated-lunch-bags-for-daily-carry']},
 {'family':'measuring_cups','slugs':['household-measuring-cups','kitchen-measuring-cups','kitchen-measuring-cups-for-liquids']},
 {'family':'powder_face_cleansers','slugs':['powder-face-washes-6-picks','powder-face-washes-for-daily-use','facial-powder-cleansers']},
 {'family':'microwave_steamers','slugs':['microwave-safe-steamer-containers','microwave-steamer-containers']},
 {'family':'monitor_light_bars','slugs':['pc-monitor-light-bars','monitor-light-bars-for-desks']},
 {'family':'eye_makeup_removers','slugs':['eye-makeup-removers','eye-makeup-removers-comparison']},
 {'family':'drawer_dividers','slugs':['adjustable-drawer-dividers-for-home','drawer-storage-dividers']}
]
assert all(set(x['slugs'])<={row['slug'] for row in slugs} for x in clusters)
(out/'manifest.json').write_text(json.dumps({'task_id':'TASK-BU002-FAILED37-REPLAN-20261115','total':37,'writer_failed':sum(x['failed_stage']=='WRITER_FAILED' for x in slugs),'product_failed':sum(x['failed_stage']=='PRODUCT_FAILED' for x in slugs),'entries':slugs,'original_jobs_changed':False,'publication_changed':False,'production_qa_gate_changed':False,'potential_overlapping_topic_families':clusters,'topic_overlap_note':'Potential overlap, not proven duplicate. Independent Planning QA must decide on every job; do not auto-delete jobs.'},ensure_ascii=False,indent=2)+'\n')
print('prepared',len(slugs),'with original SHA preflight verified; no live job writes')
