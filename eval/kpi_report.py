"""Summarise kpi_history.jsonl into a readable table (SOT-1796).

Reads the append-only KPI log and prints one row per measurement (baseline /
screen / confirm), grouped by label, with the pooled win rate, Wilson 95%
interval, N, and fault total. Use it to eyeball the A/B cycle and to decide
promotion (confirm-phase Wilson lower bound vs the baseline point estimate).

Usage:
    python3 eval/kpi_report.py [kpi_history.jsonl] [--markdown]
"""
import argparse
import json
import sys


def load(path):
    rows = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def fmt_row(r):
    lo, hi = (r.get("wilson95_excl_draws") or [None, None])
    wr = r.get("winrate_a_excl_draws")
    wr_s = f"{wr:.4f}" if wr is not None else "  n/a "
    ci_s = (f"[{lo:.4f}, {hi:.4f}]" if lo is not None else "[n/a]")
    return (r.get("label", "?"), r.get("phase", "?"),
            f"{r.get('agent_a')}v{r.get('agent_b')}",
            r.get("n_total", 0), wr_s, ci_s, r.get("faults_total", 0))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", nargs="?", default="kpi_history.jsonl")
    p.add_argument("--markdown", action="store_true")
    args = p.parse_args()

    try:
        rows = load(args.path)
    except FileNotFoundError:
        raise SystemExit(f"no KPI log at {args.path}")
    if not rows:
        raise SystemExit(f"{args.path} is empty")

    header = ("label", "phase", "match", "N", "winrate", "Wilson95", "faults")
    table = [fmt_row(r) for r in rows]
    if args.markdown:
        print("| " + " | ".join(header) + " |")
        print("| " + " | ".join("---" for _ in header) + " |")
        for row in table:
            print("| " + " | ".join(str(c) for c in row) + " |")
    else:
        widths = [max(len(str(x)) for x in col)
                  for col in zip(header, *table)]
        line = "  ".join(str(h).ljust(w) for h, w in zip(header, widths))
        print(line)
        print("-" * len(line))
        for row in table:
            print("  ".join(str(c).ljust(w) for c, w in zip(row, widths)))

    faulty = [r["label"] for r in rows if r.get("faults_total", 0)]
    if faulty:
        print(f"\n!! nonzero faults in: {sorted(set(faulty))}", file=sys.stderr)


if __name__ == "__main__":
    main()
