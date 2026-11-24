from unittest.mock import patch
from growth_role_business import collect_unique_products, _verified_product

AXES=[{'id':x,'label':x,'keywords':[x]} for x in ('popular','ingredient','scent')]
TOPIC={'query':'bath additive', 'comparison_axes':AXES}

def item(i,axis=None,verified=False):
    return {'asin':f'B{i:09d}', 'amazon_title':f'Unique model {i}',
            'image_url':f'https://example.com/{i}.jpg',
            'discovery_axis_id':axis,
            'sources':[{'verified':True,'excerpt':f'fact {i} about ingredient, scent and use'}] if verified else []}

def test_one_by_one_verification_continues_past_ten_unverified_listings():
    found={'popular':[item(i) for i in range(28)], 'ingredient':[item(i) for i in range(30,58)],
           'scent':[item(i) for i in range(60,88)]}
    examined=[]
    def search(topic,*,cache_only):return found[topic['query'].split()[-1]]
    def verify(product,*,cache_only):
        examined.append(product['asin'])
        i=int(product['asin'][1:]);return item(i,verified=True) if i%2==0 else None
    with patch('growth_role_business.discover_products',side_effect=search), \
         patch('growth_role_business._verified_product',side_effect=verify):
        rows,receipt=collect_unique_products(TOPIC,cache_only=False)
    assert receipt['complete']
    assert len(rows)==30
    assert len(set(examined))==len(examined)
    assert len(examined)>30
    assert all(len([p for p in rows if p['discovery_axis_id']==x['id']])==10 for x in AXES)
    assert all(p['sources'][0]['verified'] for p in rows)

def test_unverified_candidates_never_use_up_the_ten_slots():
    found={'popular':[item(i) for i in range(12)],'ingredient':[item(i) for i in range(20,32)],
           'scent':[item(i) for i in range(40,52)]}
    def search(topic,*,cache_only):return found[topic['query'].split()[-1]]
    def verify(product,*,cache_only):
        i=int(product['asin'][1:]);return item(i,verified=True) if i%3==0 else None
    with patch('growth_role_business.discover_products',side_effect=search), \
         patch('growth_role_business._verified_product',side_effect=verify):
        rows,receipt=collect_unique_products(TOPIC,cache_only=False)
    assert receipt['complete'] and len(rows)==12
    assert all(len([p for p in rows if p['discovery_axis_id']==a['id']])==4 for a in AXES)

def test_duplicate_moves_only_to_deficient_axis_and_preserves_two_in_origin():
    found={'popular':[item(1),item(2),item(3)],'ingredient':[item(3),item(4)],
           'scent':[item(5),item(6)]}
    def search(topic,*,cache_only):return found[topic['query'].split()[-1]]
    def verify(product,*,cache_only):return item(int(product['asin'][1:]),verified=True)
    with patch('growth_role_business.discover_products',side_effect=search), \
         patch('growth_role_business._verified_product',side_effect=verify):
        rows,receipt=collect_unique_products(TOPIC,cache_only=False)
    counts={a['id']:sum(x['discovery_axis_id']==a['id'] for x in rows) for a in AXES}
    assert receipt['complete'] and counts=={'popular':2,'ingredient':2,'scent':2}
    assert next(x for x in rows if x['asin']=='B000000003')['discovery_axis_id']=='ingredient'
    assert any(x['moved']==1 for x in receipt['attempts'])

def test_duplicate_never_moves_when_origin_would_drop_below_two():
    found={'popular':[item(1),item(2)],'ingredient':[item(2),item(4)],'scent':[item(5),item(6)]}
    def search(topic,*,cache_only):return found[topic['query'].split()[-1]]
    def verify(product,*,cache_only):return item(int(product['asin'][1:]),verified=True)
    with patch('growth_role_business.discover_products',side_effect=search), \
         patch('growth_role_business._verified_product',side_effect=verify):
        rows,receipt=collect_unique_products(TOPIC,cache_only=False)
    assert not receipt['complete']
    assert next(x for x in rows if x['asin']=='B000000002')['discovery_axis_id']=='popular'

def test_product_identification_no_longer_requires_manufacturer_research():
    calls=[]
    def amazon(asin,title):calls.append(('amazon',asin));return None
    def manufacturer(products,**kwargs):
        assert len(products)==1
        calls.append(('manufacturer',products[0]['asin']))
        return [{**products[0],'sources':[{'verified':True,'excerpt':'manufacturer specification'}]}]
    with patch('growth_role_business.fetch_amazon_item_source',side_effect=amazon), \
         patch('growth_role_business.research_selected',side_effect=manufacturer):
        result=_verified_product(item(3),cache_only=False)
    assert result is not None and result['provisional'] is True
    assert calls==[('amazon','B000000003')]

def test_raw_discovery_retrieves_listings_without_preadmitting_unverified_items():
    from growth_role_business import discover_products
    rows=[{'asin':f'B{i:09d}', 'title':f'Product {i}', 'image_url':f'https://example.com/{i}.jpg',
           'rating':4.4, 'review_count':125} for i in range(25)]
    with patch('growth_role_business.AutositeDiscoveryAdapter.search_amazon_live',return_value=rows), \
         patch('growth_role_business.fetch_amazon_item_source',side_effect=AssertionError('must not prefetch')):
        found=discover_products({'query':'test','_raw_only':True,'_max_products':30},cache_only=False)
    assert len(found)==25
    assert all('sources' not in x for x in found)
