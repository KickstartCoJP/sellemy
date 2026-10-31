"""Source-bound editorial assignment for an existing Sellemy job."""
from comparison_acceptance import digest, evidence_content

def build_editorial_plan(topic, evidence, ceo_rework=''):
    products = evidence.get('products') or []
    axes = evidence.get('comparison_axes') or []
    if len(products) != 6 or len({p.get('ref') for p in products}) != 6 or len(axes) < 2:
        raise ValueError('editorial plan requires 6 refs and 2 axes')
    requirements = [
        {'id':'E01','section':'lead','instruction':'商品の用途と記事を読む価値・選定ポイントを自然に伝える。困りごとや具体的な利用シーンを無理に作らず、用途や選定ポイントから直接始めてもよい。比較記事であることを定型的に宣言せず、家電など仕様の違いが重要なカテゴリと美容など使用感が中心のカテゴリで表現を使い分ける。「比べます」の一律禁止はしない。冒頭のleadには商品名・ブランド名を記載しない'},
        {'id':'E02','section':'summary','instruction':'冒頭に表示されるsummaryには固有の商品名・ブランド名を記載しない。読者の選定観点と用途別の選び方を簡潔に示す。固有の商品推薦は比較グループ・商品紹介で行う'},
        {'id':'E03','section':'comparison_groups','instruction':'3組2商品ずつの相違と選び分けを自然な生活者向け文章で説明する。商品名や証明・検証経緯は商品カセット以外に記載しない'},
        {'id':'E04','section':'products','instruction':'全6商品それぞれの固有の推薦理由・対象読者・差を説明する'},
        {'id':'E05','section':'how_to_choose','instruction':'複数の利用場面に応じた選定基準を自然な日本語で説明する。商品名・ブランド名・証明・検証経緯は記載しない'},
        {'id':'E06','section':'conclusion','instruction':'結びには固有の商品名・ブランド名を記載しない。比較済み商品の羅列や仕様の再説明を避け、読者が自分の用途に合う選び方を自然に思い描ける短い締めにする。具体的な商品推薦は比較・商品紹介内で完結させる'},
        {'id':'E07','section':'all','instruction':'自然な日本語で書き、内部QAや制作工程の情報を記事に露出しない'},
        {'id':'E08','section':'all','instruction':'記事の商品名・仕様・比較・推薦の事実が検証済みEvidenceで裏付けられ、事実を創作していない'},
    ]
    if ceo_rework.strip():
        import re
        parts = [x.strip() for x in re.split(r'[。\n]+', ceo_rework) if x.strip()]
        for part in parts:
            # Execution instructions belong to the runtime, not article prose QA.
            if any(term in part for term in ('QAをやり直す','再QA','再執筆','差戻し処理','レビュー再提出')):
                continue
            if '最初の商品紹介はcomparison_groups' in part:
                continue
            requirements.append({'id':f'C{len(requirements)-7:02d}', 'section':'all', 'instruction':part,
                                 'source':'CEO', 'original':ceo_rework})
    return {
        'schema':'sellemy-editorial-plan/v1',
        'topic_hash':digest({k:v for k,v in topic.items() if not k.startswith('_')}),
        'evidence_hash':digest(evidence_content(evidence)),
        'reader':topic.get('reader_persona') or {},
        'search_intent':topic.get('search_intent') or '',
        'comparison_axes':[{'id':a['id'], 'label':a.get('label','')} for a in axes],
        'selected_axis_groups':(evidence.get('selection') or {}).get('axes') or [],
        'products':[{'ref':p['ref'], 'name':p.get('amazon_title') or p['ref'],
                     'supported_axes':p.get('matched_comparison_axes') or [], 'discovery_axis_id':p.get('discovery_axis_id'), 'selection_reason':p.get('selection_reason') or ''} for p in products],
        'requirements':requirements,
        'prohibitions':['無根拠な効能','内部QAの露出','商品名の羅列','注意文だけの結論','水増し'],
    }
