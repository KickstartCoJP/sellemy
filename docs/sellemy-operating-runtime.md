# Sellemy operating runtime

The production entry point is `pipeline/growth_runtime.py`. A cycle is fail-closed and follows these boundaries:

1. `planning_runtime.py` asks the configured Planning provider for fresh candidates across `beauty`, `dailygoods`, and `gadget`. `planning_seeds.json` is context only, not a queue.
2. `product_selection.py` selects six distinct ASINs using relevance, identity completeness, comparison-axis coverage, brand diversity, feedback, and price balance as a constraint.
3. `writer_runtime.py` calls the Primary Writer. It may call one certified Secondary only for an availability error. Content or QA failures never change providers.
4. `review_gate.py` and `qa.py` must both pass before `publish_payload.py` can write a publication unit.
5. The publication unit includes article HTML, eyecatch, Evidence/Payload, ASIN canonical products, `products.json`, `articles.json`, TOP recent links, and sitemap.
6. `--publish` performs only a normal `git push origin main`, followed by HEAD/origin/clean verification.

## Runtime configuration

- `SELLEMY_PLANNING_COMMAND`, `SELLEMY_PLANNING_MODEL`
- `SELLEMY_WRITER_PRIMARY_COMMAND`, `SELLEMY_WRITER_PRIMARY_MODEL`
- `SELLEMY_WRITER_SECONDARY_COMMAND`, `SELLEMY_WRITER_SECONDARY_MODEL`
- `SELLEMY_WRITER_SECONDARY_CERTIFIED=true` is mandatory before Secondary use
- `SELLEMY_WRITER_TIMEOUT_SECONDS`, `SELLEMY_WRITER_MAX_BUDGET_USD`

The default Primary and Planning command resolves the locally installed Claude CLI. A Secondary has no default and therefore cannot be used accidentally.

## Measurement feedback

`data/analytics_feedback.json` is an adapter input for GA4 and available affiliate exports. It does not replace GA4 or create a parallel analytics store.

- `topic_metrics`: `category`, `intent_key`, `page_views`, `affiliate_ctr`, and optional `revenue`
- `product_metrics`: `asin`, `click_through_rate`, optional `conversion_rate`, and optional `average_reward`

Absent metrics contribute zero rather than fabricating performance.

## Validation

Run `python3 pipeline/validate_operating_model.py` for a non-publishing staging-equivalent check over every stored Writer article, all three categories, Planning, variable-axis selection, ASIN canonical identity, product catalog synchronization, TOP links, and the current OGP domain.
