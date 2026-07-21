# fable v2 勝率改善サイクル 計測レポート (SOT-1796)

2026-07-21. fable v1 本体（決定化 MCTS + 竹式ルールポリシー + self-deck-out ガード、`main.py` の
`FABLE_CONFIG`、SOT-1795）に対し、**screen → confirm A/B サイクル**で改善候補を計測した。

## 手法

- KPI 基盤 `eval/kpi.py`（1 計測 = `kpi_history.jsonl` の 1 行、model/条件/seed/N/CI/fault を記録）。
  候補は `FABLE_CONFIG` への **delta**（`--override-a` JSON）で表現され、行には解決後のフル config が
  残るため単独再現可能。集約は `eval/kpi_report.py`、波実行は `eval/run_kpi_wave.sh`（候補を並列
  subprocess で隔離）。
- 判定は **集約 Wilson 95% CI のみ**（SOT-1707 フェーズ2 の方針）。N=400 級の総当たりは非現実的
  （1 局 ≈ 4s）なので **small-N screen で足切り → 通過候補のみ独立 seed で confirm**。
- **p-hacking 回避:** screen（seed 2001–2004）と confirm（seed 3001–3006）は必ず別 seed。さらに
  最有力候補は **独立 seed の追検証**（seed 4001–4006）で再現性を確認した。
- 相手は Greedy 固定、mirror デッキ（fable `deck.csv` = SOT-1794 champion）。全計測 **fault 0**。

## 候補と screen 結果（seed 2001–2004, N=48/候補）

FABLE_CONFIG からの delta 14 候補（deviate_margin, uct_c, n_worlds, max_root_actions,
max_tree_depth, prior_temperature, deck_low weights, root deck guard 等）。上位:

| 候補 | delta | 勝率 | Wilson 95% CI |
| --- | --- | --- | --- |
| uctc10 | `uct_c=1.0` | **0.792** | [0.657, 0.883] |
| depth2 | `max_tree_depth=2` | 0.708 | [0.568, 0.818] |
| dm005 | `deviate_margin=0.05` | 0.688 | [0.547, 0.801] |
| root8 | `max_root_actions=8` | 0.667 | [0.525, 0.783] |
| baseline | `{}`（v1 champion） | 0.583 | [0.443, 0.712] |

（全候補は `kpi_history.jsonl` phase=`screen` を参照。`decklow_strong` 0.479 は劣後、
一律 deck-low 強化は棄却の前例（SOT-1704/1729）と整合。）

## confirm 結果（seed 3001–3006, N=48/候補）

screen 上位 + 上位2個の組み合わせ `uctc10_depth2` を独立 seed で confirm:

| 候補 | 勝率 | Wilson 95% CI |
| --- | --- | --- |
| **uctc10_depth2** | **0.729** | [0.590, 0.834] |
| uctc10 | 0.646 | [0.504, 0.766] |
| baseline | 0.625 | [0.484, 0.748] |
| dm005 | 0.542 | [0.403, 0.674] |
| depth2 | 0.500 | [0.364, 0.636] |

confirm 単発では `uctc10_depth2` が最上位（下限 0.590 > 0.5）に見えたが、baseline (0.625) と CI が
重複し **有意差なし**。screen で光った `uctc10`/`depth2` も confirm で baseline 水準へ後退した。

## 追検証（独立 seed 4001–4006, N=48）— **昇格を棄却**

confirm 単発の最上位 `uctc10_depth2` を、さらに別 seed で baseline と直接再検証:

| 候補 | 追検証 (4001–4006) | confirm 2 波プール N=96 |
| --- | --- | --- |
| baseline | 0.667 [0.525, 0.783] | **0.646 [0.546, 0.734]** |
| uctc10_depth2 | 0.583 [0.443, 0.712] | **0.656 [0.557, 0.744]** |

**追検証で勝敗が反転**（baseline 0.667 > 候補 0.583）。2 波プール（N=96）では baseline 0.646 と
候補 0.656 が**ほぼ同一・CI ほぼ完全に重複** → `uctc10_depth2` の 0.729 は seed 運であり、
**baseline に対する実質的な改善ではない**と確定した。

## 判定: **全候補 非昇格 — v1 champion を維持**

- 探索パラメータ空間はほぼ平坦。松系譜は既に調律済み（`deviate_margin=0.1` が決定打 SOT-1672、
  ablation で baseline 最良 SOT-1673）で、本サイクルもこれを裏付けた。「screen で光った候補が独立
  seed の confirm/追検証で洗い流される」パターン（SOT-1673/1698/1699）が再現した。
- SOT-1698/1699 の教訓に従い、**非昇格候補の behavior 変更は一切 main.py に入れず**、解析ハーネス
  （`eval/kpi.py` / `kpi_report.py` / `run_kpi_wave.sh`）と KPI 履歴・本 docs のみを残す。
- **チャンピオン版 = 既存 v1 `FABLE_CONFIG`（`main.py` 変更なし）。** vs Greedy baseline は 2 波プール
  で 0.646 [0.546, 0.734]。

## 受け入れ条件

- [x] `kpi_history.jsonl` に baseline + 各候補の screen/confirm が記録されている（19 行, 全 fault 0）。
- [x] 全候補の**非昇格判定**とその根拠（独立 seed 追検証で反転、プール N=96 で baseline と同一）が
  記録されている。
- [x] チャンピオン版（v1 `FABLE_CONFIG`）が `main.py` に反映済み（本サイクルで昇格なし=変更不要）。
