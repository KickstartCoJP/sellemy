# Claim QA disagreement resolution (2026-10-26)

Root cause: the production-like source article was mistakenly assumed to be a valid positive control; its Anker generation and Elecom clearance statements lack demonstrated same-product source anchors. A prior model PASS is not a verified ground truth. Treat this original article as **FAIL/pending evidence repair**, not a guaranteed-positive sample.

Independent constructed positive control: six articles' product slots, each with a distinct exact statement repeated in its corresponding verified source. The fresh reviewer returned PASS. Three mutations (invented capacity, unsupported underwater operation, and swapped verified evidence between product refs) returned FAIL, all four expected judgments matched.

Additional deterministic correction: product-specific article quotes must occur within the exact same product ref's article entry, rather than anywhere in the six-product article. Unit tests 12/12 PASS.

Limits: constructed fixtures validate decision behavior, not truth of real commercial product specifications. Claim QA is isolated and not yet connected to the live publish gate. Next production step is independent evidence restoration/removal of unsupported claims followed by full article verification and publish-gate integration.
