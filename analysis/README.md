# analysis/ — Kaggle episode replay & defeat-gap analysis (SOT-1835)

Reproducible pipeline that quantifies *why* fable loses relative to higher-rated
agents in the `pokemon-tcg-ai-battle` competition, and emits a prioritized
improvement-hypothesis report.

## What it does

| Step | Script | Output |
| --- | --- | --- |
| 1. Fetch real match history | `kaggle_episodes.py` | `data/episodes/sub_<id>.json`, `data/leaderboard.csv` |
| 2. Tag board-level defeat causes (local self-play) | `local_loss_tags.py` | `data/local_loss_tags.json` |
| 3. Analyze + write report | `analyze_episodes.py` | `../docs/episode-analysis.md`, `data/episode_summary.json` |

## Run it (from repo root)

```bash
# 1. real Kaggle episodes for our COMPLETE submissions (needs KAGGLE_API_TOKEN)
python analysis/kaggle_episodes.py

# 2. board-level defeat tags via local self-play (no network; ~7 min at defaults)
python analysis/local_loss_tags.py --n 60 --budget 420          # fable vs Greedy
python analysis/local_loss_tags.py --n 40 --mirror --budget 600 # fable vs fable (optional; slower)

# 3. regenerate the report from the cache (offline, deterministic)
python analysis/analyze_episodes.py
```

Or all at once: `bash analysis/run_all.sh`.

## Data availability (important)

* **Available**: `EpisodeService.ListEpisodes` gives every episode's outcome
  (`reward`), opponent `teamId`, both sides' rating at match time
  (`initialScore` / `updatedScore`) and the match wall-clock. This drives the
  rating-band win/loss analysis on **real** data.
* **Gated while the competition is live**: `GetEpisodeReplay` returns HTTP 404,
  so the step-by-step board is not downloadable. Board-level defeat causes
  (deck-out / prize race / board wipe / card effect) are therefore reproduced
  locally via `local_loss_tags.py`, which reads the cabt engine's terminal
  `RESULT` log `reason` code.

## Re-running / incremental fetch

`kaggle_episodes.py` merges freshly fetched episodes into the per-submission
cache by episode id, so re-running after new ladder matches only adds the new
ones. `analyze_episodes.py` reads purely from the cache, so the report is
reproducible offline once fetched.
