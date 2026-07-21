"""fable KPI harness (SOT-1796) — screen→confirm A/B win-rate measurement.

One invocation = one KPI measurement = **one appended line** in
`kpi_history.jsonl`. Each measurement plays agent A (usually the fable MCTS
under a candidate config) against agent B (a fixed reference, usually greedy)
over one or more independent seed shards, pools the shards, and records the
pooled Wilson 95% interval plus every fault counter that must stay 0.

Design (SOT-1796 / lessons from SOT-1697/1698/1699/1707):
- **Config as delta.** A candidate is expressed as `--override-a` JSON merged
  onto the fable champion config (`main.FABLE_CONFIG`), so a KPI line always
  records the FULL resolved config it measured — reproducible standalone.
- **Screen vs confirm are distinct seeds.** Pass different `--seeds` for the
  `screen` and `confirm` phases of the same candidate; promotion is judged on
  the confirm phase's pooled CI ONLY (no p-hacking on the screen seeds).
- **Aggregate-CI judgment only.** N=400-class round robins are impractical
  (each match ~4s); screen cheaply cuts, confirm pools independent seeds and
  the Wilson lower bound is the promotion gate.

Usage (from the repo root):
    python3 eval/kpi.py --label baseline --phase baseline \
        --agent-a mcts --agent-b greedy --n 25 --seeds 811,822,833,844 \
        [--override-a '{"deviate_margin":0.05}'] [--out kpi_history.jsonl]

`--config-a` sets A's config outright (skips the FABLE_CONFIG base); with
`--base-fable` (default for mcts) the champion config is the base and
`--override-a` is merged on top.
"""
import argparse
import datetime
import json
import math
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)  # libcg.so & deck.csv resolve relative to the repo root

from eval.bench import run_bench  # noqa: E402
from main import FABLE_CONFIG  # noqa: E402  (champion baseline config)

FAULT_KEYS = (
    "rejects", "exceptions", "fallbacks_a", "fallbacks_b",
    "budget_violations_a", "budget_violations_b",
    "planner_fallbacks_a", "planner_fallbacks_b", "degraded_a", "degraded_b",
    "unfinished",
)


def wilson95(wins: int, n: int) -> list:
    """Wilson score 95% interval for a binomial proportion (draws excluded)."""
    if n == 0:
        return [None, None]
    z = 1.959963984540054
    p = wins / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [center - half, center + half]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def measure(agent_a, agent_b, seeds, n, deck, config_a, config_b):
    """Run one shard per seed, pool the counts, return the pooled record."""
    shards = []
    pooled = {"wins_a": 0, "wins_b": 0, "draws": 0}
    faults = {k: 0 for k in FAULT_KEYS}
    for seed in seeds:
        rep = run_bench(agent_a, agent_b, n, seed, deck,
                        config_a=config_a, config_b=config_b)
        shards.append({"seed": seed, "n": n, "wins_a": rep["wins_a"],
                       "wins_b": rep["wins_b"], "draws": rep["draws"]})
        pooled["wins_a"] += rep["wins_a"]
        pooled["wins_b"] += rep["wins_b"]
        pooled["draws"] += rep["draws"]
        for k in FAULT_KEYS:
            faults[k] += rep.get(k, 0)
    decided = pooled["wins_a"] + pooled["wins_b"]
    n_total = n * len(seeds)
    return {
        "n_total": n_total,
        "wins_a": pooled["wins_a"], "wins_b": pooled["wins_b"],
        "draws": pooled["draws"],
        "winrate_a_excl_draws": (pooled["wins_a"] / decided) if decided else None,
        "wilson95_excl_draws": wilson95(pooled["wins_a"], decided),
        "winrate_a_draws_half": ((pooled["wins_a"] + 0.5 * pooled["draws"])
                                 / n_total) if n_total else None,
        "faults": faults,
        "faults_total": sum(faults.values()),
        "shards": shards,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--label", required=True,
                   help="candidate name, e.g. baseline / deviate_margin_005")
    p.add_argument("--phase", required=True,
                   choices=["baseline", "screen", "confirm"])
    p.add_argument("--agent-a", default="mcts")
    p.add_argument("--agent-b", default="greedy")
    p.add_argument("--n", type=int, default=25, help="matches PER seed shard")
    p.add_argument("--seeds", required=True,
                   help="comma-separated seed list (one shard each)")
    p.add_argument("--deck", default="deck.csv")
    p.add_argument("--config-a", default=None,
                   help="full JSON config for A (skips the FABLE_CONFIG base)")
    p.add_argument("--override-a", default=None,
                   help="JSON delta merged onto the FABLE_CONFIG base for A")
    p.add_argument("--config-b", default=None,
                   help="JSON config for B (default: none / plain agent)")
    p.add_argument("--base-fable", dest="base_fable", action="store_true",
                   default=None, help="force FABLE_CONFIG as A's base")
    p.add_argument("--no-base-fable", dest="base_fable", action="store_false",
                   help="A starts from an empty config (agent defaults)")
    p.add_argument("--note", default="", help="free-text rationale for the line")
    p.add_argument("--out", default="kpi_history.jsonl")
    args = p.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    if len(set(seeds)) != len(seeds):
        raise SystemExit(f"seeds must be distinct, got {seeds}")

    # Resolve A's config: explicit --config-a wins; otherwise start from the
    # FABLE_CONFIG champion base (default for mcts) and apply --override-a.
    if args.config_a is not None:
        config_a = json.loads(args.config_a)
    else:
        use_base = args.base_fable
        if use_base is None:
            use_base = (args.agent_a == "mcts")
        config_a = dict(FABLE_CONFIG) if use_base else {}
        if args.override_a:
            config_a.update(json.loads(args.override_a))
    config_b = json.loads(args.config_b) if args.config_b else None

    print(f"KPI[{args.phase}] {args.label}: {args.agent_a} vs {args.agent_b} "
          f"n={args.n}x{len(seeds)} seeds={seeds}\n  config_a={config_a}",
          flush=True)
    res = measure(args.agent_a, args.agent_b, seeds, args.n, args.deck,
                  config_a, config_b)

    line = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "label": args.label,
        "phase": args.phase,
        "agent_a": args.agent_a,
        "agent_b": args.agent_b,
        "deck": args.deck,
        "config_a": config_a,
        "config_b": config_b or {},
        "seeds": seeds,
        "n_per_seed": args.n,
        "note": args.note,
        **res,
    }
    with open(args.out, "a") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")

    lo, hi = res["wilson95_excl_draws"]
    wr = res["winrate_a_excl_draws"]
    print(f"  -> winrate_a {wr:.4f}  Wilson95 [{lo:.4f}, {hi:.4f}]  "
          f"(A {res['wins_a']} / B {res['wins_b']} / draw {res['draws']}, "
          f"N={res['n_total']})  faults={res['faults_total']}")
    print(f"  appended to {args.out}")


if __name__ == "__main__":
    main()
