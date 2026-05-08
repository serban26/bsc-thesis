#!/usr/bin/env python3
"""
Plot results from a load-balancer experiment run.

Usage:
    python3 scripts/plot_metrics.py [--metrics FILE] [--requests FILE] [--out DIR]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

# ── try to import matplotlib; give a helpful message if missing ───────────
try:
    import matplotlib
    matplotlib.use("Agg")          # headless — no display required
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
except ImportError:
    print("matplotlib not found. Install with:  pip install matplotlib", file=sys.stderr)
    sys.exit(1)

# ── colour palette (consistent across all plots) ─────────────────────────
COLORS = {"h2": "#4C72B0", "h3": "#DD8452", "h4": "#55A868"}
MARKERS = {"h2": "o", "h3": "s", "h4": "^"}
SERVER_NAMES = ["h2", "h3", "h4"]


# ─────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────

def load_metrics(path: str):
    """Return (snapshots, selects, flow_removed_events).

    snapshots      : list of {t, servers: [{name, metric…}]}  – 5-s snapshots
    selects        : list of {event:"select", t, server, …}   – per-request
    flow_removed   : list of {event:"flow_removed", t, server, active_conns, …}
    """
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
            # backwards-compat: files written before status column was added
            if "status" not in row:
                row["status"] = "ok" if row["server"] in ("h2", "h3", "h4") else "other"
            rows.append(row)
    return rows


# ─────────────────────────────────────────────────────────────────────────
# Helper: extract per-server time series from snapshots
# ─────────────────────────────────────────────────────────────────────────

def ts_for(snapshots, metric: str):
    """Return {server_name: (times[], values[])} for a given metric key."""
    out = {n: ([], []) for n in SERVER_NAMES}
    for snap in snapshots:
        t = snap["t"]
        for srv in snap["servers"]:
            name = srv["name"]
            if name in out and metric in srv:
                out[name][0].append(t)
                out[name][1].append(srv[metric])
    return out


# ─────────────────────────────────────────────────────────────────────────
# Individual plot functions
# ─────────────────────────────────────────────────────────────────────────

def plot_request_distribution(requests, out_dir):
    """Bar chart: how many requests each backend served. Dashed line = ideal."""
    counts = {n: 0 for n in SERVER_NAMES}
    counts["error"] = 0
    counts["other"] = 0
    for r in requests:
        srv = r["server"]
        if r.get("status") == "error":
            counts["error"] = counts.get("error", 0) + 1
        else:
            counts[srv] = counts.get(srv, 0) + 1

    names  = list(SERVER_NAMES)
    if counts["error"]: names.append("error")
    if counts["other"]: names.append("other")
    values = [counts[n] for n in names]
    colors = [COLORS.get(n, "#e05555" if n == "error" else "#999999") for n in names]

    fig, ax = plt.subplots(figsize=(7, 4))
    bars = ax.bar(names, values, color=colors, edgecolor="white", linewidth=0.8)
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                str(val), ha="center", va="bottom", fontsize=10)

    ok_total = sum(counts[n] for n in SERVER_NAMES)
    ideal = ok_total / len(SERVER_NAMES)
    ax.axhline(ideal, color="black", linestyle="--", linewidth=1.2,
               label=f"Ideal ({ideal:.0f} each)")

    ax.set_title("Request distribution per backend")
    ax.set_ylabel("Requests served")
    ax.set_xlabel("Backend")
    ax.legend(fontsize=9)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    fig.tight_layout()
    _save(fig, out_dir / "01_request_distribution.png")


def plot_response_times(requests, out_dir):
    """Left: latency scatter coloured by server.  Right: box plot per server."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4))

    by_srv = defaultdict(lambda: ([], []))
    for r in requests:
        srv = r["server"]
        by_srv[srv][0].append(r["req"])
        by_srv[srv][1].append(r["elapsed_s"] * 1000)

    for name in SERVER_NAMES:
        xs, ys = by_srv[name]
        if xs:
            ax1.scatter(xs, ys, label=name, color=COLORS[name],
                        marker=MARKERS[name], s=30, alpha=0.85)
    # plot errors in red
    if by_srv["error"][0]:
        ax1.scatter(*by_srv["error"], label="500 error", color="#e05555",
                    marker="x", s=50, zorder=5)
    if by_srv["other"][0]:
        ax1.scatter(*by_srv["other"], label="other", color="#999",
                    marker="x", s=30)

    ax1.set_title("Response latency per request")
    ax1.set_xlabel("Request #")
    ax1.set_ylabel("Latency (ms)")
    ax1.legend(fontsize=9)
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
        bp = ax2.boxplot(data, patch_artist=True, labels=labels, widths=0.5)
        for patch, c in zip(bp["boxes"], cols):
            patch.set_facecolor(c)
            patch.set_alpha(0.7)
    ax2.set_title("Latency distribution per backend (successful only)")
    ax2.set_ylabel("Latency (ms)")
    ax2.set_xlabel("Backend")

    fig.tight_layout()
    _save(fig, out_dir / "02_response_times.png")


def _save(fig, path):
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  {path}")


def _events_to_step(selects, flow_removed, metric="active_conns"):
    """Build per-server step time-series from select + flow_removed events.

    Both event types carry the post-update value of *metric*.  We merge them,
    sort by t, then emit a step function (xs, ys) per server starting from t=0.
    """
    if not selects and not flow_removed:
        return {n: ([], []) for n in SERVER_NAMES}
    all_events = sorted(
        [e for e in selects + flow_removed if metric in e],
        key=lambda e: e["t"]
    )
    t0 = all_events[0]["t"] if all_events else 0
    # initialise step state at t=0 with value 0
    xs   = {n: [0] for n in SERVER_NAMES}
    ys   = {n: [0] for n in SERVER_NAMES}
    last = {n: 0   for n in SERVER_NAMES}
    for ev in all_events:
        name = ev.get("server")
        if name not in last:
            continue
        t   = ev["t"] - t0
        val = ev[metric]
        # hold previous value up to this point (step)
        xs[name].append(t)
        ys[name].append(last[name])
        xs[name].append(t)
        ys[name].append(val)
        last[name] = val
    return {n: (xs[n], ys[n]) for n in SERVER_NAMES}


def plot_active_conns(selects, flow_removed, out_dir):
    """Active connections over time — primary signal for least-connections.

    Built from individual select/flow_removed events so the full ramp-up and
    ramp-down are visible, not just the coarse 5-s snapshots.
    """
    ts = _events_to_step(selects, flow_removed, "active_conns")
    fig, ax = plt.subplots(figsize=(9, 4))
    for name in SERVER_NAMES:
        xs, ys = ts[name]
        if len(xs) > 1:
            ax.plot(xs, ys, label=name, color=COLORS[name], linewidth=1.8)
    ax.set_title("Active connections per backend over time\n"
                 "(key signal for least-connections algorithm)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Active connections")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    fig.tight_layout()
    _save(fig, out_dir / "03_active_conns.png")


def plot_cumulative_selections(selects, out_dir):
    """Cumulative picks per backend — shows the rotation shape over time.

    Each step up means one new request was assigned to that backend.  For
    perfect round-robin the three lines should climb as a tight parallel staircase.
    """
    if not selects:
        return
    t0  = selects[0]["t"]
    cum = {n: 0 for n in SERVER_NAMES}
    time_series = {n: [0] for n in SERVER_NAMES}
    val_series  = {n: [0] for n in SERVER_NAMES}
    for ev in selects:
        n = ev.get("server")
        if n in cum:
            cum[n] += 1
            t = ev["t"] - t0
            for name in SERVER_NAMES:
                time_series[name].append(t)
                val_series[name].append(cum[name])
    fig, ax = plt.subplots(figsize=(9, 4))
    for name in SERVER_NAMES:
        if len(time_series[name]) > 1:
            ax.step(time_series[name], val_series[name],
                    label=name, color=COLORS[name], where="post", linewidth=1.8)
    ax.set_title("Cumulative requests assigned per backend\n"
                 "(each step = one new flow routed to that server)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Total selections")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "04_cumulative_selections.png")


def plot_queue_depth(selects, flow_removed, out_dir):
    """Request queue depth per backend over time.

    For round-robin queue_depth == active_conns (every dispatched request is
    in-flight until its flow expires).  For least-connections with serialisation
    they can differ.  Shown here for completeness and LC comparison.
    """
    ts = _events_to_step(selects, flow_removed, "queue_depth")
    fig, ax = plt.subplots(figsize=(9, 4))
    for name in SERVER_NAMES:
        xs, ys = ts[name]
        if len(xs) > 1:
            ax.plot(xs, ys, label=name, color=COLORS[name], linewidth=1.8)
    ax.set_title("Request queue depth per backend over time\n")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Queue depth")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    fig.tight_layout()
    _save(fig, out_dir / "07_queue_depth.png")


def plot_score(snapshots, out_dir):
    """Composite score over time — algorithm fitness function (lower = better)."""
    ts = ts_for(snapshots, "score")
    fig, ax = plt.subplots(figsize=(9, 4))
    for name in SERVER_NAMES:
        xs, ys = ts[name]
        if xs:
            ax.plot(xs, ys, label=name, color=COLORS[name],
                    marker=MARKERS[name], markersize=4, linewidth=1.8)
    ax.set_title("Composite score per backend (lower = preferred)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Score")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "05_score.png")


def plot_total_bytes(snapshots, out_dir):
    """Cumulative bytes served — throughput fairness across backends."""
    ts = ts_for(snapshots, "total_bytes")
    fig, ax = plt.subplots(figsize=(9, 4))
    for name in SERVER_NAMES:
        xs, ys = ts[name]
        if xs:
            ax.plot(xs, ys, label=name, color=COLORS[name],
                    marker=MARKERS[name], markersize=4, linewidth=1.8)
    ax.set_title("Cumulative bytes served per backend")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Bytes")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "06_total_bytes.png")


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

    print("\nDone.")


if __name__ == "__main__":
    main()

