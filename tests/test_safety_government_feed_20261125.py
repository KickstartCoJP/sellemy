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


def test_caa_complete_feed_and_cache_or_fail_closed(tmp_path):
    from safety_government_feed import caa_registry,GovernmentFeedUnavailable
    class Result:
        returncode=0
        stderr=''
        def __init__(self,screen,page,count):
            self.stdout=json.dumps({'screen':screen,'page':page,'count':count,'rows':[
                {'title':f'{screen}-{i} Anker Soundcore 3',
                 'url':f'https://www.recall.caa.go.jp/result/detail.php?rcl={screen}{i}',
                 'published':'2026/11/25'} for i in range(page*60,min(count,(page+1)*60))]})
    seen=[]
    def runner(cmd,**kwargs):
        screen=cmd[-3];page=int(cmd[-1]);seen.append((screen,page))
        return Result(screen,page,61 if screen=='02' else 2)
    path=tmp_path/'caa.json';partial=tmp_path/'progress.json'
    out=caa_registry(path=path,partial_path=partial,runner=runner)
    assert out['complete'] and out['count']==63
    assert seen==[('02',0),('02',1),('03',0)]
    assert 'Anker' in out['details'][0]['text']
    assert caa_registry(path=path,partial_path=partial,runner=lambda *args,**kw:(_ for _ in ()).throw(AssertionError()))==out
    path.unlink()
    def bad(cmd,**kwargs):
        r=runner(cmd,**kwargs)
        r.stdout=json.dumps({'screen':cmd[-3],'page':int(cmd[-1]),'count':4,'rows':[]})
        return r
    with pytest.raises(GovernmentFeedUnavailable):caa_registry(path=path,partial_path=partial,runner=bad)
    assert not path.exists()


def test_caa_resume_after_failed_page(tmp_path):
    from safety_government_feed import caa_registry,GovernmentFeedUnavailable
    path=tmp_path/'caa.json';partial=tmp_path/'partial.json'
    calls=[]
    def runner(cmd,**kwargs):
        screen,page=cmd[-3],int(cmd[-1]);calls.append((screen,page))
        count=61 if screen=='02' else 1
        if (screen,page)==('02',1) and calls.count(('02',1))==1:
            return type('R',(),{'returncode':1,'stderr':'remote error','stdout':''})()
        rows=[{'title':f'Model {i}','url':f'https://www.recall.caa.go.jp/result/detail.php?rcl={screen}{i}'}
              for i in range(page*60,min(count,page*60+60))]
        return type('R',(),{'returncode':0,'stderr':'','stdout':json.dumps({
            'screen':screen,'page':page,'count':count,'rows':rows})})()
    with pytest.raises(GovernmentFeedUnavailable):
        caa_registry(path=path,partial_path=partial,runner=runner)
    assert partial.exists() and not path.exists()
    result=caa_registry(path=path,partial_path=partial,runner=runner)
    assert result['complete'] and result['count']==62
    assert calls.count(('02',0))==2 and calls.count(('02',1))==2
    assert not partial.exists()


def test_caa_absence_blocks_combined_all_clear():
    from safety_government_feed import cached_combined_registry
    from unittest.mock import patch
    with patch('safety_government_feed.cached_registry',return_value={
        'source':'NITE','count':1,'checked_at':datetime.now(timezone.utc).isoformat(),
        'complete':True,'details':[{'id':'1','text':'hello'}]}):
        with patch('safety_government_feed.CAA_INDEX_PATH',Path('/tmp/missing-sellemy-caa-test-file.json')):
            assert cached_combined_registry() is None


def test_caa_changed_first_page_invalidates_cached_later_pages_even_if_count_same(tmp_path):
    from safety_government_feed import caa_registry
    path=tmp_path/'caa.json';partial=tmp_path/'pages.json'
    calls=[]
    phase={'value':0}
    def runner(cmd,**kwargs):
        screen,page=cmd[-3],int(cmd[-1]);calls.append((phase['value'],screen,page))
        count=61 if screen=='02' else 1
        rows=[]
        for i in range(page*60,min(count,(page+1)*60)):
            changed=(phase['value']>0 and screen=='02' and i==0)
            code=f'{screen}{i}' + ('changed' if changed else '')
            rows.append({'title':'Anker Soundcore 3','url':'https://www.recall.caa.go.jp/result/detail.php?rcl='+code})
        return type('R',(),{'returncode':0,'stderr':'','stdout':json.dumps({
            'screen':screen,'page':page,'count':count,'rows':rows})})()
    original=caa_registry(path=path,partial_path=partial,runner=runner)
    assert original['complete']
    # Simulate an interrupted later refresh where page one was already cached.
    import json as jsonlib
    recent={'started_at':datetime.now(timezone.utc).isoformat(),
            'pages':{'02:0':{'screen':'02','page':0,'count':61,
                             'rows':[{'title':'old','url':'https://www.recall.caa.go.jp/result/detail.php?rcl=old'}]*60},
                     '02:1':{'screen':'02','page':1,'count':61,'rows':[
                         {'title':'old','url':'https://www.recall.caa.go.jp/result/detail.php?rcl=old1'}]}}}
    partial.write_text(jsonlib.dumps(recent))
    phase['value']=1
    refreshed=caa_registry(path=path,partial_path=partial,runner=runner,force=True)
    assert refreshed['complete']
    assert (1,'02',1) in calls
