"""Source-bound editorial assignment for an existing Sellemy job."""
from comparison_acceptance import digest, evidence_content

def build_editorial_plan(topic, evidence, ceo_rework=''):
    products = evidence.get('products') or []
    axes = evidence.get('comparison_axes') or []
    if len(products) != 6 or len({p.get('ref') for p in products}) != 6 or len(axes) < 2:
        raise ValueError('editorial plan requires 6 refs and 2 axes')
    requirements = [
        {'id':'E01','section':'lead','instruction':'読者の悩みと使用場面・比較ポイントを具体化して導入する。冒頭のleadには商品名・ブランド名を記載しない'},
        {'id':'E02','section':'summary','instruction':'第一候補と用途別候補を理由付きで示す'},
        {'id':'E03','section':'comparison_groups','instruction':'3組2商品ずつの相違と選び分けを説明する'},
        {'id':'E04','section':'products','instruction':'全6商品それぞれの固有の推薦理由・対象読者・差を説明する'},
        {'id':'E05','section':'how_to_choose','instruction':'複数の利用場面に応じた選定基準と候補を説明する'},
        {'id':'E06','section':'conclusion','instruction':'購入候補を明確に選び、調査を読者へ丸投げしない'},
        {'id':'E07','section':'all','instruction':'自然な日本語で書き、内部QAや制作工程の情報を記事に露出しない'},
        {'id':'E08','section':'all','instruction':'記事の商品名・仕様・比較・推薦の事実が検証済みEvidenceで裏付けられ、事実を創作していない'},
    ]
    if ceo_rework.strip():
        import re
        parts = [x.strip() for x in re.split(r'[。\n]+', ceo_rework) if x.strip()]
        for i, part in enumerate(parts, 1):
            requirements.append({'id':f'C{i:02d}', 'section':'all', 'instruction':part,
                                 'source':'CEO', 'original':ceo_rework})
    return {
        'schema':'sellemy-editorial-plan/v1',
        'topic_hash':digest({k:v for k,v in topic.items() if not k.startswith('_')}),
        'evidence_hash':digest(evidence_content(evidence)),
        'reader':topic.get('reader_persona') or {},
        'search_intent':topic.get('search_intent') or '',
        'comparison_axes':[{'id':a['id'], 'label':a.get('label','')} for a in axes],
        'products':[{'ref':p['ref'], 'name':p.get('amazon_title') or p['ref'],
                     'supported_axes':p.get('matched_comparison_axes') or []} for p in products],
        'requirements':requirements,
        'prohibitions':['無根拠な効能','内部QAの露出','商品名の羅列','注意文だけの結論','水増し'],
    }
