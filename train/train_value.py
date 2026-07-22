"""Value-net trainer (SOT-1837).

Loads self-play samples (train/gen_selfplay.py JSONL), trains the one-hidden-
layer MLP, exports the weights as dependency-free JSON, and runs a
train-forward vs exported-inference CONSISTENCY check (the acceptance-criterion
"一致テスト"): the pure-Python inference reloaded from JSON must reproduce the
trainer's own predictions to within a tolerance.

Two training backends, same architecture and same exported format:
  - ``--backend python`` (default, stdlib-only): agents.value_net SGD. Runs
    anywhere, including this GPU-less container.
  - ``--backend torch``: trains the identical MLP with torch on GPU when
    available, then copies the learned weights into the pure-Python ValueNet
    and exports. The RTX 3080 Ti / 8h-a-day path from the issue; the exported
    artifact is byte-for-byte the same JSON schema, so inference is unchanged.

Usage (from the repo root):
    python3 train/train_value.py --data train/data/selfplay.jsonl \
        --out train/weights/value.json --hidden 16 --epochs 40 --lr 0.2
"""
import argparse
import json
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from agents.value_features import FEATURE_DIM, FEATURE_VERSION
from agents.value_net import ValueNet


def load_data(path: str):
    rows = []
    meta = {}
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if i == 0 and "meta" in obj:
                meta = obj["meta"]
                continue
            rows.append((obj["f"], float(obj["y"])))
    fv = meta.get("feature_version", FEATURE_VERSION)
    if fv != FEATURE_VERSION:
        raise ValueError(f"data feature_version {fv} != runtime {FEATURE_VERSION}")
    return rows, meta


def split(rows, val_frac, rng):
    rows = list(rows)
    rng.shuffle(rows)
    n_val = int(len(rows) * val_frac)
    val = rows[:n_val]
    train = rows[n_val:]
    return train, val


def mse(net: ValueNet, rows) -> float:
    if not rows:
        return 0.0
    s = 0.0
    for x, y in rows:
        e = net.forward(x) - y
        s += e * e
    return s / len(rows)


def train_python(train, val, hidden, epochs, lr, l2, seed):
    rng = random.Random(seed)
    net = ValueNet.init(hidden, rng, dim=FEATURE_DIM)
    X = [x for x, _ in train]
    y = [t for _, t in train]
    order = list(range(len(X)))
    for ep in range(epochs):
        rng.shuffle(order)
        Xs = [X[i] for i in order]
        ys = [y[i] for i in order]
        loss = net.train_epoch(Xs, ys, lr, l2)
        if (ep + 1) % max(1, epochs // 5) == 0 or ep == 0:
            print(f"  epoch {ep + 1}/{epochs} train_mse~{loss:.4f} "
                  f"val_mse={mse(net, val):.4f}", flush=True)
    return net


def train_torch(train, val, hidden, epochs, lr, l2, seed):
    """Train the identical MLP with torch (GPU when available), then copy the
    weights into a pure-Python ValueNet so export/inference are unchanged."""
    import torch
    torch.manual_seed(seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  torch backend on {dev}", flush=True)
    Xt = torch.tensor([x for x, _ in train], dtype=torch.float32, device=dev)
    yt = torch.tensor([[t] for _, t in train], dtype=torch.float32, device=dev)
    model = torch.nn.Sequential(
        torch.nn.Linear(FEATURE_DIM, hidden), torch.nn.Tanh(),
        torch.nn.Linear(hidden, 1), torch.nn.Sigmoid()).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=l2)
    lossf = torch.nn.MSELoss()
    for ep in range(epochs):
        opt.zero_grad()
        out = model(Xt)
        loss = lossf(out, yt)
        loss.backward()
        opt.step()
        if (ep + 1) % max(1, epochs // 5) == 0 or ep == 0:
            print(f"  epoch {ep + 1}/{epochs} train_mse={loss.item():.4f}",
                  flush=True)
    lin1, lin2 = model[0], model[2]
    W1 = lin1.weight.detach().cpu().tolist()
    b1 = lin1.bias.detach().cpu().tolist()
    W2 = lin2.weight.detach().cpu().tolist()[0]
    b2 = lin2.bias.detach().cpu().tolist()[0]
    net = ValueNet(W1, b1, W2, b2, feature_dim=FEATURE_DIM)
    # torch-forward vs pure-python-forward parity on the val set.
    if val:
        with torch.no_grad():
            tv = model(torch.tensor([x for x, _ in val], dtype=torch.float32,
                                    device=dev)).cpu().tolist()
        max_gap = max(abs(tv[i][0] - net.forward(val[i][0]))
                      for i in range(len(val)))
        print(f"  torch->python forward max gap {max_gap:.2e}", flush=True)
    return net


def consistency_check(net: ValueNet, out_path: str, rows, tol: float) -> float:
    """Reload the exported JSON and confirm pure-Python inference reproduces the
    trainer's predictions to within `tol`. Returns the max abs gap."""
    reloaded = ValueNet.load(out_path)
    sample = rows[:200] if rows else []
    if not sample:
        return 0.0
    max_gap = max(abs(net.forward(x) - reloaded.forward(x)) for x, _ in sample)
    status = "OK" if max_gap <= tol else "FAIL"
    print(f"consistency (train-forward vs reloaded inference): "
          f"max gap {max_gap:.2e} tol {tol:.0e} -> {status}", flush=True)
    if max_gap > tol:
        raise SystemExit(f"consistency check FAILED: {max_gap} > {tol}")
    return max_gap


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="train/data/selfplay.jsonl")
    ap.add_argument("--out", default="train/weights/value.json")
    ap.add_argument("--backend", choices=("python", "torch"), default="python")
    ap.add_argument("--hidden", type=int, default=16)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--lr", type=float, default=0.2)
    ap.add_argument("--l2", type=float, default=1e-4)
    ap.add_argument("--val-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=1837)
    ap.add_argument("--tol", type=float, default=1e-6)
    args = ap.parse_args()

    rows, meta = load_data(args.data)
    if not rows:
        raise SystemExit(f"no samples in {args.data}")
    rng = random.Random(args.seed)
    train, val = split(rows, args.val_frac, rng)
    base_rate = sum(1 for _, y in train if y == 1.0) / max(1, len(train))
    print(f"TRAIN backend={args.backend} samples={len(rows)} "
          f"(train {len(train)} / val {len(val)}) win_base_rate={base_rate:.3f} "
          f"hidden={args.hidden} epochs={args.epochs} lr={args.lr}", flush=True)

    trainer = train_torch if args.backend == "torch" else train_python
    net = trainer(train, val, args.hidden, args.epochs, args.lr, args.l2,
                  args.seed)
    print(f"final train_mse={mse(net, train):.4f} val_mse={mse(net, val):.4f}",
          flush=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    net.save(args.out)
    print(f"wrote weights -> {args.out}", flush=True)
    consistency_check(net, args.out, val or train, args.tol)


if __name__ == "__main__":
    main()
