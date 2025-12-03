import sys
import os
import csv


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compute_stats(rows):
    parsed = []
    for row in rows:
        name = row.get("name", "")
        arrival_ms = _to_int(row.get("arrival_ms"))
        start_ms = _to_int(row.get("start_ms"))
        finish_ms = _to_int(row.get("finish_ms"))
        server = row.get("server", "")
        status = row.get("status", "")
        parsed.append(
            {
                "name": name,
                "arrival_ms": arrival_ms,
                "start_ms": start_ms,
                "finish_ms": finish_ms,
                "server": server,
                "status": status,
            }
        )
    servers = sorted({p["server"] for p in parsed if p["server"]})
    valid = [p for p in parsed if p["arrival_ms"] is not None]
    if not valid:
        return []
    arrivals = [p["arrival_ms"] for p in valid if p["arrival_ms"] is not None]
    finishes = [p["finish_ms"] for p in valid if p["finish_ms"] is not None]
    if not arrivals or not finishes:
        return []
    start_time = min(arrivals)
    end_time = max(finishes)
    makespan = max(0, end_time - start_time)
    completed = [
        p
        for p in valid
        if p["finish_ms"] is not None and p["status"] != "too_consuming"
    ]
    failed = [
        p
        for p in valid
        if p["finish_ms"] is None or p["status"] == "too_consuming"
    ]
    total = len(valid)

    def avg(values):
        return round(float(sum(values)) / float(len(values)), 2) if values else 0.0

    response_times = [
        p["finish_ms"] - p["arrival_ms"]
        for p in completed
        if p["finish_ms"] is not None and p["arrival_ms"] is not None
    ]
    wait_times = [
        p["start_ms"] - p["arrival_ms"]
        for p in completed
        if p["start_ms"] is not None and p["arrival_ms"] is not None
    ]
    server_stats = {}
    for s in servers:
        s_rows = [p for p in parsed if p["server"] == s]
        s_completed = [
            p
            for p in s_rows
            if p["finish_ms"] is not None and p["status"] != "too_consuming"
        ]
        s_busy = sum(
            (p["finish_ms"] - p["start_ms"])
            for p in s_completed
            if p["finish_ms"] is not None and p["start_ms"] is not None
        )
        s_response = [
            p["finish_ms"] - p["arrival_ms"]
            for p in s_completed
            if p["finish_ms"] is not None and p["arrival_ms"] is not None
        ]
        s_wait = [
            p["start_ms"] - p["arrival_ms"]
            for p in s_completed
            if p["start_ms"] is not None and p["arrival_ms"] is not None
        ]
        s_total = len(s_rows)
        s_failed = s_total - len(s_completed)
        util = round((float(s_busy) / float(makespan)) * 100.0, 2) if makespan > 0 else 0.0
        server_stats[s] = {
            "busy_ms": int(s_busy),
            "requests": s_total,
            "completed": len(s_completed),
            "failed": s_failed,
            "avg_response_ms": avg(s_response),
            "avg_wait_ms": avg(s_wait),
            "utilization": util,
        }
    global_busy = sum(v["busy_ms"] for v in server_stats.values())
    capacity = max(len(servers), 1)
    global_idle = max(0, makespan * capacity - global_busy)
    if makespan > 0 and capacity > 0:
        global_util = round(
            (float(global_busy) / float(makespan * capacity)) * 100.0, 2
        )
    else:
        global_util = 0.0
    stats_rows = []
    stats_rows.append(
        {
            "algorithm": "Round Robin",
            "scope": "global",
            "name": "all_servers",
            "requests": total,
            "completed": len(completed),
            "failed": len(failed),
            "avg_response_ms": avg(response_times),
            "avg_wait_ms": avg(wait_times),
            "busy_ms": int(global_busy),
            "idle_ms": int(global_idle),
            "utilization": global_util,
            "makespan_ms": int(makespan),
        }
    )
    for s in servers:
        v = server_stats[s]
        stats_rows.append(
            {
                "algorithm": "Round Robin",
                "scope": "server",
                "name": s,
                "requests": v["requests"],
                "completed": v["completed"],
                "failed": v["failed"],
                "avg_response_ms": v["avg_response_ms"],
                "avg_wait_ms": v["avg_wait_ms"],
                "busy_ms": v["busy_ms"],
                "idle_ms": int(max(0, makespan - v["busy_ms"])),
                "utilization": v["utilization"],
                "makespan_ms": int(makespan),
            }
        )
    return stats_rows


def write_stats(rows, output_path="results/stats.csv"):
    stats_rows = compute_stats(rows)
    if not stats_rows:
        return
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = [
        "algorithm",
        "scope",
        "name",
        "requests",
        "completed",
        "failed",
        "avg_response_ms",
        "avg_wait_ms",
        "busy_ms",
        "idle_ms",
        "utilization",
        "makespan_ms",
    ]
    with open(output_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in stats_rows:
            w.writerow(row)


def main():
    if len(sys.argv) >= 2:
        path = sys.argv[1]
    else:
        path = "results/schedule.csv"
    rows = []
    with open(path, "r") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)
    header = "{:<10} {:>12} {:>14} {:>14} {:>14} {:>12}".format(
        "Request", "Arrival(ms)", "Server", "Start(ms)", "Finish(ms)", "Status"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        name = row.get("name", "")
        arrival = row.get("arrival_ms", "") or "-"
        server = row.get("server", "") or "-"
        start_ms = row.get("start_ms", "")
        finish_ms = row.get("finish_ms", "")
        status = row.get("status", "")
        if not start_ms:
            start_ms = "-"
        if not finish_ms:
            finish_ms = "-"
        if not status:
            status = "-"
        line = "{:<10} {:>12} {:>14} {:>14} {:>14} {:>12}".format(
            name, arrival, server, start_ms, finish_ms, status
        )
        print(line)
    write_stats(rows)


if __name__ == "__main__":
    main()
