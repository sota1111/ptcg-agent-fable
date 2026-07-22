"""Board-level defeat-cause tagging for fable via local self-play (SOT-1835).

Kaggle gates the step-by-step replay while the competition is live, so we
reproduce the *board-level* defeat causes locally: play the champion fable
agent (``main.agent``, exactly the submitted entry point) against a baseline
opponent through the same cabt engine used on Kaggle, and read the terminal
``RESULT`` log, whose ``reason`` code names how the match ended.

RESULT.reason (cg/api.py, LogType 23):
    1 = opponent reached 0 prize cards   -> prize-race loss (相手にサイド完走された)
    2 = you start a turn with 0 deck cards -> deck-out (山札切れ)
    3 = you have no Pokémon in the Active Spot -> board wipe (盤面全滅)
    4 = a card effect ended the game

Both agents come from fable's own ``agents`` package, so there is no
cross-repo module collision (unlike eval/battle_vs.py which needs a subprocess
per repo). Opponent defaults to fable's bundled GreedyAgent; ``--mirror`` plays
fable vs fable so every match yields a fable loss on one seat.

Output: analysis/data/local_loss_tags.json (aggregate + per-match rows).

Usage (from repo root):
    python analysis/local_loss_tags.py --n 40
    python analysis/local_loss_tags.py --n 40 --mirror --budget 90
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DATA_DIR = os.path.join(HERE, "data")
MAX_STEPS = 20000

REASON_TAG = {
    1: "prize_race_lost",   # opponent completed their prizes
    2: "deck_out",          # started a turn with an empty deck
    3: "board_wipe",        # no Pokémon left in the Active Spot
    4: "card_effect",       # a card effect ended the game
}


def _terminal_reason(obs: dict) -> int | None:
    for lg in obs.get("logs") or []:
        if lg.get("type") == 23:  # RESULT
            return lg.get("reason")
    return None


def play_match(game, seat0_act, seat1_act):
    """One engine match. Returns (winner_seat|2|-1, reason|None, steps)."""
    from agents import GreedyAgent  # noqa: F401 - ensure package import path warmed

    deck0, deck1 = play_match.decks
    obs, start = game.battle_start(deck0, deck1)
    if obs is None:
        raise RuntimeError(f"battle_start failed: errorType={start.errorType}")
    steps = 0
    while steps < MAX_STEPS:
        cur = obs.get("current") or {}
        if cur.get("result", -1) != -1:
            return cur["result"], _terminal_reason(obs), steps
        seat = cur.get("yourIndex", 0)
        action = (seat0_act if seat == 0 else seat1_act)(obs)
        obs = game.battle_select(action)
        steps += 1
    return -1, None, steps


def run(n: int, mirror: bool, budget_s: float, seed: int,
        fable_budget: float = 0.0) -> dict:
    sys.path.insert(0, REPO)
    os.chdir(REPO)
    from cg import game
    import main as M
    from agents import GreedyAgent

    # Throughput knob: cap fable's per-move MCTS budget so a single long game
    # cannot consume the whole wall-clock (the champion's own schedule already
    # drops to 0.2s under time pressure, so a small value stays near-champion
    # behaviour). 0 keeps the shipped 0.8s champion budget.
    fable_budget = fable_budget or float(os.environ.get("FABLE_TAG_BUDGET", 0) or 0)
    if fable_budget > 0:
        M.FABLE_CONFIG = {**M.FABLE_CONFIG, "time_budget_s": fable_budget}
        M.BUDGET_SCHEDULE = ((300.0, fable_budget), (420.0, fable_budget / 2),
                             (510.0, fable_budget / 4))

    deck = M.read_deck_csv()
    play_match.decks = (deck, deck)
    opp_label = "fable" if mirror else "greedy"

    def make_fable(s):
        return M.SubmissionAgent(seed=s, deck=deck)

    def make_opp(s):
        return make_fable(s) if mirror else GreedyAgent(seed=s, deck=deck)

    rows = []
    t_start = time.time()
    for i in range(n):
        if budget_s and time.time() - t_start > budget_s:
            print(f"  time budget {budget_s}s reached after {i} matches", flush=True)
            break
        fable_seat = i % 2  # alternate first/second
        fable = make_fable(seed + i)
        opp = make_opp(1000 + seed + i)
        seat0, seat1 = ((fable.act, opp.act) if fable_seat == 0
                        else (opp.act, fable.act))
        try:
            winner, reason, steps = play_match(game, seat0, seat1)
        except Exception as exc:  # noqa: BLE001 - engine/agent fault => record, continue
            rows.append({"match": i, "error": f"{type(exc).__name__}: {exc}"})
            print(f"  match {i}: ERROR {exc}", flush=True)
            continue
        fable_won = (winner == fable_seat)
        outcome = ("win" if fable_won else "loss" if winner in (0, 1)
                   else "draw" if winner == 2 else "unfinished")
        rows.append({
            "match": i,
            "fable_seat": fable_seat,
            "winner_seat": winner,
            "outcome": outcome,
            "reason_code": reason,
            "reason_tag": REASON_TAG.get(reason, "unknown"),
            "steps": steps,
        })
        print(f"  match {i}: {outcome:10s} reason={REASON_TAG.get(reason,'?')} "
              f"steps={steps}", flush=True)
    game.battle_finish()

    played = [r for r in rows if "outcome" in r]
    losses = [r for r in played if r["outcome"] == "loss"]
    loss_by_tag: dict[str, int] = {}
    for r in losses:
        loss_by_tag[r["reason_tag"]] = loss_by_tag.get(r["reason_tag"], 0) + 1
    outcome_counts: dict[str, int] = {}
    for r in played:
        outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1

    summary = {
        "opponent": opp_label,
        "seed": seed,
        "fable_move_budget_s": fable_budget or 0.8,
        "matches_played": len(played),
        "errors": len(rows) - len(played),
        "outcomes": outcome_counts,
        "losses": len(losses),
        "loss_by_tag": loss_by_tag,
        "loss_by_tag_pct": {
            k: round(100 * v / len(losses), 1) for k, v in loss_by_tag.items()
        } if losses else {},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rows": rows,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    out = os.path.join(DATA_DIR, "local_loss_tags.json")
    # Merge multiple opponents into one file keyed by opponent label.
    store = {}
    if os.path.exists(out):
        with open(out) as fh:
            store = json.load(fh)
    store[opp_label] = summary
    with open(out, "w") as fh:
        json.dump(store, fh, indent=1)
    print(f"\nfable vs {opp_label}: {outcome_counts}, loss_by_tag={loss_by_tag}")
    print(f"-> {os.path.relpath(out, REPO)}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40, help="max matches")
    ap.add_argument("--mirror", action="store_true",
                    help="fable vs fable (every match is a fable loss on one seat)")
    ap.add_argument("--budget", type=float, default=300.0,
                    help="wall-clock cap in seconds (0 = no cap)")
    ap.add_argument("--seed", type=int, default=20260722)
    ap.add_argument("--fable-budget", type=float, default=0.0,
                    help="override fable per-move MCTS seconds for throughput "
                         "(0 = shipped 0.8s champion budget)")
    args = ap.parse_args()
    run(args.n, args.mirror, args.budget, args.seed, args.fable_budget)
