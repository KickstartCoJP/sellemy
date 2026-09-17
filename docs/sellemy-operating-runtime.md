# Sellemy operating runtime

The production entry point is `pipeline/growth_runtime.py`. A cycle is fail-closed and follows these boundaries:

1. `planning_runtime.py` asks the configured Planning provider for fresh candidates across `beauty`, `dailygoods`, and `gadget`. `planning_seeds.json` is context only, not a queue.
2. `product_selection.py` selects six distinct ASINs using relevance, identity completeness, comparison-axis coverage, brand diversity, feedback, and price balance as a constraint.
3. `writer_runtime.py` calls the Primary Writer. It may call one certified Secondary only for an availability error. Content or QA failures never change providers.
4. `review_gate.py` and `qa.py` must both pass before `publish_payload.py` can write a publication unit.
5. The publication unit includes article HTML, eyecatch, Evidence/Payload, ASIN canonical products, `products.json`, `articles.json`, TOP recent links, and sitemap.
6. `--publish` performs only a normal `git push origin main`, followed by HEAD/origin/clean verification.
7. After a successful push, `adaptive_publish.py` evaluates the published artifact and derives the next frequency target from append-only Feedback. It never bypasses the pre-publish Review/QA gates.

## Adaptive publication control

The LaunchAgent is a fixed hourly heartbeat at minute `:10` JST. `growth_runtime.py --publish --scheduled` asks the controller whether the current hourly opportunity is enabled; the LaunchAgent itself is never rewritten. The controller supports a configured target from 1 through 24 publications/day and distributes exactly that many slots deterministically across 24 hours from the configured anchor. The initial 2/day target preserves 04:10 and 16:10; 12/day is every two hours; 24/day enables every hourly heartbeat. Extra slots become eligible only after the configured green streak.

All tuning values live in `config/adaptive_publish.json`. Runtime evidence is outside the Git publication unit under `~/Library/Application Support/Sellemy/adaptive-publish/` by default, or `SELLEMY_ADAPTIVE_STATE_DIR` when explicitly configured:

- `feedback.jsonl`: append-only, idempotent post-publish evaluations
- `admissions.jsonl`: append-only scheduled-slot admission receipts
- `controller_state.json`: reproducible projection of Feedback history

`content_quality` reruns the existing Review/QA checks over the published HTML, Payload, Evidence, eyecatch receipt, and publication evidence. `topic_novelty` compares the completed artifact with existing published articles. A repeated structural issue raises `canonical_feedback_required`. Persistent red Feedback at minimum frequency raises `ceo_alert_required` and pauses later publication attempts. Evaluator failure or Task/Event bridge failure is itself written as durable red quality-control Feedback and pauses later publication until repaired; an evaluation outage therefore cannot silently continue normal production. After repair, a verified successful evaluation is appended as the next Feedback record and clears the quality-control fault projection.

`feedback_task_bridge.py` projects only actionable Feedback into the existing AI Management OS `Project → Task → Event(message_id)` model. `canonical_feedback_required` creates an idempotent `BU-002` quality-improvement Task for `sellemy-ops`; `ceo_alert_required` uses the existing `user_decision_required` Task status for CEO escalation. No second queue or business status is introduced.

## Runtime configuration

- `SELLEMY_PLANNING_COMMAND`, `SELLEMY_PLANNING_MODEL`
- `SELLEMY_WRITER_PRIMARY_COMMAND`, `SELLEMY_WRITER_PRIMARY_MODEL`
- `SELLEMY_WRITER_SECONDARY_COMMAND`, `SELLEMY_WRITER_SECONDARY_MODEL`
- `SELLEMY_WRITER_SECONDARY_CERTIFIED=true` is mandatory before Secondary use
- `SELLEMY_WRITER_TIMEOUT_SECONDS`, `SELLEMY_WRITER_MAX_BUDGET_USD`

The default Primary and Planning command resolves the locally installed Claude CLI. A Secondary has no default and therefore cannot be used accidentally.

## Measurement feedback

Each scheduled production heartbeat runs `pipeline/ga4_sync.py` before Planning. The sync reads GA4 with the read-only service account and writes runtime evidence outside the Git worktree under `~/Library/Application Support/Sellemy/analytics/`:

- `ga4_latest.json`: raw page and affiliate-click event rows for the latest 28-day window
- `analytics_feedback.json`: Planning adapter input derived from the raw GA4 result

`topic_metrics` contains article-level rows plus one `intent_key: "*"` category aggregate for `beauty`, `dailygoods`, and `gadget`. Planning uses an exact intent match when available and otherwise uses the category aggregate; it never invents revenue. `product_metrics` remains empty until product-level attribution is available from a verifiable source.

The legacy Sellemy browser event name is `click`; `ga4_sync.py` also accepts the dedicated `affiliate_click` event name for forward compatibility. Absent metrics contribute zero rather than fabricating performance.

## Validation

Run `python3 pipeline/validate_operating_model.py` for a non-publishing staging-equivalent check over every stored Writer article, all three categories, Planning, variable-axis selection, ASIN canonical identity, product catalog synchronization, TOP links, and the current OGP domain.
