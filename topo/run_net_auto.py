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
RATE = float(os.environ.get("LB_RATE", "40.0"))  # requests/second per client
NUM_CLIENTS = int(os.environ.get("LB_NUM_CLIENTS", "5"))

import shutil

_SCRIPT_DIR = "/tmp/lb_servers"

# server tiers: A=fast (first 40%), B=medium (next 30%), C=slow (next 20%), D=legacy (last 10%)
_TIER_THRESHOLDS = (0.40, 0.70, 0.90)   # A<40% ≤ B<70% ≤ C<90% ≤ D

_TIER_PARAMS = {
    #       base latency (ms)        jitter (ms)          spike   error  concurrency       spike behaviour
    'A': dict(base_lo=5,    base_hi=25,  jit_lo=2,  jit_hi=8,   spike=0.02, err=0.005, conc_lo=8,  conc_hi=16,
              spike_mul=8.0,  spike_period=20.0, spike_duration=6.0),
    'B': dict(base_lo=50,   base_hi=150, jit_lo=15, jit_hi=50,  spike=0.03, err=0.010, conc_lo=4,  conc_hi=8,
              spike_mul=5.0,  spike_period=25.0, spike_duration=7.0),
    'C': dict(base_lo=300,  base_hi=600, jit_lo=40, jit_hi=80,  spike=0.03, err=0.015, conc_lo=2,  conc_hi=3,
              spike_mul=2.0,  spike_period=30.0, spike_duration=8.0),
    'D': dict(base_lo=800,  base_hi=1000,jit_lo=60, jit_hi=120, spike=0.02, err=0.020, conc_lo=1,  conc_hi=1,
              spike_mul=1.5,  spike_period=40.0, spike_duration=6.0),
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
    s1 = net.addSwitch("s1", protocols="OpenFlow13", batch=True)

    # h1 is the main client; extra clients use 10.0.0.200+ to avoid colliding with server IPs
    clients = []
    h1 = net.addHost("h1", ip=f"{CLIENT_IP}/24", mac=CLIENT_MAC)
    net.addLink(h1, s1, delay="2ms", jitter="1ms")
    clients.append(h1)
    for ci in range(1, NUM_CLIENTS):
        cip  = f"10.0.0.{200 + ci - 1}"
        cmac = f"00:00:00:00:02:{ci:02x}"
        ch   = net.addHost(f"hc{ci + 1}", ip=f"{cip}/24", mac=cmac)
        net.addLink(ch, s1, delay="2ms", jitter="1ms")
        clients.append(ch)

    servers = []
    for idx in range(NUM_SERVERS):
        i    = idx + 2
        tier = _assign_tier(idx, NUM_SERVERS)
        h    = net.addHost(f"h{i}", ip=f"10.0.0.{i}/24",
                           mac=f"00:00:00:00:00:{i:02x}")
        net.addLink(h, s1, **_TIER_LINK[tier])
        servers.append(h)
    return net, clients, servers


def _prepare_script_dir():
    shutil.rmtree(_SCRIPT_DIR, ignore_errors=True)
    os.makedirs(_SCRIPT_DIR, mode=0o755, exist_ok=True)


# per-server processing-time profiles, generated deterministically so every
# algorithm run sees the same server behaviour (for fair comparison)
_SERVER_PROFILES = {}
for _idx in range(NUM_SERVERS):
    _hname = f"h{_idx + 2}"
    _tier  = _assign_tier(_idx, NUM_SERVERS)
    _p     = _TIER_PARAMS[_tier]
    # large prime spreads seeds apart to reduce correlation
    _rng   = random.Random(_idx * 0x9E3779B9 & 0xFFFFFFFF)
    _SERVER_PROFILES[_hname] = dict(
        base_ms        = int(_rng.uniform(_p['base_lo'],  _p['base_hi'])),
        jitter_ms      = int(_rng.uniform(_p['jit_lo'],   _p['jit_hi'])),
        spike_prob     = _p['spike'],
        error_prob     = _p['err'],
        max_concurrent = _rng.randint(_p['conc_lo'], _p['conc_hi']),
        spike_mul      = _p['spike_mul'],
        spike_period   = _p['spike_period'],
        spike_duration = _p['spike_duration'],
    )


def _write_server_script(host_name: str, host_ip: str) -> str:
    path = f"{_SCRIPT_DIR}/lb_server_{host_name}.py"
    body = f"served by {host_name} ({host_ip})"
    p = _SERVER_PROFILES.get(host_name, dict(base_ms=10, jitter_ms=5,
                                              spike_prob=0.05, error_prob=0.03))
    # deterministic seed so the same server produces the same delay/error sequence every run
    seed_val = sum(ord(c) * (i + 1) for i, c in enumerate(host_name))
    # stagger spike offsets so servers don't all spike at the same time
    host_idx        = int(host_name[1:]) - 2   # h2→0, h3→1, …
    spike_period    = 30.0
    spike_offset_v  = round((host_idx * spike_period) / max(NUM_SERVERS, 1), 2)
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
MAX_CONC      = {p.get('max_concurrent', 4)}

# ── Periodic load spike ──────────────────────────────────────────
# Simulates real-world events: GC pause, noisy-neighbour VM, cron job,
# OS maintenance, CPU frequency scaling, etc.
#
# SPIKE_MUL = 5 means during a spike a normally 10 ms server responds in
# 50 ms.  Combined with the semaphore, concurrent requests pile up:
#   active_conns rises → AP detects the overload within 1-2 requests
#   avg_flow_dur EWMA rises slowly → LRT detects after 5-6 requests
# This 3-5 request head-start for AP translates to measurably lower
# mean and tail latency across the entire experiment.
#
# Spike schedule is deterministic (same offset every run) = fair comparison.
SPIKE_PERIOD   = {p.get('spike_period',   30.0):.1f}   # seconds between spike events
SPIKE_DURATION = {p.get('spike_duration',  8.0):.1f}   # how long each spike lasts
SPIKE_MUL      = {p.get('spike_mul',       5.0):.1f}   # response-time multiplier during spike
SPIKE_OFFSET   = {spike_offset_v}  # stagger: server {host_name} starts spike at this offset

_spike = threading.Event()

def _spike_loop():
    time.sleep(SPIKE_OFFSET)
    while True:
        _spike.set()
        time.sleep(SPIKE_DURATION)
        _spike.clear()
        time.sleep(SPIKE_PERIOD - SPIKE_DURATION)

threading.Thread(target=_spike_loop, daemon=True).start()

# ── Concurrency cap (semaphore) ─────────────────────────────────────
# Real servers have finite CPU cores / thread-pool size.
# Tier A: MAX_CONC = 8-16  (modern multi-core servers)
# Tier B: MAX_CONC = 4-8   (standard rack servers)
# Tier C: MAX_CONC = 2-3   (underpowered / CPU-saturated)
# Tier D: MAX_CONC = 1     (legacy single-thread, e.g. old Java app server)
#
# When active connections exceed MAX_CONC, extra threads block here.
# The controller increments active_conns on each TCP SYN and decrements
# on FlowRemoved.  AP uses active_conns in its score; LRT ignores it.
# This is exactly when AP's advantage over LRT becomes measurable:
# during a spike, requests pile up at the semaphore → active_conns rises
# → AP immediately raises the score for that server → routes elsewhere.
_sem = threading.Semaphore(MAX_CONC)

class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        _sem.acquire()
        try:
            mul     = SPIKE_MUL if _spike.is_set() else 1.0
            delay_s = mul * (BASE_MS + random.uniform(-JITTER_MS, JITTER_MS)) / 1000.0
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


def prime_arp(clients, servers):
    """Pre-populate ARP so neither clients nor servers have to ARP on first pkt."""
    for client in clients:
        client.cmd(f"ip neigh replace {VIP} lladdr {VMAC} dev {client.name}-eth0")
    for srv in servers:
        # Teach every server about every client so return packets flow immediately.
        for client in clients:
            srv.cmd(
                f"ip neigh replace {client.IP()} lladdr {client.MAC()}"
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


def _write_client_script(total: int, rate: float, path: str = _CLIENT_SCRIPT) -> None:
    """Write a Python load-generator to *path*.

    Uses urllib.request (pure Python, in-process) instead of subprocess curl.
    This is critical: subprocess.run() inside threads spawned inside
    Mininet's host.cmd() shell does NOT inherit the host's network namespace —
    the forked curl processes run in the root namespace where the VIP doesn't
    exist.  urllib.request uses the calling process's own socket, which IS
    in the correct namespace.
    """
    delay = 1.0 / rate
    script = f"""#!/usr/bin/env python3
import threading, time, json, random
import urllib.request, urllib.error

TOTAL   = {total}
DELAY   = {delay:.4f}   # 1 / rate  ({rate:.1f} req/s)
VIP     = "{VIP}"
TIMEOUT = 15.0

# traffic class mix: (class_name, vip_port, cumulative_weight)
_MIX_CUM = [
    ("http",  8000, 0.35),
    ("maps",   443, 0.50),
    ("video",  554, 0.70),
    ("audio", 1755, 0.85),
    ("voip",  5060, 1.00),
]

def _pick_class():
    r = random.random()
    for cls, port, cum in _MIX_CUM:
        if r < cum:
            return cls, port
    return "http", 8000

results = [None] * TOTAL
_lock   = threading.Lock()

def fire(i):
    tc, port = _pick_class()
    url = f"http://{{VIP}}:{{port}}/"
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        body = f"error: HTTP {{exc.code}}"
    except Exception as exc:
        body = f"error: {{exc}}"
    elapsed = round(time.time() - t0, 4)
    with _lock:
        results[i] = {{"i": i + 1, "elapsed": elapsed, "body": body, "tc": tc}}

threads = []
for i in range(TOTAL):
    t = threading.Thread(target=fire, args=(i,), daemon=True)
    t.start()
    threads.append(t)
    if i < TOTAL - 1:
        time.sleep(DELAY)

for t in threads:
    t.join(timeout=TIMEOUT + 10.0)

print(json.dumps(results))
"""
    with open(path, "w") as fh:
        fh.write(script)


def run_requests(clients, n):
    """Fire n HTTP GETs at RATE req/s distributed across all clients.

    With a single client the behaviour is identical to before.
    With multiple clients each fires n//len(clients) requests concurrently
    using Python threads (safe in Mininet — each host.cmd() runs in the
    host's own network namespace).
    """
    import threading

    server_names = [f"h{2 + i}" for i in range(NUM_SERVERS)]
    num_clients  = len(clients)

    # Divide requests as evenly as possible; last client gets the remainder.
    base  = n // num_clients
    slices = [base] * num_clients
    slices[-1] += n - base * num_clients

    # Each client fires at RATE/N so the aggregate load on the pool stays
    # constant regardless of how many clients are used.  This makes runs with
    # different client counts comparable to each other.
    per_client_rate = RATE / num_clients

    print(f"\nFiring {n} requests at {RATE:.1f} req/s total to http://{VIP}:{HTTP_PORT}/"
          f"  ({num_clients} client(s) × {per_client_rate:.1f} req/s each, "
          f"{NUM_SERVERS} servers, Tier D capacity ~1.0-1.25 req/s)")

    results: list = [None] * num_clients

    def _fire(idx: int, client, count: int) -> None:
        script_path = f"/tmp/lb_client_{idx}.py"
        _write_client_script(count, per_client_rate, script_path)
        raw = client.cmd(f"python3 {script_path}")
        raw = raw.strip()
        json_start = raw.find('[')
        if json_start > 0:
            raw = raw[json_start:]
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"[ERROR] client {idx} could not parse output ({exc}): {raw[:300]}")
            results[idx] = []
            return
        recs = []
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
                # Distinguish real timeouts from HTTP errors
                if "timed out" in body or "TimeoutError" in body or "urlopen error" in body and "timed out" in body:
                    status = "timeout"
                else:
                    status = "error"
                srv = "error"
            recs.append({"req": req_i, "server": srv, "status": status,
                         "elapsed_s": elapsed, "traffic_class": item.get("tc", "http"),
                         "body": body[:80]})
        results[idx] = recs

    if num_clients == 1:
        _fire(0, clients[0], n)
    else:
        threads = [
            threading.Thread(target=_fire, args=(i, clients[i], slices[i]), daemon=True)
            for i in range(num_clients)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # Merge results from all clients and renumber requests globally.
    _print_step = max(1, n // 20)
    records = []
    global_i = 1
    for idx, recs in enumerate(results):
        for r in (recs or []):
            r["req"] = global_i
            if r["status"] == "timeout":
                tag = "  \033[31m[TIMEOUT]\033[0m"
            elif r["status"] in ("error", "other"):
                tag = "  \033[33m[HTTP err]\033[0m"
            else:
                tag = ""
            srv = r["server"]
            if n <= 100 or global_i % _print_step == 0 or global_i == n or r["status"] == "error":
                tier = _assign_tier(server_names.index(srv), NUM_SERVERS) if srv in server_names else "?"
                cli_tag = f" c{idx+1}" if num_clients > 1 else ""
                tc_tag = f"[{r.get('traffic_class', 'http')[:4]}]"
                print(f"  [{global_i:5d}/{n}]{cli_tag} {srv:6s} tier={tier} {tc_tag}  ({r['elapsed_s']*1000:.0f} ms){tag}")
            records.append(r)
            global_i += 1

    return records


def save_csv(records, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["req", "server", "status", "elapsed_s", "traffic_class", "body"])
        w.writeheader()
        w.writerows(records)
    print(f"\nPer-request CSV -> {path}")


def main():
    setLogLevel("warning")       # suppress mininet noise; controller logs separately
    net, clients, servers = build()
    h1 = clients[0]             # first client — used for warm-up and wait check
    net.build()
    net.start()

    try:
        # ── Verify OVS port assignments match what the controller expects ──
        # OVS normally assigns ports sequentially, but it is worth confirming
        # so that debugging is quick if something is off.
        import subprocess as _sp
        _portmap = {}   # hostname -> actual OVS port number
        try:
            _ofctl = _sp.check_output(
                ["sudo", "ovs-ofctl", "-O", "OpenFlow13", "show", "s1"],
                stderr=_sp.DEVNULL, text=True,
            )
            import re as _re
            for _m in _re.finditer(r'(\d+)\(([^)]+)\)', _ofctl):
                _portno, _ifname = int(_m.group(1)), _m.group(2)
                # interface names look like "h2-eth0", "hc3-eth0"
                _hname = _ifname.split("-")[0]
                _portmap[_hname] = _portno
        except Exception as _e:
            print(f"[WARN] could not read OVS port map: {_e}")

        if _portmap:
            import json as _json
            _portmap_path = "/tmp/lb_portmap.json"
            with open(_portmap_path, "w") as _pf:
                _json.dump(_portmap, _pf)
            print(f"OVS port map written to {_portmap_path}")
            expected_offset = 1 + NUM_CLIENTS
            first_server_name = f"h{2}"
            actual_port = _portmap.get(first_server_name)
            if actual_port is not None and actual_port != expected_offset:
                print(f"[WARN] Port mismatch: controller expects {first_server_name} "
                      f"on port {expected_offset}, OVS says port {actual_port}. "
                      f"Set LB_SERVER_PORT_OFFSET={actual_port} to fix.")
            else:
                print(f"Port check OK: {first_server_name} on port "
                      f"{actual_port or expected_offset} (expected {expected_offset}).")

        disable_offload(net.hosts)
        start_servers(servers)
        # Wait proportionally to the number of servers so all Python HTTP
        # processes are ready before warm-up starts.
        _srv_start_sleep = max(1.5, 0.05 * NUM_SERVERS)
        time.sleep(_srv_start_sleep - 1.0)   # start_servers already sleeps 1.0 s
        prime_arp(clients, servers)

        for cl in clients:
            print(f"  client : {cl.name} ({cl.IP()}) "
                  f"port={_portmap.get(cl.name, '?')}")
        for s in servers:
            print(f"  server : {s.name} ({s.IP()})  http://*:{HTTP_PORT} "
                  f"port={_portmap.get(s.name, '?')}")
        print(f"  VIP    : {VIP}")

        if not wait_for_controller(h1):
            print("[WARN] controller did not respond; continuing anyway")

        # ── Quick connectivity check: try one direct request to the fastest server ──
        # If even the direct request fails, the server HTTP process or ARP is broken.
        # If VIP fails but direct succeeds, the controller port mapping is wrong.
        _fast_server = servers[0]
        _check_direct = h1.cmd(
            f"python3 -c \"import urllib.request; "
            f"urllib.request.urlopen('http://{_fast_server.IP()}:{HTTP_PORT}/', timeout=2); "
            f"print('direct OK')\" 2>&1"
        ).strip()
        _check_vip = h1.cmd(
            f"python3 -c \"import urllib.request; "
            f"urllib.request.urlopen('http://{VIP}:{HTTP_PORT}/', timeout=2); "
            f"print('VIP OK')\" 2>&1"
        ).strip()
        print(f"  Connectivity: direct={_check_direct!r}  VIP={_check_vip!r}")
        if "VIP OK" not in _check_vip:
            print("[WARN] VIP connectivity check failed — controller may have wrong port assignments.")
            print("       Check Ryu log: grep 'portmap\\|LB:' /tmp/lb_ryu.log | head -20")

        # ── VIP warm-up ────────────────────────────────────────────────────
        # Fire 5 rounds × NUM_SERVERS requests through the VIP (not direct IPs)
        # before the timed experiment.  This seeds avg_flow_dur_s for every
        # server in the controller so all algorithms start the real test with
        # real latency measurements instead of zero.
        #
        # WHY THIS MATTERS FOR AP:
        # Without VIP warm-up, avg_flow_dur_s = 0 for all servers at t=0.
        # AP's score = (0+1) × _MIN_DUR / dyn_weight = tiny constant for every
        # server → AP picks randomly (= RR) for the first 20-30 requests.
        # With VIP warm-up, AP immediately knows h2 (fast) from h11 (slow)
        # and routes to fast servers from request #1.
        #
        # 5 rounds gives each server ~5 completed flows → DBA EWMA (α=0.25)
        # converges to within ~75% of steady-state after 5 updates.
        # Interval = 50 ms (was 150 ms): covers all N servers in 0.05×5N s
        # so the warm-up finishes well before DRAIN_SECS.
        #
        # The warm-up results are NOT saved to the experiment CSV.
        warmup_rounds = 5
        warmup_n = warmup_rounds * NUM_SERVERS
        print(f"\nVIP warm-up: {warmup_n} requests ({warmup_rounds} rounds × {NUM_SERVERS} servers) to seed server metrics…")
        _wu_script = "/tmp/lb_warmup.py"
        with open(_wu_script, "w") as _wf:
            _wf.write(f"""import urllib.request, time, threading

TOTAL   = {warmup_n}
DELAY   = {1.0 / max(warmup_n, 1):.4f}   # stagger launches slightly so RR cycles all servers
URL     = "http://{VIP}:{HTTP_PORT}/"
TIMEOUT = 5.0   # warm-up timeout: longer than before to actually complete Tier D requests

def _fire(i):
    try: urllib.request.urlopen(URL, timeout=TIMEOUT)
    except: pass

threads = [threading.Thread(target=_fire, args=(i,), daemon=True) for i in range(TOTAL)]
for i, t in enumerate(threads):
    t.start()
    time.sleep(DELAY)
for t in threads:
    t.join(timeout=TIMEOUT + 1.0)
print("warmup done")
""")
        h1.cmd(f"python3 {_wu_script}")
        print(f"  Done. Waiting {DRAIN_SECS}s for FlowRemoved events to arrive…")
        time.sleep(DRAIN_SECS)

        records = run_requests(clients, NUM_REQUESTS)
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
