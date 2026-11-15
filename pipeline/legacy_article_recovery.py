from __future__ import annotations
"""CEO-authorized legacy article remediation admission, using extracted local input.

Only existing growth-recovery jobs are used.  Source evidence is NOT a publish
approval: current Product/Writer/Reviewer/CEO gates retain authority.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from growth_recovery import (JOBS_ROOT, ROOT, load_job, job_dir, read_artifact,
                             write_artifact, set_stage)

DEFAULT_EXTRACTED = Path('/Users/kickstart/sellemy-revalidation/legacy436-current-scheme-input-20261115')
INNER = Path('drive-readback-20261014/final-consolidated-handoff-0336')
MARKER = 'legacy-repair.json'
BACKUP = 'legacy-original-20261115'
SLUG = re.compile(r'[a-z0-9][a-z0-9-]*\Z')


def source_root() -> Path:
    path = Path(os.environ.get('SELLEMY_LEGACY_LOCAL_INPUT', str(DEFAULT_EXTRACTED))).expanduser()
    return path / INNER


def _read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def candidates(root: Path | None = None) -> list[str]:
    root = root or source_root()
    accepted = _read_json(root / 'final-validation.json')
    article_dir = root / 'articles'
    slugs = sorted(p.name for p in article_dir.iterdir() if p.is_dir())
    if len(slugs) != accepted['accepted_bodies']:
        raise ValueError('extracted article count differs from signed handoff')
    if any(not SLUG.fullmatch(slug) for slug in slugs):
        raise ValueError('invalid local article slug')
    for slug in slugs:
        if not all((article_dir / slug / f).is_file()
                   for f in ('body.json', 'evidence.json', 'selection.json', 'article.html')):
            raise ValueError('incomplete extracted article: ' + slug)
    return slugs


def verify_local(root: Path | None = None) -> dict:
    root = root or source_root()
    hashes = _read_json(root / 'hashes.json')
    if not isinstance(hashes, dict) or len(hashes) < 2700:
        raise ValueError('invalid extracted evidence hash manifest')
    for relative, expected in hashes.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('missing or escaped extracted file: ' + relative)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('extracted evidence mismatch: ' + relative)
    return {'verified_files': len(hashes), 'article_count': len(candidates(root))}


def source_acquisition_ready(evidence: dict) -> bool:
    """Legacy snapshots are eligible for Product QA only with cited product facts.

    This is an intake *prerequisite*, not semantic QA approval. Existing
    Product/Reviewer/CEO gates remain the sole acceptance authorities.
    """
    products = evidence.get('products') if isinstance(evidence, dict) else None
    return isinstance(products, list) and len(products) == 6 and all(
        isinstance(product, dict) and any(
            isinstance(source, dict)
            and source.get('verified') is True
            and str(source.get('excerpt') or '').strip()
            and str(source.get('url') or '').startswith('https://')
            for source in (product.get('sources') or [])
        ) for product in products
    )



def live_baseline_available(slug: str, category: str | None) -> bool:
    """Only *currently published* articles can enter published-article repair.

    A signed historical handoff can include obsolete or nonpublic slugs. Those
    are not a production publication and must never block the whole scheduler.
    """
    if category not in {'beauty', 'dailygoods', 'gadget'}:
        return False
    return all(p.is_file() for p in (
        ROOT / 'article' / category / (slug + '.html'),
        ROOT / 'data' / 'payloads' / (slug + '.json'),
        ROOT / 'data' / 'evidence' / (slug + '.json'),
    ))


def historical_category(root: Path, slug: str) -> str | None:
    body = _read_json(root / 'articles' / slug / 'body.json')
    return body.get('category') if isinstance(body, dict) else None


def has_pending(root: Path | None = None) -> bool:
    root = root or source_root()
    for slug in candidates(root):
        marker = read_artifact(slug, MARKER)
        if not isinstance(marker, dict):
            if not live_baseline_available(slug, historical_category(root, slug)):
                continue
            checkpoint = job_dir(slug) / 'state.json'
            if checkpoint.is_file():
                state = load_job(slug)
                if (state.get('status') != 'PUBLISHED'
                        and not (state.get('status') == 'DISCARDED' and
                                 'duplicate slug already exists' in str(state.get('discard_reason') or ''))):
                    continue
            return True
        state_path = job_dir(slug) / 'state.json'
        if state_path.is_file():
            state = load_job(slug)
            if (state.get('source') == 'legacy_repair' and
                    state.get('status') == 'ACTIVE' and
                    state.get('current_stage') == 'PRODUCT_HOLD' and
                    not source_acquisition_ready(read_artifact(slug, 'evidence.json', {}))):
                return True
    return False


def _backup_original(slug: str) -> None:
    directory = job_dir(slug)
    backup = directory / BACKUP
    if backup.exists():
        if not (backup / 'state.json').is_file():
            raise ValueError('incomplete historical backup: ' + slug)
        return
    parent = directory.parent
    with tempfile.TemporaryDirectory(dir=parent, prefix='.legacy-copy-') as tmp:
        copy = Path(tmp) / 'snapshot'
        shutil.copytree(directory, copy)
        if not (copy / 'state.json').is_file():
            raise ValueError('historical job lacks state: ' + slug)
        os.replace(copy, backup)


def _bootstrap_published_job(slug: str, category: str) -> None:
    """Reconstruct a *published* existing article's recovery checkpoint.

    Legacy site publications predate growth-recovery jobs. Reuse their current
    article/evidence and never create a duplicate article or publication.
    """
    from growth_recovery import create_job, _save_state
    import subprocess
    payload_path = ROOT / 'data' / 'payloads' / (slug + '.json')
    evidence_path = ROOT / 'data' / 'evidence' / (slug + '.json')
    article_path = ROOT / 'article' / category / (slug + '.html')
    if not all(p.is_file() for p in (payload_path, evidence_path, article_path)):
        raise ValueError('published baseline files missing: ' + slug)
    payload = _read_json(payload_path)
    evidence = _read_json(evidence_path)
    if payload.get('slug') != slug or evidence.get('slug') != slug:
        raise ValueError('published baseline slug differs: ' + slug)
    if payload.get('category') != category or evidence.get('category') != category:
        raise ValueError('published baseline category differs: ' + slug)
    result = subprocess.run(
        ['git', 'log', '-1', '--format=%H', '--', str(article_path.relative_to(ROOT))],
        cwd=ROOT, capture_output=True, text=True, check=True)
    commit = result.stdout.strip()
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('no original published article commit: ' + slug)
    topic = {'slug': slug, 'category': category, 'title': payload['h1'],
             'query': payload['h1'],
             'comparison_axes': evidence.get('comparison_axes') or []}
    job = create_job(topic=topic, evidence=evidence,
                     selection=evidence.get('selection') or {},
                     planning_provider={}, viability_probes=[],
                     source='legacy_published_baseline')
    write_artifact(slug, 'payload.json', payload)
    state = load_job(slug)
    state.update(status='PUBLISHED', current_stage='PUBLISHED',
                 next_retry_at=None, published_commit=commit)
    _save_state(state, event='LEGACY_PUBLISHED_BASELINE_RECOVERED',
                detail={'source_commit': commit})


def _restore_live_article_from_duplicate_discard(slug: str, state: dict) -> None:
    """The prior *job* was discarded as duplicate, but the site article is live.

    Preserve the discarded job unchanged in a backup, then establish a baseline
    for this already-published slug. Do not create a new site article.
    """
    from growth_recovery import _save_state
    import subprocess
    if state.get('status') != 'DISCARDED' or 'duplicate slug already exists' not in str(
            state.get('discard_reason') or ''):
        raise ValueError('not a recoverable published-duplicate job: ' + slug)
    category = state.get('category')
    article = ROOT / 'article' / category / (slug + '.html')
    current_payload = ROOT / 'data' / 'payloads' / (slug + '.json')
    current_evidence = ROOT / 'data' / 'evidence' / (slug + '.json')
    if not all(p.is_file() for p in (article, current_payload, current_evidence)):
        raise ValueError('discarded job is not a known live publication: ' + slug)
    if any(_read_json(p).get('slug') != slug for p in (current_payload, current_evidence)):
        raise ValueError('published duplicate baseline mismatch: ' + slug)
    result = subprocess.run(['git', 'log', '-1', '--format=%H', '--',
                             str(article.relative_to(ROOT))],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    commit = result.stdout.strip()
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('published duplicate commit is missing: ' + slug)
    _backup_original(slug)
    # This is a site-baseline reconciliation, not QA approval of the old job.
    write_artifact(slug, 'payload.json', _read_json(current_payload))
    write_artifact(slug, 'evidence.json', _read_json(current_evidence))
    state['status'] = 'PUBLISHED'
    state['current_stage'] = 'PUBLISHED'
    state['published_commit'] = commit
    state['next_retry_at'] = None
    _save_state(state, event='LEGACY_EXISTING_SITE_BASELINE_RESTORED',
                detail={'original_status': 'DISCARDED', 'site_commit': commit})


def import_one(root: Path | None = None) -> dict:
    root = root or source_root()
    for slug in candidates(root):
        marker = read_artifact(slug, MARKER)
        if isinstance(marker, dict):
            state = load_job(slug)
            if (state.get('source') == 'legacy_repair' and
                    state.get('status') == 'ACTIVE' and
                    state.get('current_stage') == 'PRODUCT_HOLD' and
                    not source_acquisition_ready(read_artifact(slug, 'evidence.json', {}))):
                marker['phase'] = 'planning_required'
                write_artifact(slug, MARKER, marker)
                set_stage(slug, 'PLANNING_PENDING', event='LEGACY_REPLAN_REQUESTED',
                          detail={'reason': 'old evidence lacks verified product sources'})
                return {'slug': slug, 'admitted': True, 'replanning': True}
            if state.get('status') == 'PUBLISHED' and marker.get('phase') in {
                    'initial_precheck', 'initial_reviewer'}:
                set_stage(slug, 'WRITER_PENDING', event='LEGACY_REPAIR_RESUMED')
                return {'slug': slug, 'admitted': True, 'resumed': True}
            continue
        # A historical export is not evidence that a page exists in today's
        # live site. Skip missing baselines without mutating any old record.
        if not live_baseline_available(slug, historical_category(root, slug)):
            continue
        article = root / 'articles' / slug
        historical = _read_json(article / 'evidence.json')
        needs_planning = not source_acquisition_ready(historical)
        if not (job_dir(slug) / 'state.json').exists():
            candidate = _read_json(article / 'body.json')
            category = candidate.get('category')
            if not isinstance(category, str):
                raise ValueError('legacy candidate category missing: ' + slug)
            _bootstrap_published_job(slug, category)
        state = load_job(slug)
        if state.get('status') == 'DISCARDED' and 'duplicate slug already exists' in str(
                state.get('discard_reason') or ''):
            _restore_live_article_from_duplicate_discard(slug, state)
            state = load_job(slug)
        if state.get('status') != 'PUBLISHED' or state.get('current_stage') != 'PUBLISHED':
            # No new slug, and never interrupt a separate unfinished article.
            continue
        payload = _read_json(article / 'body.json')
        evidence = historical
        selection = _read_json(article / 'selection.json')
        topic = read_artifact(slug, 'topic.json')
        if not isinstance(topic, dict) or topic.get('slug') != slug or payload.get('slug') != slug:
            raise ValueError('historical/current slug identity mismatch: ' + slug)
        if not needs_planning and (not isinstance(evidence, dict) or not isinstance(selection, dict)
                                   or evidence.get('slug') != slug or selection.get('slug') != slug):
            raise ValueError('historical/current slug identity mismatch: ' + slug)
        if not (ROOT / 'article' / state['category'] / (slug + '.html')).is_file():
            raise ValueError('historical article is missing from current site: ' + slug)
        if state['category'] != payload.get('category'):
            raise ValueError('historical/current category mismatch: ' + slug)
        current_evidence = read_artifact(slug, 'evidence.json') or {}
        if needs_planning:
            # Preserve old evidence and every approval until a new, independently
            # accepted six-product Planning result can be committed.
            _backup_original(slug)
            write_artifact(slug, MARKER, {
                'task_id': 'TASK-BU002-LEGACY-436-CONTINUOUS-RECOVERY-20261115',
                'phase': 'planning_required',
                'original_published_commit': state.get('published_commit'),
            })
            state['source'] = 'legacy_repair'
            from growth_recovery import _save_state
            _save_state(state, event='LEGACY_REPLAN_SOURCE_BOUND')
            set_stage(slug, 'PLANNING_PENDING', event='LEGACY_REPLAN_ADMITTED')
            return {'slug': slug, 'admitted': True, 'replanning': True}
        if state['category'] != evidence.get('category'):
            raise ValueError('historical/current category mismatch: ' + slug)
        evidence['eyecatch_image'] = evidence.get('eyecatch_image') or current_evidence.get('eyecatch_image')
        if not evidence.get('eyecatch_image'):
            raise ValueError('cannot preserve historical article image: ' + slug)
        evidence.pop('product_acceptance', None)
        _backup_original(slug)
        write_artifact(slug, 'evidence.json', evidence)
        write_artifact(slug, 'selection.json', selection)
        write_artifact(slug, 'payload.json', payload)
        # Never inherit a publication/quality authorization for changed content.
        for stale in ('qa.json', 'acceptance.json', 'qa-certification.json',
                      'independent-quality-receipts.json', 'chat-publish-approval.json',
                      'gate-feedback.json', 'product-blocker.json'):
            write_artifact(slug, stale, {})
        source_hash = hashlib.sha256((article / 'body.json').read_bytes()).hexdigest()
        write_artifact(slug, MARKER, {
            'task_id': 'TASK-BU002-LEGACY-436-CONTINUOUS-RECOVERY-20261115',
            'source_body_sha256': source_hash,
            'phase': 'initial_precheck',
            'original_published_commit': state.get('published_commit'),
        })
        set_stage(slug, 'WRITER_PENDING', event='LEGACY_REPAIR_ADMITTED',
                  detail={'source_body_sha256': source_hash})
        from growth_recovery import _save_state
        state = load_job(slug)
        state['source'] = 'legacy_repair'
        _save_state(state, event='LEGACY_REPAIR_SOURCE_BOUND')
        return {'slug': slug, 'admitted': True, 'source_body_sha256': source_hash}
    return {'admitted': False}



def replan_one(slug: str) -> dict:
    """Replan the same published article through live Product QA before Writer.

    Failed Planning/source discovery leaves old site and job evidence intact;
    it never increments the old Product QA failure streak.
    """
    from planning_runtime import replan_published_topic
    from growth_role_business import collect_unique_products, build_evidence
    from product_selection import select_six
    from analytics_feedback import load_feedback
    from semantic_review import independent_review
    from comparison_acceptance import product_issues
    from growth_recovery import _save_state, replace_product_evidence

    state = load_job(slug)
    marker = read_artifact(slug, MARKER) or {}
    if (state.get('source') != 'legacy_repair' or state.get('status') != 'ACTIVE'
            or state.get('current_stage') != 'PLANNING_PENDING'
            or marker.get('phase') != 'planning_required'
            or not state.get('published_commit')):
        raise ValueError('published legacy Planning restart not authorized for ' + slug)
    old_topic = read_artifact(slug, 'topic.json') or {}
    old_payload = read_artifact(slug, 'payload.json') or {}
    original_title = str(old_payload.get('h1') or old_topic.get('title') or '').strip()
    if not original_title:
        raise ValueError('published article title missing')
    try:
        topic, provider = replan_published_topic(slug=slug, category=state['category'],
                                                previous_title=original_title)
        products, trace = collect_unique_products(topic, cache_only=False)
        if len(products) != 6 or trace.get('complete') is not True:
            raise ValueError('new Planning product research lacks six distinct items')
        selected, selection = select_six(products, topic, load_feedback())
        evidence = build_evidence(topic, selected, selection)
        original_evidence = read_artifact(slug, 'evidence.json') or {}
        if not isinstance(original_evidence, dict):
            raise ValueError('existing published image evidence is invalid')
        evidence['eyecatch_image'] = original_evidence.get('eyecatch_image')
        if not evidence['eyecatch_image']:
            raise ValueError('published image must be preserved')
        if not source_acquisition_ready(evidence):
            raise ValueError('replanned six products lack authentic verified source excerpts')
        evidence['product_acceptance'] = independent_review(evidence)
        issues = product_issues(evidence)
        if issues:
            raise ValueError('new independent Product QA blocked: ' + '; '.join(issues))
    except Exception as exc:
        # One bounded attempt; owner must explicitly change the candidate/source
        # input before retrying, preventing repeated identical quality failures.
        write_artifact(slug, 'legacy-planning-blocker.json', {
            'reason': f'{type(exc).__name__}: {exc}'[:1600],
            'next_action': 'obtain a new Planning candidate and authentic six-product sources',
        })
        state = load_job(slug)
        state['current_stage'] = 'PLANNING_HOLD'
        state['next_retry_at'] = None
        _save_state(state, event='LEGACY_REPLAN_HOLD', detail={'reason': str(exc)[:500]})
        return {'slug': slug, 'action': 'hold', 'reason': str(exc)[:500]}
    # Final acceptance precedes mutation; revoke all changed-content Gate
    # receipts via the existing replace_product_evidence implementation.
    write_artifact(slug, 'topic.json', topic)
    replace_product_evidence(slug, evidence, selection)
    write_artifact(slug, 'planning-provider.json', provider)
    write_artifact(slug, 'legacy-planning-blocker.json', {})
    marker['phase'] = 'regular_repair'
    write_artifact(slug, MARKER, marker)
    return {'slug': slug, 'action': 'writer', 'product_qa': 'PASS'}


def precheck(slug: str) -> dict:
    from comparison_acceptance import product_issues
    from semantic_review import independent_review
    from editorial_plan import build_editorial_plan
    from growth_recovery import request_product_reselection
    marker = read_artifact(slug, MARKER) or {}
    phase = marker.get('phase')
    if phase == 'planning_required' and load_job(slug).get('current_stage') == 'WRITER_PENDING':
        # Recover a crash after accepted Product QA advanced to Writer but
        # before the legacy marker was finalized.
        evidence = read_artifact(slug, 'evidence.json')
        if isinstance(evidence, dict) and not product_issues(evidence):
            marker['phase'] = 'regular_repair'
            write_artifact(slug, MARKER, marker)
            return {'action': 'writer'}
    if phase == 'regular_repair':
        return {'action': 'writer'}
    if phase in {'initial_reviewer', 'review_running'}:
        return {'action': 'continue'}
    if phase != 'initial_precheck':
        raise ValueError('legacy precheck not requested: ' + slug)
    evidence = read_artifact(slug, 'evidence.json')
    issues = product_issues(evidence)
    if issues == ['independent acceptance missing']:
        evidence['product_acceptance'] = independent_review(evidence)
        write_artifact(slug, 'evidence.json', evidence)
        issues = product_issues(evidence)
    if issues:
        marker['phase'] = 'regular_repair'
        write_artifact(slug, MARKER, marker)
        request_product_reselection(slug, issues)
        return {'action': 'product', 'issues': issues}
    topic = read_artifact(slug, 'topic.json')
    write_artifact(slug, 'editorial-plan.json', build_editorial_plan(topic, evidence, ''))
    marker['phase'] = 'initial_reviewer'
    write_artifact(slug, MARKER, marker)
    return {'action': 'continue'}


def mark_review_started(slug: str) -> None:
    marker = read_artifact(slug, MARKER) or {}
    if marker.get('phase') not in {'initial_reviewer', 'review_running'}:
        raise ValueError('legacy review not ready: ' + slug)
    marker['phase'] = 'review_running'
    write_artifact(slug, MARKER, marker)


def finish_initial_review(slug: str) -> None:
    marker = read_artifact(slug, MARKER) or {}
    marker['phase'] = 'regular_repair'
    write_artifact(slug, MARKER, marker)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--has-pending', action='store_true')
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_local(), ensure_ascii=False))
    elif args.has_pending:
        print('1' if has_pending() else '0')
    else:
        parser.error('choose a read-only action')
