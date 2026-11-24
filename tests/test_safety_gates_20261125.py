"""Three COO incidents must be independently blocked, with normal uses allowed."""
import json
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from safety_recall import catalog_matches,contains_model,unsafe_products
from safety_article_qa import obvious_unsafe_quotes,subject,review,require_receipt
from safety_monitor import run as monitor_run
from independent_quality_gate import QualityGateError,require_receipts


def product(asin='B000000001',title='通常の家庭用品',brand='Normal'):
    return {'ref':'p1','asin':asin,'product_id':'AMZ-'+asin,'amazon_title':title,
            'brand':brand,'sources':[{'verified':True,'source_type':'manufacturer_official',
                  'url':'https://example.com/manual','excerpt':'使用中は放置せず設定を確認してください。'}]}


def inputs(description):
    p={'h1':'安全な商品の選び方','lead':'通常の用途で商品を選ぶ','summary':'必要な使用条件を確認する',
       'how_to_choose':'説明書に従って使用する','conclusion':'用途に適したものを選ぶ',
       'comparison_groups':[{'title':f'用途{i}','angle':'普段の用途','product_refs':[f'p{2*i+1}',f'p{2*i+2}']} for i in range(3)],
       'products':[{'ref':f'p{i+1}','h3':f'商品{i+1}','description':description if i==0 else '自宅で通常使用する'} for i in range(6)]}
    e={'slug':'test','category':'dailygoods','comparison_axes':[],
       'products':[dict(product(asin=f'B{i+1:09d}'),ref=f'p{i+1}') for i in range(6)]}
    return p,e


def test_recall_soundcore_3_prevents_unqualified_recommendation():
    x=product('B0A3117000','Anker Soundcore 3 Bluetoothスピーカー','Soundcore')
    hit=catalog_matches(x)
    assert len(hit)==1 and hit[0]['model_number']=='A3117'
    flags,rows=unsafe_products([x],live=False)
    assert flags and rows[0]['status']=='RECALL'
    assert 'safe-lite.nite.go.jp' in flags[0]['signals'][0]['source']
    assert catalog_matches(product('B000001111','Soundcore 3i','Anker'))==[]
    assert catalog_matches(product('B000001112','Soundcore Motion X600','Anker'))[0]['model_number']=='A3130'


def test_flooded_road_recommendation_is_blocked_but_warning_passes():
    bad='増水した道でも踏ん張れるので安心して歩けます。'
    safe='冠水した道路を安全に歩けるわけではありません。増水した道には立ち入らないでください。'
    assert bad.rstrip('。') in obvious_unsafe_quotes(subject(*inputs(bad))['article'])
    assert obvious_unsafe_quotes(subject(*inputs(safe))['article'])==[]


def test_inflator_auto_stop_cannot_remove_human_pressure_check():
    bad='作業中にゲージを見続けなくても入れすぎを防ぎやすいのが特徴です。'
    safe='自動停止機能を備えます。使用中は放置せず、空気圧を確認します。'
    assert bad.rstrip('。') in obvious_unsafe_quotes(subject(*inputs(bad))['article'])
    assert obvious_unsafe_quotes(subject(*inputs(safe))['article'])==[]


def test_article_safety_llm_fail_produces_exact_quote_and_source_anchor():
    p,e=inputs('自動停止があり便利です。')
    with patch('safety_article_qa.require_product_safety'):
        receipt=review(p,e,lambda _: {'verdict':'FAIL','reason':'safety monitoring missing',
            'findings':[{'article_quote':'自動停止があり便利です。',
                         'risk':'manual says do not leave unattended',
                         'source_quote':'使用中は放置せず設定を確認してください。',
                         'source_url':'https://example.com/manual'}]})
    assert receipt['verdict']=='FAIL'
    with patch('safety_article_qa.require_product_safety'):
        passed=review(p,e,lambda _: {'verdict':'PASS','reason':'normal use','findings':[]})
    with patch('safety_article_qa.require_product_safety'):
        assert require_receipt(passed,p,e)
    p['products'][0]['description']='商品を改稿しました。'
    with pytest.raises(ValueError,match='stale'):
        require_receipt(passed,p,e)


def test_safety_untrusted_citation_and_false_pass_rejected():
    p,e=inputs('屋内で使う。')
    with patch('safety_article_qa.require_product_safety'):
        with pytest.raises(ValueError,match='unverified'):
            review(p,e,lambda _: {'verdict':'FAIL','reason':'unsafe',
                'findings':[{'article_quote':'屋内で使う。','risk':'risk',
                             'source_quote':'捏造した資料','source_url':'https://bad.example/manual'}]})


def test_publish_gate_requires_safety_receipt():
    p,e=inputs('屋内で使う。')
    with patch('independent_quality_gate.require_acceptance'),patch('independent_quality_gate.validate_verdict',return_value=[]):
        with pytest.raises(QualityGateError,match='safety'):
            require_receipts(p,e,{}, {'editorial':{'verdict':'PASS'},'claim':{'verdict':'PASS'}})


def test_monitor_alerts_published_only_and_deduplicates(tmp_path):
    root=tmp_path/'site'; (root/'data'/'evidence').mkdir(parents=True)
    (root/'article'/'gadget').mkdir(parents=True)
    (root/'article'/'gadget'/'wireless-speakers-6-picks.html').write_text('test')
    (root/'data'/'evidence'/'wireless-speakers-6-picks.json').write_text(json.dumps({
        'slug':'wireless-speakers-6-picks','category':'gadget',
        'products':[product('B000000001','Anker Soundcore 3 Bluetooth Speaker','Anker')]},ensure_ascii=False))
    (root/'data'/'evidence'/'unpublished.json').write_text(json.dumps({
        'slug':'unpublished','category':'gadget','products':[product()]}))
    out=tmp_path/'monitor'
    registry={'complete':True,'count':1,'checked_at':'2026-11-25T00:00:00+00:00',
              'details':[{'id':'2025102101','source':'https://safe-lite.nite.go.jp/recall/detail/2025102101',
                          'text':'アンカー・ジャパン株式会社 Soundcore 3 型番 A3117011 リコール 回収'}]}
    first=monitor_run(root=root,output=out,now=datetime(2026,11,25,tzinfo=timezone.utc),registry=registry)
    assert first['published_articles']==1 and first['new_alerts']==1
    second=monitor_run(root=root,output=out,now=datetime(2026,11,26,tzinfo=timezone.utc),registry=registry)
    assert second['new_alerts']==0 and second['total_alerts']==1
    alert=json.loads((out/'alerts.json').read_text())['items'][0]
    assert alert['article_slug']=='wireless-speakers-6-picks'
    assert alert['asin']=='B000000001' and alert['status']=='RECALL'


def test_complete_official_register_determines_every_product_not_a_search_snippet():
    from safety_recall import scan_products
    registry={'complete':True,'count':1,'checked_at':'2026-11-25T00:00:00+00:00',
              'details':[{'id':'2025102101','source':'https://safe-lite.nite.go.jp/recall/detail/2025102101',
                           'text':'アンカー・ジャパン株式会社 製品名 Soundcore 3 型番 A3117011 リコール'}]}
    products=[product('B000000021','Anker Soundcore 3 Bluetoothスピーカー','Anker'),
              product('B000000022','一般の水切りラック','Generic')]
    rows=scan_products(products,registry=registry)
    assert rows[0]['status']=='RECALL'
    assert rows[1]['status']=='NO_OFFICIAL_SIGNAL'
    rows=scan_products(products,registry={'complete':False,'count':1,'details':[]})
    assert rows[0]['status']=='RECALL' and rows[1]['status']=='NOT_CHECKED'


def test_government_matching_without_preloaded_recall_list():
    from safety_recall import scan_products
    registry={'complete':True,'count':1,'checked_at':'2026-11-25T00:00:00+00:00',
              'details':[{'id':'2026112501','source':'https://safe-lite.nite.go.jp/recall/detail/2026112501',
                          'text':'架空メーカー モデル XM-4500 電池発火のため回収'}]}
    p=product('B000000030','架空メーカー XM-4500 卓上機器','Sample')
    result=scan_products([p],registry=registry)[0]
    assert result['status']=='POTENTIAL_RECALL'
    assert result['signals'][0]['source_type']=='NITE_SAFE_LITE'


def test_monitor_scans_all_products_without_daily_limit(tmp_path):
    root=tmp_path/'site';(root/'data'/'evidence').mkdir(parents=True)
    (root/'article'/'gadget').mkdir(parents=True)
    (root/'article'/'gadget'/'many.html').write_text('published')
    items=[product(f'B{i:09d}',f'Generic household tool model K-{i:05d}','Generic') for i in range(1,102)]
    (root/'data'/'evidence'/'many.json').write_text(json.dumps({'slug':'many','category':'gadget','products':items}))
    reg={'complete':True,'count':1,'checked_at':'2026-11-25T00:00:00+00:00',
         'details':[{'id':'2026112502','source':'https://safe-lite.nite.go.jp/recall/detail/2026112502',
                     'text':'別の製品 型番 Z-99999 リコール'}]}
    receipt=monitor_run(root=root,output=tmp_path/'out',registry=reg)
    assert receipt['scanned']==101 and receipt['unique_products']==101
    assert receipt['official_recall_notices']==1


def test_real_catalog_precisely_matches_recalled_model_not_similar_series():
    from safety_recall import catalog_matches
    names=['Anker Soundcore 3 Pro','Anker Soundcore 3i','Anker Soundcore 30']
    for name in names:assert catalog_matches(product('B000000029',name,'Anker'))==[]


def test_short_unrelated_sku_does_not_generate_false_recall():
    from safety_recall import scan_products
    reg={'complete':True,'count':2,'checked_at':'2026-11-25T00:00:00+00:00',
         'details':[{'id':'1','source':'https://safe-lite.nite.go.jp/recall/detail/1',
                     'text':'三洋電機の食器洗い乾燥機 D503 を回収', 'manufacturer':'三洋電機株式会社'},
                    {'id':'2','source':'https://safe-lite.nite.go.jp/recall/detail/2',
                     'text':'ブラザー工業 2002年のミシンリコール', 'manufacturer':'ブラザー工業株式会社'}]}
    rows=scan_products([product('B000000091','パナソニック NH-D503','パナソニック'),
                        product('B000000092','ビタクラフト フライパン No.2002','ビタクラフト')],registry=reg)
    assert all(r['status']=='NO_OFFICIAL_SIGNAL' for r in rows)


def test_model_key_includes_digit_leading_and_short_hyphenated_skus():
    from safety_recall import _product_models
    a=_product_models(product('B000000077','ハイセンス 液晶テレビ 43E50S','ハイセンス'))
    b=_product_models(product('B000000078','レコルト RAO-3(W) クリームホワイト','レコルト'))
    c=_product_models(product('B000000079','カレンダー 2027年',''))
    assert '43E50S' in a and 'RAO-3' in b
    assert '2027' not in c


def test_monitor_retries_five_times_and_preserves_failure_notice(tmp_path):
    import safety_monitor
    errors=[]
    def broken():
        errors.append(1)
        raise RuntimeError('CAA feed unavailable')
    with patch.object(safety_monitor,'OUTPUT',tmp_path), \
         patch.object(safety_monitor,'run',side_effect=broken), \
         patch('safety_monitor.time.sleep'), \
         patch('sys.argv',['safety_monitor']):
        with pytest.raises(RuntimeError,match='five attempts'):
            safety_monitor.main()
    assert len(errors)==5
    saved=json.loads((tmp_path/'last-error.json').read_text())
    assert saved['attempt']==5 and saved['status']=='FAILED'
    assert 'CAA' in saved['error']


def test_monitor_clears_last_error_after_success(tmp_path):
    import safety_monitor
    (tmp_path/'last-error.json').write_text('old alert')
    with patch.object(safety_monitor,'OUTPUT',tmp_path), \
         patch.object(safety_monitor,'run',return_value={'scanned':3}), \
         patch('sys.argv',['safety_monitor']):
        safety_monitor.main()
    assert not (tmp_path/'last-error.json').exists()


def test_short_source_outage_uses_saved_complete_registry_without_global_veto(tmp_path):
    from safety_recall import scan_products
    from safety_government_feed import GovernmentFeedUnavailable
    registry={'complete':True,'stale':True,'count':1,
              'checked_at':'2026-11-25T00:00:00+00:00',
              'details':[{'id':'1','source':'https://safe-lite.nite.go.jp/recall/detail/1',
                          'text':'Anker Soundcore 3', 'manufacturer':'Anker'}]}
    with patch('safety_recall.combined_registry',side_effect=GovernmentFeedUnavailable('outage')), \
         patch('safety_recall.last_complete_registry',return_value=registry), \
         patch('safety_recall.STALENESS_ALERT',tmp_path/'source-staleness.json'):
        rows=scan_products([product('B000000042','Generic household accessory','Generic')])
    assert rows[0]['status']=='NO_OFFICIAL_SIGNAL'
    assert rows[0]['recall_source_stale'] is True
    alert=json.loads((tmp_path/'source-staleness.json').read_text())
    assert alert['status']=='OFFICIAL_RECALL_SOURCE_STALE'


def test_source_outage_with_no_recent_snapshot_still_blocks_unverified_safety_clearance():
    from safety_recall import unsafe_products
    from safety_government_feed import GovernmentFeedUnavailable
    with patch('safety_recall.combined_registry',side_effect=GovernmentFeedUnavailable('outage')), \
         patch('safety_recall.last_complete_registry',return_value=None):
        flags,_=unsafe_products([product('B000000043','Generic household accessory','Generic')])
    assert flags[0]['status']=='NOT_CHECKED'
