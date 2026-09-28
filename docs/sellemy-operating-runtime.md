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

The LaunchAgent wakes the scheduler once per minute; no Writer or Planning work starts unless the controller admits the current minute. The same minute heartbeat also refreshes publication monitoring after each scheduler attempt: `article_publication_metrics.py` counts published article URLs from the current `sitemap.xml`, validates them against immutable `published_at` metadata for daily history, and `actuals_adapter.py --sync` projects the result to UnitDetails. Monitoring failure is logged but must not block content publication. Normal publication times divide the local day evenly from 00:00 (for example, 3/day = 00:00, 08:00, 16:00; 2/day = 00:00, 12:00). If a publication fails before publish, only that failed slot is recovered: the next retry is placed halfway between the failed attempt and the next downstream scheduled slot, leaving downstream slots unchanged. Repeated failures continue halving the remaining interval (for 00:00→08:00: 04:00, 06:00, 07:00). If the next retry would leave less than one hour before the downstream slot, local recovery stops and the remaining required publications are redistributed evenly from that downstream slot to midnight (for three still outstanding at 08:00: 08:00, 13:20, 18:40). A failed attempt never counts as a publication. Extra target capacity still becomes eligible only after the configured green streak.

All tuning values live in `config/adaptive_publish.json`. Runtime evidence is outside the Git publication unit under `~/Library/Application Support/Sellemy/adaptive-publish/` by default, or `SELLEMY_ADAPTIVE_STATE_DIR` when explicitly configured:

- `feedback.jsonl`: append-only, idempotent post-publish evaluations
- `admissions.jsonl`: append-only scheduled-slot admission receipts
- `controller_state.json`: reproducible projection of Feedback history

`content_quality` reruns the existing Review/QA checks over the published HTML, Payload, Evidence, eyecatch receipt, and publication evidence. `topic_novelty` compares the completed artifact with existing published articles. A repeated structural issue raises `canonical_feedback_required`. Persistent red Feedback at minimum frequency raises `ceo_alert_required` and pauses later publication attempts. Evaluator failure or Task/Event bridge failure is itself written as durable red quality-control Feedback and pauses later publication until repaired; an evaluation outage therefore cannot silently continue normal production. After repair, a verified successful evaluation is appended as the next Feedback record and clears the quality-control fault projection.

`feedback_task_bridge.py` projects only actionable Feedback into the existing AI Management OS `Project → Task → Event(message_id)` model. `canonical_feedback_required` creates an idempotent `BU-002` quality-improvement Task for `sellemy-ops`; `ceo_alert_required` uses the existing `user_decision_required` Task status for CEO escalation. No second queue or business status is introduced.

## Runtime configuration

- `SELLEMY_PLANNING_COMMAND`, `SELLEMY_PLANNING_MODEL`
- `SELLEMY_PLANNING_SECONDARY_COMMAND`, `SELLEMY_PLANNING_SECONDARY_MODEL`, `SELLEMY_PLANNING_SECONDARY_CERTIFIED=true`
- `SELLEMY_WRITER_PRIMARY_COMMAND`, `SELLEMY_WRITER_PRIMARY_MODEL`
- `SELLEMY_WRITER_SECONDARY_COMMAND`, `SELLEMY_WRITER_SECONDARY_MODEL`, `SELLEMY_WRITER_SECONDARY_KIND=codex`
- `SELLEMY_WRITER_SECONDARY_CERTIFIED=true` is mandatory before Secondary use
- `SELLEMY_WRITER_TIMEOUT_SECONDS`, `SELLEMY_WRITER_MAX_BUDGET_USD`

The default Primary and Planning command resolves the locally installed Claude CLI. A Secondary has no default and therefore cannot be used accidentally.
The production scheduler script explicitly configures the local Codex CLI as certified Secondary for Writer and Planning. Both routes switch only after a Claude availability diagnostic. Codex runs with a read-only sandbox and ephemeral session, receives a JSON output schema, and never receives a session ID or credential in the script. Provider and fallback metadata appears in the Growth report. The existing Review, QA, affiliate and publication guards still decide whether an article can advance. See the [official OpenAI Codex exec guidance](https://developers.openai.com/blog/eval-skills) for noninteractive schema-constrained execution.

## Measurement feedback

Each scheduled production heartbeat runs `pipeline/ga4_sync.py` before Planning. GA4 is the measurement source of truth, and runtime measurement files live outside Git under `~/Library/Application Support/Sellemy/analytics/`:

- `ga4_daily_raw.json`: canonical stock at `date × pagePath` grain. It stores page title, page views, sessions, active users, and Sellemy affiliate `click` events. The configured historical backfill starts at `2025-01-01`; normal heartbeats replace the latest three-day window idempotently so late GA4 revisions are absorbed.
- `ga4_latest.json`: derived rolling aggregate for operational inspection.
- `analytics_feedback.json`: derived Planning adapter input. It does not replace GA4 or create a second measurement truth.

Cumulative, 7-day, 28-day, trend, and comparison values are derived from daily Raw rather than stored as independent source data. `topic_metrics` contains article-level rows plus an `intent_key: "*"` category aggregate. Planning uses an exact article slug match when available and otherwise the category aggregate; it never invents revenue. `product_metrics` remains empty until product-level attribution is available from a verifiable source. Absent metrics contribute zero rather than fabricating performance.

## Validation

Run `python3 pipeline/validate_operating_model.py` for a non-publishing staging-equivalent check over every stored Writer article, all three categories, Planning, variable-axis selection, ASIN canonical identity, product catalog synchronization, TOP links, and the current OGP domain.


## Affiliate actuals pipeline

`pipeline/affiliate_pipeline.py` is the sole production path for affiliate data. It stores immutable provider files, normalized order facts, reconciled daily product facts, provider watermarks, fetch runs, and append-only audit receipts under `~/Library/Application Support/Sellemy/analytics/`.

- Amazon: Associates Central official reports only. Rows enter revenue only when `tracking_id=sellemy-22`. Creators API is disabled until a verified 2xx reporting path exists.
- ValueCommerce/Yahoo: Order Report API v3 is the only automated collector. Yahoo is represented by ValueCommerce and is never added as a second provider.
- Rakuten: official CSV is ingested, but revenue eligibility remains false unless the source contains positive Sellemy attribution evidence.
- GA4: `affiliate_click` and CTR are projected only from the formal event start date; earlier absence is missing, not zero.
- Raw → Curated → Actuals is reproducible from content hashes. Re-running the same files is idempotent.
- `ops/run-growth.sh` executes GA4 refresh, affiliate daily reconcile, and Actuals read-back on the existing LaunchAgent heartbeat before content publication checks.

Backfill:

```bash
.venv/bin/python pipeline/affiliate_pipeline.py --mode backfill --start-date 2025-01-01
.venv/bin/python pipeline/actuals_adapter.py --sync
```

ValueCommerce production fetch reads the mode-600 `~/.config/valuecommerce/report-api.json` credential and uses Order Report API v3. Amazon and Rakuten use `pipeline/firefox_collectors.py` with the dedicated `amazon-associates` and `rakuten-affiliate` Firefox profiles. The collector reuses authenticated browser state, downloads only official provider CSVs, atomically installs them into Raw, and fails closed on expired authentication. `ops/run-growth.sh` runs both collectors before reconciliation; an Amazon authentication failure does not prevent Rakuten/ValueCommerce reconciliation, but it stops the publication cycle with a non-zero status after Actuals read-back.
