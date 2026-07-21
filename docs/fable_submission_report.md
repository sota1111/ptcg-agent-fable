# fable 提出パッケージ & 松竹梅クロス対戦 最終レポート (SOT-1797)

2026-07-21. fable **champion 版**（決定化 MCTS + 竹式ルールポリシー統合 + self-deck-out ガード +
時間予算・多段フォールバック; `main.py` の `FABLE_CONFIG`）を Kaggle 提出パッケージ化し、松竹梅
champion とのクロス対戦で強さを Wilson 95% CI で実証する。

champion は SOT-1795（本体アルゴリズム, PR#3）で確立し、SOT-1796（スクリーニング→確証 A/B, PR#5）
で **全候補が非昇格 → v1 champion 維持**が確定した版。本 Issue はその確定版を対象とする。

---

## 1. 提出パッケージ

`scripts/build_submission.sh` が Kaggle 提出 `submission.tar.gz` を生成・検証する。

### レイアウト（`tar -tzf submission.tar.gz`, 25 エントリ）

```
main.py            # 提出エントリ（agent 関数 + SubmissionAgent）
deck.csv           # SOT-1794 champion デッキ（60 枚, 松の提出デッキと同一リスト）
agents/            # 決定化 MCTS / greedy / rule policy / evaluator / planner ほか
cg/                # cabt エンジン ctypes バインディング + libcg.so/.dll/.dylib
```

- トップレベルに `main.py` + `deck.csv` + `cg/` を同梱（公式 sample 同等レイアウト）。サイズ約 2.0 MB。
- 参考 sha256（ビルド時, tar は mtime を埋めるため再ビルドで変化）:
  `feb64a065660b47475151a48e7812643129d58c5100b349307bc49fdc2fca104`。

### 検証

- **禁止パス除外**: `build_submission.sh` が `.env` / `.git/` / `vendor/` / `tests/` / `eval/` /
  `venv/` / `access_token` / `kaggle.json` / `__pycache__` / `*.pyc` の混入を grep で拒否。合格。
- **推論時ネットワーク/外部API/LLM 呼び出しなし**: `main.py` + `agents/` の import は純標準ライブラリ
  （`hashlib`/`math`/`os`/`random`/`sys`/`time`）のみ。`socket`/`requests`/`urllib`/`http.client` や
  `openai`/`anthropic`/`api_key`/`http(s)://` の参照は 0 件。提出物は **numpy すら不要の純 stdlib**
  で単体動作する（`python3 -c "import main; main.agent"` がシステム python 単独でロード可）。
- **単体動作テスト**: `python3 -m unittest tests.test_submission` — 13 tests **全 pass**
  （FABLE_CONFIG 配線・時間予算ガバナ・フォールバック連鎖 MCTS→Greedy→Rule→raw-legal の到達）。

→ **受け入れ条件① 達成**: `submission.tar.gz` 生成・検証済み。

---

## 2. 松竹梅クロス対戦

`eval/battle_vs.py`（SOT-1681/1795 方式のサブプロセス隔離 — 各 repo の提出 `main.agent` +
自 `deck.csv` を自 repo cwd の `eval/agent_server.py` で分離。top-level `agents` パッケージ名が
repo 間で衝突するため）。先後入替（偶数試合 fable 先手 / 奇数試合 相手先手）。ホストは cg エンジン
（プロセスグローバル単一 battle）のみ保持。エンジン RNG は注入不可のため統計計測。

松は MCTS 激重のため独立 seed シャードに分割して合算（`--aggregate`）。全対戦 raw JSON は
`docs/fable_submission/` に保存。

### 結果（fable = 先攻後攻込みの draws 除外勝率）

| 対戦 | N | fable 勝率 | Wilson 95% CI | fault | 判定 |
| --- | --- | --- | --- | --- | --- |
| fable vs **松** champion | 58 | **0.431** | [0.312, 0.559] | 0 | CI が 0.5 を含む → **統計的互角（同等）** |
| fable vs **竹** champion | 48 | **0.604** | [0.463, 0.730] | 0 | 点推定は有利だが CI が 0.5 を含む → 互角も否定できず |
| fable vs **梅** champion | 48 | **0.708** | [0.568, 0.818] | 0 | CI 下限 > 0.5 → **有意に勝ち** |

- vs 松 = SOT-1795 の 48 戦（`docs/fable_v1/vs_matsu_agg.json`, 同一 champion）＋ 本 Issue の独立
  seed 追検証 10 戦（`vs_matsu_confirm_freshseed.json`, 3-7）の合算 58 戦。追検証単体でも 0.30 と
  松やや優勢側で、48 戦の互角判定（0.458）と整合。
- 全クロス対戦 **154 戦 / fault 0 / 不正手 0 / 未完 0**。最大累計思考時間/試合は fable 32.7s・松 31.6s・
  竹 0.01s・梅 10.8s（許容 600s に対し全て余裕）。

→ **受け入れ条件② 達成**: fable vs 松/竹/梅 の N・勝率・Wilson CI・fault0 レポートが存在。

---

## 3. 「最強（松以上）」の判定

- **松に対して**: fable vs 松 の Wilson CI [0.312, 0.559] は 0.5 を含む。すなわち **fable は松 champion
  と統計的に同等**であり、劣後の有意証拠はない。本 Issue の合格基準「fable ≥ 松（分離勝ち **or 少なくとも
  CI 重複で同等**）」を **CI 重複＝同等**で満たす。
- **梅に対して**: CI 下限 0.568 > 0.5 で **有意に勝ち**。
- **竹に対して**: 点推定 0.604 で有利だが CI 下限 0.463 < 0.5 のため分離せず（互角も否定できない）。

**順位確定条件（全隣接 CI 分離）は未成立。** fable・松・竹は互いに CI が重なる最上位クラスタを形成し、
その下に梅（fable が有意に上）が分離する。よって現時点で言えるのは:

> **fable は松竹梅の最上位クラスタに属し（松と統計的同等、梅より有意に強い）、松に劣後する証拠はない。**
> 一方、松・竹に対する**分離勝ち（strict 最強）は未実証**。

### 原因分析

- fable 本体は松 champion 系譜（SOT-1672/1693 の決定化 MCTS）をベースに竹式ルール統合・deck_low を
  重ねた版。SOT-1795/1796 の A/B で fable 側デルタは **中立**（有意改善なし・champion 維持）と確定済み。
  したがって松との同等は「移植ベースの上限がそのまま出た」結果であり、実装欠陥・スループット劣化の
  兆候はない（ゼロゲート全通過, 探索 150–260 iter/手）。
- 松・竹との CI 分離には、この評価規模（N≈50/対戦, 松は計算コスト上限）では届かない。分離には
  (a) より大きい N（松の高速化 or seed シャード増）と (b) champion 自体の強化が必要。

### 次アクション

- **強さの引き上げ（strict 最強の実証）は SOT-1796 系のスクリーニング→確証 A/B サイクルのスコープ**。
  本 Issue は「提出パッケージ化 + 現 champion のクロス実証」を完了状態とし、松竹との分離勝ちは
  後続改善 Issue（SOT-1796 の再開 or 新規子 Issue）に委ねる。
- 提出物は現 champion で単体動作・fault0 が確認済みのため、提出可能状態。

→ **受け入れ条件③ 達成**: fable は松 champion 以上（CI 重複で同等・劣後証拠なし）を CI 根拠で示し、
strict 最強未達分については原因分析と次アクションを明記。

---

## 再現手順

```bash
# 提出パッケージ
bash scripts/build_submission.sh              # submission.tar.gz を生成・検証
python3 -m unittest tests.test_submission     # 単体動作 13 tests

# クロス対戦（自 repo root から。相手 repo は ../ptcg-agent-<name>）
python3 eval/battle_vs.py --opponent ../ptcg-agent-take --n 16 --json /tmp/take_s1.json
python3 eval/battle_vs.py --opponent ../ptcg-agent-ume  --n 16 --json /tmp/ume_s1.json
python3 eval/battle_vs.py --opponent ../ptcg-agent-matsu --n 10 --json /tmp/matsu_s1.json
python3 eval/battle_vs.py --aggregate /tmp/take_s*.json   # シャード合算 + Wilson CI
```

生 JSON: `docs/fable_submission/{vs_take_agg,vs_ume_agg,vs_matsu_agg,vs_matsu_confirm_freshseed}.json`。
