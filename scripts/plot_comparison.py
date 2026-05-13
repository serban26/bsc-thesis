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
    "adaptive_predictive":  "Adaptive Predictive",
}

def algo_label(name):
    return PRETTY.get(name, name.replace("_", " ").title())

def load_metrics(path):
    """Return list of flow_removed events from a JSONL metrics file."""
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

def plot_utilization_efficiency(datasets_req, datasets_metrics, out_dir):
    """Bar: mean server utilisation per algorithm."""
    algos = list(datasets_req)
    means, stds = [], []

    for algo in algos:
        flow_evts = datasets_metrics.get(algo, [])
        req_evts  = datasets_req[algo]

        if req_evts:
            times = [r.get("t", 0) for r in req_evts if "t" in r]
            if not times and flow_evts:
                times = [e["t"] for e in flow_evts]
        else:
            times = [e["t"] for e in flow_evts]

        if not times and req_evts:
            elapsed = [r["elapsed_s"] for r in req_evts]
            exp_dur = sum(elapsed)
        else:
            exp_dur = (max(times) - min(times)) if len(times) >= 2 else 1.0

        exp_dur = max(exp_dur, 1e-6)

        busy = {n: 0.0 for n in SERVER_NAMES}
        for ev in flow_evts:
            name = ev.get("server")
            dur  = ev.get("duration_s", 0.0)
            if name in busy:
                busy[name] += dur

        utils = [min(1.0, busy[n] / exp_dur) for n in SERVER_NAMES]
        m = sum(utils) / len(utils) if utils else 0
        s = math.sqrt(sum((u - m) ** 2 for u in utils) / len(utils)) if len(utils) > 1 else 0
        means.append(m * 100)
        stds.append(s * 100)

    x = np.arange(len(algos))
    fig, ax = plt.subplots(figsize=(max(6, 2 * len(algos)), 5))
    bars = ax.bar(x, means, yerr=stds, capsize=5,
                  color=[_PALETTE[i % len(_PALETTE)] for i in range(len(algos))],
                  alpha=0.85, edgecolor="white",
                  error_kw=dict(elinewidth=1.2))
    for bar, v, s in zip(bars, means, stds):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + s + 0.5,
                f"{v:.1f}%", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([algo_label(a) for a in algos], fontsize=11)
    ax.set_ylabel("Mean server utilisation (%)")
    ax.set_ylim(0, min(100, max(means, default=0) * 1.25 + 5))
    ax.set_title(
        f"Server utilisation efficiency — {_num_servers} backends\n"
        "(busy time / experiment duration, averaged across servers; error bars = std dev)"
    )
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_06_utilization.png")


def _percentile(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    idx = (len(s) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _jains_fairness(counts):
    """Jain's fairness index: 1 = perfectly fair, 1/n = totally unfair."""
    n = len(counts)
    if n == 0:
        return 1.0
    s  = sum(counts)
    s2 = sum(c * c for c in counts)
    return (s * s) / (n * s2) if s2 > 0 else 1.0


def composite_score(reqs):
    """Compute a composite algorithm quality score in [0, 1].

    Formula
    ───────
                S = R · E · F

    where:
        R  =  reliability  =  1 − error_rate

        E  =  speed score  =  1 / (1 + mean_latency_s)
                              0 ms → 1.0,  ∞ ms → 0.0

        F  =  load-balance fairness  =  1 / (1 + CV_of_per_server_mean_latency)
                              CV = std/mean of each active server's mean latency.
                              Low CV means all servers experience similar load.
                              High CV means some servers are overloaded and slow
                              while others are idle — bad routing.

    Why not Jain's fairness on request counts?
    Jain's on counts rewards sending equal numbers to all servers, which is
    correct for RR but wrong in general: sending 10 requests to a 5 ms server
    and 10 requests to an 800 ms server is terrible routing, yet Jain's = 1.
    CV of per-server latency captures actual loading imbalance.
    """
    n_total = len(reqs)
    if n_total == 0:
        return 0.0, 0.0, 0.0, 0.0
    n_ok   = sum(1 for r in reqs if r.get("status") == "ok")
    R      = n_ok / n_total
    lats   = [r["elapsed_s"] for r in reqs if r.get("status") == "ok"]
    mean_s = sum(lats) / len(lats) if lats else 1.0
    E      = 1.0 / (1.0 + mean_s)
    # per-server mean latency
    srv_means = []
    for s in SERVER_NAMES:
        slats = [r["elapsed_s"] for r in reqs if r["server"] == s and r.get("status") == "ok"]
        if slats:
            srv_means.append(sum(slats) / len(slats))
    if len(srv_means) >= 2:
        m  = sum(srv_means) / len(srv_means)
        cv = math.sqrt(sum((x - m)**2 for x in srv_means) / len(srv_means)) / m if m > 0 else 0
        F  = 1.0 / (1.0 + cv)
    else:
        F = 1.0
    return R * E * F, R, E, F


def plot_composite_score(datasets, out_dir):
    """Ranked bar chart with the composite score S = R · E · F for each algorithm.

    The formula and per-component breakdown are printed inside the figure
    so the plot is self-contained.
    """
    algos  = list(datasets)
    scores, Rs, Es, Fs = [], [], [], []
    for reqs in datasets.values():
        s, r, e, f = composite_score(reqs)
        scores.append(s); Rs.append(r); Es.append(e); Fs.append(f)

    order   = sorted(range(len(algos)), key=lambda i: scores[i], reverse=True)
    o_algos = [algos[i]  for i in order]
    o_score = [scores[i] for i in order]
    o_R     = [Rs[i]     for i in order]
    o_E     = [Es[i]     for i in order]
    o_F     = [Fs[i]     for i in order]
    colors  = [_PALETTE[i % len(_PALETTE)] for i in range(len(o_algos))]

    fig, ax = plt.subplots(figsize=(max(7, 2 * len(algos)), 6))
    x = np.arange(len(o_algos))
    bars = ax.bar(x, o_score, color=colors, alpha=0.85, edgecolor="white", zorder=3)

    for bar, s, r, e, f in zip(bars, o_score, o_R, o_E, o_F):
        cx = bar.get_x() + bar.get_width() / 2
        ax.text(cx, bar.get_height() + 0.005, f"{s:.4f}",
                ha="center", va="bottom", fontsize=11, fontweight="bold")
        detail = f"R={r:.2f}  E={e:.3f}  F={f:.3f}"
        ax.text(cx, bar.get_height() / 2, detail,
                ha="center", va="center", fontsize=8, color="white", fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels([algo_label(a) for a in o_algos], fontsize=11)
    ax.set_ylabel("Composite score  S  (higher = better)")
    ax.set_ylim(0, min(1.0, max(o_score) * 1.30))
    ax.set_title(
        "Algorithm composite score  S = R · E · F\n"
        "R = reliability (1−err)   "
        "E = speed 1/(1+mean_latency_s)   "
        "F = load-balance fairness 1/(1+CV_latency)",
        fontsize=10,
    )
    ax.grid(True, axis="y", alpha=0.3, zorder=0)
    fig.tight_layout()
    _save(fig, out_dir / "cmp_07_composite_score.png")


def plot_score_over_time(datasets, out_dir):
    """Rolling composite score S over the request sequence for each algorithm.

    Uses a sliding window of WINDOW requests.  This shows how quickly each
    algorithm adapts: a smart algorithm starts with a lower score (cold start,
    no data yet) but rises as it learns server speeds; a blind algorithm like
    Round Robin stays flat.
    """
    max_reqs = max((len(r) for r in datasets.values()), default=1)
    WINDOW   = max(5, min(20, max_reqs // 8))

    fig, ax = plt.subplots(figsize=(11, 5))

    for i, (algo, reqs) in enumerate(datasets.items()):
        if not reqs:
            continue
        sr = sorted(reqs, key=lambda r: r["req"])
        xs, ys = [], []
        for j in range(WINDOW, len(sr) + 1):
            window_reqs = sr[j - WINDOW: j]
            s, _, _, _ = composite_score(window_reqs)
            xs.append(sr[j - 1]["req"])
            ys.append(s)
        if xs:
            ax.plot(xs, ys, label=algo_label(algo),
                    color=_PALETTE[i % len(_PALETTE)],
                    linewidth=2.2, alpha=0.9)

    ax.set_xlabel("Request # (end of rolling window)")
    ax.set_ylabel(f"S = R·E·F  (rolling window = {WINDOW} requests)")
    ax.set_title(
        "Composite score over time — S = R · E · F\n"
        "R = reliability   E = speed efficiency   F = Jain's fairness\n"
        f"Rolling window: {WINDOW} requests"
    )
    ax.legend(fontsize=10)
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
    datasets_req     = {name: load_requests(rpath)  for name, _, rpath   in args.algo}
    datasets_metrics = {name: load_metrics(mpath)    for name, mpath, _   in args.algo}
    print(f"\nWriting comparison plots to {out_dir}/")
    plot_mean_latency(datasets_req, out_dir)
    plot_latency_distribution(datasets_req, out_dir)
    plot_latency_over_time(datasets_req, out_dir)
    plot_fairness(datasets_req, out_dir)
    plot_error_rate(datasets_req, out_dir)
    plot_utilization_efficiency(datasets_req, datasets_metrics, out_dir)
    plot_composite_score(datasets_req, out_dir)
    plot_score_over_time(datasets_req, out_dir)
    print("Done.")

if __name__ == "__main__":
    main()
