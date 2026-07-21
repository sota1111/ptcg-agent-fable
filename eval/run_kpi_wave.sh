#!/usr/bin/env bash
# Run one screen/confirm A/B wave in parallel (SOT-1796).
#
# Each candidate is one eval/kpi.py process (internally serial over its seed
# shards, one CPU core); the wave runs every candidate concurrently and then
# appends their KPI lines to kpi_history.jsonl IN THE GIVEN ORDER (each process
# writes a private temp file first, so concurrent appends never interleave).
#
# Usage:
#   eval/run_kpi_wave.sh <phase> <n_per_seed> <seeds> <out> \
#       <label>=<override_json> [<label>=<override_json> ...]
#
#   phase        baseline | screen | confirm
#   n_per_seed   matches per seed shard
#   seeds        comma-separated distinct seeds (one shard each)
#   out          kpi_history.jsonl (appended to)
#   label=json   candidate name = JSON delta merged onto FABLE_CONFIG
#                (use label=baseline with an empty {} for the champion config)
#
# Example:
#   eval/run_kpi_wave.sh screen 15 2001,2002,2003,2004 kpi_history.jsonl \
#       baseline='{}' dm005='{"deviate_margin":0.05}'
set -euo pipefail
cd "$(dirname "$0")/.."

PHASE="$1"; N="$2"; SEEDS="$3"; OUT="$4"; shift 4
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

declare -a TMPS=() LOGS=() LABELS=()
i=0
for spec in "$@"; do
  label="${spec%%=*}"
  override="${spec#*=}"
  tmp="$TMPDIR/$(printf '%03d' "$i")_${label}.jsonl"
  log="$TMPDIR/$(printf '%03d' "$i")_${label}.log"
  TMPS+=("$tmp"); LOGS+=("$log"); LABELS+=("$label")
  python3 eval/kpi.py --label "$label" --phase "$PHASE" \
      --agent-a mcts --agent-b greedy --n "$N" --seeds "$SEEDS" \
      --override-a "$override" --out "$tmp" \
      > "$log" 2>&1 &
  i=$((i + 1))
done

echo "launched $i candidates (phase=$PHASE, n=${N}x seeds=${SEEDS}); waiting..."
FAIL=0
wait || FAIL=1

for j in "${!TMPS[@]}"; do
  if [[ -s "${TMPS[$j]}" ]]; then
    cat "${TMPS[$j]}" >> "$OUT"
    tail -1 "${LOGS[$j]}" 2>/dev/null || true
  else
    echo "!! FAILED: ${LABELS[$j]} — see log:"
    tail -5 "${LOGS[$j]}" 2>/dev/null || true
    FAIL=1
  fi
done

echo "wave done -> $OUT"
exit "$FAIL"
