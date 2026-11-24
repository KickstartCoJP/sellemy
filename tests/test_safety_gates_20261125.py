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
    assert 'ankerjapan.com' in flags[0]['signals'][0]['source']
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
    first=monitor_run(root=root,output=out,limit=20,live=False,now=datetime(2026,11,25,tzinfo=timezone.utc))
    assert first['published_articles']==1 and first['new_alerts']==1
    second=monitor_run(root=root,output=out,limit=20,live=False,now=datetime(2026,11,26,tzinfo=timezone.utc))
    assert second['new_alerts']==0 and second['total_alerts']==1
    alert=json.loads((out/'alerts.json').read_text())['items'][0]
    assert alert['article_slug']=='wireless-speakers-6-picks'
    assert alert['asin']=='B000000001' and alert['status']=='RECALL'
