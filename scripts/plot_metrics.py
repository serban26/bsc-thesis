#!/usr/bin/env python3
"""
Plot results from a load-balancer experiment run.

Usage:
    python3 scripts/plot_metrics.py [--metrics FILE] [--requests FILE] [--out DIR]

Scale-safe: works for any number of backends (3 … 100+).
Legends list server names only when N <= 12; for larger pools a
colour-bar (server index) is shown instead.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    import matplotlib.cm as cm
    import numpy as np
except ImportError:
    print("matplotlib not found.  pip install matplotlib", file=sys.stderr)
    sys.exit(1)

# ── server list & colour assignment ──────────────────────────────────────
_num_servers = int(os.environ.get("LB_NUM_SERVERS", "3"))
SERVER_NAMES = [f"h{2 + i}" for i in range(_num_servers)]

# For small pools use a categorical palette; for large pools use a smooth
# colormap so the legend can be replaced by a colorbar.
_LEGEND_THRESHOLD = 12   # show named legend up to this many servers

_cmap = cm.get_cmap("tab20" if _num_servers <= 20 else "viridis", _num_servers)
COLORS  = {name: _cmap(i / max(_num_servers - 1, 1)) for i, name in enumerate(SERVER_NAMES)}
MARKERS = {name: ["o","s","^","D","v","P","X","*","h","+"][i % 10]
           for i, name in enumerate(SERVER_NAMES)}


def _add_server_legend(ax, drawn_names):
    """Add legend or colorbar depending on pool size."""
    if _num_servers <= _LEGEND_THRESHOLD:
        ax.legend(fontsize=8, ncol=max(1, _num_servers // 6))
    # For large pools: no legend — colour gradient is self-explanatory


def _save(fig, path):
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  {path}")


# ── data loaders ─────────────────────────────────────────────────────────

def load_metrics(path: str):
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


def load_requests(path: str):
    rows = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            row["req"] = int(row["req"])
            row["elapsed_s"] = float(row["elapsed_s"])
            if "status" not in row:
                row["status"] = "ok" if row["server"] in SERVER_NAMES else "other"
            rows.append(row)
    return rows


# ── helpers ───────────────────────────────────────────────────────────────

def ts_for(snapshots, metric: str):
    out = {n: ([], []) for n in SERVER_NAMES}
    for snap in snapshots:
        t = snap["t"]
        for srv in snap["servers"]:
            name = srv["name"]
            if name in out and metric in srv:
                out[name][0].append(t)
                out[name][1].append(srv[metric])
    return out


def _events_to_step(selects, flow_removed, metric="active_conns"):
    all_events = sorted(
        [e for e in selects + flow_removed if metric in e],
        key=lambda e: e["t"]
    )
    if not all_events:
        return {n: ([], []) for n in SERVER_NAMES}
    t0   = all_events[0]["t"]
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


# ── individual plots ──────────────────────────────────────────────────────

def plot_request_distribution(requests, out_dir):
    counts = {n: 0 for n in SERVER_NAMES}
    counts["error"] = 0
    for r in requests:
        srv = r["server"]
        if r.get("status") == "error":
            counts["error"] += 1
        else:
            counts[srv] = counts.get(srv, 0) + 1

    names  = list(SERVER_NAMES)
    if counts["error"]:
        names.append("error")
    values = [counts[n] for n in names]
    colors = [COLORS.get(n, "#e05555") for n in names]

    # Scale figure width for many servers
    fig_w = max(7, min(30, 0.4 * len(names)))
    fig, ax = plt.subplots(figsize=(fig_w, 4))
    bars = ax.bar(range(len(names)), values, color=colors,
                  edgecolor="white", linewidth=0.5)
    # Only annotate bar values when N is small enough to read
    if len(names) <= 30:
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.2,
                    str(val), ha="center", va="bottom", fontsize=7)

    ok_total = sum(counts[n] for n in SERVER_NAMES)
    ideal = ok_total / len(SERVER_NAMES)
    ax.axhline(ideal, color="black", linestyle="--", linewidth=1.2,
               label=f"Ideal ({ideal:.0f} each)")

    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=90 if len(names) > 10 else 0, fontsize=7)
    ax.set_title("Request distribution per backend")
    ax.set_ylabel("Requests served")
    ax.set_xlabel("Backend")
    ax.legend(fontsize=9)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    fig.tight_layout()
    _save(fig, out_dir / "01_request_distribution.png")


def plot_response_times(requests, out_dir):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4))

    by_srv = defaultdict(lambda: ([], []))
    for r in requests:
        srv = r["server"]
        by_srv[srv][0].append(r["req"])
        by_srv[srv][1].append(r["elapsed_s"] * 1000)

    drawn = []
    for name in SERVER_NAMES:
        xs, ys = by_srv[name]
        if xs:
            sc = ax1.scatter(xs, ys, color=COLORS[name],
                             marker=MARKERS[name], s=25, alpha=0.8,
                             label=name if _num_servers <= _LEGEND_THRESHOLD else "_")
            drawn.append(name)
    if by_srv["error"][0]:
        ax1.scatter(*by_srv["error"], label="500 error", color="#e05555",
                    marker="x", s=50, zorder=5)
        drawn.append("error")

    ax1.set_title("Response latency per request")
    ax1.set_xlabel("Request #")
    ax1.set_ylabel("Latency (ms)")
    if _num_servers <= _LEGEND_THRESHOLD or "error" in drawn:
        ax1.legend(fontsize=8, ncol=max(1, len(drawn) // 6))
    ax1.grid(True, alpha=0.25)

    data, labels, cols = [], [], []
    for n in SERVER_NAMES:
        vals = [r["elapsed_s"] * 1000 for r in requests
                if r["server"] == n and r.get("status") == "ok"]
        if vals:
            data.append(vals)
            labels.append(n)
            cols.append(COLORS[n])
    if data:
        bp = ax2.boxplot(data, patch_artist=True,
                         labels=labels if len(labels) <= 20 else [""] * len(labels),
                         widths=0.5)
        for patch, c in zip(bp["boxes"], cols):
            patch.set_facecolor(c); patch.set_alpha(0.7)
        if len(labels) > 10:
            ax2.tick_params(axis="x", labelsize=6, rotation=90)
    ax2.set_title("Latency distribution per backend (successful only)")
    ax2.set_ylabel("Latency (ms)")
    ax2.set_xlabel("Backend")
    fig.tight_layout()
    _save(fig, out_dir / "02_response_times.png")


def plot_active_conns(selects, flow_removed, out_dir):
    ts  = _events_to_step(selects, flow_removed, "active_conns")
    fig, ax = plt.subplots(figsize=(9, 4))
    drawn = []
    for name in SERVER_NAMES:
        xs, ys = ts[name]
        if len(xs) > 1:
            ax.plot(xs, ys, color=COLORS[name], linewidth=1.5, alpha=0.75,
                    label=name if _num_servers <= _LEGEND_THRESHOLD else "_")
            drawn.append(name)
    ax.set_title("Active connections per backend over time")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Active connections")
    _add_server_legend(ax, drawn)
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    fig.tight_layout()
    _save(fig, out_dir / "03_active_conns.png")


def plot_cumulative_selections(selects, out_dir):
    if not selects:
        return
    t0  = selects[0]["t"]
    cum = {n: 0 for n in SERVER_NAMES}
    ts  = {n: [0] for n in SERVER_NAMES}
    vs  = {n: [0] for n in SERVER_NAMES}
    for ev in selects:
        n = ev.get("server")
        if n in cum:
            cum[n] += 1
            t = ev["t"] - t0
            for name in SERVER_NAMES:
                ts[name].append(t)
                vs[name].append(cum[name])
    fig, ax = plt.subplots(figsize=(9, 4))
    drawn = []
    for name in SERVER_NAMES:
        if len(ts[name]) > 1:
            ax.step(ts[name], vs[name], color=COLORS[name], linewidth=1.5,
                    alpha=0.75, where="post",
                    label=name if _num_servers <= _LEGEND_THRESHOLD else "_")
            drawn.append(name)
    ax.set_title("Cumulative requests assigned per backend")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Total selections")
    _add_server_legend(ax, drawn)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "04_cumulative_selections.png")


def plot_score(snapshots, out_dir):
    ts = ts_for(snapshots, "score")
    fig, ax = plt.subplots(figsize=(9, 4))
    drawn = []
    for name in SERVER_NAMES:
        xs, ys = ts[name]
        if xs:
            ax.plot(xs, ys, color=COLORS[name], markersize=3, linewidth=1.5,
                    label=name if _num_servers <= _LEGEND_THRESHOLD else "_")
            drawn.append(name)
    ax.set_title("Composite score per backend (lower = preferred)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Score")
    _add_server_legend(ax, drawn)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "05_score.png")


def plot_total_bytes(snapshots, out_dir):
    ts = ts_for(snapshots, "total_bytes")
    fig, ax = plt.subplots(figsize=(9, 4))
    drawn = []
    for name in SERVER_NAMES:
        xs, ys = ts[name]
        if xs:
            ax.plot(xs, ys, color=COLORS[name], markersize=3, linewidth=1.5,
                    label=name if _num_servers <= _LEGEND_THRESHOLD else "_")
            drawn.append(name)
    ax.set_title("Cumulative bytes served per backend")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Bytes")
    _add_server_legend(ax, drawn)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "06_total_bytes.png")


def plot_queue_depth(selects, flow_removed, out_dir):
    ts  = _events_to_step(selects, flow_removed, "queue_depth")
    fig, ax = plt.subplots(figsize=(9, 4))
    drawn = []
    for name in SERVER_NAMES:
        xs, ys = ts[name]
        if len(xs) > 1:
            ax.plot(xs, ys, color=COLORS[name], linewidth=1.5, alpha=0.75,
                    label=name if _num_servers <= _LEGEND_THRESHOLD else "_")
            drawn.append(name)
    ax.set_title("Request queue depth per backend over time")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Queue depth")
    _add_server_legend(ax, drawn)
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    fig.tight_layout()
    _save(fig, out_dir / "07_queue_depth.png")


def plot_utilization(flow_removed, selects, out_dir):
    """Server utilization: busy time vs idle time per backend.

    busy_time  = sum of all completed flow durations for that server
    experiment = wall-clock span from first to last event
    idle_time  = experiment_duration - busy_time  (floor 0)

    A bar chart shows busy vs idle stacked, giving an instant read on which
    servers were actually used and how saturated they were.
    An aggregate efficiency score is printed in the title:
        efficiency = total_busy / (experiment_duration * num_servers)
    """
    all_events = selects + flow_removed
    if not all_events:
        return

    t_start = min(e["t"] for e in all_events)
    t_end   = max(e["t"] for e in all_events)
    exp_dur = max(t_end - t_start, 1e-6)

    busy = {n: 0.0 for n in SERVER_NAMES}
    for ev in flow_removed:
        name = ev.get("server")
        dur  = ev.get("duration_s", 0.0)
        if name in busy:
            busy[name] += dur

    total_busy  = sum(busy.values())
    total_cap   = exp_dur * _num_servers
    efficiency  = total_busy / total_cap if total_cap > 0 else 0

    names      = SERVER_NAMES
    busy_vals  = [busy[n] for n in names]
    idle_vals  = [max(0, exp_dur - busy[n]) for n in names]

    fig_w = max(7, min(30, 0.45 * len(names)))
    fig, ax = plt.subplots(figsize=(fig_w, 4))

    x = np.arange(len(names))
    ax.bar(x, busy_vals, color="#4C72B0", alpha=0.85, label="Busy (flow duration sum)")
    ax.bar(x, idle_vals, bottom=busy_vals, color="#CCCCCC", alpha=0.6, label="Idle")

    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=90 if len(names) > 10 else 0, fontsize=7)
    ax.set_ylabel("Time (s)")
    ax.set_xlabel("Backend")
    ax.set_title(
        f"Server utilization — busy vs idle time\n"
        f"Overall efficiency: {efficiency*100:.1f}%  "
        f"(total flow work / pool capacity)"
    )
    ax.legend(fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "08_utilization.png")


# ── main ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metrics",  default="/tmp/lb_metrics.jsonl")
    ap.add_argument("--requests", default="/tmp/lb_requests.csv")
    ap.add_argument("--out",      default="results")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    snapshots, selects, flow_removed = [], [], []
    requests = []

    if os.path.exists(args.metrics):
        snapshots, selects, flow_removed = load_metrics(args.metrics)
        print(f"Loaded {len(snapshots)} metric snapshots, "
              f"{len(selects)} select events, "
              f"{len(flow_removed)} flow_removed events")
    else:
        print(f"[warn] metrics file not found: {args.metrics}")

    if os.path.exists(args.requests):
        requests = load_requests(args.requests)
        print(f"Loaded {len(requests)} request records")
    else:
        print(f"[warn] requests file not found: {args.requests}")

    print(f"\nWriting plots to {out_dir}/")

    if requests:
        plot_request_distribution(requests, out_dir)
        plot_response_times(requests, out_dir)

    if selects or flow_removed:
        plot_active_conns(selects, flow_removed, out_dir)

    if selects:
        plot_cumulative_selections(selects, out_dir)

    if snapshots:
        plot_score(snapshots, out_dir)
        plot_total_bytes(snapshots, out_dir)

    if selects or flow_removed:
        plot_queue_depth(selects, flow_removed, out_dir)

    if flow_removed:
        plot_utilization(flow_removed, selects, out_dir)

    print("\nDone.")


if __name__ == "__main__":
    main()
