"""Independent per-criterion editorial acceptance, bound to plan and payload."""
import json
from comparison_acceptance import digest

class EditorialPlanError(ValueError):
    def __init__(self, message, *, receipt=None):
        super().__init__(message)
        self.receipt = receipt if isinstance(receipt, dict) else {}

def validate_receipt(plan, payload, receipt):
    if not isinstance(receipt, dict):
        return ['receipt is not an object']
    if receipt.get('schema') != 'sellemy-editorial-plan-review/v1':
        return ['schema mismatch']
    if receipt.get('plan_hash') != digest(plan) or receipt.get('payload_hash') != digest(payload):
        return ['stale plan or article']
    items = receipt.get('items')
    if not isinstance(items, list):
        return ['missing criteria']
    required = [x['id'] for x in plan['requirements']]
    if [x.get('id') for x in items if isinstance(x,dict)] != required:
        return ['criterion IDs do not match plan']
    article = json.dumps(payload, ensure_ascii=False)
    errors = []
    for item in items:
        if item.get('verdict') != 'PASS':
            errors.append(str(item.get('id')) + ': not passed: ' + str(item.get('reason') or 'reviewer supplied no reason')[:450])
        quote = item.get('article_quote')
        if not isinstance(quote,str) or len(quote.strip()) < 3 or quote not in article:
            errors.append(str(item.get('id')) + ': missing article quote')
    return errors
def independent_plan_review(plan, payload, judge=None, *, evidence=None):
    if judge is None:
        from independent_quality_gate import _judge
        judge = _judge
    instruction = (
        'Independently review this Japanese shopping article against EACH requirement ID. '
        'Return one JSON object with schema sellemy-editorial-plan-review/v1, '
        'plan_hash and payload_hash copied from INPUT, and items in the exact order '
        'of requirements. Each item needs id, verdict PASS/FAIL/UNKNOWN, '
        'article_quote copied verbatim from the article and a precise reason. '
        'Never mark PASS when the requirement is only partly fulfilled. '
        'Quote a literal article excerpt for each criterion, including failures; '
        'if no matching excerpt exists, return empty quote and FAIL. '
        'Assess E07 only for natural Japanese and exposed internal audit commentary. '
        'Assess E08 for factual grounding against the EVIDENCE supplied with INPUT. '
        'Do not claim evidence is absent when it is supplied. '
        'Do not create or rewrite article content.\nINPUT:\n'
    )
    data = {'plan':plan,'article':payload,'evidence': evidence or {},'plan_hash':digest(plan),
            'payload_hash':digest(payload)}
    receipt = judge(instruction + json.dumps(data,ensure_ascii=False))
    errors = validate_receipt(plan,payload,receipt)
    if errors:
        raise EditorialPlanError('; '.join(errors), receipt=receipt)
    return receipt
