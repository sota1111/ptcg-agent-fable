#!/usr/bin/env bash
# Run a screen/confirm A/B wave of candidates DIRECTLY against the champion
# (SOT-1863). Unlike eval/run_kpi_wave.sh (agent-b = greedy, candidate winrate
# compared to a separate baseline line), this plays each candidate MCTS head to
# head against the champion MCTS (agent-b = mcts with FABLE_CONFIG), so the
# recorded winrate_a IS the candidate-vs-champion win rate and its Wilson lower
# bound is the promotion gate straight away.
#
# Both agents run at the SAME throttled time budget (BUDGET, default 0.06s) so a
# full mirror-MCTS wave is tractable; the champion's own schedule already drops
# to 0.2s under time pressure, so a small equal budget stays a fair relative
# comparison (same convention as analysis/local_loss_tags.py --fable-budget and
# the SOT-1837 value-net A/B in README.md).
#
# Each candidate's --override-a MUST carry the FULL eval_weights it wants
# (eval_weights is merged at the top level, so a partial dict would drop the
# champion's deck-preservation gradient). Use the eval_weights the champion
# ships plus the SOT-1863 opt-in board-development terms.
#
# Usage:
#   eval/run_ab_vs_champion.sh <phase> <n_per_seed> <seeds> <out> \
#       <label>='<override_json>' [<label>='<override_json>' ...]
#
# Example:
#   eval/run_ab_vs_champion.sh screen 20 2001,2002 kpi_history.jsonl \
#       bench2_30='{"time_budget_s":0.06,"eval_weights":{"deck_low":-0.2,"deck_low_at":14,"deck_low_prize_gate":3,"bench_dev":0.3,"bench_dev_cap":2}}'
set -euo pipefail
cd "$(dirname "$0")/.."

PHASE="$1"; N="$2"; SEEDS="$3"; OUT="$4"; shift 4
BUDGET="${BUDGET:-0.06}"

# Champion (agent B) = FABLE_CONFIG at the shared throttled budget.
CHAMP_B="$(python3 - "$BUDGET" <<'PY'
import json, sys
sys.path.insert(0, ".")
from agents import GreedyAgent  # warm the repo agents package before main
from main import FABLE_CONFIG
cfg = dict(FABLE_CONFIG)
cfg["time_budget_s"] = float(sys.argv[1])
print(json.dumps(cfg))
PY
)"
echo "champion (B) = $CHAMP_B"

for spec in "$@"; do
  label="${spec%%=*}"
  override="${spec#*=}"
  python3 eval/kpi.py --label "$label" --phase "$PHASE" \
      --agent-a mcts --agent-b mcts --n "$N" --seeds "$SEEDS" \
      --override-a "$override" --config-b "$CHAMP_B" \
      --note "SOT-1863 vs-champion A/B (budget=${BUDGET}s)" --out "$OUT"
done

echo "wave done -> $OUT"
