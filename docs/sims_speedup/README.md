# SOT-1836 — MCTS sims/sec 計測・高速化と探索量増 A/B

親: SOT-1791（fable の最強デッキとアルゴリズムを構築）

決定化 MCTS の強さは「時間予算内に回せる探索反復数（sims）」に直結する、という仮説の検証。
1手あたりの **sims/sec を計測**し、**探索量を増やす構成**が純粋な棋力向上につながるかを
vs champion A/B（集約 Wilson CI）で判定した。

## 成果物

- `eval/sims_bench.py` — **固定盤面コーパス**上での sims/sec マイクロベンチ（同一盤面セットで
  before/after 比較。自己対戦の盤面分散を排除して config 間を公平に比較できる）。
  1 実行につき `sims_history.jsonl`（git 追跡）へ 1 行追記。
- `sims_history.jsonl` — champion 基準値と各候補の sims/sec 計測ログ。
- `docs/sims_speedup/ab_rollout_t2_vs_champion_n16.json` — 昇格判定に使った vs champion A/B の
  生 JSON（`eval/results/` は .gitignore のためここに保存）。

## プロファイル根拠（ホットパス）

`cProfile`（40 盤面コーパス、champion config）で 1 反復の実時間の内訳を特定：

| 関数 | tottime | 帰属 |
| --- | --- | --- |
| `cg/utils.to_dataclass`（step ごとの observation 逆シリアライズ） | 6.21s / 13.6s (46%) | **cg/ エンジン（変更禁止・提出同梱）** |
| `cg/api.search_step`（cumtime） | 10.56s / 13.6s (78%) | **cg/ エンジン** |
| `json.raw_decode`（engine 内 JSON parse） | 1.18s | **cg/ エンジン** |
| `agents/observation.adapt_engine_obs` ほか agent 側 | 合計 ~24% | agents（可変） |

**1 反復あたりの実時間の約 76% はライセンス制約で変更禁止の `cg/` エンジン内**
（`search_step` → `to_dataclass`）が占める。したがって「1 反復あたり」の純粋高速化の上限は
約 1.2 倍（agent 側オーバーヘッド分）しかない。

## sims/sec を 1.5 倍にする唯一のレバー = rollout 早期打ち切り

`cg/` が変更できない以上、sims/sec（= iterations/sec）を大きく上げる手段は
**1 反復あたりの engine step 数を減らす**こと、すなわち rollout の早期打ち切りしかない
（champion は `rollout_turns=100 / rollout_depth=200` でほぼ決着まで回す）。

固定コーパス（40 盤面, seed 20260722, 同一プロセスで champion と背中合わせ計測）:

| label | rollout_turns / depth | sims/sec | iters/search | **speedup vs champion** | faults |
| --- | --- | --- | --- | --- | --- |
| champion | 100 / 200 | 1057.5 | 606 | 1.00 | 0 |
| **rollout_t2_d20** | 2 / 20 | 1454.2 | 877 | **×1.76** | 0 |
| rollout_t3_d30 | 3 / 30 | 1033.3 | 633 | ×1.43 | 0 |
| rollout_t5_d40 | 5 / 40 | 835.1 | 516 | ×1.14 | 0 |

→ **受け入れ条件① sims/sec ×1.5 以上は達成**（rollout_t2_d20 = ×1.76, faults 0）。

## しかし探索量増は棋力向上に転換しなかった（A/B 非昇格）

最速候補 rollout_t2_d20（champion base に rollout 打ち切りのみ適用）を **vs champion** で対戦
（`eval/bench.py --agent-a mcts --agent-b mcts`、同一 deck.csv、先後入替、time_budget 0.8）:

| shard | N | 候補勝 | champion 勝 | 候補 win-rate | Wilson95 |
| --- | --- | --- | --- | --- | --- |
| seed 20260722 | 4 | 0 | 4 | 0.000 | [0.000, 0.490] |
| seed 40260722 | 16 | 7 | 9 | 0.438 | [0.231, 0.668] |
| **pooled** | **20** | **7** | **13** | **0.350** | **≈ [0.18, 0.57]** |

**昇格ゲート（集約 Wilson CI 下限 > 0.5）を満たさない** → **非昇格**。点推定 0.35 で champion より
むしろ弱く、CI 下限 0.18 は 0.5 を大きく下回る。fault 0・時間切れ fallback 0（両者とも
budget_violations=0 / planner_fallbacks=0）。

### 解釈

champion は `max_tree_depth=1`・6 root actions × 4 worlds で既に 1 手あたり ~606 反復を回しており、
各 root エッジは ~100 回訪問済みで **すでに反復飽和**している。ここから rollout を打ち切って
反復数を 606→877 に増やしても、得られるのは飽和済み統計の追加サンプルに過ぎず、
一方で 2 ターンだけのヒューリスティック評価は「ほぼ決着まで回す」champion の value 推定より
明確に粗い。**探索の深さ（value 品質）の劣化が反復数増のメリットを上回る**ため、
"sims/sec を上げれば強くなる" は fable の現行動作点では成立しない。

## 決定

- **behavior revert**：`main.py` の `FABLE_CONFIG`（champion）は変更しない。
- 成果物は sims/sec 計測ハーネス + プロファイル根拠 + A/B 証跡（本 docs）。
- 今後 rollout の value 品質を保ったまま反復を増やす道（例：学習 value 関数で rollout を置換＝
  子 SOT-1837 の GPU value net）に効果が見込める。単純な rollout 打ち切りは棋力に対して非有効。

## 再現

```bash
# sims/sec（champion 基準 + 候補 speedup 比）
python3 eval/sims_bench.py --label champion --states 40 --seed 20260722
python3 eval/sims_bench.py --label rollout_t2_d20 --states 40 --seed 20260722 \
    --override '{"rollout_turns":2,"rollout_depth":20}' --baseline

# vs champion A/B（mcts 候補 vs mcts champion, Wilson CI）
python3 eval/bench.py --agent-a mcts --agent-b mcts --n 16 --seed 40260722 \
    --config-a '{"max_root_actions":6,"max_tree_depth":1,"rollout_turns":2,"rollout_depth":20,"n_worlds":4,"time_budget_s":0.8,"deviate_margin":0.1,"eval_weights":{"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3}}' \
    --config-b '{"max_root_actions":6,"max_tree_depth":1,"rollout_turns":100,"rollout_depth":200,"n_worlds":4,"time_budget_s":0.8,"deviate_margin":0.1,"eval_weights":{"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3}}'
```
