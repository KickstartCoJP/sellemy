"""Release staging tests: no live GitHub operations."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import article_release as ar

SHA = "a" * 40
DEPLOY = "b" * 40


class ArticleReleaseTests(unittest.TestCase):
    def do_release(self, *, status="SUCCESS", base=SHA, head=SHA,
                   dirty="", pr_sha=SHA, parent=SHA):
        calls = []
        remote_reads = [0]
        def run(root, *args, check=True):
            calls.append(args)
            if args[:3] == ("git", "status", "--porcelain"):
                return SimpleNamespace(stdout=dirty)
            if args[:3] == ("git", "rev-parse", "HEAD"):
                return SimpleNamespace(stdout=head)
            if args[:2] == ("git", "rev-parse") and args[2].endswith("^"):
                return SimpleNamespace(stdout=parent)
            if args[:3] == ("git", "rev-parse", "origin/main"):
                remote_reads[0] += 1
                return SimpleNamespace(stdout=(base if remote_reads[0] == 1 else DEPLOY))
            return SimpleNamespace(stdout="")
        def gh(root, *args):
            calls.append(args)
            if args[:2] == ("pr", "view"):
                if args[2] == "article-release/item-":
                    raise AssertionError("unexpected branch name")
                if args[2].startswith("article-release/"):
                    return {"number": 17, "headRefOid": pr_sha}
                return {"headRefOid": pr_sha, "statusCheckRollup":
                        [{"name": "change-contract", "conclusion": status}]}
            if args[:3] == ("api", "-X", "PUT"):
                return {"merged": True, "sha": DEPLOY}
            return {}
        # The mock preserves Git commands and forbids actual network.
        with patch.object(ar, "_run", side_effect=run), patch.object(ar, "_gh", side_effect=gh):
            return ar.release(Path("/unused"), "item", SHA, timeout=1), calls

    def test_rejects_invalid_slug_without_io(self):
        with patch.object(ar, "_run", side_effect=AssertionError("I/O attempted")):
            with self.assertRaisesRegex(ar.ArticleReleaseError, "slug"):
                ar.release(Path("/unused"), "../bad", SHA)

    def test_rejects_dirty_checkout(self):
        with self.assertRaisesRegex(ar.ArticleReleaseError, "dirty"):
            self.do_release(dirty=" M ops/change_contract_gate.py")

    def test_rejects_drift_before_staging(self):
        with self.assertRaisesRegex(ar.ArticleReleaseError, "main advanced"):
            self.do_release(base="c"*40)

    def test_rejects_ci_failure(self):
        with self.assertRaisesRegex(ar.ArticleReleaseError, "CI rejected"):
            self.do_release(status="FAILURE")

    def test_rejects_changed_candidate(self):
        with self.assertRaisesRegex(ar.ArticleReleaseError, "SHA changed"):
            self.do_release(pr_sha="c"*40)

    def test_success_requires_checks_and_exact_merge(self):
        sha, calls = self.do_release()
        self.assertEqual(sha, DEPLOY)
        self.assertTrue(any("context=ceo-staging-approval" in x for x in calls))
        self.assertTrue(any(x[:3] == ("git", "reset", "--hard") for x in calls))


if __name__ == "__main__":
    unittest.main()

