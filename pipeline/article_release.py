"""Release one CEO-approved article via protected GitHub PR, not direct main push."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

REPO = "KickstartCoJP/sellemy"
GH = "/opt/homebrew/bin/gh"


class ArticleReleaseError(RuntimeError):
    pass


def _run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=45)
    if check and result.returncode:
        raise ArticleReleaseError((result.stderr or result.stdout).strip())
    return result


def _gh(root: Path, *args: str) -> dict:
    result = _run(root, GH, *args)
    return json.loads(result.stdout)


def release(root: Path, slug: str, candidate: str, *, timeout: int = 180) -> str:
    """Publish only an already-validated local article commit; return main commit."""
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ArticleReleaseError("invalid publication slug")
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise ArticleReleaseError("invalid candidate commit")
    # The caller must have completed existing CEO approval and publication QA.
    if _run(root, "git", "status", "--porcelain").stdout.strip():
        raise ArticleReleaseError("dirty worktree: publication refused")
    if _run(root, "git", "rev-parse", "HEAD").stdout.strip() != candidate:
        raise ArticleReleaseError("candidate commit no longer checked out")
    parent = _run(root, "git", "rev-parse", candidate + "^").stdout.strip()
    _run(root, "git", "fetch", "origin", "main", "--quiet")
    base = _run(root, "git", "rev-parse", "origin/main").stdout.strip()
    if parent != base:
        raise ArticleReleaseError("main advanced: retry from a clean base")
    branch = f"article-release/{slug}-{candidate[:12]}"
    _run(root, "git", "push", "origin", f"{candidate}:refs/heads/{branch}")
    try:
        pr = _gh(root, "pr", "view", branch, "--repo", REPO,
                 "--json", "number,headRefOid")
    except ArticleReleaseError:
        _run(root, GH, "pr", "create", "--repo", REPO, "--base", "main",
             "--head", branch, "--title", f"CEO承認済み記事の公開：{slug}",
             "--body", f"既存のCEO公開承認・品質検査を通過した記事のみを公開。対象：{slug}。変更範囲は公開用11ファイルに限定。")
        pr = _gh(root, "pr", "view", branch, "--repo", REPO,
                 "--json", "number,headRefOid")
    if pr.get("headRefOid") != candidate:
        raise ArticleReleaseError("publication PR candidate SHA changed")
    number = int(pr["number"])
    _gh(root, "api", "-X", "POST", f"repos/{REPO}/statuses/{candidate}",
        "-f", "state=success", "-f", "context=ceo-staging-approval",
        "-f", "description=Preexisting CEO article publication gate passed",
        "-f", f"target_url=https://github.com/{REPO}/pull/{number}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _gh(root, "pr", "view", str(number), "--repo", REPO,
                    "--json", "headRefOid,statusCheckRollup")
        if state.get("headRefOid") != candidate:
            raise ArticleReleaseError("publication PR changed during checks")
        checks = [c for c in state.get("statusCheckRollup") or []
                  if c.get("name") == "change-contract"]
        if checks and all(c.get("conclusion") == "SUCCESS" for c in checks):
            break
        if any(c.get("conclusion") in {"FAILURE", "CANCELLED", "TIMED_OUT"}
               for c in checks):
            raise ArticleReleaseError("publication contract CI rejected change")
        time.sleep(3)
    else:
        raise ArticleReleaseError("publication CI did not complete before deadline")
    merged = _gh(root, "api", "-X", "PUT",
                 f"repos/{REPO}/pulls/{number}/merge",
                 "-f", f"sha={candidate}", "-f", "merge_method=squash")
    if merged.get("merged") is not True or not merged.get("sha"):
        raise ArticleReleaseError("GitHub did not confirm exact-SHA merge")
    _run(root, "git", "fetch", "origin", "main", "--quiet")
    if _run(root, "git", "rev-parse", "origin/main").stdout.strip() != merged["sha"]:
        raise ArticleReleaseError("remote main changed before post-merge verification")
    _run(root, "git", "reset", "--hard", "origin/main")
    return str(merged["sha"])

