# Sellemy Shared Runtime Brief

## Purpose
SellemyのPlanning / Writerを、Evidenceと現行Gateに従って高品質かつ省トークンで実行する。CodexのPlannerとWriterが共有する短い運用知見。過去の記事本文・画像・会話履歴は引き継がない。

## Fixed rules
- Business purpose: SEO/AEO traffic and affiliate referrals; near-term production milestone 10,000 published articles. Monitor conversion and profitability separately.
- Preserve six verified, distinct, relevant products in the current three editorial groups, with correct images and affiliate links. Reject mismatched variants, invented specifications, unsafe claims and confirmed duplicates.
- General article sections (h1, lead, summary, group titles/angles, how_to_choose, conclusion) provide SEO/AEO-friendly topic knowledge and NEVER mention or lead back to selected brands/products. Product names and individual facts are limited to product cassettes.
- Product cassettes target 2–3 natural sentences, roughly 60–120 Japanese characters as a nonbinding writing guide, and allow one sentence when sufficient; NO length floor or sentence-count gate. Do not require distinct persuasive appeals, target audiences, usage scenarios, or specs for every item. Write an optional spec only when accurate and relevant.
- Lead and conclusion should have substantive general information, not padded repetition.
- Product/Evidence/Claim/Safety/SEO/Final Review and CEO publication gates remain independent; do not equate missing optional details with an actual wrong fact.
- Only the current runtime provider routing is authoritative. Never use historical API fallback claims to authorize paid API use.
- The routine Planning and Writer turns generate structured content only; they never perform code modifications.

## Learned
- 過去の短文化検証で商品説明の同義反復が減少。現行の商品説明文字数下限は撤廃されており、過去の文字数NGは現行品質基準として扱わない。
- 2026-10品質インシデント：6 ASINは6独立商品を意味しない。brand + base model/family等のsource anchorでidentityを確認し、色・数量・bundle・seller・listing wording差だけで別製品扱いしない。
- Marketplace listingは候補発見には使えるが、性能・互換性・材質・耐久性などdecision-critical claimの自動証拠にはしない。メーカー公式商品ページ・仕様・説明書等を優先し、marketing claimや記載なしを事実へ変換しない。
- Topic/search intent・紹介切り口・6商品・本文は同じ商品クラスと購入判断を扱う。周辺品、交換品、別カテゴリ混入はWriterで修辞修正せずProduct/Evidenceへ戻す。
- 確認できない任意仕様は書かない。実際の不適合や危険・重大な根拠不足の具体主張のみ品質NGにし、一般情報と個別商品を分離する。
- READY JSON・文字数・Machine QAだけでは公開不可。独立semantic acceptanceがdistinct identity / evidence / meaningful discovery and recommendations / grounded claims / buying guidance / completenessを確認する。
- 共通契約の新しい欠陥が独立QAで見つかった場合はBrief/Prompt/Gateを更新し、contract hash更新により既存acceptanceを再検証する。
- 重複は購入目的・使用場面・商品群・既存記事の範囲で判定し、提案済み・未採用・公開済みを区別する。季節・対象者・付属品だけで独立企画にしない。カテゴリ配分と直近の偏りを別に見て、採用を品質改善の実証としない。
- 調査は公式一覧から仕様・説明書へ絞り、検索の欠落は単独・ドメイン指定で補う。運営元・販売終了を確認し、ページ存在と現行販売、仮評価と実測を分ける。一部の確認で全候補の成立を確定しない。
- 名称・型番・選択仕様・役割を先に整理する。本体・部品・容器、加熱方式、電動・手動を区別し、色・数量違いや類似説明から製品の異同を確定しない。代替には実名・型番・根拠URLを添える。
- 紹介切り口は価格帯・用途・暮らし・商品特性などからテーマに自然な3つを作る。名称・段階数を性能、記載欠落を非搭載、説明量を機能差としない。監修・受賞・特許の表示を適合や効果の証明に置き換えない。
- 記事は検索意図に合う一般情報を中心に、商品カセットのみで個別商品を簡潔に紹介する。全商品の固有の魅力・推薦理由を無理に作文しない。
- 数値は対象・単位・測定状態を揃える。外寸と有効範囲、単体と合計、容量と使用時間を分ける。全体荷重を棚数で割らず、静止・移動時と脚部構成を照合する。サイズ記号は製品ごとの実寸で確認する。
- 調整可能をカット可能とせず、変更できる部位・範囲・手順を確認する。取り外し・洗浄・消毒・加熱は部品別に読み、材質や防水表示から方法を補わない。互換性は名称や容量でなく寸法・品番・使用条件で照合する。
- 端子・通信規格・機種・OS・ソフトを別々に確認する。充電と転送、読み書きと映像再生、説明書とソフト付属を区別する。文字起こし・話者識別を音声の頭出しや速度変更と同一視せず、保存可能時間と電源持続時間も分ける。
- 成分・対象素材・使用量・放置時間・すすぎ・併用条件を個別に確認する。水あり対応を洗浄方法、無香料を低刺激、形状名を使いやすさの根拠としない。
- 字数合わせ・同義反復・無理な具体仕様や推薦作文を禁止する。短い商品説明はそれだけで不合格ではない。
