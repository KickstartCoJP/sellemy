import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
from product_selection import select_six,ProductSelectionError

TOPIC={'query':'シャンプー','comparison_axes':[{'id':'formula','keywords':['洗浄成分']},{'id':'application','keywords':['すすぎ']} ]}

def candidates():
 return [{'asin':f'B{i:09}', 'amazon_title':f'Shampoo-{i}', 'brand':f'Brand-{i}',
          'image_url':f'https://m.media-amazon.com/images/I/{i}.jpg', 'discovery_score':1,
          'sources':[{'verified':True,'excerpt':'洗浄成分 すすぎ方法', 'title':f'Shampoo-{i}'}]} for i in range(6)]

def test_selection_passes_six_with_grounded_two_axes():
 products,_=select_six(candidates(),TOPIC,{})
 assert len(products)==6

def test_selection_excludes_unsupported_product_before_independent_qa():
 rows=candidates();rows[-1]['sources'][0]['excerpt']='人気のシャンプー'
 with pytest.raises(ProductSelectionError,match='verified topic-axis'):
  select_six(rows,TOPIC,{})

def test_selection_requires_two_grounded_axes():
 rows=candidates()
 for row in rows:row['sources'][0]['excerpt']='洗浄成分'
 with pytest.raises(ProductSelectionError,match='two grounded'):
  select_six(rows,TOPIC,{})
