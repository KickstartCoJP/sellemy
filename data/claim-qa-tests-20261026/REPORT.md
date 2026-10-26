# Claim QA negative-control test — 2026-10-26

Article: usb-c-multiport-card-readers, original six-product payload.

| Condition | Result | Observation |
|---|---|---|
| Untouched baseline | FAIL | Previous independent run returned PASS; newly detected potentially unsupported Anker generation and Elecom physical clearance claims. Baseline is **not established as a true-positive fixture**; this is an unresolved disagreement, not a false rejection proven. |
| Fabricated 500TB internal SSD | FAIL | p1 unsupported claim detected |
| Fabricated 100m underwater operation | FAIL | p2 unsupported claim detected |
| p1/p2 verified Evidence swapped | FAIL | Product-ref mismatch detected |

All returned structurally valid grounded receipts. Negative controls detected: 3/3. Baseline PASS reproducibility: not established. Claim QA receipt validator hardened against empty findings and passing without coverage of all six products; 11 unit tests passed.

Remaining: positive-control corpus with independently validated genuine claims, independent coverage/completeness checks, integration with live Growth pipeline and publish fail-closed gating. This probe alone does not justify production activation.
