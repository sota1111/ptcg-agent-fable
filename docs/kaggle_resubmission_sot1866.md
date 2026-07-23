# fable 第2次強化 総括 & champion Kaggle再提出 (SOT-1866)

2026-07-23. 第2次強化（SOT-1862）の3本柱（SOT-1863/1864/1865）の昇格判定が全て確定し、
**3本柱すべて非昇格 → champion 更新なし**となった。本 Issue は当初「昇格あり→再提出／昇格
なし→総括のみ」の分岐設計だったが、人間の明示指示（Linearコメント「kaggleに提出してください」）
を受け、**現 champion を Kaggle へ再提出し、メタ変動後の収束スコアでベースラインを再確認**する。

---

## 1. 第2次強化 3本柱の判定総括（全非昇格）

| 子Issue | 施策 | 判定 | 主要エビデンス | PR |
| --- | --- | --- | --- | --- |
| SOT-1863 | board_wipe 対策（ベンチ展開/進化準備の盤面評価加点, opt-in） | **非昇格** | confirm 0.533 [0.431, 0.629]（CIが0.5を含む）・board_wipe 構成比も非低下 | #10 |
| SOT-1864 | MCTS 深さ方向探索拡張（max_tree_depth≥2 + progressive widening, opt-in） | **非昇格** | 50戦 0.440 [0.309, 0.579]（CI下限<0.5）・fault0 | #11 |
| SOT-1865 | on-policy value net 再挑戦（自己対戦データ + 本格学習） | **非昇格** | 2案とも非昇格（葉評価0.500 / 浅rollout0.440）。val MSE 0.214 > SOT-1837の0.197 | #12 |

### 共通の知見
- **探索量・深さの投資は棋力に転換しない**（SOT-1836の sims/sec 高速化、SOT-1864の深さ拡張とも、
  vs champion で有意差なし）。fable は既に「探索飽和」領域にあり、追加の探索予算は棋力を上げない。
- **「MSE が低い ≠ champion に効く」**（SOT-1865/1837）。学習した value net は葉評価の誤差を下げても、
  MCTS に組み込むと勝率が champion を上回らない。value net は既定 OFF を維持。
- **敗着クラスタ（board_wipe）への盤面評価加点も棋力に転換しない**（SOT-1863）。加点でベンチ展開を
  促しても confirm 勝率は 0.5 圏内で、board_wipe 構成比も下がらなかった。

3本柱の変更はすべて **opt-in（既定 OFF）** として merge 済み。champion の既定挙動（`main.py` の
`FABLE_CONFIG`）はベースライン **34064b3**（SOT-1797, PR#6）と**バイト等価**である
（`main.py` / `deck.csv` は無変更、`agents/` は既定 OFF の追加コードのみ）。

---

## 2. 再提出パッケージと検証

`scripts/build_submission.sh` で `submission.tar.gz`（28エントリ, 約2.0MB）を生成。
提出前ローカル検証を全通過：

- **gzip 整合** ✓（`gzip -t`）
- **必須ファイル top-level** ✓（`main.py`, `deck.csv` がトップレベル・ネストなし）
- **禁止パス / 秘密情報混入なし** ✓（`.env` / `.git/` / `vendor/` / `tests/` / `eval/` / `venv/` /
  `access_token` / `kaggle.json` / `__pycache__` / `*.pyc` 除外を grep で確認。展開後の
  `KAGGLE_API_TOKEN|KAGGLE_USERNAME|KAGGLE_KEY` キー名スキャンも 0 件）
- **exec 互換シミュレーション** ✓（**必須ゲート**）— 提出物を一時ディレクトリへ展開し、
  Kaggle と同じく `exec()`・`__file__` なし・submission dir を cwd 起点で解決する条件で
  `main.py` をロード。`agent({"select": None})` が 60枚デッキを返し、cabt エンジンで
  自己対戦を完走（**不正手 0・例外 0**で決着）。
  - 本開発環境には別プロジェクトの残骸 `/kaggle_simulations/agent`（ume系 PPO）が存在し、
    ローカルで自前 `agents/` を shadow する。exec 互換シミュレーションはこの残骸を無効化して
    自前 `agents/` を解決させることで、Kaggle 本番（当該 dir が fable 自身の展開物になる）と
    等価な import 経路を再現した。champion の `main.py` は無変更。
- **単体テスト 71/71 pass**（クリーン環境。`test_submission` 13 / `test_learned_value` 16 ほか
  全通過。combined 実行時に 1 件失敗するのは `test_selfplay_shard` の chdir に起因する
  cwd 順序の癖で、提出物の欠陥ではない — 個別実行で pass 確認済み）

---

## 3. Kaggle 提出結果

- **競技**: `pokemon-tcg-ai-battle`（The Pokémon Company - PTCG AI Battle Challenge）
- **提出 ref**: `54921798`
- **提出 SHA**: `d46222b`（fable champion, 既定挙動は 34064b3 と等価）
- **提出時刻**: 2026-07-23 07:09 UTC
- **status**: `SubmissionStatus.COMPLETE`（**Validation エラーなし** — 受理確認済み）

### スコア比較

| 提出 | ref | status | publicScore | 備考 |
| --- | --- | --- | --- | --- |
| **fable d46222b（本提出）** | 54921798 | **COMPLETE** | **600.0（暫定）** | 第2次 champion（挙動=34064b3） |
| fable 34064b3（ベースライン） | 54883092 | COMPLETE | **550.7** | SOT-1797/PR#6 |
| matsu champion | 54811671 | COMPLETE | 557.2 | 参考（最上位帯） |
| take champion | 54904755 | COMPLETE | 575.5 | 参考 |

> **暫定値と収束見込みの分離**：本提出の直後 publicScore は **600.0**。この 600.0 は本競技で
> 新規提出に付与される**初期プレースホルダ**であり（ランク付けエピソードが消化される前の値）、
> 収束値ではない。ベースライン 34064b3 も提出直後は 600.0 起点で、ランク戦の消化に伴い **550.7**
> へ収束した。
>
> **収束見込み**：本 champion の既定挙動はベースライン 34064b3 と**バイト等価**（アルゴリズム
> 無変更）なので、**収束スコアはベースライン ≈550–555 圏へ回帰する見込み**。今回の提出目的は
> 「メタ変動後も fable champion が同帯（≈550–555）を維持するか」の再確認であり、スコアの有意な
> 上振れは（無変更ゆえ）期待していない。収束の最終確定値はランク戦消化後（数時間〜）に
> `kaggle competitions submissions -c pokemon-tcg-ai-battle` で追確認できる。

---

## 4. 親 Issue SOT-1862 の最終判定

**SOT-1862（fable第2次強化: 敗着クラスタ対策と価値関数の質でスコア555の頭打ちを超える）**
の受け入れに対する最終判定：

- 目標「スコア555の頭打ちを超える」は **未達**。第2次で試みた3本柱（敗着クラスタ対策=SOT-1863、
  探索の深さ=SOT-1864、価値関数の質=SOT-1865）はいずれも vs champion で有意差を出せず、
  champion を更新できなかった。従って提出スコアはベースライン圏（≈550–555）に留まる。
- 第2次で得た確度の高い知見は「**現行 fable は探索飽和領域にあり、探索量・深さ・学習 value の
  質のいずれの投資も棋力に転換しない**」こと。頭打ち突破には、これら（探索/評価の量的改善）とは
  別軸のアプローチが必要である。

### 第3次（SOT-1791 系列の次段）候補の提案

1. **上位帯メタ対策デッキ / デッキ多様化**：エージェント側が飽和した以上、次はデッキ側の再検討。
   上位帯（matsu 557 / take 575）との差はデッキ構成・対面相性に依存する可能性が高い。
   SOT-1852 のデッキプール再編（4repo）でデッキ側も枯れつつあるが、上位帯特化のメタデッキ探索は未着手。
2. **debate トラックとの統合判断**：`ptcg-agent-debate`（SOT-1832〜1834, champion 0.8217）の
   合議・討論ベース手選択を fable の決定化 MCTS と統合できるか検討。探索の「量」ではなく「手の
   選び方の質」を変える別軸。
3. **上位帯リプレイの敗着解析（開催中の GetEpisodeReplay 復旧待ち）**：SOT-1835 で上位帯 vs
   同格の勝率差（28% vs 47%）と board_wipe 敗着を特定済みだが、盤面評価加点では解けなかった。
   個別リプレイの手順単位の敗着解析（開催中 404 の API 復旧後）で、量的でない構造的欠陥を特定する。

---

## 再現手順

```bash
cd /workspaces/ptcg-agent-fable
bash scripts/build_submission.sh                 # submission.tar.gz 生成・検証
python3 -m unittest tests.test_submission        # 単体 13 tests（クリーン環境）
kaggle competitions submit -c pokemon-tcg-ai-battle -f submission.tar.gz -m "<SHA> ..."
kaggle competitions submissions -c pokemon-tcg-ai-battle   # status / score 確認
```
