#!/usr/bin/env python3
"""Plot results from a load-balancer experiment run.

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

# for small pools use a categorical palette; for large pools use a smooth colormap
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

_TIER_COLORS = {"A": "#55A868", "B": "#4C72B0", "C": "#DD8452", "D": "#C44E52"}


def _server_tier(name):
    """Returns the tier letter for a server hostname."""
    idx = int(name[1:]) - 2
    n = _num_servers
    if idx < n * 0.4:
        return "A"
    elif idx < n * 0.6:
        return "B"
    elif idx < n * 0.8:
        return "C"
    return "D"


def plot_request_distribution(requests, out_dir):
    """Shows how many requests each backend received, colour-coded by tier."""
    counts = {n: 0 for n in SERVER_NAMES}
    n_errors = 0
    for r in requests:
        if r.get("status") == "error":
            n_errors += 1
        else:
            srv = r["server"]
            counts[srv] = counts.get(srv, 0) + 1

    values = [counts[n] for n in SERVER_NAMES]
    colors = [_TIER_COLORS[_server_tier(n)] for n in SERVER_NAMES]

    ok_total = sum(values)
    ideal = ok_total / len(SERVER_NAMES) if SERVER_NAMES else 0

    fig_w = max(7, min(32, 0.35 * len(SERVER_NAMES)))
    fig, ax = plt.subplots(figsize=(fig_w, 4))
    bars = ax.bar(range(len(SERVER_NAMES)), values, color=colors,
                  edgecolor="white", linewidth=0.4)

    ax.axhline(ideal, color="black", linestyle="--", linewidth=1.2,
               label=f"Ideal ({ideal:.0f} each)")

    # Tier boundary markers
    n = _num_servers
    for boundary, label in [
        (int(n * 0.4), "A|B"), (int(n * 0.6), "B|C"), (int(n * 0.8), "C|D")
    ]:
        ax.axvline(boundary - 0.5, color="gray", linestyle=":", linewidth=1.0)
        ax.text(boundary - 0.5, ax.get_ylim()[1] * 0.95,
                label, ha="center", va="top", fontsize=7, color="gray")

    if n_errors:
        ax.bar(len(SERVER_NAMES), n_errors, color="#e05555", label=f"errors ({n_errors})")

    ax.set_xticks([])
    ax.set_xlabel(f"Backend servers  (Tier A ← fast    slow → Tier D)")
    ax.set_ylabel("Requests served")
    ax.set_title("Request distribution per backend  (colour = tier)")
    ax.legend(fontsize=9)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    fig.tight_layout()
    _save(fig, out_dir / "01_request_distribution.png")


def plot_response_times(requests, out_dir):
    """Two subplots: rolling mean latency over time, and per-tier latency box plots."""
    ok = [r for r in requests if r.get("status") == "ok"]
    sr = sorted(ok, key=lambda r: r["req"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4))

    # Left: rolling mean latency over request sequence
    max_req = len(sr)
    window = max(5, min(100, max_req // 20))
    all_lats = [r["elapsed_s"] * 1000 for r in sr]
    all_xs = [r["req"] for r in sr]
    rolling = []
    for i in range(len(all_lats)):
        chunk = all_lats[max(0, i - window + 1): i + 1]
        rolling.append(sum(chunk) / len(chunk))
    ax1.scatter(all_xs, all_lats, s=4, alpha=0.25, color="#4C72B0", zorder=2)
    ax1.plot(all_xs, rolling, color="#C44E52", linewidth=2.0, zorder=3,
             label=f"rolling avg (w={window})")
    n_err = sum(1 for r in requests if r.get("status") == "error")
    if n_err:
        err_xs = [r["req"] for r in requests if r.get("status") == "error"]
        ax1.scatter(err_xs, [5000] * len(err_xs), marker="x", color="#e05555",
                    s=30, zorder=4, label=f"errors ({n_err})")
    ax1.set_title("Response latency over time")
    ax1.set_xlabel("Request number")
    ax1.set_ylabel("Latency (ms)")
    ax1.legend(fontsize=9)
    ax1.grid(True, alpha=0.25)

    # Right: one box per tier (4 boxes instead of 50)
    tier_data = defaultdict(list)
    for r in ok:
        tier_data[_server_tier(r["server"])].append(r["elapsed_s"] * 1000)
    tier_order = [t for t in "ABCD" if tier_data[t]]
    if tier_order:
        bp = ax2.boxplot(
            [tier_data[t] for t in tier_order],
            patch_artist=True,
            labels=[f"Tier {t}" for t in tier_order],
            widths=0.5,
            medianprops=dict(color="white", linewidth=2),
            flierprops=dict(marker=".", markersize=3, alpha=0.4),
        )
        for patch, t in zip(bp["boxes"], tier_order):
            patch.set_facecolor(_TIER_COLORS[t])
            patch.set_alpha(0.85)
    else:
        ax2.text(0.5, 0.5, "No successful requests",
                 ha="center", va="center", transform=ax2.transAxes,
                 fontsize=12, color="red")
    ax2.set_title("Latency distribution per tier")
    ax2.set_ylabel("Latency (ms)")
    ax2.set_xlabel("Server tier")
    ax2.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save(fig, out_dir / "02_response_times.png")


def plot_active_conns(selects, flow_removed, out_dir):
    """Active connections over time, aggregated by tier when N is large."""
    ts = _events_to_step(selects, flow_removed, "active_conns")
    fig, ax = plt.subplots(figsize=(9, 4))

    if _num_servers <= _LEGEND_THRESHOLD:
        # Small pool: one line per server
        for name in SERVER_NAMES:
            xs, ys = ts[name]
            if len(xs) > 1:
                ax.plot(xs, ys, color=COLORS[name], linewidth=1.5, alpha=0.75, label=name)
        ax.legend(fontsize=8)
    else:
        # Large pool: sum active conns per tier and plot 4 lines
        all_events = sorted(
            [e for e in selects + flow_removed if "active_conns" in e],
            key=lambda e: e["t"],
        )
        if all_events:
            t0 = all_events[0]["t"]
            tier_vals = defaultdict(lambda: defaultdict(int))
            last = {n: 0 for n in SERVER_NAMES}
            times = []
            snapshots = []
            for ev in all_events:
                name = ev.get("server")
                if name not in last:
                    continue
                last[name] = ev["active_conns"]
                t = ev["t"] - t0
                times.append(t)
                snap = defaultdict(int)
                for n in SERVER_NAMES:
                    snap[_server_tier(n)] += last[n]
                snapshots.append(snap)
            for t_letter, color in _TIER_COLORS.items():
                ys = [s[t_letter] for s in snapshots]
                if any(y > 0 for y in ys):
                    ax.plot(times, ys, color=color, linewidth=1.8,
                            alpha=0.85, label=f"Tier {t_letter}")
        ax.legend(fontsize=9)

    ax.set_title("Active connections over time  (aggregated by tier)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Active connections")
    ax.grid(True, alpha=0.3)
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    fig.tight_layout()
    _save(fig, out_dir / "03_active_conns.png")


def plot_cumulative_selections(selects, out_dir):
    """Cumulative selections over time, shown per tier for large pools."""
    if not selects:
        return
    t0 = selects[0]["t"]
    fig, ax = plt.subplots(figsize=(9, 4))

    if _num_servers <= _LEGEND_THRESHOLD:
        cum = {n: 0 for n in SERVER_NAMES}
        ts = {n: [0] for n in SERVER_NAMES}
        vs = {n: [0] for n in SERVER_NAMES}
        for ev in selects:
            n = ev.get("server")
            if n in cum:
                cum[n] += 1
                t = ev["t"] - t0
                for name in SERVER_NAMES:
                    ts[name].append(t)
                    vs[name].append(cum[name])
        for name in SERVER_NAMES:
            if len(ts[name]) > 1:
                ax.step(ts[name], vs[name], color=COLORS[name],
                        linewidth=1.5, alpha=0.75, where="post", label=name)
        ax.legend(fontsize=8)
    else:
        cum_tier = defaultdict(int)
        tier_ts = defaultdict(lambda: [0])
        tier_vs = defaultdict(lambda: [0])
        for ev in selects:
            n = ev.get("server")
            if n not in COLORS:
                continue
            t = ev["t"] - t0
            tier = _server_tier(n)
            cum_tier[tier] += 1
            for tl in "ABCD":
                tier_ts[tl].append(t)
                tier_vs[tl].append(cum_tier[tl])
        for tl, color in _TIER_COLORS.items():
            if len(tier_ts[tl]) > 1:
                ax.step(tier_ts[tl], tier_vs[tl], color=color,
                        linewidth=2.0, alpha=0.9, where="post", label=f"Tier {tl}")
        ax.legend(fontsize=9)

    ax.set_title("Cumulative selections over time  (aggregated by tier)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Total requests assigned")
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
