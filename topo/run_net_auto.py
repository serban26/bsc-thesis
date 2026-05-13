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
import json
import os
import random
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
REQUESTS_CSV  = os.environ.get("LB_REQUESTS_CSV", "/tmp/lb_requests.csv")
NUM_SERVERS   = int(os.environ.get("LB_NUM_SERVERS", "3"))
# Rate at which the client fires requests (requests/second).
#
# With 50 servers at RATE=40: each server receives 0.8 req/s from RR.
# Tier D (last 10%, 800-1000 ms service, max_concurrent=1):
#   capacity ≈ 1.0-1.25 req/s  →  ρ ≈ 0.65-0.80
#   M/M/1 mean wait = ρ/(1-ρ) × E[S] ≈ 1.5-3.2 s  →  total ≈ 2-4 s per req
# AP avoids Tier D after warm-up  →  mean ≈ 30-60 ms (Tier A+B only)
# → RR mean ~400 ms  vs  AP mean ~40 ms  — 10× difference in every plot
RATE = float(os.environ.get("LB_RATE", "40.0"))

import shutil

_SCRIPT_DIR = "/tmp/lb_servers"

# ─── heterogeneous server pool ─────────────────────────────────────────────
#
# Servers are ordered best-first (h2 = fastest, hN = slowest) so that WRR
# weights (higher index → lower weight) naturally align with capacity, and
# smart algorithms (LC, LRT, AP) converge on the fast tier quickly.
#
# Four performance tiers based on fractional position in the pool:
#   A (fast,   first 40%): powerful machines, low latency, high concurrency
#   B (medium, next  30%): standard rack servers
#   C (slow,   next  20%): underpowered / CPU-saturated machines
#   D (legacy, last  10%): old hardware, strictly single-threaded
#
# At RATE=40 with 50 servers (RR sends 0.8 req/s per server):
#   Tier D capacity ≈ 1.0-1.25 req/s  →  ρ ≈ 0.65-0.80
#   M/M/1 mean wait = ρ/(1-ρ) × E[S] ≈ 1.5-3 s  →  total ≈ 2-4 s per request
#   AP avoids Tier D after warm-up → mean ≈ 30-60 ms (Tier A+B only)
#   Result: RR mean ~400 ms  vs  AP mean ~40 ms  — 10× visible in every plot
_TIER_THRESHOLDS = (0.40, 0.70, 0.90)   # A<40% ≤ B<70% ≤ C<90% ≤ D

_TIER_PARAMS = {
    #       base latency (ms)        jitter (ms)          spike   error  concurrency
    'A': dict(base_lo=5,    base_hi=25,  jit_lo=2,  jit_hi=8,   spike=0.02, err=0.005, conc_lo=8,  conc_hi=16),
    'B': dict(base_lo=50,   base_hi=150, jit_lo=15, jit_hi=50,  spike=0.05, err=0.010, conc_lo=4,  conc_hi=8),
    'C': dict(base_lo=300,  base_hi=600, jit_lo=40, jit_hi=80,  spike=0.08, err=0.015, conc_lo=2,  conc_hi=3),
    'D': dict(base_lo=800,  base_hi=1000,jit_lo=60, jit_hi=120, spike=0.10, err=0.020, conc_lo=1,  conc_hi=1),
}

# Network link delays per tier (fast servers = good network placement)
_TIER_LINK = {
    'A': dict(delay="2ms",  jitter="1ms"),
    'B': dict(delay="5ms",  jitter="2ms"),
    'C': dict(delay="10ms", jitter="4ms"),
    'D': dict(delay="18ms", jitter="6ms"),
}


def _assign_tier(idx: int, n: int) -> str:
    """Return tier letter (A/B/C/D) for the idx-th server (0-based) in a pool of n."""
    frac = idx / max(n - 1, 1)
    if frac < _TIER_THRESHOLDS[0]: return 'A'
    if frac < _TIER_THRESHOLDS[1]: return 'B'
    if frac < _TIER_THRESHOLDS[2]: return 'C'
    return 'D'


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
    for idx in range(NUM_SERVERS):
        i    = idx + 2
        tier = _assign_tier(idx, NUM_SERVERS)
        h    = net.addHost(f"h{i}", ip=f"10.0.0.{i}/24",
                           mac=f"00:00:00:00:00:{i:02x}")
        net.addLink(h, s1, **_TIER_LINK[tier])
        servers.append(h)
    return net, h1, servers


def _prepare_script_dir():
    shutil.rmtree(_SCRIPT_DIR, ignore_errors=True)
    os.makedirs(_SCRIPT_DIR, mode=0o755, exist_ok=True)


# ─── per-server processing-time profiles ───────────────────────────────────
# Generated from _TIER_PARAMS using a per-server deterministic RNG so every
# algorithm run sees identical server behaviour (fair comparison).
# max_concurrent = semaphore inside the server: controls real parallelism.
# Tier D (max_concurrent=1) strictly queues incoming requests, creating
# measurable back-pressure that LC/LRT/AP learn to avoid.
_SERVER_PROFILES = {}
for _idx in range(NUM_SERVERS):
    _hname = f"h{_idx + 2}"
    _tier  = _assign_tier(_idx, NUM_SERVERS)
    _p     = _TIER_PARAMS[_tier]
    # Large prime multiplier spreads seeds far apart to avoid correlation
    _rng   = random.Random(_idx * 0x9E3779B9 & 0xFFFFFFFF)
    _SERVER_PROFILES[_hname] = dict(
        base_ms        = int(_rng.uniform(_p['base_lo'],  _p['base_hi'])),
        jitter_ms      = int(_rng.uniform(_p['jit_lo'],   _p['jit_hi'])),
        spike_prob     = _p['spike'],
        error_prob     = _p['err'],
        max_concurrent = _rng.randint(_p['conc_lo'], _p['conc_hi']),
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
import http.server, socketserver, time, random, threading

# Fixed seed — reproducible delay/error sequence across algorithm runs.
random.seed({seed_val})

BODY          = b"{body}\\n"
PORT          = {HTTP_PORT}
BASE_MS       = {p['base_ms']}
JITTER_MS     = {p['jitter_ms']}
SPIKE_PROB    = {p['spike_prob']}
ERROR_PROB    = {p['error_prob']}
MAX_CONC      = {p.get('max_concurrent', 2)}

# Semaphore: only MAX_CONC requests process simultaneously.
# Any extra connections are accepted by the OS (TCP backlog) and
# physically wait here until a slot is free.  This creates a real,
# measurable queue that active_conns in the controller reflects.
_sem = threading.Semaphore(MAX_CONC)

class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        _sem.acquire()
        try:
            delay_s = (BASE_MS + random.uniform(-JITTER_MS, JITTER_MS)) / 1000.0
            delay_s = max(delay_s, 0.001)
            if random.random() < SPIKE_PROB:
                delay_s += random.uniform(0.05, 0.30)
            time.sleep(delay_s)
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
        finally:
            _sem.release()
    def log_message(self, fmt, *args): pass

# ThreadingTCPServer: each incoming TCP connection gets its own thread.
# Combined with the semaphore above this gives us:
#   • real concurrent connection acceptance (no request dropped at TCP level)
#   • configurable parallelism (MAX_CONC requests actually running at once)
#   • measurable queue depth = accepted_threads - processing_threads
class _S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

with _S(("0.0.0.0", PORT), _H) as srv:
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


_CLIENT_SCRIPT = "/tmp/lb_client.py"


def _write_client_script(total: int, rate: float) -> None:
    """Write a Python load-generator to /tmp/lb_client.py.

    Uses urllib.request (pure Python, in-process) instead of subprocess curl.
    This is critical: subprocess.run() inside threads spawned inside
    Mininet's host.cmd() shell does NOT inherit the host's network namespace —
    the forked curl processes run in the root namespace where the VIP doesn't
    exist.  urllib.request uses the calling process's own socket, which IS
    in the correct namespace.
    """
    delay = 1.0 / rate
    script = f"""#!/usr/bin/env python3
import threading, time, json
import urllib.request, urllib.error

TOTAL   = {total}
DELAY   = {delay:.4f}   # 1 / rate  ({rate:.1f} req/s)
URL     = "http://{VIP}:{HTTP_PORT}/"
# 30 s timeout: Tier D servers (800-1000 ms, max_concurrent=1) under RR
# accumulate queues → response times of 2-25 s depending on queue depth.
# Keeping them as measured latency (not errors) makes the RR vs AP
# difference visible in the latency and percentile plots, not error plots.
TIMEOUT = 30.0

results = [None] * TOTAL
_lock   = threading.Lock()

def fire(i):
    t0 = time.time()
    try:
        with urllib.request.urlopen(URL, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        body = f"error: HTTP {{exc.code}}"
    except Exception as exc:
        body = f"error: {{exc}}"
    elapsed = round(time.time() - t0, 4)
    with _lock:
        results[i] = {{"i": i + 1, "elapsed": elapsed, "body": body}}

threads = []
for i in range(TOTAL):
    t = threading.Thread(target=fire, args=(i,), daemon=True)
    t.start()
    threads.append(t)
    if i < TOTAL - 1:
        time.sleep(DELAY)

for t in threads:
    t.join(timeout=TIMEOUT + 30.0)  # extra headroom for Tier D queue drain

print(json.dumps(results))
"""
    with open(_CLIENT_SCRIPT, "w") as fh:
        fh.write(script)


def run_requests(client, n):
    """Fire n HTTP GETs at RATE req/s using a threaded Python client on h1.

    Requests overlap: the client fires one every 1/RATE seconds regardless
    of whether previous ones have finished.  This creates real concurrent
    load that exposes the queue at slow servers.
    """
    server_names = [f"h{2 + i}" for i in range(NUM_SERVERS)]

    _write_client_script(n, RATE)
    print(f"\nFiring {n} requests at {RATE:.1f} req/s to http://{VIP}:{HTTP_PORT}/")
    print(f"  (inter-request delay: {1000/RATE:.0f}ms, {NUM_SERVERS} servers, "
          f"Tier D capacity ~1.0-1.25 req/s)")
    # For large runs print ~20 progress lines instead of one per request
    _print_step = max(1, n // 20)

    raw = client.cmd(f"python3 {_CLIENT_SCRIPT}")

    # strip any stray mininet prompt / newlines that might prefix the JSON
    raw = raw.strip()
    json_start = raw.find('[')
    if json_start > 0:
        raw = raw[json_start:]

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] could not parse client output ({exc}): {raw[:300]}")
        return []

    records = []
    for item in sorted(data, key=lambda x: (x or {}).get("i", 0)):
        if item is None:
            continue
        body    = item.get("body", "")
        elapsed = item.get("elapsed", 0.0)
        req_i   = item.get("i", 0)

        srv    = "other"
        status = "other"
        for name in server_names:
            if f"served by {name} " in body:
                srv    = name
                status = "ok"
                break
        if srv == "other":
            status = "error"
            srv    = "error"

        records.append({
            "req":       req_i,
            "server":    srv,
            "status":    status,
            "elapsed_s": elapsed,
            "body":      body[:80],
        })
        tag = f"  \033[31m[err/timeout]\033[0m" if status == "error" else ""
        if n <= 100 or req_i % _print_step == 0 or req_i == n or status == "error":
            tier = _assign_tier(server_names.index(srv), NUM_SERVERS) if srv in server_names else "?"
            print(f"  [{req_i:5d}/{n}] {srv:6s} tier={tier}  ({elapsed*1000:.0f} ms){tag}")

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
