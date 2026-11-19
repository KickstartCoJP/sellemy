"""Source-bound editorial assignment for an existing Sellemy job."""
from comparison_acceptance import digest, evidence_content

def build_editorial_plan(topic, evidence, ceo_rework=''):
    products = evidence.get('products') or []
    axes = evidence.get('comparison_axes') or []
    if len(products) != 6 or len({p.get('ref') for p in products}) != 6 or len(axes) < 2:
        raise ValueError('editorial plan requires 6 refs and 2 axes')
    requirements = [
        {'id':'E01','section':'lead','instruction':'冒頭で記事の対象商品や利用場面に関係する具体的な価値を伝える。商品の用途や使う場面を自然に伝える。困りごとや具体的な利用シーンを無理に作らず、商品の面白さや使い道から直接始めてもよい。比較記事であることを定型的に宣言せず、家電など仕様の違いが重要なカテゴリと美容など使用感が中心のカテゴリで表現を使い分ける。「比べます」の一律禁止はしない。冒頭のleadには商品名・ブランド名を記載しない'},
        {'id':'E02','section':'summary','instruction':'冒頭に表示されるsummaryではleadの言い換えや重複を避ける。固有の商品名・ブランド名を記載せず、記事で扱う商品の特徴と用途を簡潔に示す。固有の商品推薦は商品紹介で行う'},
        {'id':'E03','section':'comparison_groups','instruction':'企画と商品選定で確定した3組×各2商品の割当を維持する。各組では商品に共通する用途・楽しみ方・特徴などを読者向けの自然な文章で短く伝え、各商品の固有の事実・推薦理由は商品カードに書く。制作上の分類や企画の説明を本文に出さない。商品同士の差異や優劣の説明は必須にしない。商品名や証明・検証経緯は商品カセット以外に記載しない'},
        {'id':'E04','section':'products','instruction':'全6商品それぞれについて、根拠のある固有の特徴、魅力、向く人、推薦する理由を説明する。他商品との比較や優劣を強制しない。読者がその商品を知りたくなる固有の魅力と使用場面を優先する。商品説明は各160〜260字を初期目安とし、根拠のない水増しをしない'},
        {'id':'E05','section':'how_to_choose','instruction':'商品紹介の後に、読者が商品に興味を持つきっかけや、使い道を想像できるポイントを自然な日本語で説明する。商品カードと同じ事実の再掲ではなく、商品を理解するための実用的なヒントを示す。確認事項の羅列で終わらせない。商品名・ブランド名・証明・検証経緯は記載しない'},
        {'id':'E06','section':'conclusion','instruction':'結びには固有の商品名・ブランド名を記載しない。lead・summary・説明の繰り返し、商品羅列や仕様の再説明を避け、記事の内容に合う自然な短い締めにする。内容を言い換えるだけの抽象的な結びではなく、読者が用途を絞れる具体的な判断の手がかりを残す。具体的な推薦は商品紹介内で完結させる'},
        {'id':'E07','section':'all','instruction':'自然な日本語で書き、内部QAや制作工程の情報を記事に露出しない。特定語句の使用・不使用を指定せず、商品の特徴と推薦理由に沿った自然な表現を用いる'},
        {'id':'E08','section':'all','instruction':'記事の商品名・仕様・推薦・必要な比較の事実が検証済みEvidenceで裏付けられ、事実を創作していない'},
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
        'topic_hash':digest({k:v for k,v in topic.items() if not k.startswith('_') and k != 'title'}),
        'evidence_hash':digest(evidence_content(evidence)),
        'reader':topic.get('reader_persona') or {},
        'reader_value_goal':(topic.get('reader_persona') or {}).get('purchase_goal') or '',
        'search_intent':topic.get('intent_key') or topic.get('search_intent') or '',
        'comparison_axes':[{'id':a['id'], 'label':a.get('label','')} for a in axes],
        'selected_axis_groups':(evidence.get('selection') or {}).get('axes') or [],
        'products':[{'ref':p['ref'], 'name':p.get('amazon_title') or p['ref'],
                     'supported_axes':p.get('matched_comparison_axes') or [], 'discovery_axis_id':p.get('discovery_axis_id'), 'selection_reason':p.get('selection_reason') or ''} for p in products],
        'requirements':requirements,
        'prohibitions':['無根拠な効能','内部QAの露出','商品名の羅列','注意文だけの結論','水増し'],
    }
