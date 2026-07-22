"""Self-play data generation for the value net (SOT-1837).

Plays N side-alternating matches on the local cabt engine and records
(board features, final-result label) pairs sampled from the decision states,
writing them as JSONL: one ``{"f": [floats], "y": label}`` object per line.

Label convention (value target = win probability for the side to move at that
state): 1.0 if the acting player eventually WON the match, 0.0 if it LOST, 0.5
on a draw. Unfinished matches contribute nothing.

Features come from ``agents.value_features.extract`` applied to the raw battle
observation from the acting player's POV — the exact same extractor the
inference evaluator runs on the engine's search observations, so training and
inference see identical feature layouts.

Usage (from the repo root):
    python3 train/gen_selfplay.py --n 200 --agent greedy --seed 20260722 \
        --out train/data/selfplay.jsonl [--stride 2] [--max-per-match 40]

`--agent` picks BOTH players (mirror self-play, matching the planner's mirror
opponent model). Use `greedy` for volume; `mcts` for on-policy champion data
(far slower).
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)  # libcg.so & deck.csv resolve relative to the repo root

from cg import game
from agents import make_agent
from agents.rng import Rng
from agents.value_features import FEATURE_VERSION, extract

MAX_DECISIONS = 100000


def load_deck(path: str) -> list:
    with open(path) as f:
        return [int(x) for x in f.read().split("\n")[:60]]


def play_and_record(agent0, agent1, stride: int, max_per_match: int):
    """One match. Returns a list of (features, acting_index) recorded at
    decision states, plus the final result (or -1 if unfinished)."""
    obs, start = game.battle_start(agent0._deck, agent1._deck)
    if obs is None:
        raise RuntimeError(
            f"battle_start failed: errorPlayer={start.errorPlayer} "
            f"errorType={start.errorType}")
    samples = []
    try:
        decisions = 0
        while decisions < MAX_DECISIONS:
            current = obs.get("current") or {}
            result = current.get("result", -1)
            if result != -1:
                return samples, result
            actor = current.get("yourIndex", 0)
            # Record before acting (board state the value is asked about).
            if (decisions % max(1, stride) == 0
                    and len(samples) < max_per_match):
                samples.append((extract(obs, actor), actor))
            agent = agent0 if actor == 0 else agent1
            try:
                action = agent.act(obs)
                obs = game.battle_select(action)
            except Exception:
                return samples, -1  # fault: drop the match's labels
            decisions += 1
        return samples, -1
    finally:
        game.battle_finish()


def label_for(result: int, actor: int):
    if result == actor:
        return 1.0
    if result == 1 - actor:
        return 0.0
    if result == 2:
        return 0.5
    return None  # unfinished / unknown


def generate(agent_name: str, n: int, seed: int, deck_path: str,
             stride: int, max_per_match: int, config=None):
    deck = load_deck(deck_path)
    base = Rng(seed)
    rows = []
    faults = 0
    for i in range(n):
        seed_a = base.child(f"m{i}.a").seed
        seed_b = base.child(f"m{i}.b").seed
        a = make_agent(agent_name, seed=seed_a, deck=deck, **(config or {}))
        b = make_agent(agent_name, seed=seed_b, deck=deck, **(config or {}))
        p0, p1 = (a, b) if i % 2 == 0 else (b, a)
        samples, result = play_and_record(p0, p1, stride, max_per_match)
        if result not in (0, 1, 2):
            faults += 1
            continue
        for feats, actor in samples:
            y = label_for(result, actor)
            if y is not None:
                rows.append({"f": feats, "y": y})
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{n} matches, {len(rows)} samples", flush=True)
    return rows, faults


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--agent", default="greedy")
    ap.add_argument("--seed", type=int, default=20260722)
    ap.add_argument("--deck", default="deck.csv")
    ap.add_argument("--stride", type=int, default=2,
                    help="record every k-th decision state")
    ap.add_argument("--max-per-match", type=int, default=40)
    ap.add_argument("--config", default=None, help="JSON agent kwargs")
    ap.add_argument("--out", default="train/data/selfplay.jsonl")
    args = ap.parse_args()

    config = json.loads(args.config) if args.config else None
    print(f"GEN: agent={args.agent} n={args.n} seed={args.seed} "
          f"stride={args.stride} config={config}", flush=True)
    rows, faults = generate(args.agent, args.n, args.seed, args.deck,
                            args.stride, args.max_per_match, config)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(json.dumps({"meta": {"feature_version": FEATURE_VERSION,
                                     "n_matches": args.n, "seed": args.seed,
                                     "agent": args.agent, "faults": faults,
                                     "samples": len(rows)}}) + "\n")
        for r in rows:
            f.write(json.dumps(r) + "\n")
    pos = sum(1 for r in rows if r["y"] == 1.0)
    print(f"wrote {len(rows)} samples ({pos} win / {len(rows) - pos} other), "
          f"faults={faults} -> {args.out}")


if __name__ == "__main__":
    main()
