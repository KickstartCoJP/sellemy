# Sellemy Editorial QA — independent editorial gate

Purpose: judge whether finished prose helps readers discover and choose products. Product identity, specifications, citations and source sufficiency are evaluated separately by Product QA and Claim QA.

Input: complete article prose, six product descriptions, editorial brief when available. Do not include the raw product-evidence corpus.

PASS conditions:
1. The article makes an actionable recommendation: six credible, product-specific recommendations that help the reader discover items they might enjoy. Do not rank products, require a top choice, or invent a universal winner. Comparing differences is OPTIONAL; fail generic product blurbs or mere spec lists lacking a recommendation, not the absence of comparison.
2. Each of the six products has its own evidence-grounded appeal, a reader or use case, and a clear reason to recommend it. Do not require direct contrasts with the other five.
3. The writing is appealing, natural Japanese appropriate to Sellemy rather than a bureaucratic specification report, with no internal QA or investigation narrative.
4. The article does not delegate essential product research or final selection to the reader. Necessary safety and device checks can appear briefly when required.
5. Introduction, three existing two-product groups, six product sections and conclusion support discovery and consistent recommendations. Preserve the existing layout and field names.

Judge the whole prose, not a banned-word list. For each FAIL provide exact article quotations and a concrete failing condition. Do not fail just because there is no universal #1. Record product factual validation as NOT_EVALUATED. Editorial PASS alone never authorizes publication.

Dependencies: ProductQA=(product manifest,evidence,comparison axes,contract); EditorialQA=(article prose,editorial brief,editorial contract); ClaimQA=(article claims,evidence,contract). Invalidate only receipts affected by changed dependencies. Publication requires all current receipts plus final assembled HTML gate.
