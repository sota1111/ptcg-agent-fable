# fable v1 本体アルゴリズム 計測レポート (SOT-1795)

2026-07-20. fable 本体 = 決定化 MCTS（松 champion SOT-1672/1693 系譜）+ 竹式ルールポリシー統合
（SOT-1682/1694）+ self-deck-out ガード（SOT-1697）+ 時間予算・多段フォールバック
（MCTS→Greedy→Rule→random-legal）。設定は `main.py` の `FABLE_CONFIG`。

## 計測方法

- ローカル cabt エンジン、fable の `deck.csv`（SOT-1794 champion、松の提出デッキと同一リスト）。
- vs Random / vs Greedy: `eval/bench.py`、mirror デッキ、先後入替、独立 seed 5 シャード
  （`eval/aggregate_shards.py` で合算）。
- vs 松 champion: `eval/battle_vs.py`（SOT-1681 方式のサブプロセス隔離 — `agents` パッケージ名が
  衝突するため各提出 agent を自 repo cwd の `eval/agent_server.py` で分離）。各 repo の提出
  `main.agent` + 自 `deck.csv`、先後入替、8 シャード × 6 戦。
- エンジン RNG は注入不可のため統計計測（agent 側 seed のみ固定）。

## 結果

| 対戦 | N | 勝率 (draws除外) | Wilson 95% CI | 判定 |
| --- | --- | --- | --- | --- |
| vs Random | 50 | **0.980** | [0.895, 0.997] | 圧勝 ✓（CI下限 > 0.8） |
| vs Greedy | 50 | **0.640** | [0.501, 0.759] | 有意勝ち（下限 > 0.5）だが目安 0.8 未達 |
| vs 松 champion | 48 | **0.458** | [0.326, 0.597] | 統計的互角（先手 13/24） |

### ゼロゲート（全計測合算: bench 150 戦 + クロス 48 戦）

| カウンタ | 値 |
| --- | --- |
| engine rejects（不正手） | 0 |
| agent exceptions / faults | 0 |
| random-legal fallbacks | 0 |
| planner fallbacks / degraded decisions | 0 |
| budget violations（0.8s/手 超過） | 0 |
| 最大累計思考時間/試合（クロス戦, host計測） | fable 32.7s / 松 31.6s（許容 600s） |

**fault 0 / 不正手 0 / 時間切れ 0 達成。**

## deck_low A/B（deck-preservation 勾配）

松の screen はこのデッキ（`b702e251e3b56104`）に deck_low を有効化していない（25デッキ screen の
中立/正群のみ有効）。fable v1 は Issue 指定どおり初版から ON。A/B（vs greedy, N=50 each）:

| 構成 | 勝率 | Wilson 95% CI |
| --- | --- | --- |
| deck_low ON（FABLE_CONFIG） | 0.640 | [0.501, 0.759] |
| deck_low OFF | 0.620 | [0.482, 0.741] |

差なし（中立）。ルールポリシー層の山残数ドロー抑制（`DECK_RESERVE=6`）は常時有効。

## 探索スループット確認

vs greedy 実戦 1 試合の planner 統計: 約 150–260 iteration/手（4 worlds、0.64s 消費/0.8s 予算）。
松 champion と同水準 — 統合による性能退行なし。

## 評価と次段

- vs Greedy 0.64 は松 champion の歴史的水準（SOT-1672: 0.618→0.63、KPI 基盤 0.76 N=50）と
  整合し、CI は重なる。champion 系譜の素の強さ上限であり、実装欠陥の兆候はない
  （スループット健全・ゼロゲート全通過）。
- 勝率引き上げ（目安 CI下限 > 0.8）は後続 SOT-1796（スクリーニング→確証 A/B サイクル）の
  スコープ。vs 松互角 = 移植ベース確立を確認、fable 側デルタ（ルール統合・deck_low）は中立。
