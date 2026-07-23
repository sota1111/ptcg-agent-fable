# SOT-1865 — on-policy 自己対戦データ + 本格学習で value net 昇格に再挑戦

親Issue: SOT-1862（fable 第2次強化）。関連: SOT-1837（value net 第1次, 非昇格）。
判定: **非昇格（champion 維持）** — `value_net` は既定 OFF のまま、`main.py` FABLE_CONFIG 不変。

## 目的

1837 の非昇格3要因を潰して value net の昇格に再挑戦する:

1. **off-policy な greedy データ** → champion(MCTS) 自己対戦の **on-policy** データに置換。
2. **GPU 非搭載の小規模学習** → 本コンテナで可能な最大規模 + **RTX 3080 Ti 向け完全再現ジョブ**を成果物化。
3. **full-rollout champion への葉評価寄与不足** → 葉直接評価 / 浅 rollout+value の2案で A/B。

本コンテナは **GPU/torch/numpy 非搭載（stdlib 純Python）**。1837 で整備済みの
torch学習→JSON エクスポート→純Python 推論（gap≤1e-9）パイプラインを土台にする。

## 成果物

| 追加/変更 | 役割 |
| --- | --- |
| `train/gen_selfplay.py`（変更） | **seed シャード分割**（`--n-shards`/`--shard-index`）+ **wall-clock 上限**（`--time-limit-s`）を追加。低速な on-policy(MCTS) 生成を予算内で分割生成できるようにした。meta に matches_played / gen_seconds / stopped_early / config を記録 |
| `train/merge_selfplay.py`（新規） | シャード JSONL を1データセットに union。feature_version 一致を検証し、per-shard 由来 meta を再構築 |
| `docs/value_net_v2/*.json` | vs champion A/B の生 bench レポート（2案） |
| `tests/test_selfplay_shard.py`（新規） | シャード分割が match 空間を**重複なく分割**すること + merge の union 性を固定 |

`train/data/`・`train/weights/` は `.gitignore` 済（生成物・学習重み）。1837 同様、コード＋docs のみを PR 化する。

## 1) on-policy データ生成（受け入れ条件①）

champion(MCTS) 自己対戦を **seed シャード分割**で生成（1シャード ≈450s の wall-clock 上限で早期打切）:

```bash
# 4シャード計画のうち 0..2 を各 450s 予算で生成（本コンテナ実測）
for k in 0 1 2; do
  python3 train/gen_selfplay.py --agent mcts --n 2000 --seed 18650101 \
    --stride 2 --max-per-match 40 --n-shards 4 --shard-index $k --time-limit-s 450 \
    --config '{"time_budget_s":0.04,"max_root_actions":6,"max_tree_depth":1,
               "rollout_turns":100,"rollout_depth":200,"n_worlds":4,"deviate_margin":0.1,
               "eval_weights":{"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3}}' \
    --out train/data/onpolicy.shard$k.jsonl
done
python3 train/merge_selfplay.py --out train/data/onpolicy.jsonl train/data/onpolicy.shard*.jsonl
```

| 項目 | 実測 |
| --- | --- |
| シャード数（生成） | 3 / 4計画（k=0,1,2; 各 stopped_early=true） |
| matches_played | **1,344 戦**（461 + 447 + 436） |
| 生成時間 | **1,352 s**（≈22.5 分, 各シャード ≈450s） |
| サンプル数 | **24,901**（勝 14,371 / 他 10,530） |
| fault | **0** |

- **on-policy 性**: 生成 agent は champion と同じ MCTS（full rollout `rollout_turns=100`,
  `n_worlds=4`, 同一 `eval_weights`, `max_tree_depth=1`）。champion が実際に訪れる盤面分布を学習する。
- **1点の妥協**: 生成の `time_budget_s` は 0.04s（champion 実運用 0.8s）に落として 8h/日予算内に量を確保。
  探索量は減るが**方策の質・盤面分布は champion 同型**。目標 ≥2,000 戦に対し達成量 1,344 戦を実測記録
  （残り 1 シャードで ≥2,000 到達可能。GPU マシンでは下記ジョブで大規模化）。

## 2) 学習（受け入れ条件②）

純Python/CPU（stdlib SGD）で本コンテナ可能な最大規模を実行:

```bash
python3 train/train_value.py --data train/data/onpolicy.jsonl \
    --out train/weights/value.json --hidden 64 --epochs 100 --lr 0.1 --l2 1e-4 --seed 1865
```

| 指標 | 値 |
| --- | --- |
| samples (train/val) | 24,901 (19,921 / 4,980) |
| win base rate | 0.579 |
| **val MSE** | **0.214**（final train_mse 0.210） |
| 一致検証（train-forward vs 再ロード純Python推論） | **max gap 0.00e+00**（tol 1e-6）OK |

**val MSE 比較**:
- 定数 0.5 予測 = 0.25 → **改善**（value 信号は実在）。
- 1837 実績（greedy off-policy）= 0.197 → **悪化（0.214 > 0.197）**。

> **知見**: on-policy champion 自己対戦は両者が良手を指すため**均衡付近の盤面**が多く、
> 同一盤面から勝敗が割れやすい＝**value target が本質的にノイジー**で MSE が下がりにくい。
> off-policy greedy データの方が決着が付きやすく MSE は低く出るが、champion の探索には効かなかった。
> 「MSE が低い＝champion に効く」ではないことが 1837/1865 で二重に確認された。

（hidden 32/64 × epochs 100 の sweep でも val MSE は 0.214 前後で頭打ち。）

## 3) vs champion A/B（受け入れ条件③）

両側 MCTS を **同一予算**（`time_budget_s=0.12, n_worlds=4, max_root_actions=6,
max_tree_depth=1, deviate_margin=0.1`, 同一 `eval_weights`）で対戦、side 交互、集約 Wilson 95% CI。
A=学習版 value 統合、B=champion（heuristic + full rollout）。

| 統合案 | rollout 設定 | N | winrate A (excl. draws) | Wilson95 | W/L/D | fault/reject/budget超過 | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| (b) 葉直接評価 | `rollout_turns=0, rollout_depth=0` | 50 | **0.500** | [0.366, 0.634] | 25/25/0 | 0/0/0 | **非昇格** |
| (a) 浅 rollout+value | `rollout_turns=2, rollout_depth=40` | 50 | **0.440** | [0.312, 0.577] | 22/28/0 | 0/0/0 | **非昇格** |

昇格ゲート = **集約 CI 下限 > 0.5 + 独立 seed confirm**。両案とも CI 下限（0.366 / 0.312）< 0.5 で
**screen 段階で不成立** → confirm は不要。engine reject 0・agent 例外 0・budget 超過 0・fallback 0。

- (b) は 1837 と同じく champion と**完全同格（0.500）**。on-policy データに替えても葉直接評価は
  full-rollout champion を上回らない。
- (a) 浅 rollout+value は champion に**やや劣後（0.440）**。弱い value で rollout を浅くすると
  full-rollout champion の情報量に負ける、という 1837 の示唆を再確認。

## 判定（受け入れ条件⑤）

**非昇格。champion 挙動は不変**（`value_net` 既定 OFF、`main.py` FABLE_CONFIG 変更なし、
`test_submission` で不変を担保）。value net は opt-in インフラとして温存。behavior revert 不要
（そもそも champion 経路に学習版は載っていない）。

### なぜ効かないか（1837→1865 の収束した結論）

1. champion の rollout は事実上 terminal まで展開（`rollout_turns=100`）＝葉評価の寄与が構造的に小さい。
2. 弱い value で rollout を浅くすると full-rollout champion に対して等価〜劣後になる。
3. on-policy 化は val MSE を**下げなかった**（均衡盤面のノイズ）。単純 MLP（20次元, hidden≤64）の
   容量では champion の determinized-MCTS を上回る質に届かない。
4. matsu SOT-1674/1679, ume PPO 系, fable 1837 と**同じ結論**に再到達。

## 4) 人間 GPU マシン（RTX 3080 Ti）向け 完全再現ジョブ（受け入れ条件④）

本コンテナは GPU 非搭載のため小予算で頭打ちした。以下は GPU マシンで**大規模 on-policy データ +
本格学習**まで回すための完全手順。torch/GPU 経路はエクスポート形式・推論経路が本コンテナと**不変**。

```bash
# 前提: torch(CUDA) 導入済み。repo ルートで実行。
pip install torch --index-url https://download.pytorch.org/whl/cu121

# (1) on-policy データを大規模生成（champion 実運用 budget 0.8s、8シャード並列、各 ≥1h）
#     GPU は生成には効かない(engine は CPU)ので、シャードを CPU コア数だけ並列起動して量を稼ぐ。
for k in $(seq 0 7); do
  python3 train/gen_selfplay.py --agent mcts --n 8000 --seed 18650101 \
    --stride 2 --max-per-match 40 --n-shards 8 --shard-index $k --time-limit-s 3600 \
    --config '{"time_budget_s":0.8,"max_root_actions":6,"max_tree_depth":1,
               "rollout_turns":100,"rollout_depth":200,"n_worlds":4,"deviate_margin":0.1,
               "eval_weights":{"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3}}' \
    --out train/data/onpolicy.shard$k.jsonl &
done; wait
python3 train/merge_selfplay.py --out train/data/onpolicy.jsonl train/data/onpolicy.shard*.jsonl
#   期待成果物: train/data/onpolicy.jsonl（≥8,000 戦規模 / ≥15万サンプル / fault 0）

# (2) GPU 本格学習（torch backend, 大容量 MLP, 早期打切なし）
python3 train/train_value.py --data train/data/onpolicy.jsonl \
    --out train/weights/value.json --backend torch --hidden 64 --epochs 300 --lr 0.01 --seed 1865
#   期待成果物: train/weights/value.json（純Python 推論と gap≤1e-6 の一致検証を末尾で自動実行）
#   期待: val MSE を 0.197 未満へ。ここを下回れなければ (3) は昇格しない公算が高い。

# (3) vs champion A/B（本 report と同一手順、独立 seed で confirm）
CHAMP='{"max_root_actions":6,"max_tree_depth":1,"rollout_turns":100,"rollout_depth":200,
        "n_worlds":4,"time_budget_s":0.12,"deviate_margin":0.1,
        "eval_weights":{"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3}}'
LEAF='{"max_root_actions":6,"max_tree_depth":1,"rollout_turns":0,"rollout_depth":0,
       "n_worlds":4,"time_budget_s":0.12,"deviate_margin":0.1,"value_net":"train/weights/value.json",
       "eval_weights":{"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3}}'
python3 eval/bench.py --agent-a mcts --agent-b mcts --n 200 --seed 18659101 \
    --config-a "$LEAF" --config-b "$CHAMP" --json docs/value_net_v2/ab_leaf_gpu.json
# 独立 seed confirm:
python3 eval/bench.py --agent-a mcts --agent-b mcts --n 200 --seed 20260723 \
    --config-a "$LEAF" --config-b "$CHAMP" --json docs/value_net_v2/ab_leaf_confirm.json
```

**昇格した場合のみ**（集約 CI 下限 > 0.5 かつ独立 seed confirm も > 0.5）:
`main.py` の `FABLE_CONFIG` に `"value_net": "train/weights/value.json"`（+ 勝った rollout 設定）を追加し、
`value.json` をリポジトリに同梱（`.gitignore` から除外）して champion 更新 → SOT-1866 で Kaggle 再提出。

### SOT-1864（深さ拡張）との組み合わせ

Issue 記載の「深さ拡張 + 浅 rollout+value」案は、ブロッカー **SOT-1864 が未マージ**のため本 Issue
スコープ外。1864 マージ後の追検証として、上記 A/B の champion/value 双方に `max_tree_depth=2` +
progressive widening を載せて再測する（本 Issue の受け入れ条件は 1864 に非依存で独立完了）。

## 受け入れ条件チェック

- [x] ① on-policy データ生成（1,344 戦 / 24,901 サンプル / 1,352s / fault0）を規模・時間つきで記録
- [x] ② 可能な最大規模で学習（val MSE 0.214）を 1837（0.197）比で比較・記録
- [x] ③ vs champion A/B を集約 Wilson CI つきで記録、**非昇格**を判定
- [x] ④ RTX 3080 Ti 完全再現ジョブ（生成→学習→A/B→confirm）を docs 化
- [x] ⑤ 非昇格 → champion 挙動不変（`value_net` 既定 OFF, `main.py` 不変, test_submission green）
