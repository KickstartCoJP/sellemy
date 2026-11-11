"""Contract gate is independent of Sellemy production article generation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
import change_contract_gate as gate


class ChangeContractGateTests(unittest.TestCase):
    def setUp(self):
        self.contract_path = 'change-contracts/TEST.json'
        self.code_path = 'pipeline/example.py'
        self.test_path = 'tests/test_example.py'
        self.data = {
            'schema': 'kickstart-change-contract/v1',
            'task_id': 'TASK-1',
            'instruction_ref': 'CEO instruction 1',
            'instruction': 'Change one explicit function',
            'implementer': 'implementation role',
            'canonical_refs': ['https://example.com/canonical'],
            'allowed_paths': [self.contract_path, self.code_path, self.test_path],
            'preserved_rules': ['Product QA is unchanged'],
            'acceptance_tests': [self.test_path],
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'tests').mkdir()
        (self.root / self.test_path).write_text('pass\n')
        self.addCleanup(self.tmp.cleanup)

    def validate(self, changes, data=None):
        with patch.object(gate, 'ROOT', self.root), patch.object(gate, 'git', return_value=json.dumps(data or self.data)):
            return gate.validate_contract(set(changes), 'commit')

    def test_approved_exact_paths(self):
        self.assertEqual(self.validate([self.contract_path, self.code_path, self.test_path]), [])

    def test_unauthorized_code_file_rejected(self):
        issues = self.validate([self.contract_path, self.code_path, self.test_path, 'config/qa.json'])
        self.assertTrue(any('out-of-scope' in x for x in issues))

    def test_no_contract_rejected(self):
        self.assertTrue(gate.validate_contract({self.code_path}, 'HEAD'))

    def test_no_test_change_rejected(self):
        self.assertTrue(any('test change' in x for x in self.validate([self.contract_path, self.code_path])))

    def test_wildcard_scope_rejected(self):
        data = {**self.data, 'allowed_paths': [self.contract_path, 'pipeline/*', self.test_path]}
        self.assertTrue(any('exact' in x for x in self.validate([self.contract_path, self.code_path, self.test_path], data)))

    def test_missing_canonical_ref_rejected(self):
        data = {**self.data, 'canonical_refs': []}
        self.assertTrue(any('canonical_refs' in x for x in self.validate([self.contract_path, self.code_path, self.test_path], data)))

    def test_staging_contract_requires_exact_paths(self):
        self.assertEqual(self.validate([self.contract_path, self.code_path, self.test_path]), [])
        issues=self.validate([self.contract_path, self.code_path, self.test_path, 'ops/unauthorized.py'])
        self.assertTrue(any('out-of-scope' in item for item in issues))

    def test_direct_main_development_rejected(self):
        changed = {self.contract_path, self.code_path, self.test_path}
        with patch.object(gate, 'changed_paths', return_value=changed), patch.object(gate, 'is_publication_only', return_value=False):
            self.assertTrue(any('direct development' in x for x in gate.check('old', 'new', destination='refs/heads/main')))

    def test_single_article_publication_remains_allowed(self):
        slug, category = 'sample-article', 'dailygoods'
        changed = gate.publication_paths(slug, category)
        with patch.object(gate, 'git', return_value=gate.PREFIX + slug):
            self.assertTrue(gate.is_publication_only(changed, 'old', 'new'))
        with patch.object(gate, 'changed_paths', return_value=changed), patch.object(gate, 'git', return_value=gate.PREFIX + slug):
            self.assertEqual(gate.check('old', 'new', destination='refs/heads/main'), [])

    def test_publication_with_extra_code_is_rejected(self):
        changed = gate.publication_paths('sample-article', 'dailygoods') | {self.code_path}
        with patch.object(gate, 'changed_paths', return_value=changed), patch.object(gate, 'git', return_value=gate.PREFIX + 'sample-article'):
            self.assertTrue(gate.check('old', 'new', destination='refs/heads/main'))

    def test_multiple_publication_commits_not_whitelisted(self):
        changed = gate.publication_paths('sample-article', 'dailygoods')
        with patch.object(gate, 'git', return_value=(gate.PREFIX + 'sample-article\n') * 2):
            self.assertFalse(gate.is_publication_only(changed, 'old', 'new'))


if __name__ == '__main__':
    unittest.main()
