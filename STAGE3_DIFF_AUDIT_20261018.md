# Sellemy Stage 3 differential audit — 2026-10-18

## Scope and integrity
- Stage 3 base: `5f3c9fa` (pre-modification); Stage 3 dirty worktree: `/Users/kickstart/sellemy-revalidation/work-20261010/runtime`.
- Production baseline: `a8d1f5838bacf784115904f13d41b078798645cb`; untouched.
- Stage 3 source units audited: **57** (48 tracked modified + 9 substantive untracked, excluding three generated artifacts).
- Classification counts: {'conflict_or_requires_line_review': 17, 'already_in_production': 17, 'not_appropriate_for_runtime_merge': 20, 'integration_required': 3}. Detailed per-file classification: `audit-stage3-manifest.json`.
- Independent worktree: `/tmp/sellemy-stage3-audit-20261018`. No Production merge, push, publish, job mutation, or restart.

## Integrated in audit worktree only
- New `pipeline/product_image_audit.py` from Stage 3: image URL / binary SHA-256 duplicates fail; perceptual dHash similarities require review; missing/unreadable image cannot PASS.
- New image audit tests adapted for current production fixtures/imports.
- `pipeline/qa.py`: preserve current `require_semantic_acceptance` signature, add opt-in `image_audit` evidence and `SELLEMY_REQUIRE_PRODUCT_IMAGE_AUDIT=1` fail-closed guard. **Flag is not enabled; no image downloading is wired.** Unverified image guard is NOT certified as active in production.
- Stage 3 business/data JSON payload changes, independent runtime runner/scraper files, staged shared brief, old scheduler/publish and routing revisions are excluded. These would overwrite newer Production logic or evidence without provenance review.

## Gate audit
- Same-series variant ASINs: **OPEN**. Current ProductSelection content dedupe uses normalized full title and canonical image URL; reviewer checks unique `identity_key` but does not deterministically collapse all brand/series variants. Separate ASIN is not sufficient; regression with same-series different model names remains required.
- Manufacturer/model/series identity: **PARTIAL**. Independent review requires source quote anchored identity, but dedicated manufacturer/model/series structure is not universally required.
- Unknown identity: independent review fails without source quote; **model-level unknown identity not proven impossible**.
- Six distinct products: six review refs and unique normalized identity keys enforced, but distinct series remains open.
- Evidence/axes: source quotes, two axis coverage and adequate-facts review enforced, but live reviewer accuracy is uncalibrated.
- Product images: duplicate bytes/URL/missing -> FAIL or REVIEW_REQUIRED in isolated audit module; QA integration flag remains disabled and image retrieval unconnected.
- Category/unfinished/unsubstantiated writing: mechanical category checks, blocked article rejection and independent article review exist; complete real-world fail-corpus not yet executed.
- Independent QA to publishing: hash-bound receipt and publisher gates already present in Production; source/tool integration unchanged.
- Postpublish read-back: no newly executed deployed read-back proof; offline tests alone cannot certify it.

## Regression evidence
- Stage 3 report claims 202 offline tests at its original 2026-10-10 base; not directly comparable to evolving Production count.
- Current production base had 322 tests on prior execution. Audit worktree: **326 passed, 19 subtests passed** with four new image tests; `git diff --check` to verify separately.
- Known-bad fixtures: bathtub covers, tabletop insulated pots, reading stands covered by existing independent acceptance tests. Live-reviewer blind-spot testing, full series-variant corpus, page read-back and complete recovery-436 regression **not proven**.
- **Required criterion of 0 missed known-critical FAIL cases is NOT certified.** Do not mark release PASS.

## Production state and rollback
- Existing Production `config/publishing-control.json` inspected and had `enabled: true`, contrary to requested held/stopped state. LaunchAgent service was subsequently absent from `launchctl list`, and no growth processes were observed. No Production file was changed. The active publish config is a release blocker; verify that nothing re-registers LaunchAgent and apply a separately approved publication kill switch before rollout.
- Prepare rollback before future deployment: record HEAD and backups of control config and job snapshots; stage changes in isolated branch; merge only with approval; if deployment is later authorized, disable publication first and restore previous git commit by safe revert (never hard reset shared data), keep job/acceptance evidence immutable, validate source/published read-back.

## Decision
**NO-GO for Production.** The isolated audit integration is testable, but image evidence ingestion, series identity validation, full known-critical corpus and read-back proof are incomplete. Preserve 436 existing recovery work and hold new article production. No publication resumption is authorized by this audit.
