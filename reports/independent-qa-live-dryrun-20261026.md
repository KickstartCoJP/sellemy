# Live-data prepublication dry-run, 2026-10-26

NO publication, scheduler activation, image generation, live job mutation, or git push performed.

## Source-backed, unpublished job
- slug: winter-clothing-storage-covers; actual 6-product payload/evidence and current independent product acceptance read from recovery state.
- Product acceptance: PASS.
- Original article QA certification: stale/missing; re-certified IN MEMORY only using existing QA record; no live job state modified.
- Independent live Reviewer: Editorial FAIL due to bureaucratic/investigation tone and repetitive dimension reporting.
- Result: correctly BLOCKED before Claim QA and before publication. This is a real-job negative-route test, NOT an end-to-end passing publication candidate.

## Archived article control
- usb-c-hubs-for-laptops-comparison: old Product acceptance invalid against current evidence; existing certification cannot be reused. Correctly refused by the earliest gate. No legacy acceptance bypassed.

## Tests
- 305 full-suite tests PASS with discovery top-level set to repository root.
- Timeout/subprocess OS failure now classified as reviewer_runtime so Writer does not count infrastructure failure as content-quality NG.

## Readiness
- Prepublication fail-closed behavior verified with real job data.
- End-to-end successful real-job PASS through all three QA and assembled-page gate still unverified; this dry-run stopped at Editorial FAIL.
- Real job requires rewriting its editorial prose and rerunning independent QA before activation. Do not present this as completed successful end-to-end.
