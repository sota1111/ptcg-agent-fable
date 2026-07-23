# MCTS 深さ方向拡張（max_tree_depth≥2 + progressive widening）A/B レポート (SOT-1864)

2026-07-23. fable の上位帯（レート差 +25 以上）実勝率 **28%**（同格 47%、SOT-1835 実データ）の
主因と推定される「読みの浅さ」を、**深さ方向**への探索拡張で削る試み。SOT-1836 で champion は
**depth1・~606 反復/手で反復飽和**（反復数を増やしても棋力に転換しない）と判明済みのため、飽和した
反復を「反復数」ではなく「木の深さ」に投資する仮説を検証した。

親 Issue: SOT-1862 fable第2次強化。関連: SOT-1835（敗因解析）/ SOT-1836（sims/sec 反復飽和）/
SOT-1796（screen 反転の教訓）。

## 仮説と実装

champion（`max_tree_depth=1`）は実質「ルート子ノードを 1 手展開 → 即 rollout」で、木の読みは
1-ply。深さを 2〜3 に伸ばせば相手応手を織り込んだ読みができるが、そのままでは分岐爆発
（`max_root_actions × max_child_actions`）で飽和 ~600 反復が薄く散る。そこで **progressive widening
（PW）** を `agents/planner.py` `_select_edge` に opt-in 追加した:

- ノードは訪問数に応じて上位事前確率の `ceil(pw_c * (visits+1)**pw_alpha)` 本の枝のみを選択対象とし、
  残りは訪問が貯まるまでロックする。低訪問の内部ノードで枝を絞り、飽和反復を最有力枝の深掘りに回す。
- **既定 OFF**（`pw_enabled=False`）。無効時 `_select_edge` は champion と byte-identical。`FABLE_CONFIG`
  は `max_tree_depth=1`・PW 未設定のまま＝ champion は完全不変。

## 深さ別の反復数・fault 計測（`eval/sims_bench.py`, 同一 20 状態コーパス, budget 0.8s）

| config | iters/search 平均 | sims/sec | faults |
| --- | --- | --- | --- |
| champion (depth1) | 605.4 | 1021.8 | 0 |
| depth2 (PW なし) | 579.6 | 1005.3 | 0 |
| depth2 + PW | 342.1 | 534.4 | 0 |
| depth3 + PW | 259.2 | 405.2 | 0 |

- 深さ拡張はいずれも **時間予算内・fault 0**（budget_violations / planner_fallbacks / degraded = 0）。
  受け入れ条件「depth≥2 が予算内で動作」を満たす。
- **PW は反復数を大きく削る**（342 vs 579）: 選択毎に事前確率で上位 k 本をソートするオーバーヘッド分。
  PW なし depth2 は分岐が `max_child_actions=8` で既に抑えられ、champion 並みの反復数を保ったまま深く
  読める。depth3+PW は反復が 259 まで落ち最も痩せる。→ A/B は反復を保てる **depth2 系**を主対象にした。

## vs champion A/B（mirror MCTS, 同一 0.5s throttle）

champion 実予算 0.8s では 1 試合 ~25s で mirror A/B が非現実的なため、両者を同一の絞った予算 0.5s
（~350 反復、深さが効くには十分）で対戦させる（SOT-1863 の vs-champion 慣行に準拠）。記録される
`winrate_a` がそのまま候補 vs champion 勝率で、Wilson 95% CI 下限が昇格ゲート。全計測 **fault 0**。

### screen（seeds 2001,2002 / N=20, 対 champion, 0.5s）

| 候補 | delta | 勝率 | Wilson 95% CI |
| --- | --- | --- | --- |
| **depth2** | `max_tree_depth=2` | **0.400** | [0.219, 0.613] |
| baseline | `{}`（champion 対 champion） | 0.350 | [0.181, 0.567] |
| depth2 + PW | `+pw_enabled, pw_c=1.0, pw_alpha=0.5` | 0.300 | [0.146, 0.519] |

- baseline（champion 対 champion）が 0.350（N=20 の seat/seed ノイズ床。CI は 0.5 を含む）。同一 seed で
  depth2 は 0.400 = baseline 比 +0.05 に留まる。**PW 版は 0.300 と baseline を下回る**（反復数減が棋力減に
  直結）。深さ拡張で明確に champion を上回る候補はなく、唯一 baseline を上回った depth2 のみ confirm へ。

### confirm（独立 seeds 3001–3003 / N=30, 対 champion, 0.5s）

| 候補 | 勝率 | Wilson 95% CI |
| --- | --- | --- |
| depth2 | 0.4667 | [0.3023, 0.6386] |

- 独立 seed でも **0.467、CI 下限 0.302 << 0.5**。点推定は mirror MCTS の期待値 0.5 とほぼ同値＝ champion
  と統計的に区別できない。screen+confirm 集計 50 戦で A 22 / B 28 = **0.440 [0.309, 0.579]**、CI 下限
  0.309 < 0.5。

## 判定: **非昇格 — champion を維持**

- 昇格ゲート（集約 Wilson CI 下限 > 0.5 + 独立 seed confirm）未達。depth2 は champion と有意差なし、
  depth2+PW / depth3+PW はむしろ弱い。SOT-1698/1699/1796/1863 の運用ルールに従い、**champion
  （`main.py` `FABLE_CONFIG`）の挙動は一切変更しない**。
- 追加した深さ探索 / PW インフラは **既定 OFF の opt-in** として温存（`deck_guard` / `value_net` /
  `bench_dev` と同じ dormant infra 扱い。後続候補が再利用できる）。
- **示唆**: SOT-1836（反復増は非転換）に続き、飽和反復を深さに投資しても棋力に転換しなかった。上位帯の
  読み負けの主因は探索の広さ/深さではなく **リーフ評価（ヒューリスティック評価器）と rollout 方策の質**
  にあると強く示唆される。次レバーは SOT-1865（champion 自己対戦の on-policy データ + GPU 本格学習で
  value net 昇格に再挑戦）へ。

## 受け入れ条件

- [x] depth≥2 候補が時間予算内（fault 0）で動作する計測記録がある（`sims_bench.py`, `sims_history.jsonl`：
  depth2 / depth2+PW / depth3+PW すべて 0.8s 予算内・fault 0）。
- [x] vs champion A/B が集約 Wilson CI つきで記録され、昇格/非昇格が判定されている（`kpi_history.jsonl`
  の baseline 1 行 + screen 2 行 + confirm 1 行、全 fault 0、非昇格判定）。
- [x] 非昇格につき champion（`main.py` `FABLE_CONFIG`）の挙動は不変（`max_tree_depth=1`・PW 既定 OFF）。

## 再現コマンド

```bash
# 深さ別 反復数/fault 計測（budget 0.8s）
python3 eval/sims_bench.py --label champion  --states 20 --seed 20260723 --override '{}'
python3 eval/sims_bench.py --label depth2    --states 20 --seed 20260723 --override '{"max_tree_depth":2}'
python3 eval/sims_bench.py --label depth2_pw --states 20 --seed 20260723 \
  --override '{"max_tree_depth":2,"pw_enabled":true,"pw_c":1.0,"pw_alpha":0.5}'

# vs champion A/B（B = FABLE_CONFIG @0.5s; A も time_budget_s=0.5 を必ず含める）
CHAMP_B='{"max_root_actions":6,"max_tree_depth":1,"rollout_turns":100,"rollout_depth":200,"n_worlds":4,"time_budget_s":0.5,"deviate_margin":0.1,"eval_weights":{"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3}}'
# screen（N=20）
python3 eval/kpi.py --label depth2_05 --phase screen --agent-a mcts --agent-b mcts \
  --n 10 --seeds 2001,2002 --override-a '{"time_budget_s":0.5,"max_tree_depth":2}' --config-b "$CHAMP_B"
# confirm（独立 seed, N=30）
python3 eval/kpi.py --label depth2_05 --phase confirm --agent-a mcts --agent-b mcts \
  --n 10 --seeds 3001,3002,3003 --override-a '{"time_budget_s":0.5,"max_tree_depth":2}' --config-b "$CHAMP_B"
```
