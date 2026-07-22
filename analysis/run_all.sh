#!/usr/bin/env bash
# End-to-end episode analysis pipeline (SOT-1835). Run from repo root.
#   bash analysis/run_all.sh [greedy_n] [greedy_budget_s]
set -euo pipefail
cd "$(dirname "$0")/.."

GN="${1:-60}"
GB="${2:-420}"

echo "== 1/3 fetch real Kaggle episodes =="
python analysis/kaggle_episodes.py

echo "== 2/3 local board-level defeat tags (fable vs Greedy, n=$GN budget=${GB}s) =="
python analysis/local_loss_tags.py --n "$GN" --budget "$GB"

echo "== 3/3 analyze + write docs/episode-analysis.md =="
python analysis/analyze_episodes.py
