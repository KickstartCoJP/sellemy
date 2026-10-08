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
- Sellemy is a product discovery/comparison medium for Beauty / Daily Goods / Gadget.
- Optimize for user fit, choice clarity, independent search intent and total affiliate economics. Article count itself is not the KPI.
- Do not create near-duplicate or cannibalizing topics. Weakly independent intent becomes an existing-article improvement candidate.
- Final six products must be distinct identities. Different ASIN alone does not make a distinct product.
- Comparison axes come from topic-specific purchase reasons, uses and meaningful performance differences. Fixed high/mid/low price bands are not the primary framework.
- Evidence and identity gates are fail-closed. Never fabricate missing facts or relax QA to make publication succeed.
- Production Planning/Writer use one shared canon bridge, runtime brief, Evidence contract, output schema, and downstream QA gates regardless of provider.
- The Provider Router uses OpenAI Responses API with GPT-6.1 Sol for normal Planning/Writer turns and GPT-6 Astra only for quality escalation. Codex is the first runtime fallback and certified Claude remains the final availability fallback.
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
- Write natural Japanese that makes concrete differences visible. Do not copy Amazon listing titles verbatim, expose ASIN/internal terms, or pad mechanically.
- When evidence is insufficient, qualify the statement or fail closed rather than infer.
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
