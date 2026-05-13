#!/usr/bin/env python3
"""
Multi-algorithm comparison plots — algorithm-level aggregates only.
Works regardless of how many backend servers are configured (3, 5, 30, …).

Usage:
    python3 scripts/plot_comparison.py \
        --algo round_robin           /tmp/lb_metrics_round_robin.jsonl           /tmp/lb_requests_round_robin.csv \
        --algo weighted_round_robin  /tmp/lb_metrics_weighted_round_robin.jsonl  /tmp/lb_requests_weighted_round_robin.csv \
        --out results/comparison
"""
from __future__ import annotations
import argparse, csv, json, math, os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

_num_servers = int(os.environ.get("LB_NUM_SERVERS", "3"))
SERVER_NAMES = [f"h{2 + i}" for i in range(_num_servers)]

_PALETTE = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3", "#937860", "#CCB974", "#64B5CD"]

PRETTY = {
    "round_robin":          "Round Robin",
    "weighted_round_robin": "Weighted RR",
    "least_connections":    "Least Connections",
    "least_response_time":  "Least Resp. Time",
}

def algo_label(name):
    return PRETTY.get(name, name.replace("_", " ").title())

def load_requests(path):
    rows = []
    try:
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                row["req"] = int(row["req"])
                row["elapsed_s"] = float(row["elapsed_s"])
                rows.append(row)
    except FileNotFoundError:
        pass
    return rows

def _save(fig, path):
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {path}")

def _rolling_avg(values, window):
    out = []
    for i in range(len(values)):
        chunk = values[max(0, i - window + 1): i + 1]
        out.append(sum(chunk) / len(chunk))
    return out

def plot_mean_latency(datasets, out_dir):
    """Bar: overall mean latency per algorithm with std-dev error bars."""
    algos = list(datasets)
    means, stds = [], []
    for reqs in datasets.values():
        vals = [r["elapsed_s"] * 1000 for r in reqs if r.get("status") == "ok"]
        m = sum(vals) / len(vals) if vals else 0
        means.append(m)
        stds.append(math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0)
    x = np.arange(len(algos))
    fig, ax = plt.subplots(figsize=(max(6, 2 * len(algos)), 5))
    bars = ax.bar(x, means, yerr=stds, capsize=5,
                  color=[_PALETTE[i % len(_PALETTE)] for i in range(len(algos))],
                  alpha=0.85, edgecolor="white", error_kw=dict(elinewidth=1.2))
    for bar, v in zip(bars, means):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(stds, default=0) * 0.05 + 1,
                f"{v:.1f} ms", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels([algo_label(a) for a in algos], fontsize=11)
    ax.set_ylabel("Mean latency (ms)")
    ax.set_title("Overall mean response latency per algorithm\n(error bars = std dev)")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout(); _save(fig, out_dir / "cmp_01_mean_latency.png")

def plot_latency_distribution(datasets, out_dir):
    """Box plot: one box per algorithm, all successful latencies pooled."""
    algos = list(datasets)
    data = [[r["elapsed_s"] * 1000 for r in reqs if r.get("status") == "ok"]
            for reqs in datasets.values()]
    fig, ax = plt.subplots(figsize=(max(6, 2 * len(algos)), 5))
    bp = ax.boxplot(data, patch_artist=True, labels=[algo_label(a) for a in algos],
                    widths=0.5, medianprops=dict(color="white", linewidth=2))
    for patch, c in zip(bp["boxes"], [_PALETTE[i % len(_PALETTE)] for i in range(len(algos))]):
        patch.set_facecolor(c); patch.set_alpha(0.8)
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Response latency distribution per algorithm (successful requests)")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout(); _save(fig, out_dir / "cmp_02_latency_distribution.png")

def plot_latency_over_time(datasets, out_dir):
    """Lines: rolling-average latency over request sequence, one line per algorithm."""
    max_reqs = max((len(r) for r in datasets.values()), default=1)
    window = max(1, min(50, max_reqs // 20))
    fig, ax = plt.subplots(figsize=(10, 5))
    for i, (algo, reqs) in enumerate(datasets.items()):
        if not reqs:
            continue
        sr = sorted(reqs, key=lambda r: r["req"])
        ax.plot([r["req"] for r in sr], _rolling_avg([r["elapsed_s"] * 1000 for r in sr], window),
                label=algo_label(algo), color=_PALETTE[i % len(_PALETTE)], linewidth=2.0, alpha=0.9)
    ax.set_xlabel("Request #")
    ax.set_ylabel(f"Latency (ms)  [rolling avg, window={window}]")
    ax.set_title("Response latency over time — all algorithms")
    ax.legend(fontsize=10); ax.grid(True, alpha=0.25)
    fig.tight_layout(); _save(fig, out_dir / "cmp_03_latency_over_time.png")

def plot_fairness(datasets, out_dir):
    """Bar: coefficient of variation of request counts across backends (lower = fairer)."""
    algos = list(datasets)
    cvs = []
    for reqs in datasets.values():
        counts = [c for c in [sum(1 for r in reqs if r["server"] == s) for s in SERVER_NAMES] if c > 0]
        if len(counts) < 2:
            cvs.append(0.0); continue
        mean = sum(counts) / len(counts)
        std = math.sqrt(sum((c - mean) ** 2 for c in counts) / len(counts))
        cvs.append(std / mean if mean else 0)
    x = np.arange(len(algos))
    fig, ax = plt.subplots(figsize=(max(6, 2 * len(algos)), 5))
    bars = ax.bar(x, cvs, color=[_PALETTE[i % len(_PALETTE)] for i in range(len(algos))],
                  alpha=0.85, edgecolor="white")
    for bar, v in zip(bars, cvs):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.005,
                f"{v:.3f}", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels([algo_label(a) for a in algos], fontsize=11)
    ax.set_ylabel("Coefficient of Variation (lower = more balanced)")
    ax.set_title(f"Load-balance fairness across {_num_servers} backends\n(CV of request counts — 0 = perfectly even)")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout(); _save(fig, out_dir / "cmp_04_fairness.png")

def plot_error_rate(datasets, out_dir):
    """Bar: HTTP 500 error percentage per algorithm."""
    algos = list(datasets)
    totals = [sum(1 for r in reqs if r.get("status") == "error") for reqs in datasets.values()]
    pcts = [100 * n / len(reqs) if reqs else 0 for n, reqs in zip(totals, datasets.values())]
    x = np.arange(len(algos))
    fig, ax = plt.subplots(figsize=(max(6, 2 * len(algos)), 5))
    bars = ax.bar(x, pcts, color=[_PALETTE[i % len(_PALETTE)] for i in range(len(algos))],
                  alpha=0.85, edgecolor="white")
    for bar, pct, n in zip(bars, pcts, totals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                f"{pct:.1f}%\n({n})", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels([algo_label(a) for a in algos], fontsize=11)
    ax.set_ylabel("Error rate (%)"); ax.set_title("HTTP 500 error rate per algorithm")
    ax.yaxis.set_major_formatter(ticker.PercentFormatter())
    ax.grid(True, axis="y", alpha=0.3)
    if not any(totals):
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No errors recorded", transform=ax.transAxes,
                ha="center", va="center", fontsize=12, color="gray")
    fig.tight_layout(); _save(fig, out_dir / "cmp_05_error_rate.png")

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--algo", nargs=3, action="append", metavar=("NAME", "METRICS", "REQUESTS"))
    ap.add_argument("--out", default="results/comparison")
    args = ap.parse_args()
    if not args.algo:
        ap.error("Provide at least one --algo name metrics.jsonl requests.csv")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    datasets_req = {name: load_requests(rpath) for name, _, rpath in args.algo}
    print(f"\nWriting comparison plots to {out_dir}/")
    plot_mean_latency(datasets_req, out_dir)
    plot_latency_distribution(datasets_req, out_dir)
    plot_latency_over_time(datasets_req, out_dir)
    plot_fairness(datasets_req, out_dir)
    plot_error_rate(datasets_req, out_dir)
    print("Done.")

if __name__ == "__main__":
    main()
