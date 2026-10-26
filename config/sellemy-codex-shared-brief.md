# Sellemy Shared Runtime Brief

## Purpose
SellemyのPlanning / Writerを、Evidenceと現行Gateに従って高品質かつ省トークンで実行する。CodexのPlannerとWriterが共有する短い運用知見。過去の記事本文・画像・会話履歴は引き継がない。

## Fixed rules
- 全段階の公開成立条件はRuntime管理の `config/comparison-acceptance.md`（stage promptに同文を付与）に従う。6独立製品・根拠あるtopic固有比較・購入判断支援を必須とし、必要情報不足は本文にせず構造化BLOCKED / needs_product_reselectionで返す。Writer自己評価は公開承認にしない。
- Planning: 既存記事との重複を避け、購入意図・商品成立性・Evidence成立性を優先する。
- Writer: 渡されたEvidence以外を事実根拠にしない。slug/category/refは変更しない。
- 6商品を必ず3グループ×2商品へ一意に分割する。比較軸はtopic固有の軸を使う。
- Amazon listing titleの丸写し、ASIN、内部運用語、固定価格帯構造、根拠のない断定は禁止。
- Writer本文は用途ごとの具体的なおすすめと理由を先に示す。選択・重要仕様の調査を読者に丸投げしない。内部QA・資料の不足・過剰な言い訳や堅苦しい専門用語の羅列を本文に出さず、自然な日本語にする。安全上必要な注意は正確に伝える。scriptによる水増しは禁止。
- 後段Review / Machine QA / Publish Gateは緩和しない。情報不足はfail-closed。
- Planning / Writerはアイキャッチ生成を担当しない。
- P0 436記事Recovery中はResponses APIのみを使用し、Codex / Claude / local fallbackは禁止。API transport/config failureはfail-closed。Solは通常、AstraはEvidenceが足りている文章・意味品質の昇格時だけ使用する。

## Learned
- 2026-10品質インシデント：6 ASINは6独立商品を意味しない。brand + base model/family等のsource anchorでidentityを確認し、色・数量・bundle・seller・listing wording差だけで別製品扱いしない。
- Marketplace listingは候補発見には使えるが、性能・互換性・材質・耐久性などdecision-critical claimの自動証拠にはしない。メーカー公式商品ページ・仕様・説明書等を優先し、marketing claimや記載なしを事実へ変換しない。
- Topic/search intent・比較軸・6商品・本文は同じ商品クラスと購入判断を扱う。周辺品、交換品、別カテゴリ混入はWriterで修辞修正せずProduct/Evidenceへ戻す。
- Evidence不足はBLOCKED + needs_product_reselection。Astraは不足Evidenceの代替にしない。文章品質とEvidence品質を分離する。
- READY JSON・文字数・Machine QAだけでは公開不可。独立semantic acceptanceがdistinct identity / evidence / meaningful comparison / grounded claims / buying guidance / completenessを確認する。
- 共通契約の新しい欠陥が独立QAで見つかった場合はBrief/Prompt/Gateを更新し、contract hash更新により既存acceptanceを再検証する。
- 重複は購入目的・使用場面・商品群・既存記事の範囲で判定し、提案済み・未採用・公開済みを区別する。季節・対象者・付属品だけで独立企画にしない。カテゴリ配分と直近の偏りを別に見て、採用を品質改善の実証としない。
- 調査は公式一覧から仕様・説明書へ絞り、検索の欠落は単独・ドメイン指定で補う。運営元・販売終了を確認し、ページ存在と現行販売、仮評価と実測を分ける。一部の確認で全候補の成立を確定しない。
- 名称・型番・選択仕様・役割を先に整理する。本体・部品・容器、加熱方式、電動・手動を区別し、色・数量違いや類似説明から製品の異同を確定しない。代替には実名・型番・根拠URLを添える。
- 比較軸は寸法・構造・対応条件から作る。名称・段階数を性能、記載欠落を非搭載、説明量を機能差としない。監修・受賞・特許の表示を適合や効果の証明に置き換えない。
- 説明は「明示仕様→用途との照合→固有の確認点」。冒頭と結びを商品固有にし、共通注意や保留文を語尾だけ変えて反復しない。
- 数値は対象・単位・測定状態を揃える。外寸と有効範囲、単体と合計、容量と使用時間を分ける。全体荷重を棚数で割らず、静止・移動時と脚部構成を照合する。サイズ記号は製品ごとの実寸で確認する。
- 調整可能をカット可能とせず、変更できる部位・範囲・手順を確認する。取り外し・洗浄・消毒・加熱は部品別に読み、材質や防水表示から方法を補わない。互換性は名称や容量でなく寸法・品番・使用条件で照合する。
- 端子・通信規格・機種・OS・ソフトを別々に確認する。充電と転送、読み書きと映像再生、説明書とソフト付属を区別する。文字起こし・話者識別を音声の頭出しや速度変更と同一視せず、保存可能時間と電源持続時間も分ける。
- 成分・対象素材・使用量・放置時間・すすぎ・併用条件を個別に確認する。水あり対応を洗浄方法、無香料を低刺激、形状名を使いやすさの根拠としない。
- 各欄と本文総量を同時に配分し、見出し・共通節込みで上限に余裕を残す。各説明が範囲内でも総量は超過する。修正は超過分ぎりぎりにせず、共通節と重複を削って下限と固有性を保つ。見積もりと描画後の実測・合格、文字数適合と比較成立を分け、執筆・検証・保存・返却を個別に確認する。
