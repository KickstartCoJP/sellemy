from __future__ import annotations
import json, re, subprocess, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

class ProductionDeployError(RuntimeError):
    pass

REPO = "KickstartCoJP/sellemy"
BASE = "https://www.sellemy.jp"
STATE_PATH = Path.home()/"Library/Application Support/Sellemy/production-deploy/latest.json"

def _gh(path: str, *, method: str = "GET"):
    cmd = ["gh", "api"]
    if method != "GET":
        cmd += ["--method", method]
    cmd.append(path)
    p = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if p.returncode:
        detail = (p.stderr or p.stdout).strip()[:500]
        raise ProductionDeployError(f"GitHub API failed: {detail}")
    return json.loads(p.stdout) if p.stdout.strip() else {}

def _get(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent":"Sellemy-ProductionVerify/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return int(r.status), str(r.geturl()), r.read()

def verify(slug: str, category: str, commit: str, *, timeout_seconds: int = 420, poll_seconds: int = 10) -> dict:
    article = f"{BASE}/article/{category}/{slug}.html"
    deadline = time.monotonic() + timeout_seconds
    triggered = False
    last = {}
    while time.monotonic() < deadline:
        pages = _gh(f"repos/{REPO}/pages")
        builds = _gh(f"repos/{REPO}/pages/builds?per_page=10")
        build = next((b for b in builds if b.get("commit") == commit), None)
        last = {"pages_status": pages.get("status"), "build": build}
        if build and build.get("status") == "built":
            break
        if not triggered and (not build or build.get("status") not in {"building", "queued"}):
            _gh(f"repos/{REPO}/pages/builds", method="POST")
            triggered = True
        time.sleep(poll_seconds)
    else:
        raise ProductionDeployError(f"Pages build not built for {commit}: {last}")
    status, final, body = _get(article)
    if status != 200:
        raise ProductionDeployError(f"article HTTP {status}: {final}")
    text = body.decode("utf-8", "replace")
    match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', text, re.I)
    if not match:
        match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', text, re.I)
    if not match:
        raise ProductionDeployError("og:image missing from production article")
    og = match.group(1)
    og_status, og_final, _ = _get(og)
    if og_status != 200:
        raise ProductionDeployError(f"OG image HTTP {og_status}: {og_final}")
    top_status, top_final, top_body = _get(f"{BASE}/")
    if top_status != 200 or slug.encode() not in top_body:
        raise ProductionDeployError(
            f"TOP read-back missing slug; status={top_status} url={top_final}"
        )
    result = {
        "production_verified": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "slug": slug,
        "category": category,
        "commit": commit,
        "article_url": final,
        "article_status": status,
        "top_url": top_final,
        "top_status": top_status,
        "og_url": og_final,
        "og_status": og_status,
        "pages_build_status": "built",
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding='utf-8')
    tmp.replace(STATE_PATH)
    return result

