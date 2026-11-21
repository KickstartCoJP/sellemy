"""Compact, lossless actionable QA return to the producing role.

Full receipts remain in the existing artifact store and are never rewritten here.
"""
from __future__ import annotations


def writer_feedback(feedback: dict | None) -> dict:
    """Retain failed criteria and actionable instructions, not successful QA receipts."""
    if not isinstance(feedback, dict):
        return {}
    out: dict = {}
    for key in ('ceo_rework', 'editorial_plan_requirements', 'evaluation_exception',
                'semantic_acceptance_issues', 'local_split_qa_requires_revision'):
        if feedback.get(key):
            out[key] = feedback[key]
    failures = {k: v for k, v in (feedback.get('qa_failures') or {}).items()
                if v is False and k != 'overall_pass'}
    if failures:
        out['qa_failures'] = failures
        measures = feedback.get('measurements') or {}
        relevant = {}
        for key in failures:
            prefix = key.replace('_in_range', '').replace('_pass', '')
            for measure in (prefix + '_len', prefix + '_lens'):
                if measure in measures:
                    relevant[measure] = measures[measure]
        if 'distinct_openings_endings_pass' in failures:
            out['revision_instruction'] = 'Remove repeated opening/ending phrases across affected products.'
            for key in ('duplicate_openings', 'duplicate_endings'):
                if feedback.get(key):
                    out[key] = feedback[key]
        if relevant:
            out['measurements'] = relevant
    quality = feedback.get('independent_quality_gate')
    if isinstance(quality, dict) and quality.get('issues'):
        out['independent_quality_gate'] = {
            'gate': quality.get('gate'), 'issues': quality['issues']}
    findings = feedback.get('review_findings')
    if findings:
        out['review_findings'] = findings
    semantic = feedback.get('semantic_review') or {}
    if isinstance(semantic, dict) and semantic.get('status') in ('BLOCKED', 'REVISION_REQUIRED'):
        out['semantic_issue'] = {
            'status': semantic.get('status'), 'reason': semantic.get('reason'),
            'needs_product_reselection': semantic.get('needs_product_reselection')}
    return out


def writer_evidence(evidence: dict) -> dict:
    """Keep all six product source records; omit the already completed Product QA."""
    return {k: v for k, v in evidence.items() if k != 'product_acceptance'}
