# 上位帯リプレイ敗着解析 → prior設計仮説 (SOT-1894)

データ: `docs/replay_loss_analysis.md` / `analysis/data/replay_summary.json`
（実Kaggle敗北121件・手順単位、生成コマンドは同レポート冒頭を参照）。

## 前提となる事実（手順単位解析の要約）

1. **敗着の88% (107/121) は盤面全滅 (board_wipe)** — active KO時にベンチ0で即敗北。
   プライズレース負け（相手6枚先取）は観測されたスナップショット上ほぼ存在しない。
2. wipe のうち **92%は最終決定時に手札にたねが1枚もない**（= その瞬間の選択では防げない資源枯渇）。
   決定点で「ベンチに出せるたねを見送って ATTACK/END した」wipe は 10件 (9%) に留まる。
3. **56%はたね0のまま進化カード（Mega Abomasnow ex）だけ握って敗北**（dead evolutions）。
4. **36%は双方プライズ0のセットアップ負け**（短期戦中心）、一方 **36%はプライズリードを
   持ったまま wipe 負け**（全敗北では late reversal 63%）。相手は最終盤面でベンチ3枚以上が72%。
5. デッキ構造上の制約: 現championデッキのたねは **60枚中6枚のみ**（Kyogre×2, Snover×4）。
   サーチ札 (Cyrano / Mega Signal) が取れるのは **ex進化側 (723) だけ** で、たねはドロー
   （Lillie's Determination）でしか増えない。

読み: fable は「単騎で殴ってレースには勝つが、6枚しかない身体資源を維持できず、
1回のKOで即死する形」を探索が過大評価している。SOT-1863（ベンチ展開の盤面加点、非昇格）が
効かなかったのは、敗着の主因が **展開の選択ミスではなく、盤面枯渇リスクの評価不在** だから。

## prior設計仮説（SOT-1892 の action-prior / rollout フックに載せる）

### 仮説H1: ベンチ0状態の生存リスクを rollout/葉評価に入れる（state-value軸）

- 内容: ベンチ0の局面は、プライズリードに関係なく「active KO 1回で敗北」の脆弱状態。
  rollout/葉評価に **bench==0 ペナルティ（相手盤面の攻撃能力でスケール）** を加え、
  探索全体が「1体残す」ラインを選好するようにする。
- 根拠: 事実1・4（リード保持のまま wipe 36%、late reversal 63%）。
- SOT-1863 との違い: 行動への flat な展開加点ではなく、状態価値のリスク項として木全体に効かせる。

### 仮説H2: たね温存・即ベンチの action prior（decision-point軸）

- 内容: (a) ベンチ0で MAIN に PLAY(たね) が提示されたら、ATTACK/END より強い prior を付けて
  実質必着にする（見送り22回・10敗北ぶんの直接取り返し）。
  (b) 効果による手札/山札選択 (DISCARD / TO_DECK 系) では、たねを捨てない選択に prior を寄せる
  （6枚しかない身体資源の保全）。
- 根拠: 事実2の「9%は決定点で防げた」+ 事実5（資源が構造的に希少）。
- 期待効果は wipe の1割前後が上限（92%は決定点に選択肢がない）— H1/H3 との併用が前提。

### 仮説H3: 盤面が薄いときの development-first 手順 prior（sequencing軸）

- 内容: ベンチ0 or 手札にたね0のとき、攻撃続行より **ドロー系サポーター
  (Lillie's Determination) / サーチ (Cyrano→ex確保, Mega Signal)** を先に切って身体と進化先を
  確保する手順に prior を寄せる。Snover が場にいて 723 が手札にあるなら即進化
  （350hp が壁になり wipe 連鎖を止める）。
- 根拠: 事実3（dead evolutions 56%）・事実4（セットアップ負け36%・相手ベンチ3+が72%）。
  上位帯の相手はドローを回して盤面を作り切ってから殴ってくる（相手デッキ残15 vs 当方41の
  観測例あり）— fable の rollout はこの発展速度を過小評価している。

## 検証経路（このIssueでは実装しない — 昇格ゲートの定義のみ）

1. **実装**: 各仮説を SOT-1892 と同じ **opt-in フラグ**（環境変数 / `FABLE_TACTICS` 系）で
   fable-MCTS の prior / rollout フックに実装する。champion の既定動作は変えない。
2. **screen**: small-N（例 N=50）で候補 vs 現champion。勝率が有意に劣るものは足切り。
3. **confirm**: 大きめN で **Wilson CI 下限 > 0.5 のみ昇格**。届かなければ非昇格
   （behavior revert + docs のみPR化、champion維持）— SOT-1863/1864/1865/1892 と同一ゲート。
4. **Kaggle実証**: 昇格時のみ exec互換ゲート（`docs/ai/kaggle-exec-runtime-gate` 相当）を通して
   再提出し、収束スコア（ep≥40・直近10帯±20pt・W/L均衡）で判定する。
5. **既知の限界**: ローカル対戦プールは上位帯の「発展重視メタ」を再現しきれない可能性がある
   （fable はローカル 0.82 勝率だが Kaggle 上位帯に負け越し）。confirm 非昇格でも
   wipe率・平均ベンチ数などの **プロセスKPI**（`analyze_replays.py` の指標をローカル対戦に
   適用）を併記し、Kaggle実証まで判定を保留する選択肢を残す。
   なお SOT-1896 の league KPI ゲート（matsu/take/ume/zero総当たり）は fable 起動不可のため
   使えない — fable の検証は本リポジトリの screen→confirm ハーネスで行う。
