"""Targeted CEO safety fix for the already published rain-boots article."""
from pathlib import Path
from html.parser import HTMLParser

HTML=(Path(__file__).resolve().parents[1]/'article/dailygoods/rain-boots-6-picks.html').read_text()

class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.product_ids=[]
        self.amazon=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'data-product-id' in attrs:self.product_ids.append(attrs['data-product-id'])
        if tag=='a' and 'amazon.co.jp/dp/' in attrs.get('href',''):self.amazon.append(attrs['href'])

def test_no_flooded_road_recommendation():
    for unsafe in ('増水した道や濡れた床でも安心して歩けます','増水した道でも、足元をしっかりガード','増水した道を歩く場面でも踏ん張りやすい','深い水たまりや強い横殴りの雨でも裾からの浸水を防ぎやすい'):
        assert unsafe not in HTML
    assert '冠水・増水した道路は歩かず' in HTML
    assert '増水した道には立ち入らないでください' in HTML
    assert '不要不急の外出を控え' in HTML
    assert '滑り止め加工の有無にかかわらず慎重に歩いてください' in HTML

def test_products_and_affiliate_link_count_remain_six():
    p=ArticleParser();p.feed(HTML)
    assert len(p.product_ids)==6 and len(set(p.product_ids))==6
    assert len(p.amazon)==6
    assert '<h1>台風・秋雨シーズンに！レインブーツ6選</h1>' in HTML
