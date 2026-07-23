# ptcg-agent-fable

PTCG AI Battle agent **fable** — submission layout, local eval harness, and
Random/Greedy baselines for the Kaggle Pokémon TCG AI Battle Challenge.

Submission layout (top level): `main.py` + `deck.csv` + `cg/` (engine).

> **License guard:** `cg/` (engine) and `data/` (card CSVs) are
> competition-use-only and MUST NEVER be committed. Both are gitignored on
> independent lines; before every commit run
> `git diff --cached --name-only` and check nothing under `cg/` or `data/`
> is staged.

## Setup

1. Fetch the pinned common-core revision:

   ```bash
   git submodule update --init --recursive
   bash scripts/check_core_compatibility.sh
   ```

2. Engine + card data (copied from a sibling competition checkout, both
   land gitignored):

   ```bash
   bash scripts/setup_engine.sh
   # default SRC=/workspaces/kaggle-ptcg-ume/data/simulation/extracted
   # override: SRC=/path/to/extracted bash scripts/setup_engine.sh
   ```

3. No pip dependencies for local eval (`requirements.txt` is documentation
   only) — plain `python3` works.

## Shared core dependency

This repository consumes [`ptcg-agent-core`](vendor/ptcg-agent-core) as a
pinned Git submodule, matching the integration boundary used by the PTCG
agent family.  The core owns algorithm-independent contracts and the shared
[Kaggle submission guide](vendor/ptcg-agent-core/docs/kaggle-submission.md).
Fable continues to own its Python adapter, deck, and search/policy code; those
are deliberately outside the common-core contract.

The pinned commit makes a checkout reproducible. To update it, first review
the core changelog and schema versions, then run:

```bash
git -C vendor/ptcg-agent-core fetch origin main
git -C vendor/ptcg-agent-core checkout origin/main
bash scripts/check_core_compatibility.sh
python3 -m unittest discover -s tests -v
git add vendor/ptcg-agent-core
```

Commit the resulting gitlink change together with compatibility evidence.
If an update is incompatible, restore the previously recorded gitlink with
`git checkout -- vendor/ptcg-agent-core` and re-run
`git submodule update --init`. Never point the submodule at an unreviewed
moving branch during a submission build.

## Run one match

```bash
python3 eval/run_match.py            # deck.csv vs deck.csv, random policies
python3 eval/run_match.py deckA.csv deckB.csv
```

Prints the winner and the decision count; raises on engine faults.

## N-match benchmark (win rate + Wilson 95% CI)

```bash
python3 eval/bench.py --agent-a greedy --agent-b random --n 100 --seed 20260720
```

- Sides alternate every match; per-match agent seeds derive from `--seed`
  (engine-internal RNG is not seedable, so agent-side reproducibility only).
- Faults are aggregated and must be 0: engine rejects (illegal actions),
  agent exceptions, and `BaseAgent` random-legal fallbacks.
- Reports win rate excl. draws with a Wilson 95% CI (and draws-as-half),
  timing per match/decision. `--json out.json` writes the full report.

### Sharded long benches

Run independent shards (same agents, distinct seeds) in parallel, then pool
the counts and recompute the CI:

```bash
python3 eval/bench.py --agent-a greedy --agent-b random --n 250 --seed 1 --json /tmp/s1.json &
python3 eval/bench.py --agent-a greedy --agent-b random --n 250 --seed 2 --json /tmp/s2.json &
wait
python3 eval/aggregate_shards.py /tmp/agg.json /tmp/s1.json /tmp/s2.json
```

## Deck selection (SOT-1794)

`deck.csv` is the measured pick from a 26-candidate field (25 Turin/NAIC 2026
tournament decks + the shared matsu/take/ume champion deck): a full greedy
round-robin screen (N=40/pair, mirrors + side alternation) followed by an
independent-seed confirm of the top 4 (N=120/pair vs the full field), judged
by aggregate Wilson 95% CI only. The champion deck won both stages
CI-separated from the runner-up; 25,800 games, 0 faults. Details:
`docs/deck_selection_report.md`.

```bash
python3 eval/deck_validator.py                      # deck.csv + decks/**/*.csv legality
python3 eval/compare_decks.py --n-per-pair 40 \
    --seed 1794001 --json eval/results/screen.json  # candidate round-robin
```

## Agents (`agents/`)

- `random_agent.py` — uniform random legal action (floor baseline).
- `greedy_agent.py` — one-ply heuristic over the engine's legal options;
  action ordering keeps development (play/attach/evolve/ability) ABOVE
  attack, and attack above retreat/end (putting attack on top loses:
  attacking ends the turn).
- `base.py` — submission contract + degradation to random-legal on any
  internal error (counted in `fallback_count`, expected 0 on the known pool).
- `observation.py` / `actions.py` — raw obs dict -> View; `obs.select` is the
  single source of truth for legality.
- `cards.py` — card-attribute feature index from the engine card master
  (no per-card ID/name special cases; unknown IDs -> neutral defaults).
- `rng.py` — single externally-seeded RNG; no global `random` in agents.
- `rule_policy.py` — 竹式 per-context rule policy (SOT-1682/1694): an
  explicit COUNT_MODE entry for ALL 49 SelectContexts (no random-fallback
  holes) plus the deck-reserve draw guard; `RuleAgent` is fallback layer 3.
- `evaluator.py` — heuristic leaf value for search (prizes dominant) with an
  optional deck-preservation gradient (`deck_low*` weights, SOT-1697).
- `planner.py` — determinized anytime MCTS over the engine search API
  (SOT-1672 lineage; `deviate_margin=0.1`), rule-table-driven in-tree counts.
- `mcts_agent.py` — the planner under the agent contract (per-decision Rng
  streams, budget-violation counting, greedy fallback).

`main.py` is the fable submission entry point (SOT-1795): champion
determinized MCTS (`FABLE_CONFIG`) + remaining-time budget governor +
layered fallbacks MCTS → Greedy → Rule → random-legal. `deck.csv` is the
SOT-1794 measured selection (see above). v1 measurements:
`docs/fable_v1_report.md` (aggregates in `docs/fable_v1/`).

## Learned value net (SOT-1837, opt-in)

A pure-Python (numpy-free, Kaggle-compatible) value network can replace the
heuristic leaf evaluator, trained from self-play. It is **off by default** —
the champion (`main.py` `FABLE_CONFIG`) is unchanged — and only activates when a
bench/agent config sets `value_net=<weights.json>` on `MctsAgent`.

```bash
# self-play data -> train (--backend torch trains on GPU; python is stdlib-only)
python3 train/gen_selfplay.py --n 250 --agent greedy --out train/data/selfplay.jsonl
python3 train/train_value.py --data train/data/selfplay.jsonl \
    --out train/weights/value.json --hidden 24 --epochs 60
# A/B a mode vs champion (leaf-eval: rollout_turns=0; early-cutoff: rollout_turns=2)
python3 eval/bench.py --agent-a mcts --agent-b mcts --n 40 \
    --config-a '{"value_net":"train/weights/value.json","rollout_turns":0,"time_budget_s":0.12,...}' \
    --config-b '{...FABLE_CONFIG at the same budget...}'
```

The exported weights reload byte-identically into the pure-Python inference path
(`agents/value_net.py` / `agents/learned_value.py`); the trainer and
`tests/test_learned_value.py` both assert this consistency. The SOT-1837 screen
(greedy off-policy data) landed at champion parity for both integration modes →
**not promoted**; see `docs/value_net_report.md` for the result and the
on-policy + real-GPU next steps.

## Board-development eval terms (SOT-1863, opt-in)

`HeuristicEvaluator` has two opt-in, default-OFF board-development weights aimed
at fable's biggest local loss cluster, board wipe (盤面全滅): `bench_dev` /
`bench_dev_cap` (a saturating bench-insurance bonus) and `evo_ready` (per
evolved Pokémon). The champion (`FABLE_CONFIG`) sets neither, so its behaviour is
unchanged. `eval/run_ab_vs_champion.sh` runs a candidate MCTS head-to-head
against the champion MCTS (so the recorded `winrate_a` is the vs-champion win
rate), and `analysis/local_loss_tags.py` takes a `FABLE_TAG_EVAL` JSON override
to re-tag board_wipe share under a candidate config. The SOT-1863 screen→confirm
(`bench_dev` best at 0.3/cap2) washed out on independent seeds (0.533 [0.431,
0.629]) and did not lower board_wipe share → **not promoted**; see
`docs/board_wipe_report.md`.

## Cross-repo battle (vs a sibling submission)

```bash
python3 eval/battle_vs.py --opponent ../ptcg-agent-matsu --n 30 --json out.json
python3 eval/battle_vs.py --aggregate shard1.json shard2.json   # pool shards
```

Each submission agent runs in its own subprocess (`eval/agent_server.py`,
cwd=its repo) because the repos' top-level `agents` packages collide
(SOT-1681); the host owns only this repo's engine and the orchestration.
Faults (agent error / illegal action / dead server) are charged as losses
and reported — the acceptance gate is 0.

## Building a submission

```bash
bash scripts/build_submission.sh
```

The builder follows the core-owned submission layout and validates that the
archive has the required top-level files and excludes the core checkout,
Git metadata, caches, credentials, tests, and local evaluation code. `cg/`
must come from `scripts/setup_engine.sh`; never commit the tarball. For setup,
authentication, submission, result checks, and troubleshooting, follow the
[shared core guide](vendor/ptcg-agent-core/docs/kaggle-submission.md).
