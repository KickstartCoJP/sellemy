"""Central official register: complete all-page inventory or fail closed."""
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
import pytest
from safety_government_feed import refresh,full_registry,cached_registry,GovernmentFeedUnavailable


class Response:
    def __init__(self,html=None,body='',status=200):
        self.html=html;self.content=body.encode();self.status_code=status
        self.url='https://safe-lite.nite.go.jp/recall/detail/2025102101'
    def raise_for_status(self):
        if self.status_code!=200:raise RuntimeError('HTTP error')
    def json(self):return {'htmlContent':self.html}


def fragment(start,count):
    rows=''.join(f'<tr><td>{i}</td><td>2026/11/25</td><td>HP</td><td><a href="./recall/detail/{i}">スピーカー</a></td><td>Soundcore 3</td><td>アンカー</td><td>回収</td></tr>' for i in range(start,start+count))
    return f'<span id="recall-count">{count}</span><table><tbody>{rows}</tbody></table>'


def test_refresh_completes_and_caches_single_page(tmp_path):
    class Session:
        def get(self,*a,**kw):return Response()
        def post(self,*a,**kw):return Response(fragment(1,2))
    path=tmp_path/'index.json'
    x=refresh(path=path,session=Session(),force=True)
    assert x['complete'] and x['count']==2
    assert x['items'][0]['source']=='https://safe-lite.nite.go.jp/recall/detail/1'
    assert refresh(path=path,session=Session())==x


def test_incomplete_pages_never_write_success_snapshot(tmp_path):
    class Session:
        def get(self,url,*a,**kw):
            return Response(fragment(101,99)) if 'page=2' in url else Response()
        def post(self,*a,**kw):
            first=fragment(1,100).replace('id="recall-count">100','id="recall-count">101')
            return Response(first)
    path=tmp_path/'index.json'
    with pytest.raises(GovernmentFeedUnavailable,match='changed during pagination'):
        refresh(path=path,session=Session(),force=True)
    assert not path.exists()


def test_detail_backfill_requires_every_official_notice(tmp_path):
    index=tmp_path/'index.json';details=tmp_path/'details.json'
    now=datetime.now(timezone.utc)
    index.write_text(json.dumps({'schema':'sellemy-nite-recall-index/v1',
        'source':'https://safe-lite.nite.go.jp/recall/search/index',
        'checked_at':now.isoformat(),'complete':True,'count':2,
        'items':[{'id':'one','source':'https://safe-lite.nite.go.jp/recall/detail/one'},
                 {'id':'two','source':'https://safe-lite.nite.go.jp/recall/detail/two'}]}))
    def fake(row,session=None):return {'id':row['id'],'source':row['source'],'text':'Soundcore 3'}
    with patch('safety_government_feed.detail',side_effect=fake):
        with pytest.raises(GovernmentFeedUnavailable,match='incomplete'):
            full_registry(index_path=index,details_path=details,limit=1)
        assert len(json.loads(details.read_text())['details'])==1
        complete=full_registry(index_path=index,details_path=details)
        assert complete['count']==2 and complete['complete']
    assert cached_registry(index_path=index,details_path=details)['count']==2
    stale=now+timedelta(days=2)
    assert cached_registry(index_path=index,details_path=details,now=stale) is None
