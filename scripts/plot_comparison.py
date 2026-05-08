#!/usr/bin/env python3
"""
Side-by-side comparison plots: Round Robin vs Least Connections.

Usage:
    python3 scripts/plot_comparison.py \
        --rr-metrics  /tmp/lb_metrics_round_robin.jsonl \
        --rr-requests /tmp/lb_requests_round_robin.csv \
        --lc-metrics  /tmp/lb_metrics_least_connections.jsonl \
        --lc-requests /tmp/lb_requests_least_connections.csv \
        --out results/comparison
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

COLORS = {"h2": "#4C72B0", "h3": "#DD8452", "h4": "#55A868"}
MARKERS = {"h2": "o", "h3": "s", "h4": "^"}
SERVER_NAMES = ["h2", "h3", "h4"]
ALGO_COLORS = {"round_robin": "#4C72B0", "least_connections": "#DD8452"}
ALGO_LABELS = {"round_robin": "Round Robin", "least_connections": "Least Connections"}


# ── loaders (same as plot_metrics.py) ────────────────────────────────────

def load_metrics(path):
    snapshots, selects, flow_removed = [], [], []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = obj.get("event")
            if ev == "select":
                selects.append(obj)
            elif ev == "flow_removed":
                flow_removed.append(obj)
            elif "servers" in obj:
                snapshots.append(obj)
    return snapshots, selects, flow_removed


def load_requests(path):
    rows = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            row["req"] = int(row["req"])
            row["elapsed_s"] = float(row["elapsed_s"])
            if "status" not in row:
                row["status"] = "ok" if row["server"] in SERVER_NAMES else "other"
            rows.append(row)
    return rows


def events_to_step(selects, flow_removed, metric="active_conns"):
    all_events = sorted(
        [e for e in selects + flow_removed if metric in e],
        key=lambda e: e["t"]
    )
    if not all_events:
        return {n: ([], []) for n in SERVER_NAMES}
    t0 = all_events[0]["t"]
    xs   = {n: [0] for n in SERVER_NAMES}
    ys   = {n: [0] for n in SERVER_NAMES}
    last = {n: 0   for n in SERVER_NAMES}
    for ev in all_events:
        name = ev.get("server")
        if name not in last:
            continue
        t   = ev["t"] - t0
        val = ev[metric]
        xs[name].append(t); ys[name].append(last[name])
        xs[name].append(t); ys[name].append(val)
        last[name] = val
    return {n: (xs[n], ys[n]) for n in SERVER_NAMES}


def _save(fig, path):
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {path}")


# ── comparison plots ──────────────────────────────────────────────────────

def cmp_latency_distribution(rr_req, lc_req, out_dir):
    """Box plots: latency per backend, RR vs LC side by side."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    datasets = [("round_robin", rr_req), ("least_connections", lc_req)]
    for ax, (algo, reqs) in zip(axes, datasets):
        data, labels, cols = [], [], []
        for n in SERVER_NAMES:
            vals = [r["elapsed_s"] * 1000 for r in reqs
                    if r["server"] == n and r.get("status") == "ok"]
            if vals:
                data.append(vals)
                labels.append(n)
                cols.append(COLORS[n])
        if data:
            bp = ax.boxplot(data, patch_artist=True, labels=labels, widths=0.5)
            for patch, c in zip(bp["boxes"], cols):
                patch.set_facecolor(c)
                patch.set_alpha(0.75)
        ax.set_title(ALGO_LABELS[algo], fontsize=12, fontweight="bold")
        ax.set_xlabel("Backend")
        ax.set_ylabel("Latency (ms)")
        ax.grid(True, axis="y", alpha=0.3)
    fig.suptitle("Response latency distribution — RR vs LC", fontsize=13)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_01_latency_distribution.png")


def cmp_mean_latency_bar(rr_req, lc_req, out_dir):
    """Grouped bar chart: mean latency per backend per algorithm."""
    import numpy as np
    x = range(len(SERVER_NAMES))
    width = 0.35
    fig, ax = plt.subplots(figsize=(8, 5))
    for offset, (algo, reqs) in zip([-width/2, width/2],
                                     [("round_robin", rr_req),
                                      ("least_connections", lc_req)]):
        means = []
        for n in SERVER_NAMES:
            vals = [r["elapsed_s"] * 1000 for r in reqs
                    if r["server"] == n and r.get("status") == "ok"]
            means.append(sum(vals) / len(vals) if vals else 0)
        bars = ax.bar([xi + offset for xi in x], means, width,
                      label=ALGO_LABELS[algo],
                      color=ALGO_COLORS[algo], alpha=0.8, edgecolor="white")
        for bar, v in zip(bars, means):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.5,
                    f"{v:.0f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(x))
    ax.set_xticklabels(SERVER_NAMES)
    ax.set_ylabel("Mean latency (ms)")
    ax.set_xlabel("Backend")
    ax.set_title("Mean response latency per backend — RR vs LC")
    ax.legend(fontsize=10)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_02_mean_latency.png")


def cmp_request_distribution(rr_req, lc_req, out_dir):
    """Grouped bar: requests per backend per algorithm."""
    import numpy as np
    x = range(len(SERVER_NAMES))
    width = 0.35
    fig, ax = plt.subplots(figsize=(8, 5))
    for offset, (algo, reqs) in zip([-width/2, width/2],
                                     [("round_robin", rr_req),
                                      ("least_connections", lc_req)]):
        counts = [sum(1 for r in reqs if r["server"] == n) for n in SERVER_NAMES]
        bars = ax.bar([xi + offset for xi in x], counts, width,
                      label=ALGO_LABELS[algo],
                      color=ALGO_COLORS[algo], alpha=0.8, edgecolor="white")
        for bar, v in zip(bars, counts):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.1,
                    str(v), ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(x))
    ax.set_xticklabels(SERVER_NAMES)
    ax.set_ylabel("Requests served")
    ax.set_xlabel("Backend")
    ax.set_title("Request distribution per backend — RR vs LC")
    ax.legend(fontsize=10)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_03_request_distribution.png")


def cmp_active_conns(rr_sel, rr_fr, lc_sel, lc_fr, out_dir):
    """Active connections over time for both algorithms, one subplot each."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 4), sharey=True)
    datasets = [
        ("round_robin",       rr_sel, rr_fr),
        ("least_connections", lc_sel, lc_fr),
    ]
    for ax, (algo, sel, fr) in zip(axes, datasets):
        ts = events_to_step(sel, fr, "active_conns")
        for name in SERVER_NAMES:
            xs, ys = ts[name]
            if len(xs) > 1:
                ax.plot(xs, ys, label=name, color=COLORS[name], linewidth=1.8)
        ax.set_title(ALGO_LABELS[algo], fontsize=12, fontweight="bold")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Active connections")
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    fig.suptitle("Active connections over time — RR vs LC", fontsize=13)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_04_active_conns.png")


def cmp_latency_over_time(rr_req, lc_req, out_dir):
    """Scatter: latency over request# for both algorithms overlaid."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 4), sharey=True)
    datasets = [("round_robin", rr_req), ("least_connections", lc_req)]
    for ax, (algo, reqs) in zip(axes, datasets):
        for name in SERVER_NAMES:
            xs = [r["req"] for r in reqs if r["server"] == name]
            ys = [r["elapsed_s"] * 1000 for r in reqs if r["server"] == name]
            if xs:
                ax.scatter(xs, ys, label=name, color=COLORS[name],
                           marker=MARKERS[name], s=30, alpha=0.85)
        errors = [r for r in reqs if r.get("status") == "error"]
        if errors:
            ax.scatter([r["req"] for r in errors],
                       [r["elapsed_s"] * 1000 for r in errors],
                       label="500 error", color="#e05555", marker="x", s=50, zorder=5)
        ax.set_title(ALGO_LABELS[algo], fontsize=12, fontweight="bold")
        ax.set_xlabel("Request #")
        ax.set_ylabel("Latency (ms)")
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.25)
    fig.suptitle("Response latency per request — RR vs LC", fontsize=13)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_05_latency_over_time.png")


def cmp_error_rate(rr_req, lc_req, out_dir):
    """Bar chart: error count per backend per algorithm."""
    import numpy as np
    x = range(len(SERVER_NAMES))
    width = 0.35
    fig, ax = plt.subplots(figsize=(8, 4))
    for offset, (algo, reqs) in zip([-width/2, width/2],
                                     [("round_robin", rr_req),
                                      ("least_connections", lc_req)]):
        counts = [sum(1 for r in reqs
                      if r["server"] == n and r.get("status") == "error")
                  for n in SERVER_NAMES]
        bars = ax.bar([xi + offset for xi in x], counts, width,
                      label=ALGO_LABELS[algo],
                      color=ALGO_COLORS[algo], alpha=0.8, edgecolor="white")
        for bar, v in zip(bars, counts):
            if v:
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.05,
                        str(v), ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(x))
    ax.set_xticklabels(SERVER_NAMES)
    ax.set_ylabel("500 errors")
    ax.set_xlabel("Backend")
    ax.set_title("Error count per backend — RR vs LC")
    ax.legend(fontsize=10)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_06_error_rate.png")


# ── main ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rr-metrics")
    ap.add_argument("--rr-requests")
    ap.add_argument("--lc-metrics")
    ap.add_argument("--lc-requests")
    ap.add_argument("--out", default="results/comparison")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    _, rr_sel, rr_fr = load_metrics(args.rr_metrics)
    _, lc_sel, lc_fr = load_metrics(args.lc_metrics)
    rr_req = load_requests(args.rr_requests)
    lc_req = load_requests(args.lc_requests)

    print(f"\nWriting comparison plots to {out_dir}/")
    cmp_latency_distribution(rr_req, lc_req, out_dir)
    cmp_mean_latency_bar(rr_req, lc_req, out_dir)
    cmp_request_distribution(rr_req, lc_req, out_dir)
    cmp_active_conns(rr_sel, rr_fr, lc_sel, lc_fr, out_dir)
    cmp_latency_over_time(rr_req, lc_req, out_dir)
    cmp_error_rate(rr_req, lc_req, out_dir)
    print("Done.")


if __name__ == "__main__":
    main()
