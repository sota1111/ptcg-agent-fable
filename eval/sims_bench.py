"""MCTS sims/sec micro-benchmark on a FIXED board-state corpus (SOT-1836).

The determinized-MCTS agent's strength is bounded by how many search
iterations ("sims") fit inside the per-decision time budget. Raw self-play
sims/sec is dominated by *which* board states get sampled (early-game
decisions rollout whole games; endgame decisions settle in a few steps), so
a single-match probe of the same config can swing 2x run to run.

This harness removes that variance by measuring every config against the
**same frozen corpus** of decision observations (the acceptance criterion:
"同一盤面セットで比較"):

1. `capture_corpus` plays fast greedy-vs-greedy matches and freezes the raw
   observation dicts at the decision points (skipping forced single-option
   selects — MCTS answers those on the fast path with zero iterations).
2. `measure_config` replays the SAME corpus through a fresh `MctsAgent` per
   state, under a config, and pools `planner.last_stats` across states:
   sims/sec = Σ iterations / Σ search wall-clock. Because every config gets
   an identical time budget on identical states, more iterations in the same
   budget is a pure sims/sec win.

It also reports the fault counters that must stay 0 (planner fallbacks,
budget violations, degraded decisions) so a "faster" config that starts
timing out or degrading is caught here, not in the win-rate bench.

Usage (from the repo root):
    python3 eval/sims_bench.py --states 48 --seed 20260722 \
        --label champion            # baseline = FABLE_CONFIG
    python3 eval/sims_bench.py --states 48 --seed 20260722 \
        --label rollout_t3 --override '{"rollout_turns":3,"rollout_depth":20}'

Every run appends one line to sims_history.jsonl (git-tracked) and prints a
before/after-friendly summary. Pass --baseline-override to also measure the
champion in the same process and print the speedup ratio directly.
"""
import argparse
import datetime
import json
import os
import statistics
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)  # libcg.so & deck.csv resolve relative to the repo root

from cg import game  # noqa: E402
from agents import make_agent  # noqa: E402
from agents.rng import Rng  # noqa: E402
from main import FABLE_CONFIG  # noqa: E402

MAX_DECISIONS = 100000


def load_deck(path: str) -> list:
    with open(path) as f:
        return [int(x) for x in f.read().split("\n")[:60]]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def _is_search_state(obs: dict) -> bool:
    """A decision the planner would actually search (>1 real option).

    Forced selects (0 or 1 legal action, or take-everything) are answered on
    the fast path with zero iterations, so they carry no sims/sec signal.
    """
    cur = obs.get("current") or {}
    if cur.get("result", -1) != -1:
        return False
    sel = obs.get("select")
    if not sel:
        return False
    opts = sel.get("option") or []
    if len(opts) < 2:
        return False
    lo = int(sel.get("minCount") or 0)
    hi = int(sel.get("maxCount") or 0)
    # take-everything / take-nothing forced selects are also fast-pathed
    if lo == hi and lo in (0, len(opts)):
        return False
    return True


def capture_corpus(n_states: int, seed: int, deck: list) -> list:
    """Freeze `n_states` searchable decision obs dicts from greedy self-play.

    Greedy-vs-greedy is fast and deterministic (agent seeds derived from
    `seed`); we only need representative mid-game states, not MCTS play, so
    the corpus is cheap and identical across configs.
    """
    base = Rng(seed)
    corpus = []
    match_i = 0
    while len(corpus) < n_states:
        a = make_agent("greedy", seed=base.child(f"m{match_i}.a").seed, deck=deck)
        b = make_agent("greedy", seed=base.child(f"m{match_i}.b").seed, deck=deck)
        match_i += 1
        obs, start = game.battle_start(a._deck, b._deck)
        if obs is None:
            continue
        try:
            decisions = 0
            while decisions < MAX_DECISIONS and len(corpus) < n_states:
                cur = obs.get("current") or {}
                if cur.get("result", -1) != -1:
                    break
                agent = a if cur.get("yourIndex", 0) == 0 else b
                if _is_search_state(obs):
                    # Sample every 3rd searchable state to spread the corpus
                    # across game phases rather than clustering in one match.
                    if (decisions % 3) == 0:
                        corpus.append(json.loads(json.dumps(obs)))
                action = agent.act(obs)
                obs = game.battle_select(action)
                decisions += 1
        finally:
            game.battle_finish()
    return corpus[:n_states]


def measure_config(corpus: list, config: dict, seed: int, deck: list) -> dict:
    """Replay the frozen corpus through fresh MctsAgents; pool search stats."""
    base = Rng(seed)
    tot_iters = 0
    tot_search_s = 0.0
    searched = 0
    per_state_sps = []
    faults = {"fallback": 0, "planner_fallbacks": 0,
              "budget_violations": 0, "degraded": 0}
    for i, obs in enumerate(corpus):
        agent = make_agent("mcts", seed=base.child(f"s{i}").seed,
                           deck=deck, **dict(config))
        agent.act(obs)
        st = getattr(getattr(agent, "_planner", None), "last_stats", {}) or {}
        iters = st.get("iterations") or 0
        elapsed = st.get("elapsed_s") or 0.0
        if iters and elapsed > 0:
            tot_iters += iters
            tot_search_s += elapsed
            searched += 1
            per_state_sps.append(iters / elapsed)
        faults["fallback"] += agent.fallback_count
        faults["planner_fallbacks"] += agent.planner_fallbacks
        faults["budget_violations"] += agent.budget_violations
        faults["degraded"] += agent.degraded_count
    sims_sec = (tot_iters / tot_search_s) if tot_search_s else 0.0
    return {
        "states_total": len(corpus),
        "states_searched": searched,
        "total_iterations": tot_iters,
        "total_search_s": round(tot_search_s, 4),
        "sims_sec": round(sims_sec, 1),
        "sims_sec_median_per_state": round(statistics.median(per_state_sps), 1)
        if per_state_sps else 0.0,
        "iters_per_search_mean": round(tot_iters / searched, 1) if searched else 0,
        "faults": faults,
        "faults_total": sum(faults.values()),
    }


def resolve_config(base_fable: bool, override: str | None,
                   config_json: str | None) -> dict:
    if config_json is not None:
        return json.loads(config_json)
    cfg = dict(FABLE_CONFIG) if base_fable else {}
    if override:
        cfg.update(json.loads(override))
    return cfg


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--label", required=True, help="config name for the log line")
    p.add_argument("--states", type=int, default=48,
                   help="frozen corpus size (searchable decisions)")
    p.add_argument("--seed", type=int, default=20260722)
    p.add_argument("--deck", default="deck.csv")
    p.add_argument("--override", default=None,
                   help="JSON delta merged onto FABLE_CONFIG for the candidate")
    p.add_argument("--config", default=None,
                   help="full JSON config (skips the FABLE_CONFIG base)")
    p.add_argument("--no-base-fable", dest="base_fable", action="store_false",
                   default=True, help="candidate starts from agent defaults")
    p.add_argument("--baseline", dest="baseline", action="store_true",
                   help="also measure champion FABLE_CONFIG and print the ratio")
    p.add_argument("--note", default="")
    p.add_argument("--out", default="sims_history.jsonl")
    args = p.parse_args()

    deck = load_deck(args.deck)
    print(f"SIMS-BENCH: capturing {args.states}-state corpus (seed={args.seed})",
          flush=True)
    t0 = time.perf_counter()
    corpus = capture_corpus(args.states, args.seed, deck)
    print(f"  corpus frozen: {len(corpus)} states "
          f"({time.perf_counter() - t0:.1f}s)", flush=True)

    cfg = resolve_config(args.base_fable, args.override, args.config)
    print(f"SIMS-BENCH[{args.label}]: measuring on frozen corpus\n"
          f"  config={cfg}", flush=True)
    res = measure_config(corpus, cfg, args.seed, deck)

    ratio = None
    if args.baseline:
        print("SIMS-BENCH[champion]: measuring FABLE_CONFIG on the SAME corpus",
              flush=True)
        base_res = measure_config(corpus, dict(FABLE_CONFIG), args.seed, deck)
        if base_res["sims_sec"]:
            ratio = round(res["sims_sec"] / base_res["sims_sec"], 3)
        res["baseline_champion"] = base_res
        res["speedup_vs_champion"] = ratio

    line = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "label": args.label,
        "seed": args.seed,
        "states": args.states,
        "deck": args.deck,
        "config": cfg,
        "note": args.note,
        **res,
    }
    with open(args.out, "a") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")

    print(f"""
SIMS-BENCH RESULT [{args.label}]
  states searched : {res['states_searched']}/{res['states_total']}
  sims/sec (pooled): {res['sims_sec']}   (median per-state {res['sims_sec_median_per_state']})
  iters/search mean: {res['iters_per_search_mean']}
  faults          : {res['faults']}  total={res['faults_total']}""")
    if ratio is not None:
        print(f"  champion sims/sec: {res['baseline_champion']['sims_sec']}   "
              f"SPEEDUP x{ratio}")
    print(f"  appended to {args.out}")


if __name__ == "__main__":
    main()
