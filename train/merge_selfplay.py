"""Merge sharded self-play datasets into one training file (SOT-1865).

``train/gen_selfplay.py --n-shards M --shard-index k`` writes one JSONL per
shard, each covering the disjoint global match indices ``i % M == k`` of the
same base run. This unions those shards back into a single dataset and rebuilds
a combined meta line (summed matches/samples/faults/gen-seconds, per-shard
provenance), verifying every shard shares the same ``feature_version``.

Usage (from the repo root):
    python3 train/merge_selfplay.py --out train/data/onpolicy.jsonl \
        train/data/onpolicy.shard*.jsonl
"""
import argparse
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from agents.value_features import FEATURE_VERSION


def read_shard(path: str):
    meta = {}
    rows = []
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if i == 0 and "meta" in obj:
                meta = obj["meta"]
                continue
            rows.append(obj)
    return meta, rows


def main_argv(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="train/data/onpolicy.jsonl")
    ap.add_argument("inputs", nargs="+",
                    help="shard JSONL paths (globs allowed)")
    args = ap.parse_args(argv)

    paths = []
    for pat in args.inputs:
        hits = sorted(glob.glob(pat)) or [pat]
        paths.extend(hits)
    # de-dup while preserving order
    seen = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]

    combined = []
    shard_meta = []
    total_played = total_faults = 0
    total_gen_seconds = 0.0
    feature_version = FEATURE_VERSION
    for p in paths:
        meta, rows = read_shard(p)
        fv = meta.get("feature_version", FEATURE_VERSION)
        if fv != FEATURE_VERSION:
            raise SystemExit(
                f"{p}: feature_version {fv} != runtime {FEATURE_VERSION}")
        combined.extend(rows)
        total_played += int(meta.get("matches_played", 0) or 0)
        total_faults += int(meta.get("faults", 0) or 0)
        total_gen_seconds += float(meta.get("gen_seconds", 0.0) or 0.0)
        shard_meta.append({
            "path": os.path.basename(p),
            "seed": meta.get("seed"),
            "shard_index": meta.get("shard_index"),
            "n_shards": meta.get("n_shards"),
            "matches_played": meta.get("matches_played"),
            "samples": len(rows),
            "gen_seconds": meta.get("gen_seconds"),
            "agent": meta.get("agent"),
        })

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    pos = sum(1 for r in combined if r.get("y") == 1.0)
    meta = {
        "feature_version": feature_version,
        "merged_from": shard_meta,
        "n_shards_merged": len(paths),
        "matches_played": total_played,
        "faults": total_faults,
        "gen_seconds": round(total_gen_seconds, 1),
        "samples": len(combined),
        "win_samples": pos,
    }
    with open(args.out, "w") as f:
        f.write(json.dumps({"meta": meta}) + "\n")
        for r in combined:
            f.write(json.dumps(r) + "\n")
    print(f"merged {len(paths)} shards -> {args.out}: {len(combined)} samples "
          f"({pos} win / {len(combined) - pos} other), "
          f"matches_played={total_played}, faults={total_faults}, "
          f"gen_seconds={total_gen_seconds:.0f}")


if __name__ == "__main__":
    main_argv()
