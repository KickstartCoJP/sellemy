"""One-shot fresh replan for an explicit CEO-reworked, previously unpublished job."""
from __future__ import annotations

from growth_recovery import load_job, read_artifact, write_artifact, set_stage


def replan_one(slug: str) -> dict:
    from planning_runtime import replan_published_topic
    from growth_role_business import collect_unique_products, build_evidence
    from product_selection import select_six
    from analytics_feedback import load_feedback
    from semantic_review import independent_review
    from comparison_acceptance import product_issues
    from growth_recovery import replace_product_evidence

    state = load_job(slug)
    marker = read_artifact(slug, 'planning-restart-required.json', {}) or {}
    decision = read_artifact(slug, 'ceo-review-decision.json', {}) or {}
    if not (state.get('source') == 'runtime'
            and state.get('status') == 'ACTIVE'
            and state.get('current_stage') in {'PLANNING_PENDING', 'PLANNING_HOLD'}
            and not state.get('published_commit')
            and marker.get('required_stage') == 'PLANNING'
            and marker.get('retain_slug') == slug
            and marker.get('retain_category') == state.get('category')
            and decision.get('decision') == '差戻し'
            and marker.get('product_theme')
            and marker.get('attempted') is not True):
        raise ValueError('CEO-unpublished article Planning restart not authorized')
    # Do not use old title, previous products, previous Evidence or previous
    # editorial sections as new Planner input. Read only the saved category name.
    theme = marker['product_theme']
    marker['attempted'] = True  # One bounded attempt; no automatic retry after HOLD.
    write_artifact(slug, 'planning-restart-required.json', marker)
    try:
        topic, provider = replan_published_topic(
            slug=slug, category=state['category'], product_theme_name=theme)
        products, trace = collect_unique_products(topic, cache_only=False)
        if len(products) != 6 or trace.get('complete') is not True:
            raise ValueError('replanning did not find six independent products')
        selected, selection = select_six(products, topic, load_feedback())
        evidence = build_evidence(topic, selected, selection)
        evidence['product_acceptance'] = independent_review(evidence)
        issues = product_issues(evidence)
        if issues:
            raise ValueError('independent Product QA blocked: ' + '; '.join(issues))
    except Exception as exc:
        marker['last_error_type'] = type(exc).__name__
        marker['last_error'] = str(exc)[:500]
        write_artifact(slug, 'planning-restart-required.json', marker)
        set_stage(slug, 'PLANNING_HOLD', event='CEO_FRESH_REPLAN_HOLD',
                  detail={'reason': str(exc)[:500]})
        return {'slug': slug, 'action': 'hold', 'reason': str(exc)[:500]}
    # Existing replacement routine revokes payload, certification and CEO
    # approval before the new Writer; no old artifact is ever republished.
    write_artifact(slug, 'topic.json', topic)
    replace_product_evidence(slug, evidence, selection)
    write_artifact(slug, 'planning-provider.json', provider)
    marker['required_stage'] = 'completed'
    marker.pop('last_error', None)
    marker.pop('last_error_type', None)
    write_artifact(slug, 'planning-restart-required.json', marker)
    return {'slug': slug, 'action': 'writer', 'product_qa': 'PASS'}
