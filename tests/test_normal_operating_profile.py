import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pipeline'))
import operating_profile

class NormalOperatingProfileTests(unittest.TestCase):
    def test_normal_enabled_only_with_recovery_inactive(self):
        with patch.object(operating_profile,'active_policy',return_value=None):
            self.assertTrue(operating_profile.normal_profile())
        with patch.object(operating_profile,'active_policy',return_value={'active':True}):
            self.assertFalse(operating_profile.normal_profile())
    def test_missing_or_invalid_config_fails_closed_to_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(operating_profile,'ROOT',Path(tmp)):
                self.assertFalse(operating_profile.normal_profile())
                (Path(tmp)/'config').mkdir()
                (Path(tmp)/'config/operating-profile.json').write_text('{invalid')
                self.assertFalse(operating_profile.normal_profile())
    def test_normal_does_not_disable_publishing_gate(self):
        from publish_gate import require_publishing_enabled,PublishGateError
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'config').mkdir();(p/'config/publishing-control.json').write_text('{"enabled":false}')
            with self.assertRaises(PublishGateError):require_publishing_enabled(p)

if __name__=='__main__':unittest.main()
