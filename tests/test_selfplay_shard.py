"""SOT-1865: sharded on-policy self-play generation + merge.

These tests are engine-independent — they exercise the shard *partition* math
and the merge/union file logic, NOT the cabt engine (which gen_selfplay drives
for real data). The point is to lock two properties the on-policy data pipeline
relies on:

  1. `--n-shards M --shard-index k` for k in 0..M-1 partitions the global match
     space `range(n)` with NO overlap and NO gap (disjoint union), so merging
     the shards reconstructs one coherent length-n run.
  2. `merge_selfplay` unions the shard rows and rebuilds a combined meta
     (summed matches/faults/gen-seconds, per-shard provenance), and rejects a
     shard whose feature_version disagrees with the runtime.
"""
import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from agents.value_features import FEATURE_VERSION
from train import merge_selfplay


class TestShardPartition(unittest.TestCase):
    """The modulo predicate `i % M == k` is exactly the shard selector inside
    gen_selfplay.generate(); assert it partitions range(n)."""

    def test_shards_partition_match_space(self):
        n, M = 2000, 4
        owned = [set(i for i in range(n) if i % M == k) for k in range(M)]
        # disjoint
        for a in range(M):
            for b in range(a + 1, M):
                self.assertEqual(owned[a] & owned[b], set(),
                                 f"shards {a},{b} overlap")
        # cover
        union = set().union(*owned)
        self.assertEqual(union, set(range(n)))
        # a partial run (subset of shards) is still a disjoint subset — never
        # double-counts a match.
        partial = owned[0] | owned[1] | owned[2]
        self.assertEqual(len(partial), len(owned[0]) + len(owned[1]) + len(owned[2]))

    def test_single_shard_is_the_whole_run(self):
        n = 50
        owned = set(i for i in range(n) if i % 1 == 0)
        self.assertEqual(owned, set(range(n)))


def _write_shard(path, rows, **meta):
    meta.setdefault("feature_version", FEATURE_VERSION)
    with open(path, "w") as f:
        f.write(json.dumps({"meta": meta}) + "\n")
        for r in rows:
            f.write(json.dumps(r) + "\n")


class TestMerge(unittest.TestCase):
    def test_union_and_combined_meta(self):
        with tempfile.TemporaryDirectory() as d:
            s0 = os.path.join(d, "s0.jsonl")
            s1 = os.path.join(d, "s1.jsonl")
            out = os.path.join(d, "merged.jsonl")
            _write_shard(s0, [{"f": [0.0], "y": 1.0}, {"f": [1.0], "y": 0.0}],
                         seed=1, shard_index=0, n_shards=2,
                         matches_played=10, faults=1, gen_seconds=100.0,
                         agent="mcts")
            _write_shard(s1, [{"f": [2.0], "y": 0.5}],
                         seed=1, shard_index=1, n_shards=2,
                         matches_played=8, faults=0, gen_seconds=90.0,
                         agent="mcts")
            merge_selfplay.main_argv(["--out", out, s0, s1])

            with open(out) as f:
                lines = [l for l in f.read().splitlines() if l]
            meta = json.loads(lines[0])["meta"]
            rows = [json.loads(l) for l in lines[1:]]

            self.assertEqual(len(rows), 3)              # union of 2 + 1
            self.assertEqual(meta["samples"], 3)
            self.assertEqual(meta["win_samples"], 1)    # exactly one y==1.0
            self.assertEqual(meta["matches_played"], 18)
            self.assertEqual(meta["faults"], 1)
            self.assertEqual(meta["gen_seconds"], 190.0)
            self.assertEqual(meta["n_shards_merged"], 2)
            self.assertEqual(len(meta["merged_from"]), 2)

    def test_feature_version_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.jsonl")
            out = os.path.join(d, "merged.jsonl")
            _write_shard(bad, [{"f": [0.0], "y": 1.0}],
                         feature_version=FEATURE_VERSION + 999,
                         seed=1, shard_index=0, n_shards=1)
            with self.assertRaises(SystemExit):
                merge_selfplay.main_argv(["--out", out, bad])


if __name__ == "__main__":
    unittest.main()
