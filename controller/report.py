import sys
import csv


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
        arrival = row.get("arrival_ms", "")
        server = row.get("server", "")
        start_ms = row.get("start_ms", "")
        finish_ms = row.get("finish_ms", "")
        status = row.get("status", "")
        if not arrival:
            arrival = "-"
        if not server:
            server = "-"
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


if __name__ == "__main__":
    main()
