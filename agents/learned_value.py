"""Learned leaf evaluator (SOT-1837) — optional value-net drop-in for layer [4].

`LearnedEvaluator` implements the same `Evaluator` interface as
`HeuristicEvaluator` (agents/evaluator.py): `evaluate(obs, root_player) -> [0,1]`
estimated win probability for `root_player`. Terminal states are scored EXACTLY
as the heuristic does (result-based, not learned) so a mispredicting net can
never invert a decided game; only non-terminal leaves use the network.

The forward pass and feature extraction are the pure-Python, numpy-free modules
shared with the trainer (`agents.value_net` / `agents.value_features`), so the
weights a GPU run produces reload unchanged into the Kaggle submission path.

This evaluator is NOT wired into the champion (main.py FABLE_CONFIG is
untouched); it is only constructed when a bench/A-B config asks for it
(`value_net=<path>` on MctsAgent, or `make_evaluator({"learned": path})`).
Feature-version mismatch raises at load time rather than silently mispredicting.
"""
from .evaluator import Evaluator
from .value_features import FEATURE_VERSION, extract
from .value_net import ValueNet


class LearnedEvaluator(Evaluator):
    """Value-net leaf evaluation with exact terminal handling."""

    def __init__(self, net: ValueNet):
        if net.feature_version != FEATURE_VERSION:
            raise ValueError(
                f"value net feature_version {net.feature_version} != "
                f"runtime {FEATURE_VERSION}; retrain/export before use")
        self.net = net

    @classmethod
    def from_path(cls, path: str) -> "LearnedEvaluator":
        return cls(ValueNet.load(path))

    def evaluate(self, obs, root_player: int) -> float:
        current = getattr(obs, "current", None)
        if current is None:
            return 0.5
        result = getattr(current, "result", -1)
        if result is not None and result != -1:
            if result == root_player:
                return 1.0
            if result == 1 - root_player:
                return 0.0
            return 0.5  # draw (result == 2) or unknown future value
        players = getattr(current, "players", None) or ()
        if len(players) < 2:
            return 0.5
        return self.net.forward(extract(obs, root_player))
