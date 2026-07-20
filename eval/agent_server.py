"""Subprocess agent server for cross-repo battles (SOT-1795, from
ptcg-agent-matsu SOT-1681).

Runs one project's Kaggle submission agent (``main.agent``) in its OWN
process and working directory, and exposes it over a line-delimited JSON
protocol so a host process (``eval/battle_vs.py``) can drive it without
importing that repo's ``agents`` / ``cg`` packages.

Why a subprocess instead of an in-process import: the sibling repos
(``ptcg-agent-fable`` / ``-matsu`` / ``-take`` / ``-ume``) each ship a
top-level ``agents`` package whose module names collide, so they cannot be
imported side-by-side in one interpreter (SOT-1681). Isolating each agent in
its own process side-steps the collision and lets each ``main.agent``
resolve its own ``deck.csv`` / native engine relative to its repo root.

Protocol (one JSON value per line, both directions):

* stdin  ← ``obs_dict`` (the raw engine observation, exactly what the Kaggle
  harness passes to ``agent(obs_dict)``).
* stdout → the action, a ``list[int]`` of option indices; or, if the agent
  raised, ``{"__error__": "<ExceptionType>: <message>"}`` so the host can
  attribute the fault to this agent (a loss) instead of crashing the batch.

The server prints a single ``READY`` line to stderr once ``main.agent`` is
importable, then serves requests until stdin is closed. Launch with
``cwd=<repo>`` so ``import main`` / the repo's ``cg`` resolve locally.
"""
import json
import os
import sys


def main() -> int:
    sys.path.insert(0, os.getcwd())  # repo root: resolve `main` / `cg`
    import main as main_mod  # the project's Kaggle submission entry point
    assert hasattr(main_mod, "agent")

    sys.stderr.write("READY\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        obs = json.loads(line)
        try:
            action = main_mod.agent(obs)
        except Exception as exc:  # noqa: BLE001 - report, never crash
            payload = {"__error__": f"{type(exc).__name__}: {exc}"}
        else:
            payload = action
        sys.stdout.write(json.dumps(payload))
        sys.stdout.write("\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
