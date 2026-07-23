# 盤面全滅（board_wipe）敗着対策 A/B レポート (SOT-1863)

2026-07-23. fable の最大ローカル敗着クラスタ **盤面全滅（board_wipe: アクティブ不在で敗北、
RESULT.reason=3）** — SOT-1835 のローカル敗着分類で **93.8%（30/32件）** を占めた — に対し、
盤面評価に **ベンチ展開（board-wipe insurance）** と **進化準備** の opt-in 加点項を追加し、
screen → confirm の A/B サイクルで昇格可否を判定した。

親 Issue: SOT-1862 fable第2次強化。関連: SOT-1835（敗因解析）/ SOT-1796（screen反転の教訓）。

## 仮説と対策

盤面全滅は「アクティブが気絶したとき、昇格できるベンチ Pokémon が 1 体もない」状態で起きる。
よって直接のレバーは **ベンチを常に厚く保つことを評価で報酬する**こと。`agents/evaluator.py`
`HeuristicEvaluator` に既定 OFF（0）の opt-in 重みを追加した（champion 挙動は不変）:

- `bench_dev` / `bench_dev_cap`: ベンチ Pokémon 1 体ごとの加点を `bench_dev_cap` 体で飽和させる
  **サチュレーション項**。既存の線形 `pokemon` 項と違い「0→1 体（全滅を防ぐ最初の控え）」に価値を
  前寄せする。
- `evo_ready`: 進化済み Pokémon（`preEvolution` スタックが非空）への加点。進化ラインは HP が高く
  KO 連鎖に強い、という補助仮説。カードマスタ非依存（属性のみ）。

いずれも `main.py` `FABLE_CONFIG` は設定しない＝ champion は完全不変。

## 手法

- `eval/kpi.py` を **候補 vs champion の直接対戦**（`--agent-a mcts --agent-b mcts`、B=champion
  `FABLE_CONFIG`）で実行するラッパ `eval/run_ab_vs_champion.sh` を追加。記録される `winrate_a` が
  そのまま候補の対 champion 勝率になり、Wilson 95% CI 下限が昇格ゲート。
- 両者を同一の絞った time budget（0.06s）で走らせる（mirror MCTS を実時間内に収めるため。champion
  自身のスケジュールも時間圧で 0.2s まで落ちるので、同一予算の相対比較は公平 — value-net A/B / 
  `local_loss_tags.py --fable-budget` と同じ慣行）。
- **昇格ゲート: 集約 Wilson 95% CI 下限 > 0.5、独立 seed の confirm 必須**（SOT-1796 の
  「screen で光った候補が独立 seed で洗い流される」反転教訓）。全計測 **fault 0**。

## screen 結果（seeds 2001,2002 / N=40, 対 champion, budget 0.06s）

| 候補 | eval_weights delta | 勝率 | Wilson 95% CI |
| --- | --- | --- | --- |
| **bench2_30** | `bench_dev=0.3, cap=2` | **0.600** | [0.446, 0.737] |
| bench2_15 | `bench_dev=0.15, cap=2` | 0.525 | [0.375, 0.671] |
| baseline | `{}`（champion 相当） | 0.500 | [0.352, 0.648] |
| bench2_50 | `bench_dev=0.5, cap=2` | 0.450 | [0.307, 0.602] |
| bench_evo | `bench_dev=0.3, cap=2, evo_ready=0.2` | 0.425 | [0.285, 0.578] |
| evo_20 | `evo_ready=0.2` | 0.400 | [0.264, 0.554] |

- baseline（champion 対 champion）が 0.500 ちょうど → 対戦ハーネスは無バイアス（サニティ合格）。
- `bench_dev` は単峰: 0.15 は動かず、0.3 が最良、0.5 は過剰で後退。**`evo_ready` は明確に有害**
  （0.40）で、bench と併用しても悪化（0.425） → 進化準備項は棄却。
- 唯一 baseline を上回った `bench2_30` を confirm に昇格。

## confirm 結果（独立 seeds 3001–3003 / N=90, 対 champion）

| 候補 | 勝率 | Wilson 95% CI |
| --- | --- | --- |
| bench2_30 | 0.5333 | [0.4310, 0.6329] |
| baseline | 0.5222 | [0.4202, 0.6224] |

screen の 0.600 は独立 seed の confirm で **0.533 へ後退**、CI 下限 **0.431 < 0.5** で昇格ゲート
未達。baseline 自身も 0.522（ノイズ床）で、両 CI はほぼ完全に重複 → **有意差なし**。SOT-1673 /
1698 / 1699 / 1796 と同じ「screen の輝きが独立 seed で洗い流される」パターンが再現した。

## 敗着タグ再計測（mirror / N=40, budget 0.08s, seed 5001）

`analysis/local_loss_tags.py`（`FABLE_TAG_EVAL` で eval_weights を注入可能に拡張）で board_wipe
構成比を再計測・比較:

| 構成 | losses | board_wipe | prize_race |
| --- | --- | --- | --- |
| champion | 15 | **86.7%**（13） | 13.3%（2） |
| bench_dev=0.3, cap=2 | 14 | **100%**（14） | 0% |

**対策版でも board_wipe 構成比は下がらない**（むしろ全損失が board_wipe）。ベンチ加点は
盤面全滅損失を減らせておらず、勝率非改善と整合する。盤面全滅はリーフ評価の微修正で避けられる
単発の「ベンチ忘れ」ではなく、ゲームを通じて削り切られる（basic を引けない等）構造的敗着で、
評価の 1 項では動かないと示唆される。

## 判定: **非昇格 — champion を維持**

- 昇格ゲート（confirm CI 下限 > 0.5）未達、かつ board_wipe 構成比の低下も確認できず。
- SOT-1698/1699/1796 の運用ルールに従い、**champion（`main.py` `FABLE_CONFIG`）の挙動は一切変更
  しない**。追加した評価項は **既定 OFF の opt-in** として温存（`deck_low` / `value_net` と同じ
  dormant infra 扱い。将来 on-policy 学習や別レバーと組み合わせる後続候補が再利用できる）。
- 残す成果物: opt-in 評価項（`agents/evaluator.py`＋単体テスト）、対 champion A/B ハーネス
  `eval/run_ab_vs_champion.sh`、`local_loss_tags.py` の eval override、KPI 履歴、本 docs。

## 受け入れ条件

- [x] 敗着タグ再計測で対策版の board_wipe 構成比が計測・比較されている（champion 86.7% vs 対策版
  100%、`analysis/data/local_loss_tags.json` + 本表）。
- [x] vs champion A/B が集約 Wilson CI つきで記録され、昇格/非昇格が判定されている
  （`kpi_history.jsonl` の screen 6 行 + confirm 2 行、全 fault 0、非昇格判定）。
- [x] 非昇格につき champion（`main.py` `FABLE_CONFIG`）の挙動は不変（新重みは既定 OFF）。

## 再現コマンド

```bash
# screen（対 champion, N=40）
EW='"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3'
BUDGET=0.06 eval/run_ab_vs_champion.sh screen 20 2001,2002 kpi_history.jsonl \
  bench2_30="{\"time_budget_s\":0.06,\"eval_weights\":{$EW,\"bench_dev\":0.3,\"bench_dev_cap\":2}}"
# confirm（独立 seed, N=90）
BUDGET=0.06 eval/run_ab_vs_champion.sh confirm 30 3001,3002,3003 kpi_history.jsonl \
  baseline="{\"time_budget_s\":0.06,\"eval_weights\":{$EW}}" \
  bench2_30="{\"time_budget_s\":0.06,\"eval_weights\":{$EW,\"bench_dev\":0.3,\"bench_dev_cap\":2}}"
# 敗着タグ再計測
FABLE_TAG_BUDGET=0.08 python3 analysis/local_loss_tags.py --n 40 --mirror --seed 5001            # champion
FABLE_TAG_BUDGET=0.08 FABLE_TAG_EVAL='{"bench_dev":0.3,"bench_dev_cap":2}' \
  python3 analysis/local_loss_tags.py --n 40 --mirror --seed 5001                                # 対策版
```
