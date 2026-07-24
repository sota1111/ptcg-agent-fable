# SOT-1892: take実証タクティクスの prior/rollout/fallback 移植 — screen→confirm 計測と昇格判定

親 Issue: SOT-1887 fable第3次強化。関連: SOT-1835（敗因解析: 上位帯勝率 28%、盤面全滅が最多敗着）/
SOT-1682・1694・1730（take 側のタクティクス出典）/ SOT-1796（screen 反転の教訓）。

## 概要

take（ルールベース, Kaggle 収束 575.5）で実証済みのタクティクスを fable の決定化 MCTS に
**opt-in（既定OFF）** で移植し、探索の量・深さ・value net に一切触れずに「手選択の質」だけで
champion を上回れるかを screen → confirm の A/B で判定した。

**判定: 非昇格。** confirm（独立 seeds, N=90）pooled 0.422 [0.325, 0.525]、CI 下限 0.325 < 0.5。
champion の既定挙動は不変（全注入点 opt-in・既定OFF のまま温存）。

## 移植したタクティクス（agents/take_tactics.py）

GreedyAgent スコアへのコンテキスト限定オーバーライドとして抽出（全て card 属性ベース、
カードID特殊ケースなし）:

1. **KO即取り**（take S_LETHAL, SOT-1635）: 弱点/抵抗補正込みで防御側を倒せる ATTACK を
   全展開行動より上位へ（T_LETHAL=400）。
2. **場切れガード**（S_BENCH_INSURANCE, SOT-1694）: ベンチ空のとき、たねポケモンの PLAY を
   lethal 以外の全てより上位へ（SOT-1835 の最多敗着「盤面全滅」対策）。
3. **doomed-Active ガード**（SOT-1682/1694）: 次ターン落ちる Active への ATTACH を END 未満へ、
   EVOLVE を展開行動未満へ（先に KO を取れる場合は除外）。
4. **Supporter 山切れガード**（S_DECK_GUARD, SOT-1694）: 自山札 ≤6 で Supporter / pure-draw
   ability を END 未満へ。
5. **プライズトレード昇格**（SOT-1682/1730）: TO_ACTIVE/SWITCH を「今撃てるか → net prize race →
   エネルギー不足数 → 打点 → 献上プライズ小 → HP」の take タプル順で序列化。

## 注入点（3点、各々独立に opt-in・既定OFF）

- **(a) action prior**: `PlannerConfig.tactics_prior` — root 候補の並び/softmax prior。
- **(b) rollout policy**: `PlannerConfig.tactics_rollout` — rollout の両側手選択。
- **(c) フォールバック層**: `SubmissionAgent(tactics=...)` / 環境変数 `FABLE_TACTICS`
  （`prior,rollout,fallback` / `full`）— Greedy 層を TacticalGreedyAgent に、Rule 層を
  `RulePolicy(tactics=True)` に差し替え。

既定OFFの不変性は tests/test_take_tactics.py のインバリアンステストで固定
（タクティクス非発火コンテキストでスコア完全一致、planner/submission 配線の既定確認）。

## 計測（eval/run_ab_vs_champion.sh 方式、対 champion ミラー、budget 0.06s、全 fault 0）

昇格ゲート: **confirm（独立 seed）pooled Wilson 95% CI 下限 > 0.5 のみ**（SOT-1796 反転教訓）。

### screen（seeds 2001,2002 / N=40）

| 候補 | winrate | Wilson95 | 判定 |
| --- | --- | --- | --- |
| tactics_prior（(a)のみ） | 0.450 | [0.307, 0.602] | 足切り |
| tactics_rollout（(b)のみ） | **0.525** | [0.375, 0.671] | confirm へ |
| tactics_both（(a)+(b)） | 0.425 | [0.285, 0.578] | 足切り |

prior 注入は screen の時点で champion を下回った。greedy prior の並び替えは root 候補
enumeration（max_root_actions=6）と deviate_margin=0.1 の champion バランスを崩す方向に働き、
併用（both）も prior 側の劣化が支配した。

### confirm（独立 seeds 3001–3003 / N=90, tactics_rollout のみ）

| seed | winrate | A/B |
| --- | --- | --- |
| 3001 | 0.467 | 14/16 |
| 3002 | 0.400 | 12/18 |
| 3003 | 0.400 | 12/18 |

**pooled: 38/90 = 0.422 [0.325, 0.525]** — CI 下限 0.325 < 0.5 で昇格ゲート未達。
screen の 0.525 は独立 seed で 0.422 へ後退（SOT-1698/1699/1796/1863 と同じ
「screen の輝きが独立 seed で洗い流される」パターンの再現）。

## 判定と考察

- **非昇格・champion 維持。** 全注入点 opt-in（既定OFF）のため behavior revert は不要
  （champion 既定挙動はコミット前後で不変）。
- take のタクティクスは一手貪欲の**ルールベースとしては**強いが、fable の MCTS は rollout の
  greedy 方策と leaf evaluator を通じて同等の判断（lethal は KO ボーナス +300、deck_low 勾配、
  展開優先の tier）を既に暗黙に持っており、明示バンドの上書きは探索が拾う情報を狭める側に
  働いたと読む。第2次（量/深さ/value 質）に続き、**手選択バイアス軸でも champion 探索飽和は
  非転換** — fable の壁は rollout 方策の表現力ではなく、より上流（デッキ相性 SOT-1893 /
  敗着パターン特定 SOT-1894）にある可能性が高い。
- 副産物: take プライズトレード昇格 / 場切れガードは `FABLE_TACTICS` 経由でいつでも再計測可能な
  形で温存（Kaggle 提出には未投入）。

## 受け入れ条件

- [x] take戦術の prior/rollout/fallback 注入が opt-in で実装され、既定OFFで champion 挙動が不変
      （tests/test_take_tactics.py 106テスト中の該当インバリアンステスト、全suite PASS・fault 0）
- [x] screen→confirm 計測が完了し、昇格/非昇格が CI 根拠付きで判定されている
      （kpi_history.jsonl: screen 3 行 + confirm 3 行、全 fault 0、非昇格）
- [x] 非昇格時は revert + docs で終端（opt-in 既定OFFにつき挙動 revert 不要、本 docs で終端。
      Kaggle 提出なし）

## 再現手順

```bash
# screen（対 champion, N=40/候補）
BUDGET=0.06 eval/run_ab_vs_champion.sh screen 20 2001,2002 kpi_history.jsonl \
  tactics_rollout='{"time_budget_s":0.06,"tactics_rollout":true,"eval_weights":{"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3}}'
# confirm（独立 seed, N=90 を per-seed シャードで）
python3 eval/kpi.py --label tactics_rollout --phase confirm --agent-a mcts --agent-b mcts \
  --n 30 --seeds 3001 --override-a '{"time_budget_s":0.06,"tactics_rollout":true,"eval_weights":{...同上...}}' \
  --config-b "$CHAMP_B"   # 3002 / 3003 も同様
```
