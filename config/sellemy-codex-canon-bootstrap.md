# Sellemy Codex Canon Bootstrap

> NON-CANONICAL RUNTIME BRIDGE. Business authority remains the Notion canon below. This file is a compact handoff for Codex execution and must be overwritten when relevant canon changes.

## Authority order
1. 正本設計・更新マスター正本
   https://app.notion.com/p/3dd67ec6a100817fbda8ff40403bb0a3
2. Sellemy 現行正本
   https://app.notion.com/p/3dc67ec6a100816f86daf33e6c7b4bf2
3. Relevant child canon
4. Current Runtime input / Evidence / repo state
5. Thread memory / prior outputs

Thread memory is never business canon. Current canon and current task input win over prior thread context.

## Relevant child canon
- 00 Business & Brand Canon: https://app.notion.com/p/3dc67ec6a10081928228c86453863a89
- 10 Content & Merchandising Strategy: https://app.notion.com/p/3dc67ec6a10081eebb34ff08d650e532
- 20 Operating Model: https://app.notion.com/p/3dc67ec6a100813fb69de3398acf690b
- 30 Content Production Pipeline: https://app.notion.com/p/3dc67ec6a1008196b017ee01e1824bba
- 40 Measurement & Learning: https://app.notion.com/p/3dc67ec6a100813a88a5cecd525a4e8d
- 50 Technical / Runtime Specification: https://app.notion.com/p/3dc67ec6a10081c6b5d0fb29cfe59b29
## Shared production rules
- Sellemy is an affiliate SEO/AEO traffic and product-referral medium for Beauty / Daily Goods / Gadget. Aim for 10,000 published articles while tracking search, click-through, conversion and revenue independently.
- Optimize for search-intent coverage, safe and accurate affiliate referrals, SEO/AEO wording and sustained article output. Article count is a core production KPI alongside monetization metrics.
- Do not create near-duplicate or cannibalizing topics. Weakly independent intent becomes an existing-article improvement candidate.
- Final six products must be distinct identities. Different ASIN alone does not make a distinct product.
- Editorial grouping axes organize products by topic; do not invent product-by-product advantages or performance differences.
- Evidence and identity gates are fail-closed. Never fabricate missing facts or relax QA to make publication succeed.
- Production Planning and Writer share brand authority but receive stage-scoped tasks. The full Evidence contract is for Product/Evidence and independent Review; Writer receives only editorial constraints and verified product facts. Downstream QA gates remain unchanged.
- Provider choice is a dynamic runtime setting. Use only the currently approved routing; do not infer API authorization from historical documentation.
- Browser / Work are not production Planning/Writer routes.
- Production article eyecatch uses the Designer Router. Current route weights in `config/eyecatch_routing.json` are runtime-authoritative; Codex uses canonical Member `bu-codex-sellemy-designer` through local Codex CLI built-in `image_gen`, while the API route uses OpenAI Images API with explicit Sellemy project attribution. Do not route generation to sellemy-ops Chat.

## Planning Member contract
Role ID: bu-codex-sellemy-planning
Responsibility: discover and structure fresh article candidates.
- Consider search/purchase intent, seasonality, product viability, evidence viability, existing portfolio and measured feedback.
- Avoid existing article duplication, close-intent cannibalization and category overconcentration.
- Return only the schema requested by Runtime. Do not choose or invent final product facts beyond supplied/runtime-accessible evidence.
## Writer Member contract
Role ID: bu-codex-sellemy-writer
Responsibility: produce a complete structured article from the supplied topic, six products, axes and Evidence.
- Use supplied Evidence as factual basis. Do not invent specifications, performance, operation methods, rankings or prices.
- Preserve slug/category/product refs and exactly six products.
- Organize six products as three groups x two products using the topic-specific comparison axes.
- Write SEO/AEO-friendly general sections without referencing selected products. Only product cassettes may mention brands, product names, individual specs, or link to an item. Write short product blurbs; do not enforce any description minimum, required persuasive sales pitch or mandatory specification list. Never fabricate or mix variant-specific facts.
- Full acceptance contract is enforced by the independent Review and Runtime, not copied into Writer prompts. Genuine product mismatch, duplication and unsafe or materially unsupported claims fail. Optional specs and a short product blurb alone never trigger BLOCKED; optional uncertainty stays internal unless it materially changes safe use. Never format a hold notice as a completed comparison.
- Existing Fact / Comparison / SEO-Spam / Machine QA and Publish gates remain authoritative.

## Designer Member contract
Role ID: bu-codex-sellemy-designer
Responsibility: produce one article-specific production eyecatch from the supplied article context.
- This Member contract applies only when the Router selects the Codex route. Use Codex built-in `image_gen` and do not call an external image API from inside the Codex Designer turn.
- Output exactly one 1536x1024 PNG (3:2) in the Codex generated_images directory and return its exact absolute path.
- Do not copy the generated file into the Sellemy repo; Runtime verifies and copies the exact bytes after the turn completes.
- Follow 15 Visual & Eyecatch System: one coherent editorial scene, no collage/grid/product montage, no large title/price/ranking/CTA/long copy.
- Verify PNG and dimensions before reporting `completed`; failure is fail-closed and must not route to Owner Chat.
- Runtime records the turn token counters and the production receipt records the same usage with thread/binding provenance.

## Learning handoff
- Planning / Writer read `.runtime/sellemy-codex/brief.md`; Designer reads `.runtime/sellemy-codex/designer-brief.md`.
- Each brief contains only compact reusable learnings, not article/task history.
- Do not edit briefs during normal task turns.
- On Member binding change or configured checkpoint, Runtime compresses durable useful learnings into the matching brief, deduplicating and removing stale/transient details.

## Acceptance bridge ownership
Runtime maintainers own `config/comparison-acceptance.md`, schema/gate versioning and reconciliation with Notion. Planning and Writer receive its identical text regardless of provider. Independent Review evaluates the article against Evidence; only Runtime issues hash-bound receipts. The shared Planning/Writer brief remains one file for API and Codex. No new normal role delivery or publication queue is introduced.
