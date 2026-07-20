"""Candidate deck round-robin comparison (SOT-1794).

Plays every unordered pair of candidate decks — mirror pairs included — with a
fast agent on BOTH sides (default: greedy), alternating the first player every
match, and reports a per-deck AGGREGATE win rate over the whole field with a
Wilson 95% CI. Ranking uses ONLY the aggregate CI: per-pair small-N win rates
are noise-fishing (SOT-1707 lesson) and are emitted to the JSON for audit
only.

Two-stage protocol (SOT-1794):

- screen : all candidates, small N per pair::

      python3 eval/compare_decks.py --decks-dir decks/candidates \
          --n-per-pair 40 --seed 1794001 --json eval/results/screen.json

- confirm: finalists only (``--include``), larger N, independent ``--seed``.
  Each finalist still plays the FULL field so the statistic stays comparable
  to screening; non-finalist field decks are measured but marked
  ``reference``::

      python3 eval/compare_decks.py --decks-dir decks/candidates \
          --include 01_dragapult,26_stw_champion --n-per-pair 120 \
          --seed 1794100 --json eval/results/confirm.json

Faults (engine rejects, agent exceptions, random-legal fallbacks) are
aggregated and the process exits 1 if any occurred — the whole measurement
must be fault 0.
"""
import argparse
import glob
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)  # libcg.so resolves relative to the repo root

from agents import make_agent            # noqa: E402
from agents.rng import Rng               # noqa: E402
from eval.bench import play_match, wilson_ci  # noqa: E402
from eval.deck_validator import load_deck_csv  # noqa: E402


def load_field(decks_dir: str):
    """Return ``[(name, card_ids)]`` for every ``*.csv`` in ``decks_dir``."""
    paths = sorted(glob.glob(os.path.join(decks_dir, "*.csv")))
    if not paths:
        raise SystemExit(f"no *.csv decks found in {decks_dir}")
    return [(os.path.splitext(os.path.basename(p))[0], load_deck_csv(p))
            for p in paths]


def run_comparison(field, candidates, n_per_pair, seed, agent_name):
    """Round-robin over unordered pairs involving >=1 candidate.

    Returns ``(per_deck, per_pair, faults)`` where ``per_deck`` maps deck name
    to its aggregate tally. A non-mirror game updates both decks' tallies; a
    mirror game is counted once, from the side-A agent's perspective.
    """
    base = Rng(seed)
    names = [n for n, _ in field]
    decks = dict(field)
    per_deck = {n: {"wins": 0, "losses": 0, "draws": 0, "unfinished": 0}
                for n in names}
    faults = {"rejects": 0, "exceptions": 0, "fallbacks": 0}
    per_pair = {}

    pairs = []
    for i, a in enumerate(names):
        for b in names[i:]:  # includes the mirror (a, a)
            if a in candidates or b in candidates:
                pairs.append((a, b))

    total_games = len(pairs) * n_per_pair
    print(f"COMPARE: {len(candidates)}/{len(names)} candidate decks, "
          f"{len(pairs)} pairs x {n_per_pair} games = {total_games} games, "
          f"agent={agent_name}, seed={seed}", flush=True)

    t0 = time.perf_counter()
    for pi, (a, b) in enumerate(pairs):
        tally = {"wins_a": 0, "wins_b": 0, "draws": 0, "unfinished": 0}
        for g in range(n_per_pair):
            seed_a = base.child(f"{a}|{b}|{g}.a").seed
            seed_b = base.child(f"{a}|{b}|{g}.b").seed
            pa = make_agent(agent_name, seed=seed_a, deck=decks[a])
            pb = make_agent(agent_name, seed=seed_b, deck=decks[b])
            a_first = (g % 2 == 0)
            p0, p1 = (pa, pb) if a_first else (pb, pa)
            result, _, reject, exception = play_match(p0, p1)
            faults["rejects"] += int(reject)
            faults["exceptions"] += int(exception)
            faults["fallbacks"] += pa.fallback_count + pb.fallback_count
            if result in (0, 1):
                a_won = (result == 0) == a_first
                tally["wins_a" if a_won else "wins_b"] += 1
            elif result == 2:
                tally["draws"] += 1
            else:
                tally["unfinished"] += 1
        if a == b:  # mirror: count each game once, side-A perspective
            per_deck[a]["wins"] += tally["wins_a"]
            per_deck[a]["losses"] += tally["wins_b"]
            per_deck[a]["draws"] += tally["draws"]
            per_deck[a]["unfinished"] += tally["unfinished"]
        else:
            per_deck[a]["wins"] += tally["wins_a"]
            per_deck[a]["losses"] += tally["wins_b"]
            per_deck[b]["wins"] += tally["wins_b"]
            per_deck[b]["losses"] += tally["wins_a"]
            for d in (a, b):
                per_deck[d]["draws"] += tally["draws"]
                per_deck[d]["unfinished"] += tally["unfinished"]
        per_pair[f"{a} vs {b}"] = tally
        if (pi + 1) % 25 == 0 or pi + 1 == len(pairs):
            dt = time.perf_counter() - t0
            print(f"  pair {pi + 1}/{len(pairs)} ({dt:.0f}s elapsed)",
                  flush=True)
    return per_deck, per_pair, faults


def summarize(per_deck, candidates):
    rows = []
    for name, t in per_deck.items():
        decided = t["wins"] + t["losses"]
        winrate = t["wins"] / decided if decided else None
        lo, hi = wilson_ci(t["wins"], decided)
        rows.append({
            "deck": name,
            "candidate": name in candidates,
            **t,
            "n_decided": decided,
            "winrate_excl_draws": winrate,
            "wilson95_excl_draws": [lo, hi],
        })
    rows.sort(key=lambda r: -(r["winrate_excl_draws"] or 0.0))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decks-dir", default="decks/candidates")
    parser.add_argument("--include", default=None,
                        help="comma-separated deck stems to measure as "
                             "candidates (default: all decks in --decks-dir)")
    parser.add_argument("--n-per-pair", type=int, default=40)
    parser.add_argument("--seed", type=int, default=1794001)
    parser.add_argument("--agent", default="greedy")
    parser.add_argument("--json", default=None,
                        help="write the full report to this JSON file")
    args = parser.parse_args()

    field = load_field(args.decks_dir)
    names = {n for n, _ in field}
    if args.include:
        candidates = set(args.include.split(","))
        missing = candidates - names
        if missing:
            raise SystemExit(f"--include deck(s) not found: {sorted(missing)}")
    else:
        candidates = names

    per_deck, per_pair, faults = run_comparison(
        field, candidates, args.n_per_pair, args.seed, args.agent)
    rows = summarize(per_deck, candidates)

    print(f"\nRANKING (aggregate win rate excl. draws, Wilson 95% CI, "
          f"n_per_pair={args.n_per_pair}):")
    for rank, r in enumerate(rows, 1):
        if not r["candidate"]:
            continue
        lo, hi = r["wilson95_excl_draws"]
        print(f"  {rank:2d}. {r['deck']:<40s} "
              f"{r['winrate_excl_draws']:.4f} [{lo:.4f}, {hi:.4f}] "
              f"(n={r['n_decided']}, draws={r['draws']}, "
              f"unfinished={r['unfinished']})")
    print(f"FAULTS: rejects={faults['rejects']} "
          f"exceptions={faults['exceptions']} "
          f"fallbacks={faults['fallbacks']}")

    if args.json:
        os.makedirs(os.path.dirname(args.json), exist_ok=True) \
            if os.path.dirname(args.json) else None
        with open(args.json, "w") as f:
            json.dump({
                "decks_dir": args.decks_dir,
                "candidates": sorted(candidates),
                "n_per_pair": args.n_per_pair,
                "seed": args.seed,
                "agent": args.agent,
                "faults": faults,
                "ranking": rows,
                "per_pair": per_pair,
            }, f, indent=2)
        print(f"wrote {args.json}")

    return 1 if any(faults.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
