# Sellemy Editorial QA — independent editorial gate

Purpose: judge whether a finished article helps readers discover and choose products. Do not grade product identities, citations, sales status, images, or source sufficiency here; those belong to Product QA and Claim QA.

Input: full rendered article prose (h1, lead, summary, comparison group titles/angles, 6 product descriptions, buying guide, conclusion); compact editorial brief when available. No raw marketplace search results or source corpora.

PASS only when all conditions hold:
1. A defensible first recommendation and concrete, distinct reader/use-case recommendations are identifiable from the article.
2. All six product descriptions convey their unique value, not six interchangeable specification dumps.
3. Readable, natural consumer editorial voice; no overbearing bureaucratic jargon, internal investigation or QA narrative, or repeated interchangeable boilerplate.
4. Does not outsource product research, decisive product comparison, or the final recommendation to the reader. Necessary safety/device requirements may be explained without turning the entire recommendation into a checklist.
5. The whole article is coherent: introduction, headings, individual products and conclusion agree.

Review the actual text; do not judge by individual banned words alone. Provide exact failing quotation(s), failure category and concrete editorial repair. An uncertain borderline case is FAIL with a reason, not an invented PASS. Record product-truth validation as NOT_EVALUATED; an editorial PASS never authorizes publication.

Separate dependency keys: ProductQA=(product_manifest,evidence,comparison_axes,product_contract); EditorialQA=(rendered_article,editorial_brief,editorial_contract); ClaimQA=(article_claims,evidence,claim_contract). Any dependency change invalidates only its dependent QA. Publication requires current PASS for all three and final HTML checks.
