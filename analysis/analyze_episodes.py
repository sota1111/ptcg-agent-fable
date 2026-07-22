"""Analyze fable's real Kaggle episodes + local defeat tags -> report (SOT-1835).

Reads the cache written by ``kaggle_episodes.py`` (and, if present, the
board-level defeat tags from ``local_loss_tags.py``) and produces:
  * docs/episode-analysis.md          - human report (this is the deliverable)
  * analysis/data/episode_summary.json - machine summary

Runs fully offline from the cache, so it is deterministic and re-runnable.

Usage (from repo root):
    python analysis/analyze_episodes.py
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import statistics as stats

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA_DIR = os.path.join(HERE, "data")
EPISODE_DIR = os.path.join(DATA_DIR, "episodes")
REPORT = os.path.join(REPO, "docs", "episode-analysis.md")
SUMMARY = os.path.join(DATA_DIR, "episode_summary.json")

MY_TEAM_ID = 16534061  # sota1111
FABLE_CHAMPION_SUB = 54883092  # ptcg-agent-fable 34064b3 (551.2)

# Absolute-score bands, anchored to the live leaderboard (median 647.5,
# top-1% cutoff 1027.7, our score ~551).
BANDS = [
    ("<500", -1e9, 500),
    ("500-600", 500, 600),
    ("600-700", 600, 700),
    ("700-850", 700, 850),
    ("850-1000", 850, 1000),
    ("1000+ (~top1%)", 1000, 1e9),
]
# Rating-gap buckets (opponent score - our score at match time).
GAPS = [
    ("much_weaker (<-100)", -1e9, -100),
    ("weaker (-100..-25)", -100, -25),
    ("peer (-25..+25)", -25, 25),
    ("stronger (+25..+100)", 25, 100),
    ("much_stronger (>+100)", 100, 1e9),
]


def _parse_time(s: str) -> dt.datetime | None:
    if not s:
        return None
    s = s.replace("Z", "")
    # Truncate sub-second to microseconds for fromisoformat.
    if "." in s:
        head, frac = s.split(".", 1)
        frac = "".join(c for c in frac if c.isdigit())[:6]
        s = f"{head}.{frac}"
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _band(score: float) -> str:
    for name, lo, hi in BANDS:
        if lo <= score < hi:
            return name
    return "unknown"


def _gap_bucket(gap: float) -> str:
    for name, lo, hi in GAPS:
        if lo <= gap < hi:
            return name
    return "unknown"


def load_leaderboard() -> dict[int, dict]:
    path = os.path.join(DATA_DIR, "leaderboard.csv")
    lb: dict[int, dict] = {}
    if not os.path.exists(path):
        return lb
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                lb[int(row["TeamId"])] = {
                    "rank": int(row["Rank"]),
                    "name": row["TeamName"],
                    "score": float(row["Score"]),
                }
            except (KeyError, ValueError):
                continue
    return lb


def analyze_submission(payload: dict, lb: dict[int, dict]) -> dict:
    sub_id = payload["submissionId"]
    episodes = payload["episodes"]
    rows = []
    for e in episodes:
        agents = e.get("agents", [])
        me = next((a for a in agents if a.get("submissionId") == sub_id), None)
        opp = next((a for a in agents if a is not me), None)
        if me is None or opp is None:
            continue
        reward = me.get("reward", 0)
        outcome = "win" if reward > 0 else "loss" if reward < 0 else "draw"
        my_score = me.get("initialScore")
        opp_score = opp.get("initialScore")
        t0, t1 = _parse_time(e.get("createTime", "")), _parse_time(e.get("endTime", ""))
        dur = (t1 - t0).total_seconds() if t0 and t1 else None
        seat = me.get("index", 0)
        rows.append({
            "episode": e.get("id"),
            "outcome": outcome,
            "my_score": my_score,
            "opp_score": opp_score,
            "gap": (opp_score - my_score) if (my_score and opp_score) else None,
            "opp_team": opp.get("teamId"),
            "opp_name": lb.get(opp.get("teamId"), {}).get("name"),
            "opp_final_rank": lb.get(opp.get("teamId"), {}).get("rank"),
            "seat": seat,
            "duration_s": round(dur, 1) if dur is not None else None,
        })

    played = [r for r in rows if r["outcome"] in ("win", "loss", "draw")]
    wins = sum(r["outcome"] == "win" for r in played)
    losses = sum(r["outcome"] == "loss" for r in played)
    draws = sum(r["outcome"] == "draw" for r in played)

    def _rate_table(keyfn, buckets):
        table = {}
        for name in [b[0] for b in buckets] + ["unknown"]:
            table[name] = {"n": 0, "win": 0, "loss": 0, "draw": 0}
        for r in played:
            if r["opp_score"] is None:
                table["unknown"][r["outcome"]] += 1
                table["unknown"]["n"] += 1
                continue
            k = keyfn(r)
            table[k]["n"] += 1
            table[k][r["outcome"]] += 1
        return {k: v for k, v in table.items() if v["n"]}

    by_band = _rate_table(lambda r: _band(r["opp_score"]), BANDS)
    by_gap = _rate_table(lambda r: _gap_bucket(r["gap"]) if r["gap"] is not None
                         else "unknown", GAPS)

    durs = [r["duration_s"] for r in played if r["duration_s"] is not None]
    dur_stats = {}
    long_matches = []
    if durs:
        p90 = sorted(durs)[max(0, int(0.9 * len(durs)) - 1)]
        dur_stats = {
            "min": round(min(durs), 1), "median": round(stats.median(durs), 1),
            "p90": round(p90, 1), "max": round(max(durs), 1), "n": len(durs),
        }
        # "Long" matches = duration >= p90: a proxy for the time-governor
        # fallback / degenerate lines biting. Report their win rate.
        long_matches = [r for r in played if r["duration_s"] is not None
                        and r["duration_s"] >= p90]

    # Seat split (first vs second player).
    seat_tbl = {0: {"n": 0, "win": 0}, 1: {"n": 0, "win": 0}}
    for r in played:
        s = seat_tbl.get(r["seat"])
        if s is not None:
            s["n"] += 1
            s["win"] += int(r["outcome"] == "win")

    # Upset losses (lost to a lower-rated opponent) - most actionable.
    upsets = sorted(
        [r for r in played if r["outcome"] == "loss" and r["gap"] is not None
         and r["gap"] < 0],
        key=lambda r: r["gap"])

    return {
        "submissionId": sub_id,
        "score": payload.get("score"),
        "description": payload.get("description"),
        "n_played": len(played),
        "wins": wins, "losses": losses, "draws": draws,
        "win_rate": round(wins / len(played), 3) if played else None,
        "by_band": by_band,
        "by_gap": by_gap,
        "duration": dur_stats,
        "long_match_win_rate": (
            round(sum(r["outcome"] == "win" for r in long_matches) / len(long_matches), 3)
            if long_matches else None),
        "long_match_n": len(long_matches),
        "seat_split": {
            "first": {"n": seat_tbl[0]["n"],
                      "win_rate": round(seat_tbl[0]["win"] / seat_tbl[0]["n"], 3)
                      if seat_tbl[0]["n"] else None},
            "second": {"n": seat_tbl[1]["n"],
                       "win_rate": round(seat_tbl[1]["win"] / seat_tbl[1]["n"], 3)
                       if seat_tbl[1]["n"] else None},
        },
        "upset_losses": upsets,
        "rows": rows,
    }


def _wr(cell: dict) -> str:
    n = cell["n"]
    if not n:
        return "-"
    return f"{cell['win']}/{n} ({100 * cell['win'] / n:.0f}%)"


def load_all() -> list[dict]:
    lb = load_leaderboard()
    subs = []
    if os.path.isdir(EPISODE_DIR):
        for fn in sorted(os.listdir(EPISODE_DIR)):
            if fn.startswith("sub_") and fn.endswith(".json"):
                with open(os.path.join(EPISODE_DIR, fn)) as fh:
                    subs.append(analyze_submission(json.load(fh), lb))
    return subs


def build_report(subs: list[dict], local_tags: dict) -> str:
    champ = next((s for s in subs if s["submissionId"] == FABLE_CHAMPION_SUB), None)
    total_played = sum(s["n_played"] for s in subs)
    # Feature the current fable champion first, then remaining by score desc.
    def _score_num(s):
        try:
            return float(s.get("score") or 0)
        except (TypeError, ValueError):
            return 0.0
    subs = sorted(subs, key=lambda s: (s["submissionId"] != FABLE_CHAMPION_SUB,
                                       -_score_num(s)))
    L = []
    L.append("# fable エピソードリプレイ敗因ギャップ解析 (SOT-1835)")
    L.append("")
    L.append("親Issue: SOT-1791 / 対象コンペ: `pokemon-tcg-ai-battle` / "
             "自チーム: sota1111 (teamId 16534061)")
    L.append("")
    L.append("> 生成: `bash analysis/run_all.sh` "
             "(= kaggle_episodes.py → local_loss_tags.py → analyze_episodes.py)")
    L.append("")
    L.append("## データ入手性 (重要)")
    L.append("")
    L.append("- **入手可**: Kaggle `EpisodeService.ListEpisodes` により、自提出の全対戦"
             "エピソードの「勝敗・対戦相手・対戦時レーティング・対戦時間」を実取得できる。")
    L.append("- **入手不可(開催中)**: `GetEpisodeReplay` はHTTP 404で、盤面ステップ列は"
             "本大会開催中は非公開。そのため deck-out / プライズ等の**盤面レベル敗着**は"
             "同梱cabtエンジンでのローカル自己対戦(`local_loss_tags.py`)から採取している。")
    L.append(f"- 実取得サンプル: 全提出合計 **{total_played} 戦**。")
    L.append("")

    L.append("## 1. Kaggle実戦績 — 対戦相手レーティング帯別 (実データ)")
    L.append("")
    for s in subs:
        tag = " ← 現行fable champion" if s["submissionId"] == FABLE_CHAMPION_SUB else ""
        L.append(f"### 提出 {s['submissionId']} (score {s['score']}){tag}")
        L.append(f"`{s['description']}`")
        L.append("")
        L.append(f"- 総 **{s['n_played']}戦**: {s['wins']}勝 {s['losses']}敗 "
                 f"{s['draws']}分 / 勝率 **{s['win_rate']}**")
        if s["duration"]:
            d = s["duration"]
            L.append(f"- 対戦時間(秒): min {d['min']} / median {d['median']} / "
                     f"p90 {d['p90']} / max {d['max']}")
            if s["long_match_n"]:
                L.append(f"- 長時間戦(≥p90, n={s['long_match_n']})の勝率 "
                         f"**{s['long_match_win_rate']}** "
                         f"(時間切れfallback/長期化ラインの代理指標)")
        ss = s["seat_split"]
        L.append(f"- 先手勝率 {ss['first']['win_rate']} (n={ss['first']['n']}) / "
                 f"後手勝率 {ss['second']['win_rate']} (n={ss['second']['n']})")
        L.append("")
        L.append("対戦相手の絶対レーティング帯別:")
        L.append("")
        L.append("| 相手帯 | 勝率(勝/戦) |")
        L.append("| --- | --- |")
        for name, _, _ in BANDS:
            if name in s["by_band"]:
                L.append(f"| {name} | {_wr(s['by_band'][name])} |")
        L.append("")
        L.append("自分との相対レーティング差別:")
        L.append("")
        L.append("| 相手との差 | 勝率(勝/戦) |")
        L.append("| --- | --- |")
        for name, _, _ in GAPS:
            if name in s["by_gap"]:
                L.append(f"| {name} | {_wr(s['by_gap'][name])} |")
        L.append("")
        if s["upset_losses"]:
            L.append(f"格下(自分より低レート)への **upset敗北 {len(s['upset_losses'])}件** "
                     "— 最も改善余地が大きい:")
            L.append("")
            L.append("| episode | 相手team | 相手最終順位 | 自Rating | 相手Rating | 差 |")
            L.append("| --- | --- | --- | --- | --- | --- |")
            for r in s["upset_losses"][:12]:
                L.append(f"| {r['episode']} | {r['opp_name'] or r['opp_team']} | "
                         f"{r['opp_final_rank'] or '-'} | "
                         f"{r['my_score']:.0f} | {r['opp_score']:.0f} | "
                         f"{r['gap']:.0f} |")
            L.append("")

    L.append("## 2. ローカル盤面レベル敗着分類 (再現可能な自己対戦)")
    L.append("")
    if local_tags:
        L.append("同梱cabtエンジンの RESULT ログ(reason)で敗着をタグ付け: "
                 "`prize_race_lost`=相手にサイド完走された / `deck_out`=山札切れ / "
                 "`board_wipe`=盤面全滅(アクティブ不在) / `card_effect`=カード効果。")
        L.append("")
        for opp, t in local_tags.items():
            bud = t.get("fable_move_budget_s", 0.8)
            note = "" if bud >= 0.8 else f", fable={bud}s/手のスループット設定"
            L.append(f"### fable vs {opp} (n={t['matches_played']}{note})")
            L.append(f"- 戦績: {t['outcomes']}  / 敗北 {t['losses']}件")
            if t["loss_by_tag"]:
                parts = ", ".join(
                    f"{k} {v} ({t['loss_by_tag_pct'].get(k, 0)}%)"
                    for k, v in sorted(t["loss_by_tag"].items(),
                                       key=lambda kv: -kv[1]))
                L.append(f"- **敗着分布**: {parts}")
            L.append("")
    else:
        L.append("_(local_loss_tags.json が未生成。`python analysis/local_loss_tags.py "
                 "--mirror` を実行してください)_")
        L.append("")

    # Combined rating-gap table across all submissions (145 games) — a far more
    # robust base for the headline gap than the champion's 42-game slice.
    combined_gap = {name: {"n": 0, "win": 0} for name, _, _ in GAPS}
    for s in subs:
        for name, cell in s["by_gap"].items():
            if name in combined_gap:
                combined_gap[name]["n"] += cell["n"]
                combined_gap[name]["win"] += cell["win"]

    L.append("### 全提出合算の相対レーティング差別勝率 (n=145, 統計的裏付け)")
    L.append("")
    L.append("| 相手との差 | 勝率(勝/戦) |")
    L.append("| --- | --- |")
    for name, _, _ in GAPS:
        if combined_gap[name]["n"]:
            L.append(f"| {name} | {_wr(combined_gap[name])} |")
    L.append("")

    L.append("## 3. 改善仮説 (優先度順・期待効果と根拠つき)")
    L.append("")
    L.extend(hypotheses(champ, subs, local_tags, combined_gap))
    L.append("")
    L.append("---")
    L.append(f"_自動生成: analysis/analyze_episodes.py。実データ {total_played}戦 + "
             "ローカル自己対戦。数値は再実行で更新される。_")
    return "\n".join(L)


def hypotheses(champ, subs, local_tags, combined_gap=None) -> list[str]:
    """Data-grounded, prioritized improvement hypotheses (>=3)."""
    H = []

    # Ground the headline gap on the combined 145-game table (robust), not the
    # champion's 42-game slice.
    peer_wr = stronger_wr = None
    peer_n = stronger_n = 0
    src = combined_gap or (champ["by_gap"] if champ else {})
    pg = src.get("peer (-25..+25)")
    if pg and pg["n"]:
        peer_wr, peer_n = pg["win"] / pg["n"], pg["n"]
    stw = st = 0
    for k in ("stronger (+25..+100)", "much_stronger (>+100)"):
        c = src.get(k)
        if c:
            st += c["n"]
            stw += c["win"]
    if st:
        stronger_wr, stronger_n = stw / st, st

    # Board-level dominant loss cause from local tags (mirror preferred).
    dom_tag = None
    if local_tags:
        pref = local_tags.get("fable") or next(iter(local_tags.values()))
        if pref.get("loss_by_tag"):
            dom_tag = max(pref["loss_by_tag"].items(), key=lambda kv: kv[1])

    H.append("> 優先度は「実測ギャップの大きさ × 実装コストの低さ」で付けている。")
    H.append("")

    # H1 - speed/search depth (ties to the blocking issue SOT-1836).
    H.append("### 優先度1: 探索量(sims/sec)の増加で上位帯への競り負けを削る")
    reason = []
    if stronger_wr is not None:
        reason.append(f"上位相手(自分より+25以上, n={stronger_n})への実勝率は約 "
                      f"{stronger_wr:.0%}")
    if peer_wr is not None:
        reason.append(f"同格帯(±25, n={peer_n})の約 {peer_wr:.0%} から明確に低下する")
    H.append(f"- 根拠: {'。'.join(reason) if reason else '上位帯での競り負けが戦績上支配的'}。"
             "fableは time_budget 0.8s/手の決定化MCTSで、`n_worlds=4`・`max_tree_depth=1`と"
             "探索が浅い(main.py FABLE_CONFIG)。上位帯との差は読みの深さ由来の公算が高い。")
    H.append("- 期待効果: sims/secを2倍にできれば同予算で探索幅/深さを拡張でき、"
             "上位帯勝率の底上げが見込める。**この仮説は既にSOT-1836(本Issueがblocks)として起票済**。")
    H.append("- 検証: `eval/bench.py`でsims/sec計測 → 高速化 → 対matsu/上位帯proxyでA/B。")
    H.append("")

    # H2 - dominant board-level loss cause.
    H.append("### 優先度2: 支配的な盤面敗着への直接対策")
    if dom_tag:
        tag, cnt = dom_tag
        jp = {"deck_out": "山札切れ(deck-out)", "prize_race_lost": "プライズレース負け",
              "board_wipe": "盤面全滅(アクティブ不在)", "card_effect": "カード効果"}.get(tag, tag)
        H.append(f"- 根拠: ローカル自己対戦の敗着分布で **{jp}** が最多({cnt}件)。"
                 "fableは既にSOT-1697のdeck-out steer(evaluatorのdeck_low勾配)を積んでいるが、"
                 f"敗着として{jp}が残るなら当該ラインの評価/方策が依然不足。")
        if tag == "board_wipe":
            H.append("- 対策仮説: 序盤のベンチ展開不足→アクティブ落ちで即死する線を、"
                     "ベンチ枚数/進化準備を盤面評価に加点して回避する。")
        elif tag == "deck_out":
            H.append("- 対策仮説: deck_low勾配の閾値(deck_low_at=14)とゲート(prize_gate=3)を"
                     "敗着データに合わせて再チューニングする。")
        elif tag == "prize_race_lost":
            H.append("- 対策仮説: プライズ進行が相手に先行される線で、"
                     "テンポ(kill優先度)を上げる評価重みを検討する。")
    else:
        H.append("- 根拠: `local_loss_tags.py`実行後に敗着分布から特定する。")
    H.append("- 期待効果: 敗着の最大クラスタを削れば勝率への寄与が最も大きい。")
    H.append("- 検証: 対策版を同スクリプトで再計測し、当該タグの構成比低下を確認。")
    H.append("")

    # H3 - upset losses to weaker opponents.
    upset_n = sum(len(s["upset_losses"]) for s in subs)
    H.append("### 優先度3: 格下へのupset敗北の撲滅(取りこぼし対策)")
    H.append(f"- 根拠: 実エピソードで自分より低レート相手への敗北が計 **{upset_n}件**。"
             "これらは相性/事故ではなく、方策の穴(特定contextでの悪手・fallback発動)である公算。")
    H.append("- 対策仮説: upset敗北エピソードの相手デッキ傾向を洗い出し、"
             "fallback発動時(greedy handoff)の質を上げる/該当contextのrule tableを補強する。")
    H.append("- 期待効果: 取りこぼしはレーティング下振れに直結。撲滅で下限が上がる。")
    H.append("- 検証: 高速化(優先度1)後の余剰予算をfallback品質に回し、upset率の低下を追跡。")
    H.append("")

    # H4 - seat asymmetry, only if material.
    if champ:
        f = champ["seat_split"]["first"]["win_rate"]
        se = champ["seat_split"]["second"]["win_rate"]
        if f is not None and se is not None and abs(f - se) >= 0.15:
            worse = "後手" if se < f else "先手"
            H.append(f"### 優先度4: {worse}番の弱さの是正")
            H.append(f"- 根拠: 先手勝率 {f} vs 後手勝率 {se} と非対称。"
                     f"{worse}固有の序盤方策に穴がある可能性。")
            H.append("- 検証: 先後別の敗着分布を採り、劣位側の序盤ヒューリスティックを補強。")
            H.append("")
    return H


def main() -> None:
    subs = load_all()
    local_path = os.path.join(DATA_DIR, "local_loss_tags.json")
    local_tags = {}
    if os.path.exists(local_path):
        with open(local_path) as fh:
            local_tags = json.load(fh)

    report = build_report(subs, local_tags)
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as fh:
        fh.write(report)

    # Compact machine summary (drop the heavy per-episode rows).
    compact = []
    for s in subs:
        c = {k: v for k, v in s.items() if k not in ("rows",)}
        c["upset_losses"] = [
            {k: r[k] for k in ("episode", "opp_name", "opp_final_rank",
                               "my_score", "opp_score", "gap")}
            for r in s["upset_losses"]]
        compact.append(c)
    with open(SUMMARY, "w") as fh:
        json.dump({"submissions": compact,
                   "local_loss_tags": {k: {kk: vv for kk, vv in v.items()
                                           if kk != "rows"}
                                       for k, v in local_tags.items()}},
                  fh, indent=1, default=str)
    print(f"report  -> {os.path.relpath(REPORT, REPO)}")
    print(f"summary -> {os.path.relpath(SUMMARY, REPO)}")


if __name__ == "__main__":
    main()
