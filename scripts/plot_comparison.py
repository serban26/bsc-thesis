#!/usr/bin/env python3
"""Generates comparison plots across all tested load-balancing algorithms.

Run via scripts/compare.sh or scripts/run_experiment.sh.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

_num_servers = int(os.environ.get("LB_NUM_SERVERS", "3"))
SERVER_NAMES = [f"h{2 + i}" for i in range(_num_servers)]

_PALETTE = [
    "#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3",
    "#937860", "#CCB974", "#64B5CD",
]
_TIER_COLORS = {"A": "#55A868", "B": "#4C72B0", "C": "#DD8452", "D": "#C44E52"}
_TIER_LABELS = {
    "A": "Tier A — fast (5–25 ms)",
    "B": "Tier B — medium (50–150 ms)",
    "C": "Tier C — slow (300–600 ms)",
    "D": "Tier D — very slow (800–1000 ms)",
}

PRETTY = {
    "round_robin":          "Round Robin",
    "weighted_round_robin": "Weighted RR",
    "least_connections":    "Least Connections",
    "least_response_time":  "Least Resp. Time",
    "adaptive_predictive":  "Adaptive Predictive",
}


def algo_label(name):
    return PRETTY.get(name, name.replace("_", " ").title())


def server_tier(server_name):
    """Returns the tier letter (A/B/C/D) for a given server hostname based on its index."""
    idx = int(server_name[1:]) - 2  # h2 -> 0, h3 -> 1, ...
    n = _num_servers
    if idx < n * 0.4:
        return "A"
    elif idx < n * 0.6:
        return "B"
    elif idx < n * 0.8:
        return "C"
    return "D"


def load_metrics(path):
    """Loads flow_removed events from a JSONL metrics file."""
    events = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("event") == "flow_removed":
                    events.append(obj)
    except FileNotFoundError:
        pass
    return events


def load_requests(path):
    """Loads per-request records from a CSV file."""
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


def _percentile(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    idx = (len(s) - 1) * p / 100
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def plot_mean_latency(datasets, out_dir):
    """Bar chart of mean response latency per algorithm with std dev error bars."""
    algos = list(datasets)
    means, stds = [], []
    for reqs in datasets.values():
        vals = [r["elapsed_s"] * 1000 for r in reqs if r.get("status") == "ok"]
        m = sum(vals) / len(vals) if vals else 0
        s = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0
        means.append(m)
        stds.append(s)

    x = np.arange(len(algos))
    fig, ax = plt.subplots(figsize=(max(7, 1.8 * len(algos)), 5))
    bars = ax.bar(
        x, means, yerr=stds, capsize=5,
        color=[_PALETTE[i % len(_PALETTE)] for i in range(len(algos))],
        alpha=0.85, edgecolor="white",
        error_kw=dict(elinewidth=1.2, ecolor="black"),
    )
    for bar, v, s in zip(bars, means, stds):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + s * 0.06 + 3,
            f"{v:.0f} ms", ha="center", va="bottom", fontsize=10, fontweight="bold",
        )
    ax.set_xticks(x)
    ax.set_xticklabels([algo_label(a) for a in algos], fontsize=11)
    ax.set_ylabel("Mean latency (ms)", fontsize=11)
    ax.set_title("Mean response latency per algorithm\n(error bars = 1 std dev)", fontsize=12)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_01_mean_latency.png")


def plot_latency_distribution(datasets, out_dir):
    """Box plot showing the full latency distribution per algorithm."""
    algos = list(datasets)
    data = [
        [r["elapsed_s"] * 1000 for r in reqs if r.get("status") == "ok"]
        for reqs in datasets.values()
    ]
    fig, ax = plt.subplots(figsize=(max(7, 1.8 * len(algos)), 5))
    bp = ax.boxplot(
        data,
        patch_artist=True,
        labels=[algo_label(a) for a in algos],
        widths=0.5,
        medianprops=dict(color="white", linewidth=2.5),
        flierprops=dict(marker=".", markersize=2, alpha=0.3),
    )
    for patch, c in zip(bp["boxes"], [_PALETTE[i % len(_PALETTE)] for i in range(len(algos))]):
        patch.set_facecolor(c)
        patch.set_alpha(0.8)
    ax.set_ylabel("Latency (ms)", fontsize=11)
    ax.set_title("Response latency distribution per algorithm\n(successful requests only)", fontsize=12)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_02_latency_distribution.png")


def plot_latency_over_time(datasets, out_dir):
    """Rolling-average latency over the request sequence, one line per algorithm."""
    max_reqs = max((len(r) for r in datasets.values()), default=1)
    window = max(10, min(150, max_reqs // 30))

    fig, ax = plt.subplots(figsize=(11, 5))
    for i, (algo, reqs) in enumerate(datasets.items()):
        if not reqs:
            continue
        sr = sorted(reqs, key=lambda r: r["req"])
        lats = [r["elapsed_s"] * 1000 for r in sr]
        xs = [r["req"] for r in sr]
        ax.plot(
            xs, _rolling_avg(lats, window),
            label=algo_label(algo),
            color=_PALETTE[i % len(_PALETTE)],
            linewidth=2.0, alpha=0.9,
        )
    ax.set_xlabel("Request number", fontsize=11)
    ax.set_ylabel(f"Latency (ms)  [rolling avg, window = {window}]", fontsize=11)
    ax.set_title("Response latency over time", fontsize=12)
    ax.legend(fontsize=10, loc="upper right")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_03_latency_over_time.png")


def plot_latency_percentiles(datasets, out_dir):
    """Grouped bar chart comparing p50, p95, and p99 latency across algorithms.

    This is probably the clearest way to show the tail latency advantage of
    adaptive routing. An algorithm that avoids slow servers will have a much
    lower p95 and p99 even if the p50 difference is smaller.
    """
    algos = list(datasets)
    p50s, p95s, p99s = [], [], []
    for reqs in datasets.values():
        lats = sorted(r["elapsed_s"] * 1000 for r in reqs if r.get("status") == "ok")
        p50s.append(_percentile(lats, 50))
        p95s.append(_percentile(lats, 95))
        p99s.append(_percentile(lats, 99))

    x = np.arange(len(algos))
    width = 0.25
    fig, ax = plt.subplots(figsize=(max(8, 2.2 * len(algos)), 5))
    b1 = ax.bar(x - width, p50s, width, label="p50 (median)", alpha=0.85, color="#55A868", edgecolor="white")
    b2 = ax.bar(x,         p95s, width, label="p95",          alpha=0.85, color="#DD8452", edgecolor="white")
    b3 = ax.bar(x + width, p99s, width, label="p99",          alpha=0.85, color="#C44E52", edgecolor="white")

    for bars, vals in [(b1, p50s), (b2, p95s), (b3, p99s)]:
        for bar, v in zip(bars, vals):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 5,
                f"{v:.0f}", ha="center", va="bottom", fontsize=8,
            )
    ax.set_xticks(x)
    ax.set_xticklabels([algo_label(a) for a in algos], fontsize=11)
    ax.set_ylabel("Latency (ms)", fontsize=11)
    ax.set_title("Latency percentiles per algorithm  (p50 / p95 / p99)", fontsize=12)
    ax.legend(fontsize=10)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_04_latency_percentiles.png")


def plot_error_rate(datasets, out_dir):
    """Bar chart of request error / timeout rate per algorithm."""
    algos = list(datasets)
    errors = [sum(1 for r in reqs if r.get("status") == "error") for reqs in datasets.values()]
    pcts = [100 * n / len(reqs) if reqs else 0 for n, reqs in zip(errors, datasets.values())]

    x = np.arange(len(algos))
    fig, ax = plt.subplots(figsize=(max(7, 1.8 * len(algos)), 5))
    bars = ax.bar(
        x, pcts,
        color=[_PALETTE[i % len(_PALETTE)] for i in range(len(algos))],
        alpha=0.85, edgecolor="white",
    )
    for bar, pct, n in zip(bars, pcts, errors):
        label = f"{pct:.1f}%\n({n})" if n > 0 else "0%"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.05,
            label, ha="center", va="bottom", fontsize=9,
        )
    ax.set_xticks(x)
    ax.set_xticklabels([algo_label(a) for a in algos], fontsize=11)
    ax.set_ylabel("Error / timeout rate (%)", fontsize=11)
    ax.set_title("Request error rate per algorithm", fontsize=12)
    ax.yaxis.set_major_formatter(ticker.PercentFormatter())
    ax.grid(True, axis="y", alpha=0.3)
    if not any(errors):
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "No errors recorded", transform=ax.transAxes,
                ha="center", va="center", fontsize=12, color="gray")
    fig.tight_layout()
    _save(fig, out_dir / "cmp_05_error_rate.png")


def plot_tier_routing(datasets, out_dir):
    """Stacked bar showing what fraction of traffic each algorithm sent to each tier.

    The most informative single plot for explaining why adaptive predictive
    outperforms the others: it pushes more traffic to Tier A and nearly
    nothing to Tier D, while RR splits evenly regardless of server speed.
    """
    algos = list(datasets)
    tier_fracs = {t: [] for t in "ABCD"}

    for reqs in datasets.values():
        ok = [r for r in reqs if r.get("status") == "ok"]
        counts = defaultdict(int)
        for r in ok:
            t = server_tier(r["server"])
            counts[t] += 1
        total = len(ok) or 1
        for t in "ABCD":
            tier_fracs[t].append(100 * counts[t] / total)

    x = np.arange(len(algos))
    fig, ax = plt.subplots(figsize=(max(7, 1.8 * len(algos)), 5))
    bottom = np.zeros(len(algos))
    for t in "ABCD":
        vals = np.array(tier_fracs[t])
        bars = ax.bar(
            x, vals, bottom=bottom,
            color=_TIER_COLORS[t], alpha=0.85,
            edgecolor="white", label=_TIER_LABELS[t],
        )
        for bar, v, b in zip(bars, vals, bottom):
            if v > 4:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    b + v / 2,
                    f"{v:.0f}%", ha="center", va="center",
                    fontsize=9, color="white", fontweight="bold",
                )
        bottom += vals

    ax.set_xticks(x)
    ax.set_xticklabels([algo_label(a) for a in algos], fontsize=11)
    ax.set_ylabel("Share of successful requests (%)", fontsize=11)
    ax.set_ylim(0, 108)
    ax.set_title(
        "Traffic distribution across server tiers\n"
        "Tier A = 5–25 ms  |  Tier B = 50–150 ms  |  Tier C = 300–600 ms  |  Tier D = 800–1000 ms",
        fontsize=11,
    )
    ax.legend(fontsize=9, loc="upper right", framealpha=0.9)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_06_tier_routing.png")


def composite_score(reqs):
    """Composite quality score S = R * E * F in [0, 1].

    R = reliability: 1 - error_rate
    E = speed score: 1 / (1 + mean_latency_s)
    F = tail score:  1 / (1 + p95_latency_s)
    """
    n_total = len(reqs)
    if n_total == 0:
        return 0.0, 0.0, 0.0, 0.0
    n_ok = sum(1 for r in reqs if r.get("status") == "ok")
    R = n_ok / n_total
    lats = sorted(r["elapsed_s"] for r in reqs if r.get("status") == "ok")
    mean_s = sum(lats) / len(lats) if lats else 1.0
    E = 1.0 / (1.0 + mean_s)
    p95 = lats[int(len(lats) * 0.95)] if lats else 1.0
    F = 1.0 / (1.0 + p95)
    return R * E * F, R, E, F


def plot_composite_score(datasets, out_dir):
    """Ranked bar chart of the composite score S = R * E * F per algorithm."""
    algos = list(datasets)
    scores, Rs, Es, Fs = [], [], [], []
    for reqs in datasets.values():
        s, r, e, f = composite_score(reqs)
        scores.append(s)
        Rs.append(r)
        Es.append(e)
        Fs.append(f)

    order = sorted(range(len(algos)), key=lambda i: scores[i], reverse=True)
    o_algos = [algos[i] for i in order]
    o_score = [scores[i] for i in order]
    o_R = [Rs[i] for i in order]
    o_E = [Es[i] for i in order]
    o_F = [Fs[i] for i in order]

    fig, ax = plt.subplots(figsize=(max(8, 1.8 * len(algos)), 6))
    x = np.arange(len(o_algos))
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(o_algos))]
    bars = ax.bar(x, o_score, color=colors, alpha=0.85, edgecolor="white", zorder=3)

    for bar, s, r, e, f in zip(bars, o_score, o_R, o_E, o_F):
        cx = bar.get_x() + bar.get_width() / 2
        ax.text(cx, bar.get_height() + 0.01, f"{s:.3f}",
                ha="center", va="bottom", fontsize=12, fontweight="bold")
        if bar.get_height() > 0.10:
            ax.text(cx, bar.get_height() * 0.5,
                    f"R={r:.2f}\nE={e:.2f}\nF={f:.2f}",
                    ha="center", va="center", fontsize=8,
                    color="white", fontweight="bold", linespacing=1.5)

    ax.set_xticks(x)
    ax.set_xticklabels([algo_label(a) for a in o_algos], fontsize=11)
    ax.set_ylabel("Composite score  S  (higher = better)", fontsize=11)
    ax.set_ylim(0, min(1.0, max(o_score) * 1.28))
    ax.set_title(
        "Composite score  S = R · E · F\n"
        "R = reliability (1−err)   E = speed 1/(1+mean_s)   F = tail score 1/(1+p95_s)",
        fontsize=11,
    )
    ax.grid(True, axis="y", alpha=0.3, zorder=0)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_07_composite_score.png")


def plot_score_over_time(datasets, out_dir):
    """Rolling composite score over the request sequence, one line per algorithm.

    Shows how quickly each algorithm converges after warm-up and how stable
    it remains under periodic server spikes.
    """
    max_reqs = max((len(r) for r in datasets.values()), default=1)
    window = max(20, min(200, max_reqs // 20))

    fig, ax = plt.subplots(figsize=(11, 5))
    for i, (algo, reqs) in enumerate(datasets.items()):
        if not reqs:
            continue
        sr = sorted(reqs, key=lambda r: r["req"])
        xs, ys = [], []
        for j in range(window, len(sr) + 1):
            s, _, _, _ = composite_score(sr[j - window: j])
            xs.append(sr[j - 1]["req"])
            ys.append(s)
        if xs:
            ax.plot(xs, ys, label=algo_label(algo),
                    color=_PALETTE[i % len(_PALETTE)],
                    linewidth=2.2, alpha=0.9)

    ax.set_xlabel("Request number", fontsize=11)
    ax.set_ylabel(f"S = R·E·F  (window = {window} requests)", fontsize=11)
    ax.set_title(f"Composite score over time  (rolling window = {window} requests)", fontsize=12)
    ax.legend(fontsize=10, loc="lower right")
    ax.set_ylim(0, None)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_08_score_over_time.png")


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
    plot_latency_percentiles(datasets_req, out_dir)
    plot_error_rate(datasets_req, out_dir)
    plot_tier_routing(datasets_req, out_dir)
    plot_composite_score(datasets_req, out_dir)
    plot_score_over_time(datasets_req, out_dir)
    print("Done.")


if __name__ == "__main__":
    main()
