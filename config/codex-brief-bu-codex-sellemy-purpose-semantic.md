# Sellemy Purpose Semantic Codex Brief

## Purpose
Purpose Runtimeのsemantic reviewを、現行特集正本と候補Evidenceに従って実行する。

## Fixed rules
- PoCはMaturity Stageであり、独立Runtimeではない。実行主体はPurpose Runtime。
- Localで絞り込まれた候補だけを意味判定する。全件を無差別に再探索しない。
- 判定は core / supporting / not_related と rationale / confidence を返す。
- 既存Relationを日常処理で自動削除しない。
- core 4 / total 6の数量だけで成立判定しない。検索意図、違和感、overlap/cannibalizationを守る。
- Evidence不足は推測で埋めずfail-closed。

## Learned
- まだ蓄積知見なし。
