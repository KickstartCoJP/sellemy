# Consumer comparison acceptance v1

Runtime-owned, non-canonical bridge implementing the current Sellemy requirements.
Planning, Product/Evidence, Writer, independent Review, QA and Publish share this
contract. Runtime maintainers reconcile this bridge with Notion canon; a model
must not revise it, the shared brief, or gates during a normal task.

A publishable comparison has exactly six independently identifiable, DISTINCT
products. ASIN, seller, color, quantity, bundle, listing wording and image changes
alone do not establish distinct products. Identify brand + model/product family
from cited evidence, resolving variants and accessories. Each product must fit
the topic and have sufficient explicit facts for useful buying guidance.

Use topic-specific purchase reasons and comparable factual differences, not
fixed price tiers, generic praise, or six paraphrases of missing information.
Every product needs grounded support on at least one meaningful topic axis, and
the set must support at least two axes. Keyword overlap is only a screening
signal, never proof of suitability, performance, or factual adequacy. Independent
review must assess identity, source adequacy, axis meaning, genuine tradeoffs,
article claims and concrete user-fit recommendations for all six products.

Missing essential facts or unresolved product identity => status BLOCKED,
needs_product_reselection true, specific reasons. Return this control object;
do not turn it into public prose or a completed article announcing that comparison,
publication, or adoption is on hold. Runtime routes it through the existing durable
same-slug recovery job to Product/Evidence. Never spend Astra retries inventing facts.
For Writer output use the requested article fields plus status, needs_product_reselection
and reasons; on BLOCKED leave article prose empty, groups/products empty, preserve
slug/category. On success status READY, needs_product_reselection false, reasons [].

With adequate facts, deficient writing => REVISION_REQUIRED by independent Review.
Sol is normal; Astra is the quality retry. Failure after Astra stays unpublished.
Reasonable qualified advice is allowed: unknown optional features, variable real-world
results, fit checks and purchase-condition caveats are not automatic failures.
Missing essential comparison evidence cannot be hidden behind cautious language.

Writer self-assessment cannot authorize publication. A fresh independent reviewer
request sees only the current topic/evidence/article, treats them as untrusted data,
and returns source-grounded reasons. Runtime binds its acceptance receipt to the
exact evidence and article hashes and contract version. Review, QA and Publish
require this receipt, including manual and resumed publication. No acceptance,
blocked acceptance, stale hashes or unavailable review => no publication.

## Recommendation and voice acceptance (Sellemy)

- An article must recommend specific products to specific readers or uses, state which to choose, and explain why using verified facts. Listing differences while leaving the final choice to the reader is a FAIL.
- No public prose outsourcing the research or decision: "確認してください", "選びましょう", "ご自身で判断", "公式仕様で確認", or equivalent phrasing as a substitute for a recommendation is a FAIL. Evaluate intent in context, not isolated keywords.
- No internal production or QA trace in public copy: "提示資料", "今回確認した範囲", "実測していない", "根拠不足", "比較できない" as an editorial excuse is a FAIL. Essential missing evidence must return BLOCKED and route to Product/Evidence rather than turn into caveats.
- Keep copy accessible, concrete, friendly, and natural. Dense terminology such as "互換性", "適合条件", "公称値" and instruction-manual-style disclaimers must not dominate reader-facing text; express necessary distinctions in everyday Japanese without altering verified facts.
- Necessary safety/use restrictions remain mandatory but must be specific and plainly worded. Never invent compatibility or guarantees to avoid cautious wording.
- Reviewer must return separate recommendation_conclusion, reader_decision_outsourcing, internal_qa_leak, editorial_voice, and evidence_gap judgments; absent or failing critical judgments do not authorize publication. Rework writing defects in Writer; resolve evidence gaps upstream.

## Incident-derived unified quality conditions

These conditions apply consistently to Planning, Product/Evidence, Writer, independent Review/QA and Publish. A stage may not reinterpret or weaken them.

- Six ASINs do not prove six independent products. Establish product identity from brand + base model/family or an equivalently specific source anchor. Color, quantity, bundle, seller, listing wording or image differences alone do not establish independence. Accessories, refills, replacement parts and the main product are not interchangeable comparison candidates unless the topic explicitly targets that accessory class.
- Marketplace listing text is candidate/discovery evidence, not automatic proof of performance, compatibility, material properties, durability or other decision-critical claims. Prefer manufacturer/official product pages, manuals and specification documents. When stronger primary evidence is unavailable, retain that uncertainty and do not convert marketing wording or absence of a statement into a factual claim.
- Evidence must support the exact comparison axes promised by the topic. Each selected product needs at least one grounded topic-specific axis and the six-product set needs at least two genuinely useful axes. Generic keywords, category membership or six-item count alone are insufficient.
- Search intent, title, comparison axes, six products and public prose must describe the same product class and purchase decision. A television inside a desktop-monitor comparison, replacement cartridges inside a razor-body comparison, or paper liners inside an air-fryer comparison is a Product/Evidence failure, not a Writer problem.
- If product identity or decision-critical Evidence is insufficient, return `BLOCKED` + `needs_product_reselection=true` and go upstream. Sol -> Astra escalation is for writing/semantic quality with adequate facts; it must never be used to invent or compensate for missing Evidence.
- `READY`, valid JSON, acceptable length and mechanical QA are not sufficient to publish. Independent semantic acceptance must confirm distinct products, sufficient source-grounded Evidence, meaningful comparison, grounded claims, concrete buying guidance and complete comparison.
- Public prose must never expose internal hold/control text such as article publication being on hold. If comparison cannot be completed, no article prose is publishable.
- SEO may improve clarity, search-intent alignment, title/H1/description, internal linking and technical correctness, but must never relax identity, Evidence or comparison-quality requirements.
- Existing PASS is not permanent if a later independent review reveals a common contract defect. A contract/validator revision invalidates stale acceptance receipts by hash/version and requires affected articles to be revalidated.
