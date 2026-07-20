"""fable vs one sibling repo — subprocess-isolated submission battle
(SOT-1795, adapted from ptcg-agent-matsu SOT-1681).

Plays THIS repo's Kaggle submission agent (``main.agent`` + ``deck.csv``)
against another repo's, each in an isolated subprocess (``agent_server.py``
launched with cwd=its repo root — the top-level ``agents`` packages collide
across repos, so they cannot share one interpreter). The host process owns
only the engine (this repo's ``cg.game``, a process-global single battle)
and the orchestration.

Fairness (先後入替): on even matches fable takes engine seat 0 (先手), on
odd matches the opponent does. Each agent plays its own repo's ``deck.csv``.

Robustness: an agent that raises, emits an illegal action (engine reject),
or whose subprocess dies is charged a **fault** and loses that match; the
faulting server is relaunched for the next match. Faults are reported — the
SOT-1795 acceptance gate is fault 0.

時間切れ evidence: per-seat cumulative think time is tracked host-side
(wall clock around each act() round-trip, subprocess overhead included) and
the per-match maximum is reported against the ~600s match allowance.

The engine has no seed API, so results are statistical, not bit-reproducible.
Shards: run several instances with distinct --tag values and pool the JSON
reports with --aggregate.

Usage (from this repo root):
    python3 eval/battle_vs.py --opponent ../ptcg-agent-matsu --n 30 \
        --json /tmp/fable_vs_matsu_s1.json
    python3 eval/battle_vs.py --aggregate /tmp/fable_vs_matsu_s*.json
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "eval", "agent_server.py")
MAX_DECISIONS = 100_000  # engine draws/decks-out long before this
MATCH_TIME_ALLOWANCE_S = 600.0


def load_deck(repo: str) -> list:
    with open(os.path.join(repo, "deck.csv")) as f:
        return [int(x) for x in f.read().split("\n")[:60]]


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


class Contestant:
    """One repo's submission agent, driven over a subprocess."""

    def __init__(self, label: str, repo: str):
        self.label = label
        self.repo = os.path.abspath(repo)
        self.deck = load_deck(self.repo)
        self.proc = None

    @property
    def python(self) -> str:
        venv = os.path.join(self.repo, "venv", "bin", "python")
        return venv if os.path.exists(venv) else sys.executable

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [self.python, SERVER], cwd=self.repo,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        line = self.proc.stderr.readline()
        if not line.startswith("READY"):
            err = self.proc.stderr.read()
            raise RuntimeError(
                f"{self.label} agent failed to start: {line}{err}")

    def act(self, obs: dict) -> list:
        assert self.proc is not None
        self.proc.stdin.write(json.dumps(obs))
        self.proc.stdin.write("\n")
        self.proc.stdin.flush()
        reply = self.proc.stdout.readline()
        if reply == "":  # server died
            raise RuntimeError(f"{self.label} agent server exited")
        action = json.loads(reply)
        if isinstance(action, dict) and "__error__" in action:
            raise RuntimeError(
                f"{self.label} agent error: {action['__error__']}")
        return action

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - best-effort teardown
            self.proc.kill()
        self.proc = None

    def restart(self) -> None:
        self.stop()
        self.start()


def play_match(game, seat0: Contestant, seat1: Contestant) -> dict:
    """One engine match. result: winner seat (0/1), 2=draw, -1=unfinished."""
    obs, start = game.battle_start(seat0.deck, seat1.deck)
    if obs is None:
        raise RuntimeError(
            f"battle_start failed: errorPlayer={start.errorPlayer} "
            f"errorType={start.errorType}")
    steps = 0
    think = [0.0, 0.0]  # per-seat cumulative act() wall clock
    try:
        while steps < MAX_DECISIONS:
            cur = obs.get("current") or {}
            result = cur.get("result", -1)
            if result != -1:
                return {"result": result, "steps": steps,
                        "fault_seat": None, "think": think}
            seat = cur.get("yourIndex", 0)
            agent = seat0 if seat == 0 else seat1
            t0 = time.perf_counter()
            try:
                action = agent.act(obs)
            except Exception:  # noqa: BLE001 - agent fault => that seat loses
                return {"result": 1 - seat, "steps": steps,
                        "fault_seat": seat, "think": think}
            finally:
                think[seat] += time.perf_counter() - t0
            try:
                obs = game.battle_select(action)
            except Exception:  # noqa: BLE001 - engine reject => illegal move
                return {"result": 1 - seat, "steps": steps,
                        "fault_seat": seat, "think": think}
            steps += 1
        return {"result": -1, "steps": steps, "fault_seat": None,
                "think": think}
    finally:
        game.battle_finish()


def run(opponent_repo: str, opponent_label: str, n: int) -> dict:
    sys.path.insert(0, REPO)
    os.chdir(REPO)  # libcg.so resolves relative to the repo root
    from cg import game

    fable = Contestant("fable", REPO)
    opp = Contestant(opponent_label, opponent_repo)
    fable.start()
    opp.start()
    stats = {"wins_fable": 0, "wins_opp": 0, "draws": 0, "unfinished": 0,
             "faults_fable": 0, "faults_opp": 0,
             "wins_fable_as_first": 0, "n_fable_as_first": 0}
    max_think = {"fable": 0.0, opponent_label: 0.0}
    match_times = []
    try:
        for i in range(n):
            fable_seat = i % 2  # 先後入替
            seat0, seat1 = ((fable, opp) if fable_seat == 0
                            else (opp, fable))
            t0 = time.perf_counter()
            out = play_match(game, seat0, seat1)
            match_times.append(time.perf_counter() - t0)
            for seat, contestant in ((0, seat0), (1, seat1)):
                max_think[contestant.label] = max(
                    max_think[contestant.label], out["think"][seat])
            if out["fault_seat"] is not None:
                faulter = seat0 if out["fault_seat"] == 0 else seat1
                key = "faults_fable" if faulter is fable else "faults_opp"
                stats[key] += 1
                faulter.restart()
            result = out["result"]
            if result in (0, 1):
                fable_won = (result == fable_seat)
                stats["wins_fable" if fable_won else "wins_opp"] += 1
                if fable_seat == 0:
                    stats["n_fable_as_first"] += 1
                    stats["wins_fable_as_first"] += int(fable_won)
            elif result == 2:
                stats["draws"] += 1
            else:
                stats["unfinished"] += 1
            print(f"  match {i + 1}/{n}: "
                  f"fable {stats['wins_fable']} - {stats['wins_opp']} "
                  f"{opponent_label} (draws {stats['draws']}, faults "
                  f"F{stats['faults_fable']}/O{stats['faults_opp']})",
                  flush=True)
    finally:
        fable.stop()
        opp.stop()

    decided = stats["wins_fable"] + stats["wins_opp"]
    lo, hi = wilson_ci(stats["wins_fable"], decided)
    return {
        "fable_repo": REPO, "opponent_repo": os.path.abspath(opponent_repo),
        "opponent": opponent_label, "n_matches": n, **stats,
        "winrate_fable_excl_draws": (stats["wins_fable"] / decided
                                     if decided else None),
        "wilson95_excl_draws": [lo, hi],
        "winrate_fable_draws_half": (stats["wins_fable"]
                                     + 0.5 * stats["draws"]) / n if n else None,
        "max_think_s": max_think,
        "match_time_allowance_s": MATCH_TIME_ALLOWANCE_S,
        "time_per_match_sec": {
            "mean": sum(match_times) / len(match_times) if match_times else 0,
            "max": max(match_times) if match_times else 0,
            "total": sum(match_times),
        },
    }


def aggregate(paths: list) -> dict:
    shards = [json.load(open(p)) for p in paths]
    opponents = {s["opponent"] for s in shards}
    if len(opponents) != 1:
        raise SystemExit(f"shards disagree on opponent: {opponents}")
    out = {"opponent": shards[0]["opponent"], "shards": len(shards)}
    for key in ("n_matches", "wins_fable", "wins_opp", "draws", "unfinished",
                "faults_fable", "faults_opp", "wins_fable_as_first",
                "n_fable_as_first"):
        out[key] = sum(s.get(key, 0) for s in shards)
    out["max_think_s"] = {
        label: max(s["max_think_s"].get(label, 0.0) for s in shards)
        for label in shards[0]["max_think_s"]}
    decided = out["wins_fable"] + out["wins_opp"]
    lo, hi = wilson_ci(out["wins_fable"], decided)
    out["winrate_fable_excl_draws"] = (out["wins_fable"] / decided
                                       if decided else None)
    out["wilson95_excl_draws"] = [lo, hi]
    n = out["n_matches"]
    out["winrate_fable_draws_half"] = ((out["wins_fable"] + 0.5 * out["draws"])
                                       / n if n else None)
    return out


def summarize(report: dict) -> str:
    lo, hi = report["wilson95_excl_draws"]
    first = (f"{report['wins_fable_as_first']}/{report['n_fable_as_first']}"
             if report.get("n_fable_as_first") else "n/a")
    return (
        f"fable vs {report['opponent']}: n={report['n_matches']}  "
        f"fable {report['wins_fable']} - {report['wins_opp']} "
        f"(draws {report['draws']}, unfinished {report['unfinished']})\n"
        f"  win rate (excl. draws): {report['winrate_fable_excl_draws']:.4f}"
        f"  Wilson95 [{lo:.4f}, {hi:.4f}]  (先手 {first})\n"
        f"  faults: fable {report['faults_fable']}  "
        f"{report['opponent']} {report['faults_opp']}\n"
        f"  max think/match: {report['max_think_s']}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--opponent", default="../ptcg-agent-matsu",
                   help="opponent repo path (default ../ptcg-agent-matsu)")
    p.add_argument("--label", default=None,
                   help="opponent label (default: repo basename)")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--json", default=None)
    p.add_argument("--aggregate", nargs="+", default=None,
                   metavar="SHARD.json", help="pool shard reports and exit")
    args = p.parse_args()

    if args.aggregate:
        report = aggregate(args.aggregate)
    else:
        label = args.label or os.path.basename(
            os.path.abspath(args.opponent)).replace("ptcg-agent-", "")
        report = run(args.opponent, label, args.n)
    print(summarize(report))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
        print(f"wrote {args.json}")


if __name__ == "__main__":
    main()
