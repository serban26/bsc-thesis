#!/usr/bin/env python3
"""
Automated Mininet runner for the load-balancer experiment.

Unlike run_net.py (which drops into an interactive CLI), this script:
  1. Builds the topology and starts backends.
  2. Fires a configurable number of sequential HTTP requests from h1 → VIP.
  3. Waits for in-flight flows to expire (FlowRemoved events land in the
     controller and decrement active_conns).
  4. Writes a per-request CSV to /tmp/lb_requests.csv.
  5. Exits cleanly (no interactive CLI).

Usage (must run as root):
    sudo -E python3 topo/run_net_auto.py [NUM_REQUESTS] [DRAIN_SECS]

Environment:
    LB_REQUESTS   number of sequential HTTP GETs  (default 30)
    LB_DRAIN      seconds to wait after last req  (default 5)
    LB_DELAY      seconds between requests         (default 0.5)
"""

import csv
import os
import signal
import sys
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

# ── topology / service constants (must match load_balancer.py) ─────────────
VIP        = "10.0.0.100"
VMAC       = "00:00:00:00:01:00"
CLIENT_IP  = "10.0.0.1"
CLIENT_MAC = "00:00:00:00:00:01"
HTTP_PORT  = 8000

# ── experiment parameters ──────────────────────────────────────────────────
NUM_REQUESTS  = int(os.environ.get("LB_REQUESTS", sys.argv[1] if len(sys.argv) > 1 else "30"))
DRAIN_SECS    = int(os.environ.get("LB_DRAIN",    sys.argv[2] if len(sys.argv) > 2 else "5"))
REQUEST_DELAY = float(os.environ.get("LB_DELAY", "0.5"))   # seconds between GETs
REQUESTS_CSV  = os.environ.get("LB_REQUESTS_CSV", "/tmp/lb_requests.csv")
NUM_SERVERS   = int(os.environ.get("LB_NUM_SERVERS", "3"))

import shutil

_SCRIPT_DIR = "/tmp/lb_servers"


def build():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=False, build=False)
    net.addController("c0", controller=RemoteController,
                      ip="127.0.0.1", port=6633)
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    h1 = net.addHost("h1", ip=f"{CLIENT_IP}/24", mac=CLIENT_MAC)
    # client link: low base delay, small jitter
    net.addLink(h1, s1, delay="2ms", jitter="1ms")
    servers = []
    # base link profiles for the first 3 servers; extras get increasing latency
    _base_profiles = [
        dict(delay="3ms",  jitter="1ms"),   # h2 — fast
        dict(delay="8ms",  jitter="3ms"),   # h3 — medium
        dict(delay="15ms", jitter="5ms"),   # h4 — slow
    ]
    for idx in range(NUM_SERVERS):
        i = idx + 2  # host number: 2, 3, 4, ...
        if idx < len(_base_profiles):
            profile = _base_profiles[idx]
        else:
            # Spread extra servers between 20ms and 40ms — meaningful but not absurd
            ms = 20 + min(20, 4 * (idx - 3))
            profile = dict(delay=f"{ms}ms", jitter="5ms")
        h = net.addHost(f"h{i}", ip=f"10.0.0.{i}/24",
                        mac=f"00:00:00:00:00:{i:02x}")
        net.addLink(h, s1, **profile)
        servers.append(h)
    return net, h1, servers


def _prepare_script_dir():
    shutil.rmtree(_SCRIPT_DIR, ignore_errors=True)
    os.makedirs(_SCRIPT_DIR, mode=0o755, exist_ok=True)


# Per-server processing-time profiles (base_ms, jitter_ms, spike_prob, error_prob)
# h2 = fast and reliable, h3 = medium with occasional spikes, h4 = slower/variable
# Extra servers (h5+) get progressively slower profiles.
_SERVER_PROFILES = {
    "h2": dict(base_ms=5,  jitter_ms=3,  spike_prob=0.05, error_prob=0.03),
    "h3": dict(base_ms=20, jitter_ms=10, spike_prob=0.10, error_prob=0.05),
    "h4": dict(base_ms=40, jitter_ms=20, spike_prob=0.15, error_prob=0.04),
}
# Auto-fill profiles for h5, h6, ... up to NUM_SERVERS servers
for _idx in range(3, NUM_SERVERS):
    _hname = f"h{_idx + 2}"
    if _hname not in _SERVER_PROFILES:
        # Spread extra servers between h4 (40ms) and ~80ms, then plateau
        _base = min(80, 40 + 5 * (_idx - 2))
        _jitter = min(25, 20 + 2 * (_idx - 2))
        _SERVER_PROFILES[_hname] = dict(
            base_ms=_base,
            jitter_ms=_jitter,
            spike_prob=min(0.20, 0.15 + 0.01 * (_idx - 2)),
            error_prob=0.05,
        )


def _write_server_script(host_name: str, host_ip: str) -> str:
    path = f"{_SCRIPT_DIR}/lb_server_{host_name}.py"
    body = f"served by {host_name} ({host_ip})"
    p = _SERVER_PROFILES.get(host_name, dict(base_ms=10, jitter_ms=5,
                                              spike_prob=0.05, error_prob=0.03))
    # Deterministic per-server seed derived from the host name.
    # Every algorithm run starts the same server with the same seed,
    # so the sequence of delays/errors is identical across runs.
    # Different servers still behave differently (different profiles AND seeds).
    seed_val = sum(ord(c) * (i + 1) for i, c in enumerate(host_name))
    script = f"""\
#!/usr/bin/env python3
import http.server, socketserver, time, random

# Fixed seed — same for every algorithm run that uses this server,
# so delay/error sequences are reproducible and comparable.
random.seed({seed_val})

BODY       = b"{body}\\n"
PORT       = {HTTP_PORT}
BASE_MS    = {p['base_ms']}
JITTER_MS  = {p['jitter_ms']}
SPIKE_PROB = {p['spike_prob']}   # probability of a latency spike this request
ERROR_PROB = {p['error_prob']}   # probability of a 500 response

class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        # simulate server processing time
        delay_s = (BASE_MS + random.uniform(-JITTER_MS, JITTER_MS)) / 1000.0
        delay_s = max(delay_s, 0.001)
        # occasional spike (e.g. GC pause, cache miss)
        if random.random() < SPIKE_PROB:
            delay_s += random.uniform(0.05, 0.25)
        time.sleep(delay_s)
        # occasional server error
        if random.random() < ERROR_PROB:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "6")
            self.end_headers()
            self.wfile.write(b"error\\n")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)
    def log_message(self, fmt, *args): pass

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("0.0.0.0", PORT), _H) as srv:
    srv.serve_forever()
"""
    with open(path, "w") as fh:
        fh.write(script)
    return path


def disable_offload(hosts):
    """Force full checksum computation before OVS sees the packets.
    Critical for TCP through the load-balancer: OVS SetField rewrites
    IP addresses and only does an incremental checksum fixup, which
    only works if the original checksum was fully computed in software.
    """
    for h in hosts:
        for intf in h.intfList():
            if intf.name == "lo":
                continue
            result = h.cmd(f"ethtool -K {intf.name} tx off gso off tso off 2>&1")
            if "not found" in result or "command not found" in result:
                h.cmd(
                    f"echo 0 > /sys/class/net/{intf.name}/features/tx-checksumming"
                    f" 2>/dev/null; true"
                )


def start_servers(servers):
    _prepare_script_dir()
    for h in servers:
        script = _write_server_script(h.name, h.IP())
        log    = f"{_SCRIPT_DIR}/lb_server_{h.name}.log"
        h.cmd(f"pkill -f 'python3 {script}' 2>/dev/null; true")
        h.cmd(f"nohup python3 {script} > {log} 2>&1 &")
    time.sleep(1.0)


def stop_servers(servers):
    for h in servers:
        script = f"{_SCRIPT_DIR}/lb_server_{h.name}.py"
        h.cmd(f"pkill -f 'python3 {script}' 2>/dev/null; true")


def prime_arp(client, servers):
    """Pre-populate ARP so neither client nor servers have to ARP on first pkt."""
    client.cmd(f"ip neigh replace {VIP} lladdr {VMAC} dev {client.name}-eth0")
    for srv in servers:
        srv.cmd(
            f"ip neigh replace {CLIENT_IP} lladdr {CLIENT_MAC}"
            f" dev {srv.name}-eth0"
        )


def wait_for_controller(client, retries=20, interval=1.0):
    """Wait until Ryu is listening on the OpenFlow port (no packets through switch)."""
    import subprocess
    print("Waiting for controller to come up", end="", flush=True)
    for _ in range(retries):
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True, text=True,
        )
        if ":6633" in result.stdout or ":6653" in result.stdout:
            print(" OK")
            return True
        print(".", end="", flush=True)
        time.sleep(interval)
    print(" TIMEOUT")
    return False


def run_requests(client, n):
    """Fire n sequential HTTP GETs from the client host. Returns list of dicts."""
    server_names = [f"h{2 + i}" for i in range(NUM_SERVERS)]
    bar_map = {name: chr(0x2460 + i) for i, name in enumerate(server_names)}
    bar_map["error"] = "✗"
    bar_map["other"] = "?"

    records = []
    print(f"\nFiring {n} sequential HTTP GETs to http://{VIP}:{HTTP_PORT}/")
    for i in range(1, n + 1):
        t0 = time.time()
        body = client.cmd(
            f"curl -s --max-time 5 http://{VIP}:{HTTP_PORT}/"
        ).strip()
        elapsed = time.time() - t0

        srv = "other"
        status = "other"
        for name in server_names:
            if f"served by {name} " in body:   # trailing space avoids h2 matching h20/h21
                srv = name
                status = "ok"
                break
        if srv == "other" and "error" in body.lower():
            srv = "error"
            status = "error"

        records.append({
            "req": i,
            "server": srv,
            "status": status,
            "elapsed_s": round(elapsed, 4),
            "body": body[:80],
        })
        bar = bar_map.get(srv, "?")
        tag = f"  \033[31m[500]\033[0m" if status == "error" else ""
        print(f"  [{i:3d}] {bar} {srv}  ({elapsed*1000:.0f} ms){tag}")
        if REQUEST_DELAY > 0 and i < n:
            time.sleep(REQUEST_DELAY)

    return records


def save_csv(records, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["req", "server", "status", "elapsed_s", "body"])
        w.writeheader()
        w.writerows(records)
    print(f"\nPer-request CSV -> {path}")


def main():
    setLogLevel("warning")       # suppress mininet noise; controller logs separately
    net, h1, servers = build()
    net.build()
    net.start()

    try:
        disable_offload(net.hosts)
        start_servers(servers)
        prime_arp(h1, servers)

        print(f"  client : {h1.name} ({h1.IP()})")
        for s in servers:
            print(f"  server : {s.name} ({s.IP()})  http://*:{HTTP_PORT}")
        print(f"  VIP    : {VIP}")

        if not wait_for_controller(h1):
            print("[WARN] controller did not respond; continuing anyway")

        # Warm-up: hit each server directly (not via VIP) so ARP converges
        # without advancing the load-balancer cursor.
        print("\nWarm-up (direct ARP probe, load balancer not touched)…")
        for s in servers:
            h1.cmd(f"curl -s --max-time 3 -o /dev/null http://{s.IP()}:{HTTP_PORT}/")
        time.sleep(0.5)

        records = run_requests(h1, NUM_REQUESTS)
        save_csv(records, REQUESTS_CSV)

        # Summary
        counts: dict = {f"h{2 + i}": 0 for i in range(NUM_SERVERS)}
        counts["error"] = 0
        counts["other"] = 0
        for r in records:
            counts[r["server"]] = counts.get(r["server"], 0) + 1
        print("\nSummary:")
        for k, v in counts.items():
            if v:
                print(f"  {k}: {v}")

        print(f"\nDraining flows ({DRAIN_SECS}s) so FlowRemoved events fire…")
        time.sleep(DRAIN_SECS)

    finally:
        stop_servers(servers)
        net.stop()
        print("Network stopped.")


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Must run as root (sudo).", file=sys.stderr)
        sys.exit(1)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    main()
