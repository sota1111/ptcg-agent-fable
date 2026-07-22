"""Fetch fable's real Kaggle match episodes (SOT-1835).

Pulls the match (episode) history for our own submissions in the
``pokemon-tcg-ai-battle`` competition from Kaggle's internal EpisodeService and
caches the raw responses so the downstream analysis (``analyze_episodes.py``)
runs offline and reproducibly.

What we CAN get (verified 2026-07-22):
  * ``ListEpisodes`` (filter ``{"submissionId": <id>}``) returns, per episode,
    both agents' ``reward`` (win/loss/draw), ``submissionId``, ``teamId`` and
    their ``initialScore`` / ``updatedScore`` (the rating at match time), plus
    the episode ``createTime`` / ``endTime`` (match wall-clock).
What we CANNOT get while the competition is live:
  * ``GetEpisodeReplay`` is not exposed (HTTP 404) — the step-by-step board
    state is gated during the active competition, so board-level defeat causes
    (deck-out / prize race / …) are derived locally instead
    (see ``local_loss_tags.py``).

Auth: uses the Kaggle API token from the ``KAGGLE_API_TOKEN`` env var, or the
``KAGGLE_KEY`` / ``kaggle.json`` fallbacks. No third-party deps (stdlib
``urllib``) to match the repo's no-pip-deps policy.

Usage (run from repo root):
    python analysis/kaggle_episodes.py                 # auto-discover COMPLETE submissions
    python analysis/kaggle_episodes.py 54883092 ...    # explicit submission ids
"""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import time
import urllib.request

COMPETITION = "pokemon-tcg-ai-battle"
LIST_EPISODES_URL = (
    "https://www.kaggle.com/api/i/competitions.EpisodeService/ListEpisodes"
)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
EPISODE_DIR = os.path.join(DATA_DIR, "episodes")


def _token() -> str:
    tok = os.environ.get("KAGGLE_API_TOKEN") or os.environ.get("KAGGLE_KEY")
    if tok:
        return tok
    for path in (
        os.path.expanduser("~/.kaggle/kaggle.json"),
        os.path.expanduser("~/.config/kaggle/kaggle.json"),
    ):
        if os.path.exists(path):
            with open(path) as fh:
                return json.load(fh).get("key", "")
    raise SystemExit(
        "No Kaggle credentials found: set KAGGLE_API_TOKEN or provide ~/.kaggle/kaggle.json"
    )


def _post(url: str, body: dict, token: str) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def list_episodes(submission_id: int, token: str) -> dict:
    """Raw ListEpisodes response for one submission (episodes/submissions/teams)."""
    return _post(LIST_EPISODES_URL, {"submissionId": int(submission_id)}, token)


def discover_submissions() -> list[dict]:
    """COMPLETE submissions for our team via the kaggle CLI (id, score, desc)."""
    try:
        out = subprocess.run(
            ["kaggle", "competitions", "submissions", COMPETITION, "-v"],
            capture_output=True, text=True, timeout=120, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(
            f"Could not auto-discover submissions via kaggle CLI ({exc}). "
            "Pass submission ids explicitly."
        )
    subs = []
    for row in csv.DictReader(io.StringIO(out)):
        if "COMPLETE" in (row.get("status") or ""):
            subs.append({
                "id": int(row["ref"]),
                "score": row.get("publicScore") or "",
                "description": (row.get("description") or "").strip(),
                "date": row.get("date") or "",
            })
    return subs


def fetch(submission_ids: list[int] | None = None) -> dict:
    """Fetch + cache episodes for the given (or auto-discovered) submissions."""
    os.makedirs(EPISODE_DIR, exist_ok=True)
    token = _token()

    if submission_ids:
        subs = [{"id": int(s), "score": "", "description": "", "date": ""}
                for s in submission_ids]
    else:
        subs = discover_submissions()
        print(f"Discovered {len(subs)} COMPLETE submission(s).")

    manifest = {"competition": COMPETITION, "fetched_at": _now_iso(),
                "submissions": []}
    for s in subs:
        sid = s["id"]
        try:
            resp = list_episodes(sid, token)
        except Exception as exc:  # noqa: BLE001 - one bad submission must not abort the run
            print(f"  submission {sid}: FETCH FAILED ({exc})")
            continue
        episodes = resp.get("episodes", [])
        # Merge into cache by episode id so re-runs accumulate new matches.
        path = os.path.join(EPISODE_DIR, f"sub_{sid}.json")
        prev = {}
        if os.path.exists(path):
            with open(path) as fh:
                prev = {e["id"]: e for e in json.load(fh).get("episodes", [])}
        merged = dict(prev)
        for e in episodes:
            merged[e["id"]] = e
        new_count = len(merged) - len(prev)
        payload = {
            "submissionId": sid,
            "score": s.get("score", ""),
            "description": s.get("description", ""),
            "date": s.get("date", ""),
            "episodes": list(merged.values()),
            # teams/submissions carry opponent metadata; keep the freshest copy.
            "teams": resp.get("teams", []),
            "submissions": resp.get("submissions", []),
        }
        with open(path, "w") as fh:
            json.dump(payload, fh, indent=1)
        print(f"  submission {sid}: {len(merged)} episodes "
              f"(+{new_count} new) -> {os.path.relpath(path, HERE)}")
        s["episodes"] = len(merged)
        s["new"] = new_count
        manifest["submissions"].append(s)

    fetch_leaderboard()

    with open(os.path.join(DATA_DIR, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    return manifest


def fetch_leaderboard() -> str | None:
    """Cache the public leaderboard CSV (teamId -> name/score/rank) via kaggle CLI.

    Best-effort: opponent rating at match time already lives in each episode, so
    the leaderboard is only used to name teams and read their *final* score.
    """
    import tempfile
    import zipfile

    dest = os.path.join(DATA_DIR, "leaderboard.csv")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["kaggle", "competitions", "leaderboard", COMPETITION,
                 "--download", "-p", tmp],
                capture_output=True, text=True, timeout=120, check=True,
            )
            zips = [f for f in os.listdir(tmp) if f.endswith(".zip")]
            if not zips:
                return None
            with zipfile.ZipFile(os.path.join(tmp, zips[0])) as zf:
                names = [n for n in zf.namelist() if n.endswith(".csv")]
                if not names:
                    return None
                with zf.open(names[0]) as src, open(dest, "wb") as out:
                    out.write(src.read())
        print(f"  leaderboard -> {os.path.relpath(dest, HERE)}")
        return dest
    except Exception as exc:  # noqa: BLE001 - enrichment only
        print(f"  leaderboard: skipped ({exc})")
        return None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


if __name__ == "__main__":
    ids = [int(a) for a in sys.argv[1:]] or None
    fetch(ids)
