"""Fetch step-level Kaggle episode replays for fable's losses (SOT-1894).

SOT-1835 established that the *internal* RPC
``competitions.EpisodeService/GetEpisodeReplay`` returns HTTP 404 while the
competition is live. SOT-1894's first job is to re-check availability — and
the check (2026-07-23) found that the **public CDN endpoint**

    https://www.kaggleusercontent.com/episodes/<episodeId>.json

now serves the full kaggle-environments replay (per-step board state
``current``, engine ``logs``, legal-move ``select`` options and both agents'
chosen ``action``), even though the internal RPC still 404s. That is exactly
the step-level data this analysis needs, so the data gate is OPEN via this
route.

This script:
  1. probes BOTH endpoints and records the result in
     ``data/replay_availability.json`` (the SOT-1894 availability record);
  2. selects fable's **lost** episodes from the cached ListEpisodes data
     (``data/episodes/sub_*.json``, written by ``kaggle_episodes.py``),
     ordered by opponent rating at match time (upper-band first);
  3. downloads their replays into ``data/replays/<episodeId>.json``
     (skip-if-cached, so re-runs only fetch new losses).

Replays are ~2MB each and are NOT committed (see .gitignore); the derived
summary from ``analyze_replays.py`` is what lands in git.

Usage (from repo root):
    python analysis/kaggle_replays.py                # availability + all losses
    python analysis/kaggle_replays.py --check-only   # availability probe only
    python analysis/kaggle_replays.py --limit 20     # cap downloads
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
EPISODE_DIR = os.path.join(DATA_DIR, "episodes")
REPLAY_DIR = os.path.join(DATA_DIR, "replays")

RPC_URL = "https://www.kaggle.com/api/i/competitions.EpisodeService/GetEpisodeReplay"
PUBLIC_URL = "https://www.kaggleusercontent.com/episodes/{eid}.json"

# Our own team on the leaderboard; used to pick our seat in each episode.
OUR_TEAM_ID = 16534061


def _http(url: str, body: dict | None = None, token: str | None = None,
          timeout: int = 60) -> tuple[int, bytes]:
    """(status, payload) for one request; HTTP errors return their status."""
    headers = {"User-Agent": "ptcg-agent-fable-analysis/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def check_availability(probe_episode_id: int) -> dict:
    """Probe the internal RPC and the public CDN endpoint for one episode."""
    from kaggle_episodes import _token

    result = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probe_episode_id": probe_episode_id,
    }
    try:
        status, _ = _http(RPC_URL, body={"episodeId": probe_episode_id},
                          token=_token())
    except Exception as exc:  # noqa: BLE001 - the probe itself must not crash
        status = f"error: {exc}"
    result["rpc_GetEpisodeReplay"] = {"url": RPC_URL, "status": status}

    url = PUBLIC_URL.format(eid=probe_episode_id)
    try:
        status, payload = _http(url)
        ok = status == 200 and payload.lstrip()[:1] == b"{"
        steps = len(json.loads(payload).get("steps", [])) if ok else 0
    except Exception as exc:  # noqa: BLE001
        status, ok, steps = f"error: {exc}", False, 0
    result["public_endpoint"] = {"url": url, "status": status,
                                 "replay_ok": ok, "steps": steps}
    result["replay_available"] = bool(result["public_endpoint"].get("replay_ok"))
    return result


def our_losses() -> list[dict]:
    """Lost episodes from the ListEpisodes cache, upper-band opponents first.

    Seat resolution uses the per-agent ``teamId`` (our leaderboard team), NOT
    the submission id, so it also works for episodes fetched for older
    submissions. ``index`` is absent for seat 0 in the raw API response.
    """
    rows = []
    for path in sorted(glob.glob(os.path.join(EPISODE_DIR, "sub_*.json"))):
        with open(path) as fh:
            payload = json.load(fh)
        for ep in payload.get("episodes", []):
            agents = ep.get("agents", [])
            if len(agents) != 2:
                continue
            ours = [a for a in agents if a.get("teamId") == OUR_TEAM_ID]
            theirs = [a for a in agents if a.get("teamId") != OUR_TEAM_ID]
            if len(ours) != 1 or len(theirs) != 1:
                continue
            if (ours[0].get("reward") or 0) >= (theirs[0].get("reward") or 0):
                continue  # win or draw
            rows.append({
                "episode_id": ep["id"],
                "submission_id": payload.get("submissionId"),
                "our_index": ours[0].get("index", 0),
                "our_rating": ours[0].get("initialScore"),
                "opp_rating": theirs[0].get("initialScore"),
                "opp_team_id": theirs[0].get("teamId"),
                "end_time": ep.get("endTime"),
            })
    # De-dup across submission caches, then strongest opponents first.
    uniq = {r["episode_id"]: r for r in rows}
    return sorted(uniq.values(),
                  key=lambda r: -(r.get("opp_rating") or 0.0))


def fetch_replay(episode_id: int) -> str | None:
    """Download one replay to the cache; returns the path (None on failure)."""
    os.makedirs(REPLAY_DIR, exist_ok=True)
    path = os.path.join(REPLAY_DIR, f"{episode_id}.json")
    if os.path.exists(path):
        return path
    status, payload = _http(PUBLIC_URL.format(eid=episode_id))
    if status != 200 or payload.lstrip()[:1] != b"{":
        print(f"  episode {episode_id}: unavailable (HTTP {status})")
        return None
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check-only", action="store_true",
                    help="probe availability, write the record, and stop")
    ap.add_argument("--limit", type=int, default=0,
                    help="max replays to download (0 = all losses)")
    args = ap.parse_args()

    losses = our_losses()
    if not losses:
        print("No cached losses — run analysis/kaggle_episodes.py first.")
        return 1

    avail = check_availability(losses[0]["episode_id"])
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "replay_availability.json"), "w") as fh:
        json.dump(avail, fh, indent=1)
    rpc = avail["rpc_GetEpisodeReplay"]["status"]
    pub = avail["public_endpoint"]["status"]
    print(f"GetEpisodeReplay RPC: HTTP {rpc} / public endpoint: HTTP {pub} "
          f"(replay_ok={avail['replay_available']})")
    if args.check_only:
        return 0
    if not avail["replay_available"]:
        print("Replays still gated — stopping without downloads.")
        return 2

    todo = losses[: args.limit] if args.limit else losses
    print(f"{len(losses)} cached losses; downloading {len(todo)} replay(s) "
          f"(opponent-rating desc)...")
    manifest = []
    for row in todo:
        path = fetch_replay(row["episode_id"])
        if path:
            row["replay_file"] = os.path.relpath(path, HERE)
            manifest.append(row)
            print(f"  episode {row['episode_id']}: opp {row['opp_rating']:.0f} "
                  f"-> {row['replay_file']}")
    with open(os.path.join(DATA_DIR, "replay_manifest.json"), "w") as fh:
        json.dump({"fetched_at": avail["checked_at"], "losses": manifest},
                  fh, indent=1)
    print(f"{len(manifest)} replay(s) cached under analysis/data/replays/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
