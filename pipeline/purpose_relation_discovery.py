from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / 'json' / 'articles.json'
SEED = ROOT / 'config' / 'purpose_relation_seed.json'
DEFAULT_OUT = Path.home() / 'Library/Application Support/Sellemy/purpose-discovery/latest.json'

TOKEN_RE = re.compile(r'[a-z0-9]+', re.I)
STOP = {'6','picks','pick','selection','top','best','for','the','and','with','of','to','in','a','an','recommendations','choices'}


def tokens(text: str) -> list[str]:
    out=[]
    for t in TOKEN_RE.findall((text or '').lower().replace('_','-')):
        if len(t) < 2 or t in STOP: continue
        out.append(t)
    return out


def article_text(row: dict[str, Any]) -> str:
    return ' '.join(str(row.get(k) or '') for k in ('slug','title','summary','category'))


def load_articles() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw=json.loads(ARTICLES.read_text(encoding='utf-8'))
    rows=raw if isinstance(raw,list) else raw.get('articles',[])
    published=[r for r in rows if r.get('status')=='published']
    source={
        'path': str(ARTICLES),
        'sha256': hashlib.sha256(ARTICLES.read_bytes()).hexdigest(),
        'mtime_utc': datetime.fromtimestamp(ARTICLES.stat().st_mtime, timezone.utc).isoformat(),
        'published_article_count': len(published),
        'total_article_count': len(rows),
    }
    return published,source


def cosine(a: Counter[str], b: Counter[str]) -> float:
    common=set(a)&set(b)
    dot=sum(a[k]*b[k] for k in common)
    na=math.sqrt(sum(v*v for v in a.values())); nb=math.sqrt(sum(v*v for v in b.values()))
    return dot/(na*nb) if na and nb else 0.0


def build_feature_profiles(seed_relations: list[dict[str, Any]], by_slug: dict[str,dict[str,Any]]) -> dict[str,dict[str,Any]]:
    grouped=defaultdict(list); titles={}
    for rel in seed_relations:
        grouped[rel['feature_id']].append(rel); titles[rel['feature_id']]=rel['feature_title']
    raw_profiles={}
    token_feature_frequency=Counter()
    for fid,rels in grouped.items():
        core=Counter(); supporting=Counter(); docs=[]
        for rel in rels:
            row=by_slug.get(rel['slug'],{})
            bag=Counter(tokens(article_text(row) or rel['slug']))
            weight=2.0 if rel.get('relation_type')=='core' else 1.0
            for k,v in bag.items():
                (core if rel.get('relation_type')=='core' else supporting)[k]+=v*weight
            docs.append(rel['slug'])
        combined=core.copy(); combined.update(supporting)
        raw_profiles[fid]={'title':titles[fid],'vector':combined,'seed_slugs':set(docs)}
        for token in combined:
            token_feature_frequency[token]+=1
    feature_count=max(1,len(raw_profiles))
    profiles={}
    for fid,p in raw_profiles.items():
        weighted=Counter()
        for token,value in p['vector'].items():
            # Terms shared by many unrelated features carry little discovery value.
            idf=math.log((feature_count+1)/(token_feature_frequency[token]+1))+1.0
            weighted[token]=value*idf
        profiles[fid]={**p,'vector':weighted}
    return profiles


def run(*, output: Path=DEFAULT_OUT) -> dict[str,Any]:
    published,source=load_articles(); by_slug={r.get('slug'):r for r in published if r.get('slug')}
    seed=json.loads(SEED.read_text(encoding='utf-8'))
    seed_relations=seed.get('relations',[])
    profiles=build_feature_profiles(seed_relations,by_slug)
    relations=[]; seen=set()
    # Preserve reviewed baseline exactly when the article still exists.
    for rel in seed_relations:
        if rel.get('slug') not in by_slug: continue
        item={k:rel.get(k) for k in ('feature_id','feature_title','article_id','slug','article_title','relation_type','confidence','rationale')}
        item['source']='reviewed_seed'; relations.append(item); seen.add((item['feature_id'],item['slug']))
    seed_slugs={r.get('slug') for r in seed_relations}
    candidates=[]
    for slug,row in by_slug.items():
        if slug in seed_slugs: continue
        bag=Counter(tokens(article_text(row)))
        # Weight article terms with the same feature-frequency IDF used by profiles.
        feature_count=max(1,len(profiles))
        token_ff=Counter()
        for fp in profiles.values():
            for token in fp['vector']:
                token_ff[token]+=1
        weighted_bag=Counter()
        for token,value in bag.items():
            idf=math.log((feature_count+1)/(token_ff[token]+1))+1.0
            weighted_bag[token]=value*idf
        ranked=sorted(((cosine(weighted_bag,p['vector']),fid,p) for fid,p in profiles.items()), reverse=True)
        top=ranked[:3]
        best_score=top[0][0] if top else 0.0
        second_score=top[1][0] if len(top)>1 else 0.0
        for rank,(score,fid,p) in enumerate(top,1):
            if score < 0.12: continue
            # Only one clear, high-confidence winner is auto-related locally.
            auto = rank==1 and score>=0.48 and (score-second_score)>=0.08
            candidate={
                'feature_id':fid,'feature_title':p['title'],'article_id':row.get('id') or row.get('article_id') or slug,
                'slug':slug,'article_title':row.get('title') or slug,'score':round(score,4),
                'margin_to_second':round(score-second_score,4) if rank==1 else None,
                'candidate_state':'auto_relation' if auto else 'semantic_review',
                'proposed_relation_type':'core' if score>=0.62 else 'supporting',
                'rationale':'local IDF-weighted similarity from reviewed relation vocabulary',
            }
            candidates.append(candidate)
            if auto and (fid,slug) not in seen:
                relations.append({
                    'feature_id':fid,'feature_title':p['title'],'article_id':candidate['article_id'],'slug':slug,
                    'article_title':candidate['article_title'],'relation_type':candidate['proposed_relation_type'],
                    'confidence':round(min(0.95,0.55+score),3),'rationale':candidate['rationale'],'source':'local_auto',
                }); seen.add((fid,slug))
    per_feature=[]
    feature_slugs=defaultdict(set)
    for r in relations: feature_slugs[r['feature_id']].add(r['slug'])
    for fid,p in profiles.items():
        rs=[r for r in relations if r['feature_id']==fid]
        core=sum(r['relation_type']=='core' for r in rs); supporting=sum(r['relation_type']=='supporting' for r in rs)
        total=len({r['slug'] for r in rs})
        per_feature.append({'feature_id':fid,'title':p['title'],'core_count':core,'supporting_count':supporting,'total_count':total,
                            'quantity_gate':'PASS' if core>=4 and total>=6 else 'FAIL'})
    mapped={r['slug'] for r in relations}; published_slugs=set(by_slug)
    overlaps=[]
    fids=sorted(feature_slugs)
    for i,a in enumerate(fids):
        for b in fids[i+1:]:
            shared=sorted(feature_slugs[a]&feature_slugs[b])
            if shared:
                overlaps.append({'feature_a':a,'feature_b':b,'shared_count':len(shared),'shared_slugs':shared[:20]})
    overlaps.sort(key=lambda x:(-x['shared_count'],x['feature_a'],x['feature_b']))
    semantic_by_slug=defaultdict(list)
    for candidate in candidates:
        if candidate['candidate_state']=='semantic_review':
            semantic_by_slug[candidate['slug']].append(candidate)
    semantic_review_queue=[]
    for slug,items in sorted(semantic_by_slug.items()):
        ranked=sorted(items,key=lambda x:-x['score'])[:3]
        semantic_review_queue.append({
            'slug':slug,
            'article_title':ranked[0]['article_title'],
            'candidate_count':len(items),
            'candidates':[{k:r.get(k) for k in ('feature_id','feature_title','score','margin_to_second','proposed_relation_type','rationale')} for r in ranked],
        })

    result={
        'schema':'sellemy-purpose-relation-discovery/v1','generated_at':datetime.now(timezone.utc).isoformat(),
        'source_snapshot':source,'mode':'read_only_local','production_write':False,
        'local_processing':{
            'reviewed_seed_relations':sum(r.get('source')=='reviewed_seed' for r in relations),
            'auto_relations':sum(r.get('source')=='local_auto' for r in relations),
            'semantic_review_candidates':sum(c['candidate_state']=='semantic_review' for c in candidates),
            'semantic_review_unique_articles':len(semantic_review_queue),
            'local_scan_reduction_percent':round((1-len(semantic_review_queue)/max(1,len(published)))*100,1),
            'rule':'all-published local scan; reviewed baseline preserved; new articles scored against reviewed per-feature vocabulary; only ambiguous candidates require semantic review',
        },
        'global':{
            'published_article_count':len(published),'relation_count':len(relations),'mapped_article_count':len(mapped),
            'orphan_count':len(published_slugs-mapped),'candidate_feature_count':len(profiles),
            'quantity_gate_pass_feature_count':sum(x['quantity_gate']=='PASS' for x in per_feature),
            'semantic_review_candidate_count':sum(c['candidate_state']=='semantic_review' for c in candidates),
        },
        'per_feature':sorted(per_feature,key=lambda x:x['feature_id']),
        'orphans':sorted(published_slugs-mapped),
        'overlap_summary':overlaps[:20],
        'semantic_review_queue':semantic_review_queue,
        'new_article_candidates':sorted(candidates,key=lambda x:(x['slug'],-x['score'],x['feature_id'])),
        'relations':relations,
        'next_gate':'Only semantic_review candidates and high-overlap/cannibalization cases need model/human semantic review. No production Relation write or publish is authorized by this report.'
    }
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=output.with_suffix(output.suffix+'.tmp'); tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); tmp.replace(output)
    return result


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=Path,default=DEFAULT_OUT); args=ap.parse_args()
    r=run(output=args.output)
    print(json.dumps({'source_snapshot':r['source_snapshot'],'global':r['global'],'local_processing':r['local_processing'],'output':str(args.output)},ensure_ascii=False))

if __name__=='__main__': main()
