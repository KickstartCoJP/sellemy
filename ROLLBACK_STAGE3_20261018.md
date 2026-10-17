# Stage 3 conditional rollout / rollback (NOT EXECUTED)

Production baseline: a8d1f5838bacf784115904f13d41b078798645cb
Audit candidate: 2ccaef4 (isolated branch; do not merge blindly)

Before a separately authorized deployment: ensure growth launch service is unloaded and publishing-control enabled=false, save exact HEAD and control-file backup outside git, export job state snapshots without modifying the 436 recovery ledger, revalidate clean worktree and latest main, and run complete offline+held-out critical tests.

Rollout sequence: use PR with individually reviewed cherry-picks, keep publishing disabled, prove image downloads / structured identity and readback in temporary staging, then controlled pilot with verified snapshot. Never mark 436 recovery items approved on the basis of new article QA.

Rollback on any mismatch: unload growth service and set publishing-control enabled=false before anything else; revert only the rollout commits using reviewed `git revert` in a new PR, restore backed-up control settings, preserve jobs/receipts and existing 436 recovery evidence, validate Pages and local read-back. Never force push or delete recovery jobs.
