"""Step-level defeat analysis of fable's real Kaggle losses (SOT-1894).

Walks the cached kaggle-environments replays (``kaggle_replays.py``) move by
move and, per lost episode, finds the *decisive break*: the last step where
fable was still even (or ahead) in the prize race before the deficit became
permanent. Around that break it extracts what fable was actually choosing
(select context, chosen option types, available-but-unchosen alternatives)
and rolls the per-episode findings up into defeat *patterns* that SOT-1892's
action-prior design can target.

SOT-1835 stopped at episode granularity (win/loss vs rating band, local
reason tags); this module is the step-granularity ("手順単位") continuation on
**real** upper-band games.

Inputs  (produced by kaggle_replays.py):
    data/replay_manifest.json      loss rows incl. our seat + opponent rating
    data/replays/<episodeId>.json  raw replays (not committed)
    data/episodes/sub_*.json       team names for opponent labels

Outputs:
    data/replay_summary.json       machine-readable per-episode + aggregate
    ../docs/replay_loss_analysis.md  generated data report

Usage (from repo root):
    python analysis/analyze_replays.py
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA_DIR = os.path.join(HERE, "data")
REPLAY_DIR = os.path.join(DATA_DIR, "replays")
sys.path.insert(0, REPO)

from agents.observation import adapt  # noqa: E402 - repo import after path fix

# cg/api.py enum names for readable reports; the engine package is local-only
# (license-gated), so degrade to numeric labels without it.
try:
    from cg.api import SelectContext, OptionType

    def _ctx_name(v):
        try:
            return SelectContext(v).name
        except ValueError:
            return f"CTX_{v}"

    def _opt_name(v):
        try:
            return OptionType(v).name
        except ValueError:
            return f"OPT_{v}"
except Exception:  # noqa: BLE001 - enum names are cosmetic
    def _ctx_name(v):
        return f"CTX_{v}"

    def _opt_name(v):
        return f"OPT_{v}"

OPT_ATTACK, OPT_END, OPT_RETREAT, OPT_ATTACH = 13, 14, 12, 8
RESULT_REASON = {1: "prize_race_lost", 2: "deck_out",
                 3: "board_wipe", 4: "card_effect"}


def team_names() -> dict:
    names = {}
    for path in glob.glob(os.path.join(DATA_DIR, "episodes", "sub_*.json")):
        with open(path) as fh:
            for t in json.load(fh).get("teams", []):
                names[t["id"]] = t.get("teamName", "?")
    return names


def _terminal_reason(steps, our_index: int, timeline: list[dict]) -> int | None:
    """RESULT.reason (LogType 23), else inferred from the final board state.

    The public CDN replays never deliver the RESULT log (the episode ends
    before a final observation carrying it is written), so in practice the
    reason comes from the board-state fallback: opponent finished their
    prizes / our deck hit 0 / our board was wiped (no bench behind the
    KO'd active). The labels match the engine's RESULT reason codes.
    """
    for step in reversed(steps[-3:]):
        for agent in step:
            for lg in (agent.get("observation", {}).get("logs") or ()):
                if lg.get("type") == 23:
                    return lg.get("reason")
    if timeline:
        last = timeline[-1]
        if last["taken_opp"] >= 6:
            return 1
        if last["deck_us"] <= 0:
            return 2
        if last["bench_us"] == 0:
            return 3
    return None


def _freshest(step) -> dict | None:
    """The most up-to-date board this step.

    kaggle-environments only refreshes the observation of the seat that is
    ACTIVE; the other seat keeps a stale copy (a turn behind, or frozen once
    the game ends). ``current.players`` is absolute-seat-indexed and the
    board metrics we need are public, so any seat's freshest view works.
    """
    best = None
    for agent in step:
        cur = (agent.get("observation") or {}).get("current")
        if cur and (best is None or (cur.get("turn") or 0) >= (best.get("turn") or 0)):
            best = cur
    return best


def _timeline(steps, our_index: int) -> list[dict]:
    """Per-step board metrics, read from the freshest seat's observation."""
    rows = []
    for t, step in enumerate(steps):
        cur = _freshest(step)
        players = (cur or {}).get("players") or []
        if len(players) != 2:
            continue
        me, opp = players[our_index], players[1 - our_index]
        rows.append({
            "step": t,
            "turn": cur.get("turn") or 0,
            "taken_us": max(0, 6 - len(me.get("prize") or ())),
            "taken_opp": max(0, 6 - len(opp.get("prize") or ())),
            "deck_us": me.get("deckCount", 0) or 0,
            "deck_opp": opp.get("deckCount", 0) or 0,
            "bench_us": len(me.get("bench") or ()),
            "bench_opp": len(opp.get("bench") or ()),
        })
    return rows


def _decisive(timeline: list[dict]) -> dict:
    """The prize-race point of no return.

    lead = prizes we took − prizes they took. The decisive break is the last
    step with lead >= 0; every later step is strictly negative. Episodes we
    never trailed in (deck-out/board-wipe with even prizes) fall back to the
    final step.
    """
    lead = [r["taken_us"] - r["taken_opp"] for r in timeline]
    decisive_i = len(timeline) - 1
    for i in range(len(timeline) - 1, -1, -1):
        if lead[i] >= 0:
            decisive_i = i
            break
    max_lead = max(lead) if lead else 0
    # opponent prize jumps of >=2 inside one step transition (EX/V KO)
    multi_ko = [
        {"step": timeline[i]["step"], "turn": timeline[i]["turn"],
         "prizes": timeline[i]["taken_opp"] - timeline[i - 1]["taken_opp"]}
        for i in range(1, len(timeline))
        if timeline[i]["taken_opp"] - timeline[i - 1]["taken_opp"] >= 2
    ]
    return {
        "decisive_step": timeline[decisive_i]["step"] if timeline else None,
        "decisive_turn": timeline[decisive_i]["turn"] if timeline else None,
        "final_lead": lead[-1] if lead else 0,
        "max_lead": max_lead,
        "late_reversal": max_lead >= 1,
        "never_led": max_lead <= 0,
        "multi_prize_kos_conceded": multi_ko,
        "bench_us_at_break": timeline[decisive_i]["bench_us"] if timeline else 0,
    }


def _decisions(steps, our_index: int, turn_lo: int, turn_hi: int) -> list[dict]:
    """Our decisions whose turn falls in [turn_lo, turn_hi].

    steps[t][i].status == ACTIVE means agent i must answer the select posed by
    its observation at step t; the chosen indices appear in steps[t+1][i].action.
    """
    out = []
    for t in range(len(steps) - 1):
        me = steps[t][our_index]
        if me.get("status") != "ACTIVE":
            continue
        obs = me.get("observation", {})
        if "current" not in obs:
            continue
        view = adapt(obs)
        if view.select is None or not (turn_lo <= view.turn <= turn_hi):
            continue
        action = steps[t + 1][our_index].get("action") or []
        chosen = [view.select.options[a] for a in action
                  if isinstance(a, int) and 0 <= a < len(view.select.options)]
        avail_types = {o.type for o in view.select.options}
        row = {
            "step": t,
            "turn": view.turn,
            "context": _ctx_name(view.select.context),
            "n_options": len(view.select.options),
            "chosen_types": [_opt_name(c.type) for c in chosen],
            "chosen_attack_ids": [c.raw.get("attackId") for c in chosen
                                  if c.type == OPT_ATTACK],
        }
        if view.select.context == 0:  # MAIN: passivity/diversity flags
            row["attack_available"] = OPT_ATTACK in avail_types
            row["chose_end"] = any(c.type == OPT_END for c in chosen)
        out.append(row)
    return out


def _bench_declines(steps, our_index: int) -> list[dict]:
    """Turn-ending choices that declined a free bench while the bench was empty.

    A MAIN select where fable chose ATTACK or END (both end the turn's
    development), its bench was empty, and a PLAY option for a *basic*
    Pokémon in hand was simultaneously on offer. Each row is a concrete
    "one KO from losing, chose not to insure" decision — the step-level
    mechanism behind the board-wipe losses.
    """
    from agents.cards import shared_index

    idx = shared_index()
    out = []
    for t in range(len(steps) - 1):
        me = steps[t][our_index]
        if me.get("status") != "ACTIVE":
            continue
        obs = me.get("observation", {})
        cur = obs.get("current")
        if not cur:
            continue
        view = adapt(obs)
        if view.select is None or view.select.context != 0:  # MAIN only
            continue
        players = cur.get("players") or []
        if len(players) != 2 or len(players[our_index].get("bench") or ()) > 0:
            continue
        hand = players[our_index].get("hand") or []
        basic_opts = set()
        for i, o in enumerate(view.select.options):
            if o.type != 7:  # PLAY
                continue
            hi = o.raw.get("index")
            if isinstance(hi, int) and 0 <= hi < len(hand):
                cid = (hand[hi] or {}).get("id")
                if cid is not None and idx.card(cid).basic:
                    basic_opts.add(i)
        if not basic_opts:
            continue
        action = steps[t + 1][our_index].get("action") or []
        chosen = [view.select.options[a] for a in action
                  if isinstance(a, int) and 0 <= a < len(view.select.options)]
        if any(a in basic_opts for a in action if isinstance(a, int)):
            continue  # benched — no decline
        if any(c.type in (OPT_ATTACK, OPT_END) for c in chosen):
            out.append({"step": t, "turn": view.turn,
                        "chose": [_opt_name(c.type) for c in chosen]})
    return out


def _final_hand(steps, our_index: int) -> dict:
    """Hand composition at our LAST fresh decision (basics vs dead evolutions)."""
    from agents.cards import shared_index

    idx = shared_index()
    for t in range(len(steps) - 1, -1, -1):
        me = steps[t][our_index]
        cur = (me.get("observation") or {}).get("current")
        if me.get("status") != "ACTIVE" or not cur:
            continue
        hand = (cur.get("players") or [{}, {}])[our_index].get("hand") or []
        feats = [idx.card(c.get("id")) for c in hand if c]
        return {"basics_in_hand": sum(1 for f in feats if f.basic),
                "evolutions_in_hand": sum(1 for f in feats
                                          if f.stage1 or f.stage2)}
    return {"basics_in_hand": 0, "evolutions_in_hand": 0}


def analyze_episode(row: dict, names: dict) -> dict | None:
    path = os.path.join(REPLAY_DIR, f"{row['episode_id']}.json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        replay = json.load(fh)
    steps = replay.get("steps") or []
    if not steps:
        return None
    our_index = row.get("our_index", 0)
    # Belt-and-braces: the replay names the teams; ours is sota1111.
    team_list = (replay.get("info") or {}).get("TeamNames") or []
    if "sota1111" in team_list:
        our_index = team_list.index("sota1111")

    timeline = _timeline(steps, our_index)
    dec = _decisive(timeline)
    lo = max(1, (dec["decisive_turn"] or 1) - 1)
    hi = (dec["decisive_turn"] or 1) + 1
    decisions = _decisions(steps, our_index, lo, hi)
    reason = _terminal_reason(steps, our_index, timeline)

    mains = [d for d in decisions if d["context"] == _ctx_name(0)]
    passive = [d for d in mains if d.get("chose_end") and d.get("attack_available")]
    declines = _bench_declines(steps, our_index)
    return {
        "bench_declines": declines,
        "bench_declines_by_break": sum(
            1 for d in declines
            if dec["decisive_turn"] is None or d["turn"] <= dec["decisive_turn"] + 1),
        "episode_id": row["episode_id"],
        "opponent": names.get(row.get("opp_team_id"), "?"),
        "opp_rating": round(row.get("opp_rating") or 0),
        "our_rating": round(row.get("our_rating") or 0),
        "reason": RESULT_REASON.get(reason, f"reason_{reason}"),
        "turns": timeline[-1]["turn"] if timeline else 0,
        **dec,
        "decisions_at_break": decisions,
        "end_with_attack_available": len(passive),
        "deck_us_final": timeline[-1]["deck_us"] if timeline else None,
        "final_taken_us": timeline[-1]["taken_us"] if timeline else 0,
        "final_taken_opp": timeline[-1]["taken_opp"] if timeline else 0,
        "bench_opp_final": timeline[-1]["bench_opp"] if timeline else 0,
        **_final_hand(steps, our_index),
    }


def _wipe_mechanism(wipes: list[dict]) -> dict:
    """Why the board-wipe losses happened, mechanically."""
    n = len(wipes)
    if not n:
        return {"episodes": 0}
    return {
        "episodes": n,
        "ended_0_0_rate":
            round(sum(1 for r in wipes if r["final_taken_us"] == 0
                      and r["final_taken_opp"] == 0) / n, 3),
        "we_led_on_prizes_rate":
            round(sum(1 for r in wipes
                      if r["final_taken_us"] > r["final_taken_opp"]) / n, 3),
        "no_basic_in_hand_rate":
            round(sum(1 for r in wipes if r["basics_in_hand"] == 0) / n, 3),
        "dead_evolution_in_hand_rate":
            round(sum(1 for r in wipes if r["basics_in_hand"] == 0
                      and r["evolutions_in_hand"] > 0) / n, 3),
        "opp_bench_3plus_rate":
            round(sum(1 for r in wipes if r["bench_opp_final"] >= 3) / n, 3),
    }


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    reasons, contexts = {}, {}
    for r in rows:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
        for d in r["decisions_at_break"]:
            contexts[d["context"]] = contexts.get(d["context"], 0) + 1
    return {
        "losses_analyzed": n,
        "loss_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "multi_prize_ko_conceded_rate":
            round(sum(1 for r in rows if r["multi_prize_kos_conceded"]) / n, 3)
            if n else 0,
        "late_reversal_rate":
            round(sum(1 for r in rows if r["late_reversal"]) / n, 3) if n else 0,
        "never_led_rate":
            round(sum(1 for r in rows if r["never_led"]) / n, 3) if n else 0,
        "mean_bench_at_break":
            round(sum(r["bench_us_at_break"] for r in rows) / n, 2) if n else 0,
        "end_with_attack_available_episodes":
            sum(1 for r in rows if r["end_with_attack_available"]),
        "bench_decline_episodes":
            sum(1 for r in rows if r["bench_declines"]),
        "bench_decline_episodes_board_wipe":
            sum(1 for r in rows
                if r["bench_declines"] and r["reason"] == "board_wipe"),
        "bench_declines_total":
            sum(len(r["bench_declines"]) for r in rows),
        "board_wipe_mechanism": _wipe_mechanism(
            [r for r in rows if r["reason"] == "board_wipe"]),
        "break_decision_contexts":
            dict(sorted(contexts.items(), key=lambda kv: -kv[1])),
    }


def write_report(rows: list[dict], agg: dict, avail: dict) -> str:
    upper = [r for r in rows if r["opp_rating"] >= r["our_rating"] - 25]
    lines = [
        "# fable 上位帯リプレイ手順単位敗着解析 (SOT-1894)",
        "",
        "生成: `python analysis/kaggle_replays.py && python analysis/analyze_replays.py`",
        "",
        "## データ可用性の記録 (受け入れ条件1)",
        "",
        f"- 確認日時: {avail.get('checked_at', '?')} / probe episode "
        f"{avail.get('probe_episode_id', '?')}",
        f"- 内部RPC `GetEpisodeReplay`: **HTTP "
        f"{avail.get('rpc_GetEpisodeReplay', {}).get('status', '?')}** (未復旧のまま)",
        f"- 公開エンドポイント `kaggleusercontent.com/episodes/<id>.json`: **HTTP "
        f"{avail.get('public_endpoint', {}).get('status', '?')}** — "
        "kaggle-environments形式のフルリプレイ (steps/board/select/action) が取得可能",
        "- 判定: **手順単位データは公開エンドポイント経由で入手可** — 本Issueのデータ待ちゲートは開通。",
        "",
        "## 対象データ",
        "",
        f"- 解析した敗北エピソード: **{agg['losses_analyzed']}** 件"
        f"（うち相手が同格以上 {len(upper)} 件）",
        "",
        "## 敗着の一次分類 (エンジンRESULT理由)",
        "",
        "| 理由 | 件数 |",
        "| --- | --- |",
    ]
    for k, v in agg["loss_reasons"].items():
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "## 手順単位の敗着シグナル (全敗北)",
        "",
        f"- 2枚以上プライズを一度に献上 (multi-prize KO被弾) があった敗北: "
        f"**{agg['multi_prize_ko_conceded_rate']:.0%}**",
        f"- リードを持ちながら逆転負け (late reversal): "
        f"**{agg['late_reversal_rate']:.0%}**",
        f"- 一度もリードできず敗北 (never led): **{agg['never_led_rate']:.0%}**",
        f"- 決定的ブレーク時点の平均ベンチ数: **{agg['mean_bench_at_break']}**",
        f"- ブレーク周辺で攻撃可能なのにターン終了を選んだ敗北: "
        f"**{agg['end_with_attack_available_episodes']}** 件",
        "",
        "## 盤面全滅 (board_wipe) のメカニズム分解",
        "",
        "wipe = active KO時にベンチ0で即敗北。その時フェイブルに「選択の余地」があったか:",
        "",
        f"- ベンチ0でMAINにPLAY(たね)が提示されていたのにATTACK/ENDで見送った敗北: "
        f"**{agg['bench_decline_episodes']}** 件 / 見送り総数 "
        f"{agg['bench_declines_total']} 回（= 決定点で防げた可能性がある wipe）",
    ]
    wm = agg.get("board_wipe_mechanism", {})
    if wm.get("episodes"):
        lines += [
            f"- wipe {wm['episodes']} 件のうち、最終決定時に手札にたねが1枚もない: "
            f"**{wm['no_basic_in_hand_rate']:.0%}**（= 決定点では防げない資源枯渇）",
            f"- たね0のまま進化カードだけ握って死んでいる (dead evolutions): "
            f"**{wm['dead_evolution_in_hand_rate']:.0%}**",
            f"- 双方プライズ0のまま終了 (セットアップ負け): **{wm['ended_0_0_rate']:.0%}**",
            f"- プライズレースはリードしたまま wipe 負け: "
            f"**{wm['we_led_on_prizes_rate']:.0%}**",
            f"- 相手は最終盤面でベンチ3枚以上: **{wm['opp_bench_3plus_rate']:.0%}**",
        ]
    lines += [
        "",
        "ブレーク周辺 (決定的ターン±1) の選択コンテキスト分布:",
        "",
        "| context | 回数 |",
        "| --- | --- |",
    ]
    for k, v in agg["break_decision_contexts"].items():
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "## エピソード別の決定的ブレーク",
        "",
        "decisive turn = プライズレースで最後に同点以上だったターン（以降は差が戻らない）。",
        "",
        "| episode | 相手 (rating) | 理由 | 総ターン | 決定タ-ン | max lead | "
        "multi-prize被弾 | ベンチ数@break |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(rows, key=lambda r: -r["opp_rating"]):
        mk = ",".join(f"T{m['turn']}x{m['prizes']}"
                      for m in r["multi_prize_kos_conceded"]) or "-"
        lines.append(
            f"| {r['episode_id']} | {r['opponent']} ({r['opp_rating']}) "
            f"| {r['reason']} | {r['turns']} | {r['decisive_turn']} "
            f"| {r['max_lead']} | {mk} | {r['bench_us_at_break']} |")
    lines += [
        "",
        "prior設計仮説と検証経路: `docs/replay_prior_hypotheses.md` を参照。",
        "",
    ]
    path = os.path.join(REPO, "docs", "replay_loss_analysis.md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
    return path


def main() -> int:
    manifest_path = os.path.join(DATA_DIR, "replay_manifest.json")
    if not os.path.exists(manifest_path):
        print("No replay manifest — run analysis/kaggle_replays.py first.")
        return 1
    with open(manifest_path) as fh:
        manifest = json.load(fh)
    with open(os.path.join(DATA_DIR, "replay_availability.json")) as fh:
        avail = json.load(fh)

    names = team_names()
    rows = [r for r in (analyze_episode(row, names)
                        for row in manifest.get("losses", [])) if r]
    agg = aggregate(rows)
    out = {"aggregate": agg, "episodes": rows}
    with open(os.path.join(DATA_DIR, "replay_summary.json"), "w") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    report = write_report(rows, agg, avail)
    print(f"Analyzed {len(rows)} lost episode(s) -> "
          f"{os.path.relpath(report, REPO)} + analysis/data/replay_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
