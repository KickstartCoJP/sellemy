import json
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipeline'))
import production_deploy as deploy


def test_readback_rejects_wrong_asins(tmp_path):
    evidence=tmp_path/'data'/'evidence';evidence.mkdir(parents=True)
    (evidence/'example.json').write_text(json.dumps({'products':[{'asin':f'B{i:09d}'} for i in range(6)],'canonical_url':'https://www.sellemy.jp/article/gadget/example.html'}))
    from unittest.mock import patch
    with patch.object(deploy,'_gh',side_effect=[{},[{'commit':'abc','status':'built'}]]), \
         patch.object(deploy,'_get',return_value=(200,'https://www.sellemy.jp/article/gadget/example.html',b'<html>wrong article</html>')), \
         patch.object(deploy,'__file__',str(tmp_path/'pipeline'/'production_deploy.py')):
        try:
            deploy.verify('example','gadget','abc',timeout_seconds=2)
        except deploy.ProductionDeployError as e:
            assert 'ASIN mismatch' in str(e)
        else:
            assert False,'read-back must fail'
