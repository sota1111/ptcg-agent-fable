"""Pure-Python value network (SOT-1837) — the SINGLE canonical forward pass.

A tiny one-hidden-layer MLP (input -> H tanh -> 1 sigmoid) implemented in the
standard library only (no numpy / torch). The trainer (train/train_value.py)
and the Kaggle-side inference evaluator (agents/learned_value.py) BOTH run this
exact forward, so a value net exported from a torch/GPU run and reloaded for
pure-Python inference produces identical predictions (the SOT-1837 "一致テスト").

Weights serialize to a plain JSON dict (no binary), so the exported artifact is
diffable and dependency-free. A learned MLP is a linear-algebra object; keeping
it in explicit Python lists costs a little speed but removes every runtime
dependency from the submission path.
"""
import json
import math

from .value_features import FEATURE_DIM, FEATURE_VERSION


def _sigmoid(z: float) -> float:
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


class ValueNet:
    """input(D) -> hidden(H, tanh) -> output(1, sigmoid) win-probability net.

    Weights are plain Python lists:
      W1: H x D, b1: H, W2: H, b2: scalar.
    """

    def __init__(self, W1, b1, W2, b2, feature_dim=None, feature_version=None):
        self.W1 = [list(row) for row in W1]
        self.b1 = list(b1)
        self.W2 = list(W2)
        self.b2 = float(b2)
        self.hidden = len(self.b1)
        self.feature_dim = feature_dim if feature_dim is not None else (
            len(self.W1[0]) if self.W1 else 0)
        self.feature_version = (FEATURE_VERSION if feature_version is None
                                else feature_version)

    # ---- construction ----------------------------------------------------

    @classmethod
    def init(cls, hidden: int, rng, dim: int = FEATURE_DIM) -> "ValueNet":
        """Deterministic small-scale init from a random.Random `rng`.

        He/Xavier-ish scaling keeps early activations in range; the exact
        constants don't matter for reproducibility as long as `rng` is seeded.
        """
        s1 = math.sqrt(1.0 / max(1, dim))
        s2 = math.sqrt(1.0 / max(1, hidden))
        W1 = [[rng.uniform(-s1, s1) for _ in range(dim)] for _ in range(hidden)]
        b1 = [0.0 for _ in range(hidden)]
        W2 = [rng.uniform(-s2, s2) for _ in range(hidden)]
        b2 = 0.0
        return cls(W1, b1, W2, b2, feature_dim=dim)

    # ---- forward ---------------------------------------------------------

    def _pre(self, x):
        """Return (a1, o): hidden activations and the sigmoid output."""
        W1, b1 = self.W1, self.b1
        a1 = [0.0] * self.hidden
        for j in range(self.hidden):
            row = W1[j]
            s = b1[j]
            for i, xi in enumerate(x):
                s += row[i] * xi
            a1[j] = math.tanh(s)
        z2 = self.b2
        W2 = self.W2
        for j in range(self.hidden):
            z2 += W2[j] * a1[j]
        return a1, _sigmoid(z2)

    def forward(self, x) -> float:
        """Win-probability in [0, 1] for the feature vector `x`."""
        return self._pre(x)[1]

    def forward_batch(self, X) -> list:
        return [self._pre(x)[1] for x in X]

    # ---- training (stdlib SGD; the torch path lives in train_value.py) ---

    def train_epoch(self, X, y, lr: float, l2: float = 0.0) -> float:
        """One full pass of mini-batch-of-1 SGD (MSE on the sigmoid output).

        Returns the mean squared error over the batch BEFORE the updates of the
        current epoch's later samples (a standard running-loss estimate).
        """
        n = len(X)
        if n == 0:
            return 0.0
        total = 0.0
        H, D = self.hidden, self.feature_dim
        for x, target in zip(X, y):
            a1, o = self._pre(x)
            err = o - target
            total += err * err
            # delta on the pre-sigmoid output: err * o'(z2), o' = o(1-o)
            delta2 = err * o * (1.0 - o)
            # output-layer grads
            b2_grad = delta2
            # hidden-layer deltas: (delta2 * W2_j) * (1 - a1_j^2)
            for j in range(H):
                dz1 = delta2 * self.W2[j] * (1.0 - a1[j] * a1[j])
                row = self.W1[j]
                if l2:
                    for i in range(D):
                        row[i] -= lr * (dz1 * x[i] + l2 * row[i])
                else:
                    for i in range(D):
                        row[i] -= lr * dz1 * x[i]
                self.b1[j] -= lr * dz1
                w2j = self.W2[j]
                self.W2[j] = w2j - lr * (delta2 * a1[j] + l2 * w2j)
            self.b2 -= lr * b2_grad
        return total / n

    # ---- serialization ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "feature_version": self.feature_version,
            "feature_dim": self.feature_dim,
            "hidden": self.hidden,
            "arch": [self.feature_dim, self.hidden, 1],
            "activation": "tanh",
            "output": "sigmoid",
            "W1": self.W1,
            "b1": self.b1,
            "W2": self.W2,
            "b2": self.b2,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ValueNet":
        return cls(d["W1"], d["b1"], d["W2"], d["b2"],
                   feature_dim=d.get("feature_dim"),
                   feature_version=d.get("feature_version"))

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def load(cls, path: str) -> "ValueNet":
        with open(path) as f:
            return cls.from_dict(json.load(f))
