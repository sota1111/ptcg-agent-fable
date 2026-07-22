# SOT-1837 — GPU 自己対戦 価値関数 × MCTS 統合レポート

親Issue: SOT-1791（fable の最強デッキとアルゴリズムを構築）
判定: **非昇格（champion 維持）** — 学習版 value を既定OFFのまま残す。

## 目的と背景

ルールベース+MCTS の現行 champion は同格帯（vs matsu 0.458）で頭打ち。LB上位帯
との差を詰めるべく、自己対戦データから小型 value net を学習し、MCTS の
葉評価/rollout 置換に統合して質的強化を狙った。過去に学習版 value が悪化した前例
（matsu SOT-1674/1679、ume PPO 系）があるため、**champion 昇格ゲート方式**で進め、
非昇格なら behavior を変えず知見を残す方針。

## 成果物（再現可能パイプライン）

すべて **stdlib のみ**で動作（本コンテナに GPU/torch/numpy は無い）。torch/GPU は
学習を高速化する任意経路として実装済みで、エクスポート形式・推論経路は不変。

| 段階 | モジュール | 役割 |
| --- | --- | --- |
| 特徴量 | `agents/value_features.py` | 決定的 side-relative 特徴量（20次元）。dict（自己対戦）と engine dataclass（推論）で同一特徴を保証。per-card 重み禁止（heuristic と同じ規律） |
| 学習器本体 | `agents/value_net.py` | 単一の正準 forward を持つ 1 隠れ層 MLP（tanh→sigmoid）。JSON export/load、stdlib SGD |
| 推論 | `agents/learned_value.py` | `LearnedEvaluator`（Kaggle 純Python）。終局は heuristic と同一の厳密処理、`feature_version` ガード |
| データ生成 | `train/gen_selfplay.py` | engine 経由の自己対戦→(特徴, 最終結果)ラベルを JSONL 出力 |
| 学習CLI | `train/train_value.py` | `--backend python`（既定）/ `--backend torch`（GPU可）。学習後に **train-forward vs 再ロード推論の一致検証** |
| MCTS配線 | `agents/evaluator.py` / `agents/mcts_agent.py` | `value_net=<path>` の feature-flag。**既定OFF**、`main.py` FABLE_CONFIG は不変 |

### 再現手順

```bash
# 1) 自己対戦データ生成
python3 train/gen_selfplay.py --n 250 --agent greedy --seed 18370101 \
    --stride 2 --max-per-match 40 --out train/data/selfplay.jsonl
# 2) 価値ネット学習（GPU があれば --backend torch）
python3 train/train_value.py --data train/data/selfplay.jsonl \
    --out train/weights/value.json --hidden 24 --epochs 60 --lr 0.25 --seed 1837
#    → 学習末尾で一致検証: max gap 0.00e+00 (tol 1e-6) OK
# 3) vs champion A/B（下記）
```

一致テスト（受け入れ条件①）は `train_value.py` 内および
`tests/test_learned_value.py::TestConsistency` の両方で担保。学習した重みを JSON
エクスポート→純Python 推論で再ロードした予測が**完全一致（gap ≤ 1e-9）**する。

## 統合2案の vs champion A/B（受け入れ条件②）

- 生成データ: greedy 自己対戦 250 戦 → 4,931 サンプル（勝 2,771 / 他 2,160、fault 0）
- 学習: hidden=24, epochs=60, lr=0.25 → val MSE 0.197（定数0.5予測=0.25、
  base-rate 0.563 予測≈0.246 より改善。**弱いが実在する**価値信号）
- A/B: 両側 MCTS を **同一予算**（time_budget 0.12s, n_worlds=2, deviate_margin 0.1,
  max_tree_depth 1, max_root_actions 6）で対戦、side 交互、集約 Wilson 95% CI。
  A=学習版, B=champion（heuristic + full rollout）。

| 統合案 | 設定 | winrate A (excl. draws) | Wilson95 | W/L/D | fault | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| (b) 葉ノード直接評価 | `rollout_turns=0, rollout_depth=0` | 0.500 | [0.352, 0.648] | 20/20/0 | 0 | **非昇格** |
| (a) rollout早期打切+価値 | `rollout_turns=2, rollout_depth=40` | 0.500 | [0.352, 0.648] | 20/20/0 | 0 | **非昇格** |

昇格ゲート = **CI 下限 > 0.5**。両案とも CI 下限 0.352 < 0.5 で不成立。点推定も
champion と完全同格（0.500）。時間切れ 0・fallback 0・engine reject 0。

## 判定と根拠（受け入れ条件③）

**非昇格。champion 挙動は変更しない**（`value_net` は既定 OFF、`main.py`
FABLE_CONFIG 不変）。behavior revert は不要（そもそも champion 経路に学習版は
載っていない）。value net は opt-in インフラとして温存する。

### なぜ勝てなかったか（知見）

1. **学習データが off-policy**: 高速化のため greedy 自己対戦でデータ生成した。
   champion(MCTS) が実際に訪れる盤面分布とずれ、葉評価としての精度が伸びない。
2. **本格 GPU 学習が未実施**: 本ランは GPU/torch 非搭載の環境。issue 前提の
   RTX 3080 Ti / 8h・日での大規模学習は人間側マシンで別途回す必要がある。
   本ランは**それを回せる再現パイプラインの整備**までを成果とする。
3. **champion の rollout は事実上 terminal まで展開**（`rollout_turns=100`）ので
   葉評価の寄与が小さい。value を効かせるには rollout を浅くするが、浅い探索で
   弱い value を使うと full-rollout champion と等価〜劣後になりやすい（今回まさに同格）。
4. 前例（matsu SOT-1674/1679、ume PPO 系）と同じ結論に再度到達。学習版 value が
   heuristic+determinized-MCTS を上回るには、**on-policy(champion自己対戦)データ +
   実GPU大規模学習 + 特徴量の質的拡張**が必要と示唆される。

### 人間が本格 GPU 学習を回す場合の次アクション

- `train/gen_selfplay.py --agent mcts --config '{...champion...}'` で **on-policy**
  データを生成（低速なので GPU マシン推奨、シャード並列）。
- `train/train_value.py --backend torch --hidden 64 --epochs 300` 等で本格学習。
- 昇格ゲート（vs champion 集約 Wilson CI 下限 > 0.5、独立 seed confirm）を通れば
  `main.py` FABLE_CONFIG に `value_net` を追加して champion 更新 + Kaggle 再提出。
